from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
from django.utils.timezone import make_aware
import datetime
import gspread
from google.oauth2.service_account import Credentials
import requests
from main.views import get_door_code_and_instructions

SERVICE_ACCOUNT_FILE = settings.BASE_DIR / 'sheetsapi-443912-7487420df9cd.json'

class Command(BaseCommand):
    help = 'Отправляет напоминания (ловит всех: и заранее, и впритык)'

    def handle(self, *args, **options):
        # 1. Настройка времени
        tz = timezone.get_current_timezone()
        now = timezone.now().astimezone(tz)
        
        # Если хотите тестировать ночью, закомментируйте строки ниже:
        if not (8 <= now.hour < 23):
            self.stdout.write(f"[{now.strftime('%H:%M')}] Нерабочее время. Пропуск.")
            # return  <-- Раскомментируйте return, когда закончите тесты!

        self.stdout.write(f"⏳ Проверка напоминаний на {now.strftime('%H:%M')}...")

        try:
            # 2. Подключение к таблице
            creds = Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE,
                scopes=["https://www.googleapis.com/auth/spreadsheets"]
            )
            client = gspread.authorize(creds)
            sh = client.open_by_url(settings.CLIENTS_HISTORY_SPREADSHEET_URL)
            ws = sh.worksheet("История клиентов")

            records = ws.get_all_records()
            headers = ws.row_values(1)
            
            # Ищем или создаем колонку "Напоминание"
            if "Напоминание" not in headers:
                new_col_idx = len(headers) + 1
                if new_col_idx > ws.col_count: ws.resize(cols=new_col_idx)
                ws.update_cell(1, new_col_idx, "Напоминание")
                remind_col_idx = new_col_idx
            else:
                remind_col_idx = headers.index("Напоминание") + 1

            # 3. Перебор броней
            updates_count = 0
            for i, row in enumerate(records):
                row_num = i + 2 
                
                # Если уже отправлено — пропускаем
                status = str(row.get("Напоминание", "")).lower()
                if status in ["да", "yes", "sent", "отправлено"]:
                    continue

                date_str = str(row.get("Дата", "")).strip()
                time_str = str(row.get("Время", "")).strip()
                if not date_str or not time_str: continue

                try:
                    booking_start_naive = datetime.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
                    booking_start = make_aware(booking_start_naive, tz)
                except ValueError:
                    continue 

                # Считаем разницу
                time_diff = booking_start - now
                hours_diff = time_diff.total_seconds() / 3600

                # === ВОТ ТУТ БЫЛА ПРОБЛЕМА ===
                # Было: if 3 <= hours_diff <= 6:
                # Стало: от 15 минут (0.25) до 6 часов
                if 0.25 <= hours_diff <= 6:
                    
                    client_phone = str(row.get("Телефон", ""))
                    room_name = str(row.get("Кабинет", "")).strip()
                    
                    if not client_phone: continue

                    # Получаем код доступа
                    door_code, _, _ = get_door_code_and_instructions(room_name)
                    if not door_code: door_code = "Код уточняется"

                    message = (
                        f"👋 Напоминание! Ждем вас сегодня.\n\n"
                        f"🏠 Кабинет: {room_name}\n"
                        f"🗓 Время: {time_str}\n"
                        f"🔑 Код от двери: *{door_code}*\n\n"
                        f"Zen Studio 🌿"
                    )

                    self.send_whatsapp(client_phone, message)
                    
                    # Ставим отметку "Отправлено"
                    ws.update_cell(row_num, remind_col_idx, "Отправлено")
                    self.stdout.write(self.style.SUCCESS(f"✅ Отправлено: {client_phone} (осталось {round(hours_diff, 1)} ч.)"))
                    updates_count += 1
                
                # (Для отладки) Раскомментируйте, если хотите видеть, почему пропускает:
                # else:
                #    self.stdout.write(f"Пропуск {time_str}: осталось {round(hours_diff, 1)} ч. (не подходит под условие)")

            self.stdout.write(f"Итог: отправлено {updates_count} сообщений.")

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
        except: pass