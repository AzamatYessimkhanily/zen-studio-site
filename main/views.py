# main/views.py

from django.views.generic import TemplateView, DetailView
from django.shortcuts import get_object_or_404
from .models import *
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from google.oauth2 import service_account
from googleapiclient.discovery import build
import datetime
from django.http import JsonResponse, HttpResponseBadRequest
from django.utils.timezone import make_aware # Для работы с часовыми поясами Django
import pytz # Библиотека для часовых поясов
from .models import Room, Tariff, SiteSettings # Убедись, что все модели импортированы
from django.db.models import Q # Для сложных запросов
import gspread
from google.oauth2.service_account import Credentials
import requests
import json
import gspread
from google.oauth2.service_account import Credentials
from django.conf import settings
import re # Для парсинга "1-3 чел"
from decimal import Decimal, InvalidOperation # Для точной работы с часами (0.5)
from django.http import JsonResponse, HttpResponseBadRequest
from .models import SiteSettings # Убедись, что SiteSettings импортирована
# --- КОНСТАНТЫ И НАСТРОЙКИ ---
from django.utils import timezone
from .models import PendingBooking # Импортируем новую модель
from django.views.decorators.http import require_POST, require_GET # Для удобства
import base64
import tempfile
import os
import PyPDF2 # <-- Библиотека, которую мы установили
from django.core.files.base import ContentFile

SERVICE_ACCOUNT_FILE = settings.BASE_DIR / 'sheetsapi-443912-7487420df9cd.json'
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
ALMATY_TZ = pytz.timezone(settings.TIME_ZONE) # Часовой пояс из настроек Django

# --- ФУНКЦИЯ ДЛЯ ПОЛУЧЕНИЯ СЛОТОВ ---
# main/views.py

# --- ФУНКЦИЯ ДЛЯ ПОЛУЧЕНИЯ СЛОТОВ (ИСПРАВЛЕННАЯ) ---
# main/views.py

# --- ФУНКЦИЯ ДЛЯ ПОЛУЧЕНИЯ СЛОТОВ (ИСПРАВЛЕННАЯ) ---
def get_google_calendar_free_slots(room, date_str, duration_minutes=60):
    """Получает список свободных слотов из Google Календаря, учитывая резервы."""
    
    # === ИСПРАВЛЕНИЕ ===
    if not room.google_calendar_id:
        print(f"Error: Calendar ID is missing for room {room.pk}.")
        return None
        
    # Очищаем ID от невидимых пробелов и кавычек — это решает проблему с кнопкой "Найти"
    calendar_id = room.google_calendar_id.strip().replace('"', '').replace("'", "").replace('\n', '').replace('\r', '')
    # ===================

    # --- СНАЧАЛА определяем selected_date ---
    try:
        if not date_str:
            print("Error: date_str is missing.")
            return None
        selected_date = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        print(f"Error: Invalid date format received: {date_str}")
        return None # Возвращаем None при неверном формате даты
    except Exception as e: # Ловим другие возможные ошибки парсинга
        print(f"Error parsing date {date_str}: {e}")
        return None
    # --- КОНЕЦ определения selected_date ---

    try:
        # --- Теперь получаем активные резервы (используя selected_date) ---
        now = timezone.now()
        active_holds = PendingBooking.objects.filter(
            room=room,
            expires_at__gt=now, # Ищем только не истекшие
            start_time__date=selected_date # Теперь selected_date определена
        )
        # Сохраняем как timezone-aware datetime
        held_slots = [(timezone.localtime(h.start_time), timezone.localtime(h.end_time)) for h in active_holds]
        # --- Конец получения резервов ---

        # --- Подключение к Google Calendar API ---
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        service = build('calendar', 'v3', credentials=creds)

        # Начало и конец дня
        start_of_day = ALMATY_TZ.localize(datetime.datetime.combine(selected_date, datetime.time(0, 0, 0)))
        end_of_day = ALMATY_TZ.localize(datetime.datetime.combine(selected_date, datetime.time(23, 59, 59)))

        # Запрашиваем занятые слоты
        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=start_of_day.isoformat(),
            timeMax=end_of_day.isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])

        busy_slots = []
        for event in events:
            start_str = event['start'].get('dateTime', event['start'].get('date'))
            end_str = event['end'].get('dateTime', event['end'].get('date'))
            if not start_str or not end_str: continue # Пропускаем события без времени
            try:
                # Убедимся, что время в правильном формате и часовом поясе
                start_aware = datetime.datetime.fromisoformat(start_str.replace('Z', '+00:00'))
                end_aware = datetime.datetime.fromisoformat(end_str.replace('Z', '+00:00'))
                start = start_aware.astimezone(ALMATY_TZ)
                end = end_aware.astimezone(ALMATY_TZ)
                busy_slots.append((start, end))
            except ValueError:
                print(f"Warning: Could not parse event time: start='{start_str}', end='{end_str}'")
                continue # Пропускаем событие с неверным форматом времени

        # --- Логика поиска свободных слотов ---
        free_slots = []
        start_work_time = room.work_time_start or datetime.time(7, 0)
        end_work_time = room.work_time_end or datetime.time(23, 0)
        current_time = ALMATY_TZ.localize(datetime.datetime.combine(selected_date, start_work_time))
        day_end_limit = ALMATY_TZ.localize(datetime.datetime.combine(selected_date, end_work_time))
        time_step = datetime.timedelta(minutes=30)
        duration_delta = datetime.timedelta(minutes=duration_minutes)
        now_aware = timezone.now().astimezone(ALMATY_TZ) # Убедимся, что now тоже aware

        # --- ИСПРАВЛЕНИЕ ЛОГИКИ ОКОНЧАНИЯ РАБОЧЕГО ДНЯ ---
        while current_time < day_end_limit: 
            slot_start = current_time
            slot_end = current_time + duration_delta

            # Новая проверка: Убедимся, что КОНЕЦ слота не выходит за рамки рабочего дня
            if slot_end > day_end_limit:
                break # Заканчиваем цикл, т.к. слот (напр. 22:00-24:00) не помещается до 23:00
            # Проверяем, не в прошлом ли слот
            if slot_start < now_aware:
                current_time += time_step
                continue

            # Проверяем пересечение с резервами (held_slots)
            is_held = False
            for held_start, held_end in held_slots:
                # Сравниваем aware datetimes
                if slot_start < held_end and slot_end > held_start:
                    is_held = True
                    # Если пересекается с резервом, сдвигаем время за конец резерва
                    current_time = held_end
                    # Выравниваем до следующего 30-минутного интервала
                    if current_time.minute % 30 != 0:
                         current_time += datetime.timedelta(minutes=(30 - current_time.minute % 30))
                    break # Выходим из проверки резервов, идем на следующую итерацию while
            if is_held:
                continue # Переходим к следующей итерации while, этот слот зарезервирован

            # Проверяем пересечение с событиями календаря (busy_slots)
            is_free_in_calendar = True
            for busy_start, busy_end in busy_slots:
                # Сравниваем aware datetimes
                if slot_start < busy_end and slot_end > busy_start:
                    is_free_in_calendar = False
                    # Передвигаем current_time к концу занятого слота
                    current_time = busy_end
                    # Выравниваем
                    if current_time.minute % 30 != 0:
                        current_time += datetime.timedelta(minutes=(30 - current_time.minute % 30))
                    break # Начинаем следующую итерацию while со сдвинутого времени

            if is_free_in_calendar: # Если не зарезервирован И свободен в календаре
                free_slots.append({
                    'start': slot_start.strftime('%H:%M'),
                    'end': slot_end.strftime('%H:%M')
                })
                current_time += time_step # Переходим к следующему возможному слоту
            # Если был занят в календаре, current_time уже сдвинут и начнется новая итерация while

        return free_slots

    except Exception as e:
        print(f"Error accessing Google Calendar or PendingBookings: {e}")
        return None # Возвращаем None при любой ошибке
