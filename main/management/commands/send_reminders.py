# main/management/commands/send_reminders.py

from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
import datetime
import gspread
from google.oauth2.service_account import Credentials
import requests
from main.views import get_door_code_and_instructions

SERVICE_ACCOUNT_FILE = settings.BASE_DIR / 'sheetsapi-443912-7487420df9cd.json'

class Command(BaseCommand):
    help = 'Отправляет напоминания о бронировании за 3-6 часов'

    def handle(self, *args, **options):
        # 1. Проверка времени (08:00 - 23:00)
        tz = timezone.get_current_timezone()
        now = timezone.now().astimezone(tz)
        
        # Если нужно тестировать ночью — закомментируйте эти 3 строки
        if not (8 <= now.hour < 23):
            self.stdout.write(f"[{now.strftime('%H:%M')}] Сейчас нерабочее время (не 08:00-23:00). Пропуск.")
            return

        self.stdout.write(f"Запуск проверки напоминаний: {now}")

        try:
            # 2. Подключение к Гуглу
            creds = Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE,
                scopes=["https://www.googleapis.com/auth/spreadsheets"]
            )
            client = gspread.authorize(creds)
            sh = client.open_by_url(settings.CLIENTS_HISTORY_SPREADSHEET_URL)
            ws = sh.worksheet("История клиентов")

            records = ws.get_all_records()
            headers = ws.row_values(1)
            
            # === АВТО-СОЗДАНИЕ КОЛОНКИ (С РАСШИРЕНИЕМ) ===
            if "Напоминание" not in headers:
                self.stdout.write("⚠️ Колонка 'Напоминание' не найдена. Создаю...")
                new_col_idx = len(headers) + 1
                
                # ВАЖНО: Если таблица мала, расширяем её
                if new_col_idx > ws.col_count:
                    ws.resize(cols=new_col_idx)
                    
                ws.update_cell(1, new_col_idx, "Напоминание")
                
                remind_col_idx = new_col_idx
                headers.append("Напоминание")
            else:
                remind_col_idx = headers.index("Напоминание") + 1
            # ================================================

            # 3. Проход по записям
            for i, row in enumerate(records):
                row_num = i + 2 
                
                # Пропускаем, если уже отправлено
                status = str(row.get("Напоминание", "")).lower()
                if status in ["да", "yes", "sent", "отправлено"]:
                    continue

                date_str = str(row.get("Дата", "")).strip()
                time_str = str(row.get("Время", "")).strip()
                
                if not date_str or not time_str: continue

                try:
                    booking_start_naive = datetime.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
                    booking_start = tz.localize(booking_start_naive)
                except ValueError:
                    continue 

                # Разница во времени
                time_diff = booking_start - now
                hours_diff = time_diff.total_seconds() / 3600

                # 4. Логика отправки (3-6 часов)
                if 3 <= hours_diff <= 6:
                    room_name = row.get("Кабинет", "")
                    client_phone = str(row.get("Телефон", ""))
                    
                    if not client_phone or not room_name: continue

                    # Получаем код
                    door_code, _, _ = get_door_code_and_instructions(room_name)
                    if not door_code: door_code = "Уточните у администратора"

                    message = (
                        f"👋 Напоминание о бронировании сегодня!\n\n"
                        f"🏠 Кабинет: {room_name}\n"
                        f"🗓 Время: {time_str}\n\n"
                        f"🔑 Актуальный код для открытия ключницы: *{door_code}*\n\n"
                        f"Ждем вас! 🌿"
                    )

                    self.send_whatsapp(client_phone, message)
                    
                    # Пишем "Отправлено" в таблицу
                    ws.update_cell(row_num, remind_col_idx, "Отправлено")
                    self.stdout.write(self.style.SUCCESS(f"✅ Напоминание отправлено: {client_phone}"))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Ошибка: {e}"))

    def send_whatsapp(self, phone, message):
        url = getattr(settings, 'BOT_WHATSAPP_API_URL', None)
        if not url: return
        
        clean_phone = ''.join(filter(str.isdigit, str(phone)))
        if clean_phone.startswith('8'): clean_phone = '7' + clean_phone[1:]
        
        chat_id = f"{clean_phone}@c.us"
        try:
            requests.post(url, json={'chat_id': chat_id, 'message': message}, timeout=5)
        except Exception as e:
            print(f"Ошибка отправки WA: {e}")