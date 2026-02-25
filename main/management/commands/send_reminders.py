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
    help = 'Напоминалка (Умная: утро ловит с вечера, день ловит за 3 часа)'

    def handle(self, *args, **options):
        tz = timezone.get_current_timezone()
        now = timezone.now().astimezone(tz)
        
        # 1. Скрипт работает только с 07:00 до 23:00
        if not (7 <= now.hour < 23):
            self.stdout.write(f"[{now.strftime('%H:%M')}] 🌙 Сплю (07-23).")
            return

        self.stdout.write(f"⏳ Проверка напоминаний на {now.strftime('%H:%M')}...")

        try:
            creds = Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE,
                scopes=["https://www.googleapis.com/auth/spreadsheets"]
            )
            client = gspread.authorize(creds)
            sh = client.open_by_url(settings.CLIENTS_HISTORY_SPREADSHEET_URL)
            ws = sh.worksheet("История клиентов")

            records = ws.get_all_records()
            headers = ws.row_values(1)
            headers_lower = [h.lower().strip() for h in headers]
            
            if "Напоминание" not in headers:
                new_col_idx = len(headers) + 1
                if new_col_idx > ws.col_count: ws.resize(cols=new_col_idx)
                ws.update_cell(1, new_col_idx, "Напоминание")
                remind_col_idx = new_col_idx
            else:
                remind_col_idx = headers.index("Напоминание") + 1

            # Находим колонки цены и статуса для проверки отмены
            price_col_name = None
            for name in ['цена', 'стоимость', 'оплата']:
                if name in headers_lower:
                    price_col_name = headers[headers_lower.index(name)]
                    break

            # Собираем все колонки статуса/примечания
            status_col_names = []
            for i, h in enumerate(headers_lower):
                if 'статус' in h or 'примечан' in h:
                    status_col_names.append(headers[i])

            updates_count = 0
            skipped_cancelled = 0

            for i, row in enumerate(records):
                row_num = i + 2 
                status = str(row.get("Напоминание", "")).lower()
                if status in ["да", "yes", "sent", "отправлено"]:
                    continue

                # === ПРОВЕРКА ОТМЕНЫ ===
                is_cancelled = False

                # 1. Проверяем колонку цены
                if price_col_name:
                    price_val = str(row.get(price_col_name, "")).lower().strip()
                    if 'отменен' in price_val or 'отмена' in price_val:
                        is_cancelled = True

                # 2. Проверяем колонки статуса/примечания
                if not is_cancelled:
                    for col_name in status_col_names:
                        cell_val = str(row.get(col_name, "")).lower().strip()
                        if 'отменен' in cell_val or 'отмена' in cell_val:
                            is_cancelled = True
                            break

                if is_cancelled:
                    skipped_cancelled += 1
                    continue
                # === КОНЕЦ ПРОВЕРКИ ОТМЕНЫ ===

                date_str = str(row.get("Дата", "")).strip()
                time_str = str(row.get("Время", "")).strip()
                if not date_str or not time_str: continue

                try:
                    booking_start_naive = datetime.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
                    booking_start = make_aware(booking_start_naive, tz)
                except ValueError:
                    continue 

                time_diff = booking_start - now
                hours_diff = time_diff.total_seconds() / 3600

                # === УМНОЕ УСЛОВИЕ ===
                is_morning_booking = (booking_start.hour < 10)  # Если бронь до 10:00 утра

                should_remind = False
                
                if is_morning_booking:
                    if 0.25 <= hours_diff <= 14:
                        should_remind = True
                else:
                    if 0.25 <= hours_diff <= 3:
                        should_remind = True

                if should_remind:
                    client_phone = str(row.get("Телефон", ""))
                    room_name = str(row.get("Кабинет", "")).strip()
                    if not client_phone: continue

                    door_code, _, _ = get_door_code_and_instructions(room_name)

                    # Формируем сообщение (без кода двери если его нет)
                    message = (
                        f"👋 Напоминание! Ждем вас.\n\n"
                        f"🏠 Кабинет: {room_name}\n"
                        f"🗓 Время: {time_str}\n"
                    )
                    if door_code:
                        message += f"🔑 Код от двери: *{door_code}*\n"
                    message += f"\nZen Studio 🌿"

                    self.send_whatsapp(client_phone, message)
                    ws.update_cell(row_num, remind_col_idx, "Отправлено")
                    self.stdout.write(self.style.SUCCESS(f"✅ Отправлено: {client_phone} ({room_name})"))
                    updates_count += 1

            self.stdout.write(f"Итог: отправлено {updates_count}, пропущено отменённых {skipped_cancelled}")

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