# --- Функция для получения кода двери из Google Sheets ---
# main/views.py
# ... (импорты gspread, Credentials, settings должны быть) ...

def get_door_code_and_instructions(room_name):
    """Читает Google Sheet и возвращает: код, сообщение для бота, и ДЕТАЛИ ДЛЯ САЙТА."""
    door_code = None
    instructions_text = None
    address = None
    final_message = ""
    
    # Словарь для передачи на фронтенд
    details = {
        "address": "Адрес уточняется у администратора",
        "enter_instruction": "",
        "general_info": ""
    }

    try:
        # --- Читаем 'Коды дверей и адреса' ---
        creds = Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE,
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
        )
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_url(settings.SPREADSHEET_URL)
        
        # 1. Лист с адресами
        try:
            worksheet_doors = spreadsheet.worksheet("Коды дверей и адреса")
            cell = worksheet_doors.find(room_name, in_column=1)
            if cell:
                row_data = worksheet_doors.row_values(cell.row)
                headers = worksheet_doors.row_values(1)
                room_details = dict(zip(headers, row_data))

                door_code = room_details.get("Код", "Код не найден")
                address = room_details.get("Адрес", "")
                instructions_text = room_details.get("Как открыть кабинет", "")
                
                # Сохраняем в словарь
                if address: details["address"] = address
                if instructions_text: details["enter_instruction"] = instructions_text
        except gspread.exceptions.WorksheetNotFound:
            print("Лист 'Коды дверей и адреса' не найден")

        # 2. Лист с общими инструкциями
        try:
            worksheet_instructions = spreadsheet.worksheet("Инструкции")
            trigger_text = "После оплаты скинуть без изменения текст полностью вместе с эмоджи:"
            instruction_cell = worksheet_instructions.find(trigger_text, in_column=1)
            post_payment_instructions = ""
            if instruction_cell:
                post_payment_instructions = worksheet_instructions.cell(instruction_cell.row, 2).value
                details["general_info"] = post_payment_instructions # Сохраняем общую инфу
        except gspread.exceptions.WorksheetNotFound:
            print("Лист 'Инструкции' не найден")

        # --- Формируем итоговое сообщение для WhatsApp (как и было) ---
        settings_obj = SiteSettings.objects.first()
        admin_phone = settings_obj.phone if settings_obj else "Номер админа не указан"

        final_message = f"✅ Ваша бронь кабинета {room_name} подтверждена!\n"
        if address: final_message += f"📍 Адрес: {address}\n"
        if door_code: final_message += f"🔑 Код двери: {door_code}\n"
        else: final_message += f"🔑 Код двери: Не найден\n"

        if instructions_text: final_message += f"\n🚪 Как открыть:\n{instructions_text}\n"
        if post_payment_instructions: final_message += f"\n{post_payment_instructions}\n"

        final_message += f"\n📞 По вопросам обращайтесь к администратору: {admin_phone}"

        # ВОЗВРАЩАЕМ 3 ЗНАЧЕНИЯ: Код, Сообщение, Словарь деталей
        return door_code, final_message.strip(), details

    except Exception as e:
        print(f"Error reading Google Sheets: {e}")
        return None, f"Ошибка получения инструкций.", details
# ... (остальной код views.py, включая create_booking) ...

def get_availability(request):
    """API эндпоинт для получения свободных слотов."""
    room_id = request.GET.get('room_id')
    date_str = request.GET.get('date')
    duration_str = request.GET.get('duration', '1') # По умолчанию 1 час

    if not room_id or not date_str:
        return HttpResponseBadRequest("Missing required parameters: room_id, date")
    try:
        room = Room.objects.get(pk=int(room_id))
        if not room.google_calendar_id:
            return JsonResponse({'error': 'Calendar ID not configured for this room'}, status=400)
    except (Room.DoesNotExist, ValueError):
        return HttpResponseBadRequest("Invalid room_id")
    try:
        duration_hours = float(duration_str)
        if duration_hours <= 0 or duration_hours * 60 % 30 != 0:
             raise ValueError("Duration must be a positive multiple of 0.5 hours")
        duration_minutes = int(duration_hours * 60)
    except ValueError as e:
        return HttpResponseBadRequest(f"Invalid duration: {e}")
    try:
        datetime.datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        return HttpResponseBadRequest("Invalid date format. Use YYYY-MM-DD")
    print(f"DEBUG: Attempting to use Calendar ID: '{room.google_calendar_id}' for Room ID: {room.pk}")
    slots = get_google_calendar_free_slots(room, date_str, duration_minutes)

    if slots is None:
        return JsonResponse({'error': 'Could not fetch availability from Google Calendar'}, status=503)

    return JsonResponse({'slots': slots})

