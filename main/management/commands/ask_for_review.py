from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
from django.utils.timezone import make_aware
import datetime
import gspread
from google.oauth2.service_account import Credentials
import requests
import time  # Добавили для пауз

# Путь к файлу ключей
SERVICE_ACCOUNT_FILE = settings.BASE_DIR / 'sheetsapi-443912-7487420df9cd.json'

# ТАБЛИЦА С НАСТРОЙКАМИ (где лежит ссылка на 2ГИС)
SETTINGS_SPREADSHEET_URL = 'https://docs.google.com/spreadsheets/d/1sorzD7-esaHJmZHnVm276yrdYPbpyOSxvHRq77uFomA/edit'

class Command(BaseCommand):
    help = 'Отправляет просьбу об отзыве после окончания брони (Только 1 раз на клиента)'

    def handle(self, *args, **options):
        tz = timezone.get_current_timezone()
        now = timezone.now().astimezone(tz)
        
        # Не отправлять ночью (с 23:00 до 09:00)
        if not (9 <= now.hour < 23):
            self.stdout.write(f"[{now.strftime('%H:%M')}] 🌙 Ночь. Пропуск рассылки.")
            return

        self.stdout.write(f"[{now.strftime('%H:%M:%S')}] 🚀 Запуск проверки отзывов...")

        try:
            # === АВТОРИЗАЦИЯ ===
            creds = Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE,
                scopes=["https://www.googleapis.com/auth/spreadsheets"]
            )
            client = gspread.authorize(creds)
            
            # ==========================================================
            # ЧАСТЬ 1: ПОЛУЧАЕМ ССЫЛКУ НА ОТЗЫВ
            # ==========================================================
            review_link = None
            try:
                sh_settings = client.open_by_url(SETTINGS_SPREADSHEET_URL)
                ws_links = sh_settings.worksheet("Ссылки") 
                
                col_values = ws_links.col_values(1)
                found_row = None
                
                for idx, val in enumerate(col_values):
                    if "2gis" in str(val).lower() or "2гис" in str(val).lower():
                        found_row = idx + 1
                        break
                
                if found_row:
                    review_link = ws_links.cell(found_row, 2).value
                
                if not review_link:
                    self.stdout.write(self.style.ERROR("❌ Ссылка на 2GIS не найдена!"))
                    return
                
                self.stdout.write(f"🔗 Ссылка: {review_link}")

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"❌ Ошибка таблицы Ссылок: {e}"))
                return

            # ==========================================================
            # ЧАСТЬ 2: ПРОВЕРЯЕМ БРОНИ
            # ==========================================================
            try:
                history_url = getattr(settings, 'CLIENTS_HISTORY_SPREADSHEET_URL', None)
                if not history_url:
                    self.stdout.write(self.style.ERROR("❌ В settings.py нет URL истории"))
                    return

                sh_history = client.open_by_url(history_url)
                
                # Умный поиск листа
                sheet_names = [ws.title for ws in sh_history.worksheets()]
                ws_history = None
                
                try:
                    ws_history = sh_history.worksheet("История Клиентов")
                except gspread.WorksheetNotFound:
                    for name in sheet_names:
                        if "история" in name.lower() and "клиентов" in name.lower():
                            ws_history = sh_history.worksheet(name)
                            break
                
                if not ws_history:
                    self.stdout.write(self.style.ERROR(f"❌ Лист истории не найден. Есть: {sheet_names}"))
                    return

                records = ws_history.get_all_records()
                headers = ws_history.row_values(1)

                if "Отзыв" not in headers:
                    self.stdout.write("⚠️ Создаю колонку 'Отзыв'...")
                    new_col_idx = len(headers) + 1
                    if new_col_idx > ws_history.col_count:
                        ws_history.resize(cols=new_col_idx)
                    ws_history.update_cell(1, new_col_idx, "Отзыв")
                    headers.append("Отзыв")
                    review_col_idx = new_col_idx
                    records = ws_history.get_all_records()
                else:
                    review_col_idx = headers.index("Отзыв") + 1

                # === ГЛОБАЛЬНАЯ ЗАЩИТА ОТ ПОВТОРОВ ===
                already_notified_phones = set()
                
                for row in records:
                    status = str(row.get("Отзыв", "")).strip().lower()
                    phone_raw = str(row.get("Телефон", "")).strip()
                    clean_ph = self.normalize_phone(phone_raw)
                    
                    if clean_ph and status in ["да", "yes", "sent", "отправлено", "отправлено (ранее)", "1", "ранее отправлено"]:
                        already_notified_phones.add(clean_ph)
                
                self.stdout.write(f"ℹ️ В базе {len(already_notified_phones)} номеров, уже получавших рассылку.")
                
                count_sent = 0
                
                for i, row in enumerate(records):
                    row_num = i + 2 
                    
                    # Если статус уже стоит - пропускаем молча
                    status = str(row.get("Отзыв", "")).strip().lower()
                    if status: 
                        continue

                    date_str = str(row.get("Дата", "")).strip()
                    time_str = str(row.get("Время", "")).strip()
                    duration_str = str(row.get("Длительность", "1")).replace(',', '.')
                    client_phone_raw = str(row.get("Телефон", "")).strip()
                    client_name = str(row.get("Имя", "Гость")).strip()
                    
                    clean_phone = self.normalize_phone(client_phone_raw)

                    if not date_str or not time_str or not clean_phone:
                        continue

                    # 3. ПРОВЕРКА НА ПОВТОР (ГЛОБАЛЬНАЯ)
                    if clean_phone in already_notified_phones:
                        # ВАЖНОЕ ИЗМЕНЕНИЕ: Мы НЕ пишем в таблицу "Ранее отправлено", чтобы не тратить лимиты!
                        # Мы просто пропускаем этот шаг. Скрипт запомнил это в памяти (set).
                        self.stdout.write(f"⏭️ Пропуск {clean_phone} (уже получал ранее)")
                        continue

                    try:
                        start_dt_naive = datetime.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
                        start_dt = make_aware(start_dt_naive, tz)
                        duration = float(duration_str)
                        end_dt = start_dt + datetime.timedelta(hours=duration)
                    except ValueError:
                        continue 

                    # 4. ПРОВЕРКА ВРЕМЕНИ (закончилось + не более 24 часов назад)
                    if end_dt < now and (now - end_dt).total_seconds() < 86400:
                        
                        message = (
                            f"Здравствуйте, {client_name}! 👋\n\n"
                            f"Спасибо, что выбрали Zen Studio. Надеемся, вам всё понравилось! 🌿\n\n"
                            f"Будем очень благодарны, если вы оставите отзыв — это помогает нам становиться лучше:\n"
                            f"{review_link}\n\n"
                            f"Ждем вас снова!"
                        )

                        self.send_whatsapp(clean_phone, message)
                        
                        # Пишем в таблицу ТОЛЬКО когда реально отправили
                        ws_history.update_cell(row_num, review_col_idx, "Отправлено")
                        
                        # Добавляем в локальный список защиты
                        already_notified_phones.add(clean_phone) 
                        
                        self.stdout.write(self.style.SUCCESS(f"✅ Отправлено: {clean_phone}"))
                        count_sent += 1
                        
                        # ПАУЗА, чтобы не злить Гугл (1 секунда между записями)
                        time.sleep(1.2)

                if count_sent == 0:
                    self.stdout.write("Нет новых завершенных броней для рассылки.")

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"❌ Ошибка обработки броней: {e}"))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Критическая ошибка: {e}"))

    def normalize_phone(self, phone):
        """Очищает номер до формата 7707..."""
        digits = ''.join(filter(str.isdigit, str(phone)))
        if not digits: return None
        if len(digits) == 10 and digits.startswith('7'): return '7' + digits
        if len(digits) == 11 and digits.startswith('8'): return '7' + digits[1:]
        if len(digits) == 11 and digits.startswith('7'): return digits
        return digits

    def send_whatsapp(self, phone, message):
        url = getattr(settings, 'BOT_WHATSAPP_API_URL', None)
        if not url: return
        
        chat_id = f"{phone}@c.us"
        try:
            requests.post(url, json={'chat_id': chat_id, 'message': message}, timeout=5)
        except Exception as e:
            print(f"❌ Ошибка WA: {e}")