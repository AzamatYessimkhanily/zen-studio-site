from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
from django.utils.timezone import make_aware
import datetime
import gspread
from google.oauth2.service_account import Credentials
import requests

SERVICE_ACCOUNT_FILE = settings.BASE_DIR / 'sheetsapi-443912-7487420df9cd.json'

class Command(BaseCommand):
    help = 'Отправляет просьбу об отзыве после окончания брони'

    def handle(self, *args, **options):
        tz = timezone.get_current_timezone()
        now = timezone.now().astimezone(tz)
        
        # Не беспокоить клиентов ночью (с 23:00 до 09:00)
        # Если бронь закончилась ночью, сообщение уйдет утром
        if not (9 <= now.hour < 23):
            self.stdout.write(f"[{now.strftime('%H:%M')}] Ночь. Пропуск рассылки отзывов.")
            return

        self.stdout.write(f"Запуск проверки завершенных броней: {now}")

        try:
            # 1. Подключение
            creds = Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE,
                scopes=["https://www.googleapis.com/auth/spreadsheets"]
            )
            client = gspread.authorize(creds)
            sh = client.open_by_url(settings.CLIENTS_HISTORY_SPREADSHEET_URL)
            
            # --- ПОЛУЧАЕМ ССЫЛКУ НА 2GIS ---
            try:
                ws_links = sh.worksheet("Ссылки")
                # Ищем ячейку с текстом "2GIS Отзыв" в первой колонке
                cell = ws_links.find("2GIS Отзыв", in_column=1)
                # Берем ссылку из соседней колонки (B)
                review_link = ws_links.cell(cell.row, 2).value
                if not review_link:
                    self.stdout.write(self.style.ERROR("Ссылка на 2GIS пустая!"))
                    return
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Ошибка получения ссылки: {e}"))
                return
            # -------------------------------

            ws_history = sh.worksheet("История клиентов")
            records = ws_history.get_all_records()
            headers = ws_history.row_values(1)

            # === АВТО-СОЗДАНИЕ КОЛОНКИ "Отзыв" ===
            if "Отзыв" not in headers:
                self.stdout.write("⚠️ Колонка 'Отзыв' не найдена. Создаю...")
                new_col_idx = len(headers) + 1
                if new_col_idx > ws_history.col_count:
                    ws_history.resize(cols=new_col_idx)
                ws_history.update_cell(1, new_col_idx, "Отзыв")
                review_col_idx = new_col_idx
            else:
                review_col_idx = headers.index("Отзыв") + 1
            # =====================================

            for i, row in enumerate(records):
                row_num = i + 2 
                
                # Пропускаем, если уже отправлено
                status = str(row.get("Отзыв", "")).lower()
                if status in ["да", "yes", "sent", "отправлено"]:
                    continue

                date_str = str(row.get("Дата", "")).strip()
                time_str = str(row.get("Время", "")).strip()
                duration_str = str(row.get("Длительность", "1")).replace(',', '.')
                
                if not date_str or not time_str: continue

                try:
                    # Дата начала
                    start_dt_naive = datetime.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
                    start_dt = make_aware(start_dt_naive, tz)
                    
                    # Дата окончания
                    duration = float(duration_str)
                    end_dt = start_dt + datetime.timedelta(hours=duration)
                    
                except ValueError:
                    continue 

                # Проверяем: Бронь УЖЕ закончилась?
                # И закончилась ли она недавно (например, в последние 24 часа), 
                # чтобы не слать тем, кто был год назад.
                if end_dt < now and (now - end_dt).total_seconds() < 86400:
                    
                    client_phone = str(row.get("Телефон", ""))
                    client_name = str(row.get("Имя", "Гость"))
                    
                    if not client_phone: continue

                    # Текст сообщения
                    message = (
                        f"Здравствуйте, {client_name}! 👋\n\n"
                        f"Спасибо, что выбрали Zen Studio. Надеемся, вам всё понравилось! 🌿\n\n"
                        f"Будем очень благодарны, если вы оставите отзыв — это помогает нам становиться лучше:\n"
                        f"{review_link}\n\n"
                        f"Ждем вас снова!"
                    )

                    self.send_whatsapp(client_phone, message)
                    
                    # Пишем "Отправлено"
                    ws_history.update_cell(row_num, review_col_idx, "Отправлено")
                    self.stdout.write(self.style.SUCCESS(f"✅ Просьба об отзыве отправлена: {client_phone}"))

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