# --- НОВЫЙ VIEW ДЛЯ ПОЛУЧЕНИЯ ЦЕНЫ ---

# main/views.py
# ... (импорты) ...
def get_calendar_service():
    """Быстрое создание сервиса календаря"""
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=['https://www.googleapis.com/auth/calendar'])
    return build('calendar', 'v3', credentials=creds)

def cleanup_expired_holds():
    """Удаляет просроченные брони из БД и Google Календаря"""
    expired_holds = PendingBooking.objects.filter(expires_at__lte=timezone.now())
    if not expired_holds.exists():
        return

    service = get_calendar_service()
    
    for hold in expired_holds:
        if hold.google_event_id and hold.room.google_calendar_id:
            try:
                calendar_id = str(hold.room.google_calendar_id).strip().replace('"', '').replace("'", "").replace(' ', '')
                service.events().delete(calendarId=calendar_id, eventId=hold.google_event_id).execute()
                print(f"Expired GCal event {hold.google_event_id} deleted.")
            except Exception as e:
                print(f"Error deleting expired GCal event: {e}")
        hold.delete()
# main/views.py

# Вспомогательная функция для парсинга диапазона людей
def parse_people_range(text):
    if not text: return None, None
    text = str(text).strip() # Преобразуем в строку на всякий случай
    match = re.match(r'(\d+)\s*-\s*(\d+)', text)
    if match:
        return int(match.group(1)), int(match.group(2))
    try:
         num = int(text)
         return num, num
    except ValueError:
         return None, None

# Вспомогательная функция для парсинга часов (включая '>=')
def parse_hours(text):
    if not text: return None, False
    text = str(text).strip().lower() # Преобразуем в строку
    is_minimum = False
    if text.startswith('≥') or text.startswith('>='):
        is_minimum = True
        text = text[1:].strip()
    elif 'по ' in text or 'более' in text or '+' in text: # Учтем слова типа "по 1500 в час"
         is_minimum = True
         # Пытаемся извлечь число перед словами
         match = re.match(r'(\d+(\.\d+)?)', text)
         if match:
             text = match.group(1)
         else: # Если число не найдено, не можем использовать
             return None, False

    try:
        # Используем Decimal для часов
        hours = Decimal(text.replace(',', '.'))
        return hours, is_minimum
    except (ValueError, InvalidOperation):
        return None, False

# main/views.py
# ... (импорты gspread, Credentials, settings, re, Decimal, ...)

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (parse_people_range, parse_hours) ---
# ОСТАВЬ ИХ КАК БЫЛИ В ПРЕДЫДУЩЕМ ОТВЕТЕ

def get_price_from_sheet(duration_hours: Decimal, people_count: int):
    """Читает прайс-лист из Google Sheets и находит цену по новой логике."""
    try:
        # --- Подключение к Google Sheets (как раньше) ---
        creds = Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE,
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
        )
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_url(settings.SPREADSHEET_URL)
        worksheet = spreadsheet.worksheet("Прайс-лист")
        data = worksheet.get_all_values()[1:] # Пропускаем заголовок

        # --- Собираем все правила для нужной группы людей ---
        rules_for_group = []
        for row in data:
            if len(row) < 3: continue
            grp_text, hours_text, price_text = row[0], row[1], row[2]
            min_p, max_p = parse_people_range(grp_text)
            hours_val, is_min = parse_hours(hours_text)

            # Проверяем группу людей
            if min_p is None or max_p is None or not (min_p <= people_count <= max_p):
                continue
            # Проверяем часы и цену
            if hours_val is None: continue
            try:
                total_price = int(str(price_text).strip().replace(' ', ''))
                # Сохраняем цену за час из колонки D, если она есть и правило >=
                rate = None
                if is_min and len(row) >= 4:
                     try: rate = int(str(row[3]).strip().replace(' ', ''))
                     except: pass

                rules_for_group.append({
                    'hours': hours_val,
                    'is_minimum': is_min,
                    'price': Decimal(total_price), # Используем Decimal для точности
                    'rate': Decimal(rate) if rate is not None else None
                })
            except (ValueError, TypeError, InvalidOperation):
                continue # Неверный формат цены

        if not rules_for_group:
            print(f"Warning: No price rules found for people count {people_count}")
            return None

        # Сортируем правила по часам
        rules_for_group.sort(key=lambda x: x['hours'])

        # --- РАСЧЕТ ЦЕНЫ ---
        final_price = None

        # 1. Точное совпадение по часам (целым или дробным)
        exact_rule = next((r for r in rules_for_group if not r['is_minimum'] and r['hours'] == duration_hours), None)
        if exact_rule:
            final_price = exact_rule['price']
            print(f"Price found (sheet, exact): {final_price} for {duration_hours}h, {people_count}p")

        # 2. Дробные часы (X.5) - ИСПОЛЬЗУЕМ НОВУЮ ЛОГИКУ
        elif duration_hours % 1 == Decimal('0.5'):
            base_hour = duration_hours - Decimal('0.5')

            # Особый случай 0.5 часа
            if base_hour == 0:
                one_hour_rule = next((r for r in rules_for_group if not r['is_minimum'] and r['hours'] == 1), None)
                if one_hour_rule:
                    final_price = one_hour_rule['price'] / 2
                    print(f"Price calculated (sheet, 0.5h): {final_price}")
                else:
                    print("Warning: 1h rule needed for 0.5h calculation not found")
            else:
                # Ищем цену для базового целого часа (X)
                base_hour_rule = next((r for r in rules_for_group if not r['is_minimum'] and r['hours'] == base_hour), None)
                if base_hour_rule:
                    base_price = base_hour_rule['price']
                    if base_hour > 0:
                        half_hour_price = base_price / (base_hour * 2) # Твоя формула
                        final_price = base_price + half_hour_price
                        print(f"Price calculated (sheet, fractional {duration_hours}h): {final_price} = {base_price} + {half_hour_price}")
                    else: # Не должно случиться из-за проверки base_hour == 0
                        final_price = base_price # На всякий случай
                else:
                    print(f"Warning: Base hour rule ({base_hour}h) not found for fractional calculation")

        # 3. Если цена все еще не найдена (например, целое число часов без точного совпадения),
        #    ищем правило ">= X часов"
        if final_price is None:
            min_rules_applicable = [r for r in rules_for_group if r['is_minimum'] and r['hours'] <= duration_hours]
            if min_rules_applicable:
                best_min_rule = max(min_rules_applicable, key=lambda x: x['hours'])
                hourly_rate = best_min_rule['rate'] # Берем ставку из колонки D

                if hourly_rate is not None and hourly_rate >= 0:
                    final_price = hourly_rate * duration_hours
                    print(f"Price calculated (sheet, minimum rule rate): {final_price} from rate {hourly_rate}")
                elif best_min_rule['hours'] > 0: # Если ставки нет, пробуем рассчитать из total_price
                    approx_rate = best_min_rule['price'] / best_min_rule['hours']
                    final_price = approx_rate * duration_hours
                    print(f"Price calculated (sheet, minimum rule approx rate): {final_price} from approx rate {approx_rate}")
                else: # Если часы = 0 в правиле >=
                     final_price = best_min_rule['price']

        # Округляем до целого в конце
        if final_price is not None:
            return int(final_price.to_integral_value(rounding='ROUND_HALF_UP')) # Округление до ближайшего целого
        else:
             print(f"Warning: No applicable price rule found for {duration_hours}h, {people_count}p")
             return None # Возвращаем None, если цена не найдена

    except gspread.exceptions.WorksheetNotFound:
        print("Error: Worksheet 'Прайс-лист' not found.")
        return None
    except Exception as e:
        print(f"Error reading Price List sheet: {e}")
        return None
