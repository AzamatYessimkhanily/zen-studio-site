# main/management/commands/send_reminders.py

from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
import datetime
import gspread
from google.oauth2.service_account import Credentials
import requests
from main.views import get_door_code_and_instructions, normalize_phone_for_sheet

# Настройки доступа (как в views.py)
SERVICE_ACCOUNT_FILE = settings.BASE_DIR / 'sheetsapi-443912-7487420df9cd.json'

class Command(BaseCommand):
    help = 'Отправляет напоминания о бронировании за 3-6 часов'

    def handle(self, *args, **options):
        # 1. Проверяем время работы бота (08:00 - 23:00)
        # Используем локальное время сервера/Астаны
        tz = timezone.get_current_timezone()
        now = timezone.now().astimezone(tz)
        
        if not (8 <= now.hour < 23):
            self.stdout.write("Сейчас нерабочее время для рассылки (не 08:00-23:00).")
            return

        self.stdout.write(f"Запуск проверки напоминаний: {now}")

        try:
            # 2. Подключаемся к Гуглу
            creds = Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE,
                scopes=["https://www.googleapis.com/auth/spreadsheets"]
            )
            client = gspread.authorize(creds)
            sh = client.open_by_url(settings.CLIENTS_HISTORY_SPREADSHEET_URL)
            ws = sh.worksheet("История клиентов")

            # Получаем все записи (список словарей для удобства)
            records = ws.get_all_records()
            # Нужно также получить заголовки, чтобы знать индекс колонки "Напоминание" для обновления
            headers = ws.row_values(1)
            try:
                remind_col_idx = headers.index("Напоминание") + 1
            except ValueError:
                self.stdout.write("Колонка 'Напоминание' не найдена. Скрипт остановлен.")
                return

            # 3. Проходим по записям
            for i, row in enumerate(records):
                row_num = i + 2 # +1 за заголовки, +1 т.к. нумерация с 1
                
                # Пропускаем, если уже отправлено
                if str(row.get("Напоминание", "")).lower() in ["да", "yes", "sent", "отправлено"]:
                    continue

                # Парсим дату и время брони
                date_str = str(row.get("Дата", "")).strip()
                time_str = str(row.get("Время", "")).strip()
                
                if not date_str or not time_str: continue

                try:
                    # Собираем полный datetime начала брони
                    booking_start_naive = datetime.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
                    booking_start = tz.localize(booking_start_naive)
                except ValueError:
                    continue # Ошибка формата даты

                # Считаем разницу во времени
                time_diff = booking_start - now
                hours_diff = time_diff.total_seconds() / 3600

                # 4. ЛОГИКА: Если до брони осталось от 3 до 6 часов
                if 3 <= hours_diff <= 6:
                    room_name = row.get("Кабинет", "")
                    client_phone = str(row.get("Телефон", ""))
                    
                    if not client_phone or not room_name: continue

                    # Получаем АКТУАЛЬНЫЙ код двери
                    door_code, _, _ = get_door_code_and_instructions(room_name)
                    if not door_code: door_code = "Уточните у администратора"

                    # Формируем сообщение
                    message = (
                        f"👋 Напоминание о бронировании сегодня!\n\n"
                        f"🏠 Кабинет: {room_name}\n"
                        f"🗓 Время: {time_str}\n\n"
                        f"🔑 Актуальный код для открытия ключницы: *{door_code}*\n\n"
                        f"Ждем вас! 🌿"
                    )

                    # Отправляем WhatsApp
                    self.send_whatsapp(client_phone, message)
                    
                    # Отмечаем в таблице
                    ws.update_cell(row_num, remind_col_idx, "Отправлено")
                    self.stdout.write(f"Напоминание отправлено: {client_phone} (Строка {row_num})")

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Ошибка: {e}"))

    def send_whatsapp(self, phone, message):
        """Вспомогательная функция отправки"""
        url = getattr(settings, 'BOT_WHATSAPP_API_URL', None)
        if not url: return
        
        # Нормализация номера
        clean_phone = ''.join(filter(str.isdigit, str(phone)))
        if clean_phone.startswith('8'): clean_phone = '7' + clean_phone[1:]
        
        chat_id = f"{clean_phone}@c.us"
        try:
            requests.post(url, json={'chat_id': chat_id, 'message': message}, timeout=5)
        except Exception as e:
            print(f"Ошибка отправки WA: {e}")