def _validate_receipt(file_name, file_data_b64):
    """
    Проверяет чек (PDF или фото).
    Логика скопирована из ZenBot (2).py
    """
    file_name = file_name.lower()
    img_ext = ('.jpg', '.jpeg', '.png', '.heic', '.webp')

    # 1. Если это картинка, считаем чеком (как в боте)
    if file_name.endswith(img_ext):
        print("Receipt check: Image file detected, skipping PDF check.")
        return True, "Image receipt received"

    # 2. Если это PDF, проверяем содержимое
    if file_name.endswith('.pdf'):
        print("Receipt check: PDF file detected, validating content...")
        try:
            # Декодируем Base64
            # Строка Base64 имеет вид "data:application/pdf;base64,JVBER..."
            # Нам нужно отрезать заголовок "data:...;base64,"
            try:
                header, b64_data = file_data_b64.split(',', 1)
            except ValueError:
                # Если заголовка нет, считаем, что вся строка - это b64
                b64_data = file_data_b64

            pdf_content = base64.b64decode(b64_data)

            # Создаем временный файл в памяти
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(pdf_content)
                pdf_path = tmp.name

            pdf_text = ""
            try:
                with open(pdf_path, 'rb') as f:
                    reader = PyPDF2.PdfReader(f)
                    for page in reader.pages:
                        txt = page.extract_text() or ""
                        pdf_text += txt
            finally:
                os.unlink(pdf_path) # Гарантированно удаляем временный файл

            # Логика проверки из ZenBot (2).py
            required = [
                "ИИН/БИН продавца 880819499179",
                "РНМ 010103225772",
                "ЗНМ KK1937686357",
            ]
            missing = [rf for rf in required if rf not in pdf_text]

            if missing:
                print(f"PDF Check FAILED. Missing: {missing}")
                return False, "Чек не прошёл проверку (не найдены реквизиты)."
            else:
                print("PDF Check SUCCESSFUL.")
                return True, "PDF receipt validated"

        except Exception as e:
            print(f"Error processing PDF receipt: {e}")
            return False, f"Ошибка обработки PDF: {e}"

    # 3. Если это не PDF и не картинка
    return False, "Неверный формат файла. Нужен PDF или фото (jpg/png)."
# ... (остальные views) ...
# main/views.py

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ID БРОНИ (из ZenBot.py) ---
def _gen_booking_id(user_id: str) -> str:
    """Короткий читаемый ID: Z<последние4цифрыТелефона>-<8hex>"""
    tail = re.sub(r'\D', '', str(user_id))[-4:] or "0000"
    return f"Z{tail}-{uuid.uuid4().hex[:8].upper()}"

def _ensure_unique_booking_id(ws, booking_id: str) -> str:
    """Проверяет коллизии в колонке 'ID брони' (из ZenBot.py)"""
    try:
        headers = ws.row_values(1)
        if "ID брони" not in headers:
            return booking_id
        col = headers.index("ID брони") + 1
        existing = set(ws.col_values(col))
        while booking_id in existing:
            booking_id = _gen_booking_id("0000") # Генерируем новый, если конфликт
        return booking_id
    except Exception as e:
        print(f"Warning: Could not check unique booking ID: {e}")
        return booking_id
# --- КОНЕЦ ВСПОМОГАТЕЛЬНЫХ ФУНКЦИЙ ---

# main/views.py

# --- НОВАЯ ФУНКЦИЯ: ЗАПИСЬ В ИСТОРИЮ КЛИЕНТОВ ---
def save_booking_to_sheet(booking_data):
    """Записывает данные о бронировании с сайта в 'История клиентов'."""
    
    # --- ИЗМЕНЕНИЕ: Форматируем ID клиента (как для WhatsApp) ---
    client_phone = booking_data.get("client_phone", "")
    
    if client_phone:
        # Форматируем номер, как для Green API (логика из create_booking)
        user_id = ''.join(filter(str.isdigit, client_phone)) + '@c.us'
        if user_id.startswith('8'): # Заменяем 8 на 7
            user_id = '7' + user_id[1:]
        elif not user_id.startswith('7') and len(user_id.split('@')[0]) == 10:
            user_id = '7' + user_id # Добавляем 7
    else:
        user_id = "website_user_unknown_phone" # Запасной, если телефона нет
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---

    try:
        # Убедимся, что SCOPES в начале файла views.py включает .../auth/spreadsheets
        creds = Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE,
            scopes=["https://www.googleapis.com/auth/spreadsheets"] # Нужны права на запись
        )
        client = gspread.authorize(creds)
        history_spreadsheet_url = getattr(settings, 'CLIENTS_HISTORY_SPREADSHEET_URL', None)
        if not history_spreadsheet_url:
             print("Error: CLIENTS_HISTORY_SPREADSHEET_URL not configured.")
             return None

        history_spreadsheet = client.open_by_url(history_spreadsheet_url)

        try:
            ws = history_spreadsheet.worksheet("История клиентов")
        except gspread.exceptions.WorksheetNotFound:
            ws = history_spreadsheet.add_worksheet(title="История клиентов", rows=1000, cols=20)
            # Устанавливаем заголовки, как в боте + "Источник"
            headers = [
                "ID клиента", "Имя", "Телефон", "Дата", "Время",
                "Длительность", "Количество человек", "Кабинет",
                "Цена", "Дата/время бронирования", "Новый клиент", "ID брони", "Источник"
            ]
            ws.append_row(headers)
            print("Создан новый лист 'История клиентов' с заголовками")

        # Проверяем заголовки и добавляем "ID брони" или "Источник", если их нет
        headers = ws.row_values(1)
        new_headers = []
        if "ID брони" not in headers:
            headers.append("ID брони")
            new_headers.append("ID брони")
        if "Источник" not in headers:
            headers.append("Источник")
            new_headers.append("Источник")

        if new_headers:
            ws.resize(cols=len(headers))
            range_label = f"R1C1:R1C{len(headers)}"
            ws.update(range_label, [headers], raw=False)
            print(f"[HIST] Добавлены колонки: {new_headers}")

        # Генерируем ID брони
        booking_id = _ensure_unique_booking_id(ws, _gen_booking_id(user_id))

        booking_timestamp = timezone.now().astimezone(ALMATY_TZ).strftime("%Y-%m-%d %H:%M:%S")
        row_dict = {
            "ID клиента": user_id,
            "Имя": booking_data.get("client_name", ""),
            "Телефон": booking_data.get("client_phone", ""),
            "Дата": booking_data.get("date", ""),
            "Время": booking_data.get("start_time", "")[:5],
            "Длительность": booking_data.get("duration_hours", ""),
            "Количество человек": booking_data.get("people_count", ""),
            "Кабинет": booking_data.get("room_name", ""),
            "Цена": booking_data.get("price", ""),
            "Дата/время бронирования": booking_timestamp,
            "Новый клиент": "Да", # С сайта пока всегда "Да" (или можно добавить проверку)
            "ID брони": booking_id,
            "Источник": "Сайт" # <-- Твоя пометка
        }

        # Собираем строку в правильном порядке заголовков
        row = [row_dict.get(h, "") for h in headers]
        ws.append_row(row)

        print(f"[HIST] Записана бронь {booking_id} для {user_id} (Источник: Сайт)")
        return booking_id

    except Exception as e:
        print(f"Ошибка при записи данных клиента в историю: {e}")
        return None
# --- КОНЕЦ НОВОЙ ФУНКЦИИ ---

def get_price(request):
    """API эндпоинт для расчета цены из Google Sheets."""
    room_id = request.GET.get('room_id') # Не используется, но оставляем
    duration_str = request.GET.get('duration')
    people_count_str = request.GET.get('people_count', '1')

    if not duration_str:
        return HttpResponseBadRequest("Missing required parameter: duration")
    try:
        duration_hours = Decimal(duration_str)
        if duration_hours <= 0: raise ValueError("Duration must be positive")
    except (ValueError, InvalidOperation):
        return HttpResponseBadRequest("Invalid duration format")
    try:
        people_count = int(people_count_str)
        if people_count <= 0: people_count = 1
    except ValueError:
        people_count = 1

    # --- Получаем цену из Google Sheets ---
    price = get_price_from_sheet(duration_hours, people_count)
    # --- КОНЕЦ ПОЛУЧЕНИЯ ЦЕНЫ ---

    if price is None:
        print(f"Warning: Price not found in sheet for {duration_hours} hours, {people_count} people. Using fallback.")
        # Запасной вариант: 5000 тг/час (или верни ошибку)
        try:
             price = int(float(duration_hours) * 5000)
             # return JsonResponse({'error': f'Price not found for {duration_hours}h / {people_count} people'}, status=404)
        except:
             return JsonResponse({'error': 'Could not determine price'}, status=500)

    return JsonResponse({'price': price})

# main/views.py
# ... (все существующие импорты и функции) ...

# === НОВЫЙ VIEW ДЛЯ ПОИСКА СВОБОДНЫХ КАБИНЕТОВ ===
# main/views.py

@require_GET
def find_available_rooms(request):
    """
    API для поиска всех свободных кабинетов в заданное время.
    (ИСПРАВЛЕННАЯ ВЕРСИЯ, ИСПОЛЬЗУЕТ get_google_calendar_free_slots)
    """
    date_str = request.GET.get('date')
    start_time_str = request.GET.get('start_time')
    duration_str = request.GET.get('duration')
    people_count_str = request.GET.get('people_count', '1')

    if not all([date_str, start_time_str, duration_str]):
        return HttpResponseBadRequest("Missing required parameters: date, start_time, duration")
    
    try:
        duration_hours = float(duration_str)
        duration_minutes = int(duration_hours * 60)
        people_count = int(people_count_str)
        
        # Валидация времени и даты
        start_time_dt = datetime.datetime.strptime(start_time_str, '%H:%M').time()
        selected_date_dt = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
        start_dt_naive = datetime.datetime.combine(selected_date_dt, start_time_dt)
        start_dt_aware = ALMATY_TZ.localize(start_dt_naive)
        
        if start_dt_aware <= timezone.now():
            return JsonResponse({'available_rooms': [], 'error': 'Cannot search in the past'})

    except (ValueError, TypeError) as e:
        return HttpResponseBadRequest(f"Invalid data format: {e}")

    available_rooms = []
    # Получаем все активные кабинеты с ID календаря
    all_rooms = Room.objects.filter(is_active=True).exclude(google_calendar_id__isnull=True).exclude(google_calendar_id__exact='')

    for room in all_rooms:
        
        # --- ИСПРАВЛЕННАЯ ЛОГИКА ---
        # 1. Мы вызываем get_google_calendar_free_slots, чтобы получить
        #    список ВСЕХ свободных слотов, которые УЖЕ УЧИТЫВАЮТ часы работы.
        
        # Передаем объект room, а не calendar_id
        all_free_slots_for_room = get_google_calendar_free_slots(room, date_str, duration_minutes) 

        if all_free_slots_for_room is None:
            # Ошибка при чтении календаря этой комнаты, пропускаем ее
            print(f"Room {room.name} skipped (GCal API error in find_available_rooms)")
            continue

        # 2. Теперь мы просто проверяем, есть ли наше ВРЕМЯ (start_time_str)
        #    в списке тех, что вернула "умная" функция.
        is_our_slot_available = any(slot['start'] == start_time_str for slot in all_free_slots_for_room)

        if is_our_slot_available:
            # 3. Слот точно свободен. Рассчитываем цену.
            room_price = get_price_from_sheet(Decimal(duration_str), people_count) # Используем Decimal
            
            available_rooms.append({
                'id': room.pk,
                'name': room.name,
                'main_image_url': room.main_image.url if room.main_image else None,
                'area': room.area_sq_m,
                'short_description': room.short_description,
                'price': room_price if room_price is not None else "N/A" # Добавляем цену
            })
        else:
            print(f"Room {room.name} skipped (Slot {start_time_str} not in free list)")
        # --- КОНЕЦ ИСПРАВЛЕННОЙ ЛОГИКИ ---

    return JsonResponse({'available_rooms': available_rooms})

@require_POST
@csrf_exempt
def hold_slot(request):
    """Временный резерв: БД (всегда) + Google Calendar (только если есть имя/телефон)."""
    
    cleanup_expired_holds()

    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
        room_id = data.get('room_id')
        date_str = data.get('date')
        start_time_str = data.get('start_time')
        duration_str = data.get('duration')
        
        # Получаем имя и телефон (могут быть пустыми на шаге 1)
        client_name = data.get('client_name')
        client_phone = data.get('client_phone')

        if not all([room_id, date_str, start_time_str, duration_str]):
            return JsonResponse({'success': False, 'error': 'Не все поля заполнены.'}, status=400)

        room = get_object_or_404(Room, pk=int(room_id))
        
        if not room.google_calendar_id:
             return JsonResponse({'success': False, 'error': 'Ошибка настройки кабинета (нет ID календаря).'}, status=500)
        calendar_id = str(room.google_calendar_id).strip().replace('"', '').replace("'", "").replace(' ', '')

        duration_hours = float(duration_str)
        start_time = datetime.datetime.strptime(start_time_str, '%H:%M').time()
        selected_date = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()

        start_dt_naive = datetime.datetime.combine(selected_date, start_time)
        start_dt_aware = ALMATY_TZ.localize(start_dt_naive)
        end_dt_aware = start_dt_aware + datetime.timedelta(hours=duration_hours)
        
        if start_dt_aware <= timezone.now():
             return JsonResponse({'success': False, 'error': 'Нельзя выбрать время в прошлом.'}, status=400)

        # Проверяем пересечения в БД
        if PendingBooking.objects.filter(room=room, expires_at__gt=timezone.now(), start_time__lt=end_dt_aware, end_time__gt=start_dt_aware).exists():
             return JsonResponse({'success': False, 'error': 'Слот уже занят или на оформлении.'}, status=409)

        gcal_event_id = None

        # === ГЛАВНОЕ ИЗМЕНЕНИЕ ===
        # Создаем событие в Google ТОЛЬКО если переданы Имя и Телефон (Шаг оплаты)
        if client_name and client_phone:
            try:
                service = get_calendar_service()
                
                # Проверяем занятость в Google
                events_result = service.events().list(
                    calendarId=calendar_id,
                    timeMin=start_dt_aware.isoformat(),
                    timeMax=end_dt_aware.isoformat(),
                    singleEvents=True
                ).execute()
                
                if events_result.get('items', []):
                    return JsonResponse({'success': False, 'error': 'Слот занят в календаре.'}, status=409)

                # Создаем событие с Именем
                event_body = {
                    'summary': f'⏳ Оформление {room.name}: {client_name} ({client_phone})',
                    'description': 'Клиент перешел к оплате. Резерв 15 минут.',
                    'start': {'dateTime': start_dt_aware.isoformat(), 'timeZone': settings.TIME_ZONE},
                    'end': {'dateTime': end_dt_aware.isoformat(), 'timeZone': settings.TIME_ZONE},
                    'colorId': '8' # Серый цвет
                }
                
                # Создаем событие с Именем И НАЗВАНИЕМ КАБИНЕТА
                gcal_event = service.events().insert(calendarId=calendar_id, body=event_body).execute()
                gcal_event_id = gcal_event.get('id')

            except Exception as e:
                print(f"Google Calendar Error: {e}")
                return JsonResponse({'success': False, 'error': 'Ошибка связи с Google Календарем.'}, status=500)
        # =========================

        # Сохраняем в БД (если gcal_event_id пустой - значит просто держим слот локально)
        pending_booking = PendingBooking.objects.create(
            room=room,
            start_time=start_dt_aware,
            end_time=end_dt_aware,
            google_event_id=gcal_event_id 
        )

        return JsonResponse({'success': True, 'hold_id': str(pending_booking.hold_id)})

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
    
@require_POST
@csrf_exempt
def cancel_hold(request):
    """Отмена: Удаляем из БД и из Google Calendar."""
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
        hold_id_str = data.get('hold_id')

        if not hold_id_str: return JsonResponse({'success': False}, status=400)

        try:
            hold_id = uuid.UUID(hold_id_str)
            pending_booking = PendingBooking.objects.get(hold_id=hold_id)
            
            # --- УДАЛЕНИЕ ИЗ GOOGLE ---
            if pending_booking.google_event_id and pending_booking.room.google_calendar_id:
                try:
                    service = get_calendar_service()
                    calendar_id = str(pending_booking.room.google_calendar_id).strip().replace('"', '').replace("'", "").replace(' ', '')
                    service.events().delete(
                        calendarId=calendar_id, 
                        eventId=pending_booking.google_event_id
                    ).execute()
                    print("GCal Hold Event deleted.")
                except Exception as e:
                    print(f"Warning: Failed to delete GCal event: {e}")
            # --------------------------

            pending_booking.delete()
            return JsonResponse({'success': True})
            
        except PendingBooking.DoesNotExist:
            return JsonResponse({'success': True})

    except Exception as e:
         return JsonResponse({'success': False, 'error': str(e)}, status=500)
# ... (остальные views: get_price, create_booking, IndexView и т.д.) ...
@require_POST
@csrf_exempt
def create_booking(request):
    if request.method != "POST": return HttpResponseBadRequest("POST only")
    try: data = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError: return HttpResponseBadRequest("Invalid JSON")

    # --- ПОЛУЧЕНИЕ ДАННЫХ ---
    hold_id_str = data.get('hold_id')
    client_name = data.get('client_name')
    client_phone = data.get('client_phone')
    
    file_data_b64 = data.get('receipt_file_data') # Может быть None
    file_name = data.get('receipt_file_name')     # Может быть None
    receipt_number = data.get('receipt_number')   # Может быть None

    # 1. ВАЛИДАЦИЯ: Основные поля
    if not hold_id_str or not client_name or not client_phone:
         return JsonResponse({'success': False, 'error': 'Не заполнены обязательные поля (Имя, Телефон, ID).'}, status=400)

    # 2. ВАЛИДАЦИЯ: Чек (Файл ИЛИ Номер)
    # Если нет ни файла, ни номера - ошибка
    if not file_data_b64 and not receipt_number:
         return JsonResponse({'success': False, 'error': 'Прикрепите скан чека или введите номер квитанции.'}, status=400)

    # 3. Проверка временного резерва
    try:
        hold_uuid = uuid.UUID(hold_id_str)
        pending_booking = get_object_or_404(PendingBooking, hold_id=hold_uuid)
        
        # Если время истекло
        if pending_booking.expires_at < timezone.now():
             return JsonResponse({'success': False, 'error': 'Время бронирования истекло. Пожалуйста, начните заново.'}, status=410)

        room = pending_booking.room
        start_dt_aware = timezone.localtime(pending_booking.start_time)
        end_dt_aware = timezone.localtime(pending_booking.end_time)
        date_str = start_dt_aware.strftime('%Y-%m-%d')
        start_time_str = start_dt_aware.strftime('%H:%M')
        duration_hours = (end_dt_aware - start_dt_aware).total_seconds() / 3600

        people_count = data.get('people_count', 1)
        price = data.get('price', 0)

    except PendingBooking.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Резерв не найден (возможно, время истекло).'}, status=404)
    except Exception as e:
         print(f"Error validating hold_id: {e}")
         return JsonResponse({'success': False, 'error': 'Ошибка данных бронирования.'}, status=400)

    # 4. ПРОВЕРКА ФАЙЛА (Только если он есть)
    if file_data_b64:
        is_valid, message = _validate_receipt(file_name, file_data_b64)
        if not is_valid:
            return JsonResponse({'success': False, 'error': message}, status=400)

    # 5. ОБНОВЛЕНИЕ КАЛЕНДАРЯ
    try:
        service = get_calendar_service()
        calendar_id = str(room.google_calendar_id).strip().replace('"', '').replace("'", "").replace(' ', '')
        
        event_summary = f'Сайт:{client_name} ({client_phone})'
        
        # Формируем описание оплаты
        if file_data_b64:
            payment_info = f"Чек загружен: {file_name}"
        else:
            payment_info = f"Номер чека: {receipt_number}"

        event_description = (
            f'Клиент: {client_name}\nТел: {client_phone}\nКол-во: {people_count}\n'
            f'Длит: {duration_hours} ч.\nЦена: {price} тг\n'
            f'Оплата: {payment_info}\nИсточник: Сайт' 
        )
        
        event_patch = {
            'summary': event_summary,
            'description': event_description,
            'colorId': None,
            }
        
        if pending_booking.google_event_id:
            try:
                service.events().patch(
                    calendarId=calendar_id,
                    eventId=pending_booking.google_event_id,
                    body=event_patch
                ).execute()
            except Exception as e:
                print(f"Failed to patch event, creating new one: {e}")
                # Fallback: создаем новое, если старое не найдено
                event_patch['start'] = {'dateTime': start_dt_aware.isoformat(), 'timeZone': settings.TIME_ZONE}
                event_patch['end'] = {'dateTime': end_dt_aware.isoformat(), 'timeZone': settings.TIME_ZONE}
                service.events().insert(calendarId=calendar_id, body=event_patch).execute()
        else:
            event_patch['start'] = {'dateTime': start_dt_aware.isoformat(), 'timeZone': settings.TIME_ZONE}
            event_patch['end'] = {'dateTime': end_dt_aware.isoformat(), 'timeZone': settings.TIME_ZONE}
            service.events().insert(calendarId=calendar_id, body=event_patch).execute()

    except Exception as e:
        print(f"Error updating Google Calendar: {e}")
        # Не блокируем успех, если календарь сбоит, главное - запись в БД

    # 6. Удаляем временный резерв
    pending_booking.delete()

    # 7. ЗАПИСЬ В ИСТОРИЮ (Google Sheets)
    try:
        sheet_booking_data = {
            "client_name": client_name,
            "client_phone": client_phone,
            "date": date_str,
            "start_time": start_time_str,
            "duration_hours": duration_hours,
            "people_count": people_count,
            "room_name": room.name,
            "price": price,
            "is_client_new": True 
        }
        save_booking_to_sheet(sheet_booking_data)
    except Exception as e:
        print(f"Error saving to History Sheet: {e}")

    # 8. Отправка WhatsApp и возврат ответа
    studio_details = {}
    try:
        door_code, client_message_text, studio_details = get_door_code_and_instructions(room.name)        
        
        # Определяем текст оплаты для админа
        if file_data_b64:
            payment_status_text = f"📎 Загружен Чек"
        else:
            payment_status_text = f"🔢 Номер чека: {receipt_number}"

        # Формируем КРАСИВОЕ сообщение для группы
        group_message_text = (
            "〰〰〰〰〰〰〰〰〰〰\n"
            "📅 Новая бронь (Сайт)\n\n"
            f"🏠 Кабинет: {room.name}\n"
            f"🗓 Дата: {date_str} | {start_time_str}\n"
            f"⏳ Длительность: {duration_hours} ч\n"
            f"👥 Гостей: {people_count}\n"
            f"👤 Клиент: {client_name} ({client_phone})\n"
            f"💰 Оплата: {price} ₸\n"
            f"{payment_status_text}\n"
            "〰〰〰〰〰〰〰〰〰〰"
        )

        bot_api_url = getattr(settings, 'BOT_WHATSAPP_API_URL', None)
        group_chat_id = getattr(settings, 'GROUP_CHAT_ID', None)

        if bot_api_url:
            # 1. Отправка КЛИЕНТУ (Инструкция)
            client_chat_id = ''.join(filter(str.isdigit, client_phone)) + '@c.us'
            if client_chat_id.startswith('8'): client_chat_id = '7' + client_chat_id[1:]
            elif not client_chat_id.startswith('7') and len(client_chat_id.split('@')[0]) == 10:
                client_chat_id = '7' + client_chat_id
            
            print(f"Attempting to send to client: {client_chat_id}")
            try: requests.post(bot_api_url, json={'chat_id': client_chat_id, 'message': client_message_text}, timeout=10)
            except Exception as req_err: print(f"Error sending to client API: {req_err}")
            
            # 2. Отправка в ГРУППУ АДМИНОВ (Полный отчет)
            if group_chat_id:
                print(f"Attempting to send to group: {group_chat_id}")
                try: requests.post(bot_api_url, json={'chat_id': group_chat_id, 'message': group_message_text}, timeout=10)
                except Exception as req_err: print(f"Error sending to group API: {req_err}")
        else:
           print("Warning: BOT_WHATSAPP_API_URL not configured.")

    except Exception as e:
        print(f"Error preparing/sending WhatsApp notifications: {e}")

    return JsonResponse({
            'success': True, 
            'studio_details': studio_details
    })



class BookingPageView(TemplateView):
    template_name = 'main/booking_page.html' # Указываем новый шаблон

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Передаем в шаблон список всех активных кабинетов
        context['rooms'] = Room.objects.filter(is_active=True).order_by('order')
        context['settings'] = SiteSettings.objects.first() # Также передаем настройки
        return context

class IndexView(TemplateView):
    template_name = 'main/index.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        settings = SiteSettings.objects.first()
        context['settings'] = settings
        context['rooms'] = Room.objects.filter(is_active=True)

        if settings:
            # Слайды и тарифы, связанные с настройками сайта (inline)
            context['sliders'] = settings.sliders.all()
            context['tariffs'] = settings.tariffs.filter(is_active=True).order_by('order', 'id')
        else:
            # безопасность на случай пустой БД
            context['sliders'] = HeroSlider.objects.none()
            context['tariffs'] = []

        return context

class PageDetailView(DetailView):
    model = Page
    template_name = "main/page_detail.html"
    context_object_name = "page"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        settings = SiteSettings.objects.first()
        page = self.object
        request = self.request

        # обязательно положим в шаблон — у тебя есть обращения к {{ settings.* }}
        ctx["settings"] = settings

        ctx["seo_title"] = page.get_meta_title(settings)
        ctx["seo_description"] = page.get_meta_description(settings)
        ctx["seo_robots"] = page.get_meta_robots(settings)
        ctx["seo_canonical"] = page.canonical_url or request.build_absolute_uri()
        ctx["seo_url"] = request.build_absolute_uri()

        # АБСОЛЮТНЫЙ URL для og:image (это критично для WhatsApp/Telegram/Facebook)
        if page.og_image:
            ctx["seo_og_image"] = request.build_absolute_uri(page.og_image.url)
        elif settings and settings.default_og_image:
            ctx["seo_og_image"] = request.build_absolute_uri(settings.default_og_image.url)
        else:
            ctx["seo_og_image"] = None

        ctx["seo_site_name"] = settings.site_name if settings else "Zen Studio"
        return ctx

class RoomDetailView(DetailView):
    model = Room
    template_name = 'main/room_detail.html'
    context_object_name = 'room'
    
    def get_queryset(self):
        return Room.objects.filter(is_active=True)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['settings'] = SiteSettings.objects.first()
        return context
    
