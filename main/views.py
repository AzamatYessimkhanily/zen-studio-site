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
import random
import time as _time  # для retry

SERVICE_ACCOUNT_FILE = settings.BASE_DIR / 'sheetsapi-443912-7487420df9cd.json'
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
ALMATY_TZ = pytz.timezone(settings.TIME_ZONE) # Часовой пояс из настроек Django

# main/views.py


@require_POST
@csrf_exempt
def api_send_auth_code(request):
    """Генерирует код и отправляет в WhatsApp"""
    try:
        data = json.loads(request.body)
        phone_raw = data.get('phone')
        
        # 1. Нормализация номера (7707...)
        phone = ''.join(filter(str.isdigit, str(phone_raw)))
        if len(phone) == 11 and phone.startswith('8'): 
            phone = '7' + phone[1:]
        elif len(phone) == 10: 
            phone = '7' + phone
            
        if len(phone) != 11:
            return JsonResponse({'success': False, 'error': 'Неверный формат номера'})

        # 2. Генерация кода (4 цифры)
        code = str(random.randint(1000, 9999))
        
        # 3. Сохраняем в сессию (действует пока браузер открыт / куки живы)
        request.session['auth_phone'] = phone
        request.session['auth_code'] = code
        request.session.set_expiry(300) # Код валиден 5 минут

        # 4. Отправка через вашего бота (Green API)
        message = f"🔒 Ваш код входа в Zen Studio: *{code}*\nНикому не сообщайте."
        send_whatsapp_client(phone, message)
        
        return JsonResponse({'success': True})

    except Exception as e:
        print(f"Auth Send Error: {e}")
        return JsonResponse({'success': False, 'error': str(e)})

@require_POST
@csrf_exempt
def api_verify_auth_code(request):
    """Проверяет введенный код"""
    try:
        data = json.loads(request.body)
        code_input = str(data.get('code')).strip()
        
        # Получаем сохраненные данные
        saved_code = request.session.get('auth_code')
        saved_phone = request.session.get('auth_phone')
        
        if not saved_code or not saved_phone:
             return JsonResponse({'success': False, 'error': 'Код устарел. Запросите новый.'})
             
        if code_input == saved_code:
            # УСПЕХ!
            request.session['user_logged_in'] = True
            request.session['user_phone'] = saved_phone
            
            # === ДОБАВЛЯЕМ ЭТУ СТРОКУ (Сессия на 1 год) ===
            request.session.set_expiry(31536000) 
            # ==============================================

            if 'auth_code' in request.session:
                del request.session['auth_code']
                
            return JsonResponse({'success': True, 'phone': saved_phone})
        else:
            return JsonResponse({'success': False, 'error': 'Неверный код'})
            
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


# =====================================================
# ДОБАВЬ ЭТУ ФУНКЦИЮ В views.py (например, после api_verify_auth_code)
# =====================================================




def _retry_gsheet(fn, max_retries=2, delay=2):
    """Обёртка для вызовов Google Sheets с retry при rate limit (429/RESOURCE_EXHAUSTED)."""
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as e:
            last_err = e
            err_str = str(e).lower()
            is_rate_limit = any(k in err_str for k in ['quota', 'rate', '429', 'resource_exhausted', 'too many'])
            if is_rate_limit and attempt < max_retries:
                _time.sleep(delay * (attempt + 1))
                continue
            raise
    raise last_err

@require_GET
def get_my_bookings(request):
    """Возвращает данные клиента + его брони из Google Sheets."""
    phone = request.session.get('user_phone')
    if not phone:
        return JsonResponse({'success': False, 'error': 'Не авторизован'}, status=401)

    phone_norm = normalize_phone_for_sheet(phone)

    try:
        def _open_sheet():
            creds = Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE,
                scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
            )
            gc = gspread.authorize(creds)
            return gc.open_by_url(settings.CLIENTS_HISTORY_SPREADSHEET_URL)

        sh = _retry_gsheet(_open_sheet)

        # --- 1. Абонемент ---
        sub_data = None
        try:
            sub_data = _retry_gsheet(lambda: get_subscription_client(phone))
        except Exception as e:
            print(f"get_my_bookings subscription lookup error (non-fatal): {e}")

        balance_display = ""
        expires = ""
        client_name = ""
        if sub_data:
            balance_display = format_hours_text(sub_data['balance'])
            expires = sub_data.get('expires', '')

        # --- 2. Имя клиента из листа Абонементы ---
        try:
            def _lookup_name():
                ws_sub = sh.worksheet("Абонементы")
                headers_sub = ws_sub.row_values(1)
                headers_sub_lower = [h.lower().strip() for h in headers_sub]
                phone_col = headers_sub_lower.index("телефон") + 1
                name_col_idx = None
                if "имя" in headers_sub_lower:
                    name_col_idx = headers_sub_lower.index("имя") + 1
                phones = ws_sub.col_values(phone_col)
                if phone_norm in phones:
                    row_idx = phones.index(phone_norm) + 1
                    if name_col_idx:
                        return ws_sub.cell(row_idx, name_col_idx).value or ""
                return ""
            client_name = _retry_gsheet(_lookup_name)
        except Exception as e:
            print(f"get_my_bookings name lookup error: {e}")

        # --- 3. Брони из "История клиентов" ---
        bookings = []
        try:
            def _read_history():
                ws_hist = sh.worksheet("История клиентов")
                headers = ws_hist.row_values(1)
                all_rows = ws_hist.get_all_values()[1:]
                return headers, all_rows
            
            headers, all_rows = _retry_gsheet(_read_history)

            headers_lower = [h.lower().strip() for h in headers]
            idx = {h: i for i, h in enumerate(headers_lower)}

            phone_col_i = idx.get('телефон')
            date_col_i = idx.get('дата')
            time_col_i = idx.get('время')
            dur_col_i = idx.get('длительность')
            room_col_i = idx.get('кабинет')
            price_col_i = idx.get('цена') or idx.get('стоимость') or idx.get('оплата')
            people_col_i = idx.get('количество человек')
            booking_id_col_i = idx.get('id брони')

            now = datetime.datetime.now(ALMATY_TZ)

            for row in all_rows:
                if phone_col_i is None or phone_col_i >= len(row):
                    continue
                row_phone = normalize_phone_for_sheet(row[phone_col_i])
                if row_phone != phone_norm:
                    continue

                def safe_get(col_i):
                    if col_i is not None and col_i < len(row):
                        return row[col_i]
                    return ""

                date_val = safe_get(date_col_i)
                time_val = safe_get(time_col_i)
                dur_val = safe_get(dur_col_i)
                room_val = safe_get(room_col_i)
                price_val = safe_get(price_col_i)
                people_val = safe_get(people_col_i)
                bid = safe_get(booking_id_col_i)

                # Определяем статус
                status = "completed"
                
                # Проверяем отмену (в цене или в колонке статус)
                is_cancelled = False
                price_lower = str(price_val).lower().strip() if price_val else ''
                if 'отменен' in price_lower or 'отмена' in price_lower:
                    is_cancelled = True
                # Проверяем колонку статус/примечание (ищем по частичному совпадению)
                for hdr_name, col_i in idx.items():
                    if ('статус' in hdr_name or 'примечан' in hdr_name) and col_i < len(row):
                        cell_lower = str(row[col_i]).lower().strip()
                        if 'отменен' in cell_lower or 'отмена' in cell_lower:
                            is_cancelled = True
                            break
                
                if is_cancelled:
                    status = "cancelled"
                else:
                    try:
                        if date_val and time_val:
                            dt = datetime.datetime.strptime(f"{date_val} {time_val}", "%Y-%m-%d %H:%M")
                            dt = ALMATY_TZ.localize(dt)
                            dur_hours = float(str(dur_val).replace(',', '.')) if dur_val else 1
                            end_dt = dt + datetime.timedelta(hours=dur_hours)
                            if end_dt > now:
                                status = "upcoming"
                    except Exception:
                        pass

                # Имя клиента из истории (если не нашли в абонементах)
                if not client_name:
                    name_col_hist = idx.get('имя')
                    if name_col_hist is not None and name_col_hist < len(row):
                        client_name = row[name_col_hist]

                # Вычисляем end_time
                end_time_str = ""
                try:
                    if date_val and time_val and dur_val:
                        dt_start = datetime.datetime.strptime(f"{date_val} {time_val}", "%Y-%m-%d %H:%M")
                        dur_h = float(str(dur_val).replace(',', '.'))
                        dt_end = dt_start + datetime.timedelta(hours=dur_h)
                        end_time_str = dt_end.strftime("%H:%M")
                except Exception:
                    pass

                bookings.append({
                    'date': date_val,
                    'time': time_val,
                    'end_time': end_time_str,
                    'duration': format_hours_text(dur_val) if dur_val else "",
                    'duration_raw': float(str(dur_val).replace(',', '.')) if dur_val else 1,
                    'room': room_val,
                    'price': price_val,
                    'people': people_val,
                    'booking_id': bid,
                    'status': status,
                })
        except Exception as e:
            print(f"Error reading history: {e}")

        # --- 4. Pending bookings (ожидают оплаты) из Django модели ---
        try:
            pending = PendingBooking.objects.filter(
                client_phone__icontains=phone_norm[-10:],
                expires_at__gt=timezone.now(),
                is_confirmed=False  # Не показываем подтверждённые админом
            ).select_related('room')
            for p in pending:
                start_local = timezone.localtime(p.start_time)
                end_local = timezone.localtime(p.end_time)
                expires_local = timezone.localtime(p.expires_at)
                dur_h = (end_local - start_local).total_seconds() / 3600
                bookings.append({
                    'date': start_local.strftime('%Y-%m-%d'),
                    'time': start_local.strftime('%H:%M'),
                    'end_time': end_local.strftime('%H:%M'),
                    'duration': format_hours_text(dur_h),
                    'duration_raw': dur_h,
                    'room': p.room.name,
                    'room_id': p.room.pk,
                    'price': '',
                    'people': '',
                    'booking_id': str(p.hold_id),
                    'status': 'pending_payment',
                    'hold_id': str(p.hold_id),
                    'expires_at': expires_local.isoformat(),
                })
        except Exception as e:
            print(f"Error reading pending bookings: {e}")

        # Сортировка: ожидающие → будущие → отменённые → завершенные (внутри — по дате DESC)
# Сортировка: сначала по статусу, потом по дате+время DESC (новые первыми)
        order_map = {'pending_payment': 0, 'upcoming': 1, 'cancelled': 2, 'completed': 3}
        bookings.sort(
            key=lambda x: x.get('date', '') + ' ' + x.get('time', ''),
            reverse=True
        )
        bookings.sort(
            key=lambda x: order_map.get(x['status'], 9)
        )

        return JsonResponse({
            'success': True,
            'client_name': client_name,
            'phone': phone_norm,
            'balance': balance_display,
            'expires': expires,
            'bookings': bookings,
        })

    except Exception as e:
        print(f"get_my_bookings error: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
    
# === ФУНКЦИЯ ДЛЯ КРАСИВОГО ОТОБРАЖЕНИЯ ЧАСОВ ===
def format_hours_text(value):
    """Превращает 1.5 в '1 ч 30 мин', 1.0 в '1 ч'"""
    try:
        val = float(str(value).replace(',', '.'))
    except (ValueError, TypeError):
        return str(value)

    hours = int(val)
    minutes = int(round((val - hours) * 60))
    
    if minutes == 60:
        hours += 1
        minutes = 0

    parts = []
    if hours > 0:
        parts.append(f"{hours} ч")
    if minutes > 0:
        parts.append(f"{minutes} мин")
    
    if not parts:
        return "0 мин"
        
    return " ".join(parts)
# ===============================================

def send_whatsapp_group(message):
    """Отправляет сообщение только в группу админов."""
    url = getattr(settings, 'BOT_WHATSAPP_API_URL', None)
    group_chat_id = getattr(settings, 'GROUP_CHAT_ID', None)
    
    if url and group_chat_id:
        try:
            requests.post(url, json={'chat_id': group_chat_id, 'message': message}, timeout=5)
        except Exception as e:
            print(f"Group WhatsApp Error: {e}")

def send_whatsapp_client(phone, message):
    """Отправляет сообщение на личный номер клиента."""
    url = getattr(settings, 'BOT_WHATSAPP_API_URL', None)
    if not url or not phone: return

    try:
        # Форматируем номер (7707... -> 7707...@c.us)
        client_chat_id = ''.join(filter(str.isdigit, str(phone))) + '@c.us'
        if client_chat_id.startswith('8'): 
            client_chat_id = '7' + client_chat_id[1:]
        elif not client_chat_id.startswith('7') and len(client_chat_id.split('@')[0]) == 10:
            client_chat_id = '7' + client_chat_id
            
        requests.post(url, json={'chat_id': client_chat_id, 'message': message}, timeout=5)
    except Exception as e:
        print(f"Client WhatsApp Error: {e}")


# --- ФУНКЦИИ ДЛЯ АБОНЕМЕНТОВ (backend) ---

def normalize_phone_for_sheet(phone):
    """Приводит телефон к виду 7707... для поиска в таблице."""
    if not phone: return ""
    clean = ''.join(filter(str.isdigit, str(phone)))
    if clean.startswith('8'): return '7' + clean[1:]
    if len(clean) == 10: return '7' + clean
    return clean

def get_subscription_client(phone):
    """
    Ищет клиента в листе 'Абонементы' ДИНАМИЧЕСКИ (по названиям колонок).
    """
    try:
        creds = Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        client = gspread.authorize(creds)
        sh = client.open_by_url(settings.CLIENTS_HISTORY_SPREADSHEET_URL)
        ws = sh.worksheet("Абонементы")
        
        phone_norm = normalize_phone_for_sheet(phone)
        
        # 1. Получаем заголовки (первая строка)
        headers = ws.row_values(1)
        # Приводим заголовки к нижнему регистру для надежности
        headers_lower = [h.lower().strip() for h in headers]

        # 2. Ищем индексы нужных колонок
        # Настройте названия ниже точно как у вас в таблице!
        try:
            # Ищем колонку с телефоном (варианты: "телефон", "phone")
            phone_col_idx = headers_lower.index("телефон") + 1
        except ValueError:
            print("Ошибка: Колонка 'Телефон' не найдена в таблице")
            return None

        try:
            # Ищем колонку с остатком (варианты: "остаток", "баланс", "купил")
            # Если у вас колонка называется I:Остаток, ищите "остаток"
            balance_col_idx = headers_lower.index("остаток") + 1
        except ValueError:
            # Запасной вариант - ищем "баланс"
            if "баланс" in headers_lower:
                balance_col_idx = headers_lower.index("баланс") + 1
            else:
                print("Ошибка: Колонка 'Остаток' не найдена")
                return None

        try:
            # Ищем колонку срока действия
            expire_col_idx = headers_lower.index("действует до") + 1
        except ValueError:
            expire_col_idx = None # Не критично, если нет

        # 3. Ищем телефон в найденной колонке
        phones_col = ws.col_values(phone_col_idx)
        
        try:
            row_idx = phones_col.index(phone_norm) + 1 
        except ValueError:
            return None # Телефон не найден
            
        # 4. Получаем данные конкретной строки
        # Получаем значение баланса напрямую по координатам
        balance_str = ws.cell(row_idx, balance_col_idx).value
        
        # Получаем дату истечения
        expire_str = ""
        if expire_col_idx:
            expire_str = ws.cell(row_idx, expire_col_idx).value

        # Парсим баланс
        if not balance_str: balance_str = "0"
        balance_str = str(balance_str).replace(',', '.')
        try:
            balance = float(balance_str)
        except:
            balance = 0.0
            
        return {
            'row': row_idx,
            'balance': balance,
            'expires': expire_str,
            'sheet_instance': ws,
            'balance_col_idx': balance_col_idx, # Возвращаем номер колонки для записи
            'expire_col_idx': expire_col_idx
        }

    except Exception as e:
        print(f"Error checking subscription: {e}")
        return None
    

def cleanup_expired_holds():
    """Удаляет просроченные брони и уведомляет всех."""
    expired_holds = PendingBooking.objects.filter(expires_at__lte=timezone.now(), is_confirmed=False)
    if not expired_holds.exists():
        return

    service = get_calendar_service()
    
    for hold in expired_holds:
        # Удаляем из Google
        if hold.google_event_id and hold.room.google_calendar_id:
            try:
                calendar_id = str(hold.room.google_calendar_id).strip().replace('"', '').replace("'", "").replace(' ', '')
                service.events().delete(calendarId=calendar_id, eventId=hold.google_event_id).execute()
            except: pass
        
        # === УВЕДОМЛЕНИЯ (ЕСЛИ ЕСТЬ КОНТАКТЫ) ===
        if hold.client_name and hold.client_phone:
            # Форматируем локальные дату/время/длительность
            start_local = timezone.localtime(hold.start_time)
            end_local = timezone.localtime(hold.end_time)
            date_str = start_local.strftime('%Y-%m-%d')
            start_time_str = start_local.strftime('%H:%M')
            duration_hours = (end_local - start_local).total_seconds() / 3600
            # ИСПОЛЬЗУЕМ НОВУЮ ФУНКЦИЮ
            duration_display = format_hours_text(duration_hours)

            # 1. Сообщение в группу (стилизовано)
            group_message_text = (
                "〰〰〰〰〰〰〰〰〰〰\n"
                "⏰ Резерв истек (Нет оплаты)\n\n"
                f"🏠 Кабинет: {hold.room.name}\n"
                f"🗓 Дата: {date_str} | {start_time_str}\n"
                f"⏳ Длительность: {duration_display} ч\n"
                f"👤 Клиент: {hold.client_name} ({hold.client_phone})\n"
                "〰〰〰〰〰〰〰〰〰〰"
            )
            send_whatsapp_group(group_message_text)

            # 2. Сообщение клиенту (короткая инструкция и вежливое уведомление)
            client_message_text = (
                "〰〰〰〰〰〰〰〰〰〰\n"
                "⏰ Ваш резерв истёк\n\n"
                f"🏠 Кабинет: {hold.room.name}\n"
                f"🗓 Дата: {date_str} | {start_time_str}\n"
                f"⏳ Длительность: {duration_display} ч\n\n"
                "Слот освобождён — оплата не поступила.\n"
                "Если хотите, выберите другой доступный слот на сайте.\n"
                "〰〰〰〰〰〰〰〰〰〰"
            )
            send_whatsapp_client(hold.client_phone, client_message_text)
        # ========================================

        hold.delete()
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

                door_code = room_details.get("🔑 Код от ключницы для открытия двери", "Код не найден")
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
# main/views.py

@require_GET
def check_whatsapp_existence(request):
    """Проверяет наличие WhatsApp на номере через Green API."""
    phone_raw = request.GET.get('phone')
    if not phone_raw:
        return JsonResponse({'success': False, 'error': 'Нет номера'})

    # Чистим номер (оставляем только цифры)
    phone = ''.join(filter(str.isdigit, str(phone_raw)))
    
    # Форматируем под 77... (Green API требует формат без +)
    if phone.startswith('8') and len(phone) == 11:
        phone = '7' + phone[1:]
    elif len(phone) == 10:
        phone = '7' + phone

    try:
        # Берем настройки
        instance_id = getattr(settings, 'GREEN_API_INSTANCE_ID', '')
        token = getattr(settings, 'GREEN_API_TOKEN', '')

        if not instance_id or not token:
            # Если ключей нет, пропускаем проверку (чтобы не блокировать работу)
            return JsonResponse({'success': True, 'exists': True, 'bypass': True})

        url = f"https://api.green-api.com/waInstance{instance_id}/checkWhatsapp/{token}"
        
        payload = {
            "phoneNumber": phone
        }
        
        # Делаем запрос к Green API
        response = requests.post(url, json=payload, timeout=5)
        data = response.json()

        # Green API возвращает: {"existsWhatsapp": true}
        if data.get('existsWhatsapp'):
            return JsonResponse({'success': True, 'exists': True})
        else:
            return JsonResponse({'success': True, 'exists': False})

    except Exception as e:
        print(f"Green API Check Error: {e}")
        # В случае ошибки API лучше разрешить, чем запретить
        return JsonResponse({'success': True, 'exists': True, 'error': str(e)})
# main/views.py

def get_price_from_sheet(duration_hours: Decimal, people_count: int):
    """Читает прайс-лист из Google Sheets (с поддержкой 'по 1500 в час')."""
    try:
        creds = Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE,
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
        )
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_url(settings.SPREADSHEET_URL)
        worksheet = spreadsheet.worksheet("Прайс-лист")
        data = worksheet.get_all_values()[1:] 

        rules_for_group = []
        for row in data:
            if len(row) < 3: continue
            grp_text, hours_text, price_text = row[0], row[1], row[2]
            
            min_p, max_p = parse_people_range(grp_text)
            hours_val, is_min = parse_hours(hours_text)

            if min_p is None or max_p is None or not (min_p <= people_count <= max_p):
                continue
            if hours_val is None: continue
            
            # === ИСПРАВЛЕНИЕ: ПАРСИНГ ЦЕНЫ (ТЕКСТ ИЛИ ЧИСЛО) ===
            total_price = None
            hourly_rate = None
            
            clean_price = str(price_text).strip().lower().replace(' ', '')
            
            # 1. Если написано "по 1500 в час"
            if 'по' in clean_price or 'вчас' in clean_price:
                # Ищем число внутри текста
                match = re.search(r'(\d+)', clean_price)
                if match:
                    rate = int(match.group(1))
                    # Если это правило ">= 20 часов", то цена = ставка * запрошенные часы
                    # Но пока сохраним ставку, посчитаем ниже
                    hourly_rate = Decimal(rate)
                    # Предварительная цена для сортировки (ставка * часы из правила)
                    total_price = hourly_rate * hours_val
            
            # 2. Если просто число "30000"
            else:
                try:
                    total_price = Decimal(clean_price)
                except:
                    continue # Непонятная цена
            
            if total_price is None and hourly_rate is None: continue

            rules_for_group.append({
                'hours': hours_val,
                'is_minimum': is_min,
                'price': total_price, 
                'rate': hourly_rate # Запоминаем ставку, если она была
            })
            # ==================================================

        if not rules_for_group:
            return None

        rules_for_group.sort(key=lambda x: x['hours'])

        final_price = None

        # 1. Точное совпадение
        exact_rule = next((r for r in rules_for_group if not r['is_minimum'] and r['hours'] == duration_hours), None)
        if exact_rule:
            final_price = exact_rule['price']

        # 2. Дробные часы (X.5)
        elif duration_hours % 1 == Decimal('0.5'):
            base_hour = duration_hours - Decimal('0.5')
            if base_hour == 0:
                one_hour_rule = next((r for r in rules_for_group if not r['is_minimum'] and r['hours'] == 1), None)
                if one_hour_rule: final_price = one_hour_rule['price'] / 2
            else:
                base_hour_rule = next((r for r in rules_for_group if not r['is_minimum'] and r['hours'] == base_hour), None)
                if base_hour_rule:
                    base_price = base_hour_rule['price']
                    if base_hour > 0:
                        half_hour_price = base_price / (base_hour * 2)
                        final_price = base_price + half_hour_price

        # 3. Правило ">= X часов" (для абонементов 20+)
        if final_price is None:
            min_rules_applicable = [r for r in rules_for_group if r['is_minimum'] and r['hours'] <= duration_hours]
            if min_rules_applicable:
                best_min_rule = max(min_rules_applicable, key=lambda x: x['hours'])
                
                # Если у правила была ставка "по 1500 в час"
                if best_min_rule['rate'] is not None:
                    final_price = best_min_rule['rate'] * duration_hours
                else:
                    # Если была фикс цена, но правило >= (например "более 5 часов - 15000")
                    # Тут спорно: либо это цена за всё, либо надо вычислять ставку.
                    # Обычно в таблице для >= пишут ставку. Если нет — берем как фикс.
                    final_price = best_min_rule['price']

        if final_price is not None:
            return int(final_price.to_integral_value(rounding='ROUND_HALF_UP'))
        else:
             return None

    except Exception as e:
        print(f"Error reading Price List sheet: {e}")
        return None

# main/views.py

@require_GET
def calculate_subscription_benefit(request):
    """Считает стоимость пакета и экономию."""
    try:
        hours_str = request.GET.get('hours')
        people_str = request.GET.get('people_count', '1')
        
        if not hours_str:
            return JsonResponse({'success': False, 'error': 'No hours specified'})

        hours = Decimal(hours_str)
        people_count = int(people_str)
        
        # 1. Получаем цену ПАКЕТА (как сейчас считает система для таблицы)
        package_price = get_price_from_sheet(hours, people_count)
        
        if package_price is None:
             return JsonResponse({'success': False, 'error': 'Цена для такого пакета не найдена'})

        # 2. Получаем БАЗОВУЮ цену за 1 час для этого кол-ва людей
        base_price_1h = get_price_from_sheet(Decimal('1'), people_count)
        
        if base_price_1h is None:
             return JsonResponse({'success': True, 'price': package_price, 'savings': 0})

        # 3. Считаем экономию
        standard_cost = base_price_1h * int(hours)
        savings = standard_cost - package_price
        if savings < 0: savings = 0

        return JsonResponse({
            'success': True,
            'price': package_price,
            'savings': savings
        })

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)




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
# --- ИСПРАВЛЕННАЯ ФУНКЦИЯ: ЗАПИСЬ В ИСТОРИЮ КЛИЕНТОВ ---
def save_booking_to_sheet(booking_data):
    """Записывает данные о бронировании с сайта в 'История клиентов'."""
    
    # 1. СНАЧАЛА ПОДКЛЮЧАЕМСЯ, ЧТОБЫ ПОЛУЧИТЬ ПЕРЕМЕННУЮ ws
    try:
        creds = Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
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
            # Если лист новый, создаем заголовки сразу
            headers = [
                "ID клиента", "Имя", "Телефон", "Дата", "Время",
                "Длительность", "Количество человек", "Кабинет",
                "Цена", "Дата/время бронирования", "Новый клиент", 
                "ID брони", "Источник", "Напоминание"
            ]
            ws.append_row(headers)
            print("Создан новый лист 'История клиентов' с заголовками")
            # Перезапрашиваем ws, чтобы убедиться
            ws = history_spreadsheet.worksheet("История клиентов")

        # 2. ТЕПЕРЬ ws СУЩЕСТВУЕТ, МОЖНО ЧИТАТЬ ЗАГОЛОВКИ
        headers = ws.row_values(1)
        new_headers_added = False

        # Проверяем и добавляем недостающие колонки
        if "ID брони" not in headers:
            headers.append("ID брони")
            new_headers_added = True
        if "Источник" not in headers:
            headers.append("Источник")
            new_headers_added = True
        if "Напоминание" not in headers:
            headers.append("Напоминание")
            new_headers_added = True

        # Если добавили новые заголовки — обновляем первую строку
        if new_headers_added:
            if len(headers) > ws.col_count:
                ws.resize(cols=len(headers))
            
            # Обновляем всю первую строку разом
            range_label = f"A1:{gspread.utils.rowcol_to_a1(1, len(headers))}"
            ws.update(range_label, [headers])
            print(f"[HIST] Обновлены заголовки: {headers}")

        # 3. ПОДГОТОВКА ДАННЫХ
        client_phone = booking_data.get("client_phone", "")
        if client_phone:
            user_id = ''.join(filter(str.isdigit, client_phone)) + '@c.us'
            if user_id.startswith('8'): user_id = '7' + user_id[1:]
            elif not user_id.startswith('7') and len(user_id.split('@')[0]) == 10:
                user_id = '7' + user_id
        else:
            user_id = "website_user_unknown_phone"

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
            "Новый клиент": "Да",
            "ID брони": booking_id,
            "Источник": "Сайт",
            "Напоминание": "Нет"
        }

        # 4. ЗАПИСЬ СТРОКИ
        # Собираем значения в том порядке, в котором идут заголовки в таблице сейчас
        # Это важно, если порядок колонок изменится вручную
        final_row = []
        for h in headers:
            final_row.append(row_dict.get(h, ""))

        ws.append_row(final_row)
        print(f"[HIST] Записана бронь {booking_id} для {user_id}")
        return booking_id

    except Exception as e:
        print(f"Ошибка при записи данных клиента в историю: {e}")
        return None

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
    all_rooms = Room.objects.filter(is_active=True, show_in_booking=True).exclude(google_calendar_id__isnull=True).exclude(google_calendar_id__exact='')

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
    """Временный резерв (БД + Google + WhatsApp Group)."""
    
    cleanup_expired_holds() # Чистим старое

    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
        room_id = data.get('room_id')
        date_str = data.get('date')
        start_time_str = data.get('start_time')
        duration_str = data.get('duration')
        
        # Данные клиента (могут быть пустыми на 1 шаге)
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

        # Проверка занятости в БД
        if PendingBooking.objects.filter(room=room, expires_at__gt=timezone.now(), start_time__lt=end_dt_aware, end_time__gt=start_dt_aware).exists():
             return JsonResponse({'success': False, 'error': 'Слот уже занят.'}, status=409)

        gcal_event_id = None

        # === ЕСЛИ ЕСТЬ ИМЯ (ПЕРЕХОД К ОПЛАТЕ) ===
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

                # Создаем серое событие
    # === СКРЫТИЕ НОМЕРА (Серый резерв) ===
                if room.hide_phone_in_calendar:
                    summary_text = f'⏳ Временный резерв {room.name}: {client_name}'
                else:
                    summary_text = f'⏳ Временный резерв {room.name}: {client_name} ({client_phone})'
                # =====================================

                # Создаем серое событие
                event_body = {
                    'summary': summary_text, # Используем переменную
                    'description': 'Клиент перешел к оплате. Резерв 15 минут.',
                    'start': {'dateTime': start_dt_aware.isoformat(), 'timeZone': settings.TIME_ZONE},
                    'end': {'dateTime': end_dt_aware.isoformat(), 'timeZone': settings.TIME_ZONE},
                    'colorId': None 
                }
                gcal_event = service.events().insert(calendarId=calendar_id, body=event_body).execute()
                gcal_event_id = gcal_event.get('id')

                # === ОТПРАВКА В ГРУППУ (НАЧАЛО) ===
# === ОТПРАВКА В ГРУППУ (НАЧАЛО) ===
                # Форматируем длительность, которая пришла строкой (duration_str)
                dur_display = format_hours_text(duration_str)
                
                msg = (
                    f"⏳ Временный резерв (Начало оформления)\n"
                    f"🏠 Кабинет: {room.name}\n"
                    f"🗓 Дата: {date_str} | {start_time_str}\n"
                    f"⏳ Длительность: {dur_display}\n" 
                    f"👤 Клиент : {client_name} ({client_phone})"
                )
                send_whatsapp_group(msg)
                # ==================================

            except Exception as e:
                print(f"GCal Error: {e}")
                return JsonResponse({'success': False, 'error': 'Ошибка связи с календарем.'}, status=500)

        # Сохраняем в БД (ТЕПЕРЬ С ИМЕНЕМ И ТЕЛЕФОНОМ)
        pending_booking = PendingBooking.objects.create(
            room=room,
            start_time=start_dt_aware,
            end_time=end_dt_aware,
            google_event_id=gcal_event_id,
            client_name=client_name,
            client_phone=client_phone
        )

        return JsonResponse({'success': True, 'hold_id': str(pending_booking.hold_id)})

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
@require_POST
@csrf_exempt
def cancel_hold(request):
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
        hold_id_str = data.get('hold_id')

        if not hold_id_str: return JsonResponse({'success': False}, status=400)

        try:
            hold_id = uuid.UUID(hold_id_str)
            pending_booking = PendingBooking.objects.get(hold_id=hold_id)
            
            # Удаляем из Google
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

            # === ОТПРАВКА В ГРУППУ (КЛИЕНТ ОТМЕНИЛ) ===
            if pending_booking.client_name and pending_booking.client_phone:
                
                # Проверяем: это Таймер (время вышло) или Ручная отмена?
                # (Сравниваем текущее время с временем истечения)
                if timezone.now() >= pending_booking.expires_at - datetime.timedelta(seconds=5):
                    # ЭТО ТАЙМЕР (время вышло)
                    start_local = timezone.localtime(pending_booking.start_time)
                    end_local = timezone.localtime(pending_booking.end_time)
                    date_str = start_local.strftime('%Y-%m-%d')
                    start_time_str = start_local.strftime('%H:%M')
                    duration_hours = (end_local - start_local).total_seconds() / 3600
                    # ИСПОЛЬЗУЕМ НОВУЮ ФУНКЦИЮ
                    duration_display = format_hours_text(duration_hours)

                    group_message_text = (
                        "〰〰〰〰〰〰〰〰〰〰\n"
                        "⏰ Время истекло (Нет оплаты)\n\n"
                        f"🏠 Кабинет: {pending_booking.room.name}\n"
                        f"🗓 Дата: {date_str} | {start_time_str}\n"
                        f"⏳ Длительность: {duration_display}\n"
                        f"👤 Клиент: {pending_booking.client_name} ({pending_booking.client_phone})\n"
                        "〰〰〰〰〰〰〰〰〰〰"
                    )

                    client_message_text = (
                        "〰〰〰〰〰〰〰〰〰〰\n"
                        "⏰ Ваш резерв истёк\n\n"
                        f"🏠 Кабинет: {pending_booking.room.name}\n"
                        f"🗓 Дата: {date_str} | {start_time_str}\n"
                        f"⏳ Длительность: {duration_display}\n\n"
                        "Слот освобождён — оплата не поступила.\n"
                        "Если хотите, выберите другой доступный слот на сайте.\n"
                        "〰〰〰〰〰〰〰〰〰〰"
                    )
                else:
                    # ЭТО РУЧНАЯ ОТМЕНА
                    start_local = timezone.localtime(pending_booking.start_time)
                    end_local = timezone.localtime(pending_booking.end_time)
                    date_str = start_local.strftime('%Y-%m-%d')
                    start_time_str = start_local.strftime('%H:%M')
                    duration_hours = (end_local - start_local).total_seconds() / 3600
                    try:
                        if float(duration_hours).is_integer():
                            duration_display = str(int(duration_hours))
                        else:
                            duration_display = f"{duration_hours:.1f}"
                    except Exception:
                        duration_display = str(duration_hours)

                    group_message_text = (
                        "〰〰〰〰〰〰〰〰〰〰\n"
                        "❌ Клиент отменил оформление\n\n"
                        f"🏠 Кабинет: {pending_booking.room.name}\n"
                        f"🗓 Дата: {date_str} | {start_time_str}\n"
                        f"⏳ Длительность: {duration_display} ч\n"
                        f"👤 Клиент: {pending_booking.client_name} ({pending_booking.client_phone})\n"
                        "〰〰〰〰〰〰〰〰〰〰"
                    )

                    client_message_text = (
                        "〰〰〰〰〰〰〰〰〰〰\n"
                        "❌ Вы отменили бронь\n\n"
                        f"🏠 Кабинет: {pending_booking.room.name}\n"
                        f"🗓 Дата: {date_str} | {start_time_str}\n"
                        f"⏳ Длительность: {duration_display} ч\n\n"
                        "Если хотите снова забронировать — выберите слот на сайте.\n"
                        "〰〰〰〰〰〰〰〰〰〰"
                    )

                # Отправляем
                send_whatsapp_group(group_message_text)
                send_whatsapp_client(pending_booking.client_phone, client_message_text)
            # ====================

            pending_booking.delete()
            return JsonResponse({'success': True})
            
        except PendingBooking.DoesNotExist:
            return JsonResponse({'success': True})

    except Exception as e:
         return JsonResponse({'success': False, 'error': str(e)}, status=500)

@require_POST
@csrf_exempt
def create_booking(request):
    if request.method != "POST": return HttpResponseBadRequest("POST only")
    try: 
        data = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError: 
        return HttpResponseBadRequest("Invalid JSON")

    # --- ПОЛУЧЕНИЕ ДАННЫХ ---
    hold_id_str = data.get('hold_id')
    client_name = data.get('client_name')
    client_phone = data.get('client_phone')
    payment_method = data.get('payment_method', 'single') # 'single' или 'subscription'
    
    # Данные чека (только для single)
    file_data_b64 = data.get('receipt_file_data') 
    file_name = data.get('receipt_file_name')     
    receipt_number = data.get('receipt_number')   

    # 1. БАЗОВАЯ ВАЛИДАЦИЯ
    if not hold_id_str or not client_name or not client_phone:
         return JsonResponse({'success': False, 'error': 'Не заполнены обязательные поля (Имя, Телефон, ID).'}, status=400)

    # 2. ПРОВЕРКА ВРЕМЕННОГО РЕЗЕРВА
    try:
        hold_uuid = uuid.UUID(hold_id_str)
        pending_booking = get_object_or_404(PendingBooking, hold_id=hold_uuid)
        
        if pending_booking.expires_at < timezone.now() and not pending_booking.is_confirmed:
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

    # 3. ЛОГИКА ОПЛАТЫ
    payment_info_text = ""
    is_subscription = (payment_method == 'subscription')
    current_balance_display = "" # === НОВОЕ: Переменная для хранения остатка ===

# Внутри create_booking ...
    if is_subscription:
        # === АБОНЕМЕНТ ===
        sub_data = get_subscription_client(client_phone)
        
        if not sub_data:
            return JsonResponse({'success': False, 'error': 'Абонемент не найден. Оплатите разово.'}, status=400)
        
        required_hours = float(duration_hours)
        if sub_data['balance'] < required_hours:
             return JsonResponse({'success': False, 'error': f'Недостаточно часов. Ваш баланс: {sub_data["balance"]} ч.'}, status=400)
        
        # Списываем часы
        try:
            new_balance = sub_data['balance'] - required_hours
            ws = sub_data['sheet_instance']
            row_idx = sub_data['row']
            
            # === ИСПРАВЛЕНИЕ: Используем динамический индекс колонки ===
            balance_col_idx = sub_data['balance_col_idx'] 
            
            ws.update_cell(row_idx, balance_col_idx, new_balance)
            print(f"Subscription deducted: {required_hours}h. New balance: {new_balance}")
            
            # Красивый вывод остатка
            current_balance_display = format_hours_text(new_balance)

        except Exception as e:
            print(f"Error updating balance: {e}")
            return JsonResponse({'success': False, 'error': 'Ошибка списания баланса.'}, status=500)
        
    else:
        # === РАЗОВАЯ ОПЛАТА ===
        if not file_data_b64 and not receipt_number:
             return JsonResponse({'success': False, 'error': 'Прикрепите скан чека или введите номер квитанции.'}, status=400)

        # Проверка файла
        if file_data_b64:
            is_valid, message = _validate_receipt(file_name, file_data_b64)
            if not is_valid:
                return JsonResponse({'success': False, 'error': message}, status=400)
            payment_info_text = f"Чек загружен: {file_name}"
        else:
            payment_info_text = f"Номер чека: {receipt_number}"

    # 4. ОБНОВЛЕНИЕ КАЛЕНДАРЯ
    try:
        service = get_calendar_service()
        calendar_id = str(room.google_calendar_id).strip().replace('"', '').replace("'", "").replace(' ', '')
        
        
        # Формируем описание для календаря

# Формируем описание для календаря
        if is_subscription:
            desc_payment = f"Абонемент (списано {duration_hours}ч, остаток {current_balance_display})"
        else:
            desc_payment = payment_info_text

        # === СКРЫТИЕ НОМЕРА (Зеленая бронь) ===
        if room.hide_phone_in_calendar:
            # 1. Заголовок БЕЗ телефона
            event_summary = f'Сайт:{client_name}' 
            
            # 2. Описание БЕЗ строки "Тел:" вообще
            event_description = (
                f'Клиент: {client_name}\n'
                f'Кол-во: {people_count}\n'
                f'Длительность: {duration_hours} ч.\nЦена: {data.get("price", price)} тг\n'
                f'Оплата: {desc_payment}\nИсточник: Сайт' 
            )
        else:
            # 1. Заголовок С телефоном
            event_summary = f'Сайт:{client_name} ({client_phone})'
            
            # 2. Описание С телефоном
            event_description = (
                f'Клиент: {client_name}\nТел: {client_phone}\n'
                f'Кол-во: {people_count}\n'
                f'Длительность: {duration_hours} ч.\nЦена: {data.get("price", price)} тг\n'
                f'Оплата: {desc_payment}\nИсточник: Сайт' 
            )
        # ======================================
        
        event_patch = {
            'summary': event_summary,
            'description': event_description,
            'colorId': None, 
        }
        
        # Если событие уже есть (создано при hold_slot), обновляем его
        if pending_booking.google_event_id:
            try:
                service.events().patch(
                    calendarId=calendar_id,
                    eventId=pending_booking.google_event_id,
                    body=event_patch
                ).execute()
            except Exception as e:
                print(f"Failed to patch event, creating new one: {e}")
                # Fallback: создаем новое
                event_patch['start'] = {'dateTime': start_dt_aware.isoformat(), 'timeZone': settings.TIME_ZONE}
                event_patch['end'] = {'dateTime': end_dt_aware.isoformat(), 'timeZone': settings.TIME_ZONE}
                service.events().insert(calendarId=calendar_id, body=event_patch).execute()
        else:
            # Создаем с нуля
            event_patch['start'] = {'dateTime': start_dt_aware.isoformat(), 'timeZone': settings.TIME_ZONE}
            event_patch['end'] = {'dateTime': end_dt_aware.isoformat(), 'timeZone': settings.TIME_ZONE}
            service.events().insert(calendarId=calendar_id, body=event_patch).execute()

    except Exception as e:
        print(f"Error updating Google Calendar: {e}")
        # Не блокируем успех, если календарь сбоит

    # 5. Удаляем временный резерв
    pending_booking.delete()

    # 6. ЗАПИСЬ В ИСТОРИЮ (Google Sheets)
    try:
        sheet_booking_data = {
            "client_name": client_name,
            "client_phone": client_phone,
            "date": date_str,
            "start_time": start_time_str,
            "duration_hours": duration_hours,
            "people_count": people_count,
            "room_name": room.name,
            "price": "Абонемент" if is_subscription else data.get('price', 0), # В историю пишем словами
            "is_client_new": True 
        }
        save_booking_to_sheet(sheet_booking_data)
    except Exception as e:
        print(f"Error saving to History Sheet: {e}")

    # 7. ОТПРАВКА УВЕДОМЛЕНИЙ (WhatsApp)
# ... (код выше без изменений) ...

    # 7. ОТПРАВКА УВЕДОМЛЕНИЙ (WhatsApp)
# 7. ОТПРАВКА УВЕДОМЛЕНИЙ (WhatsApp)
    studio_details = {}
    try:
        # Получаем данные, но сообщение будем собирать сами заново
        door_code, _, studio_details = get_door_code_and_instructions(room.name)
        
        # Извлекаем тексты из полученного словаря
        address = studio_details.get("address", "Адрес уточняется")
        enter_instr = studio_details.get("enter_instruction", "")
        general_info = studio_details.get("general_info", "")
        
        # Рассчитываем время окончания для красивого отображения (например: 14:00 - 16:00)
        end_time_dt = start_dt_aware + datetime.timedelta(hours=duration_hours)
        end_time_str = end_time_dt.strftime('%H:%M')
        
        # Телефон админа
        settings_obj = SiteSettings.objects.first()
        admin_phone = settings_obj.phone if settings_obj else "77073910808"

        # === ГЕНЕРАЦИЯ СООБЩЕНИЯ КЛИЕНТУ ===
        duration_display = format_hours_text(duration_hours)

        # === ГЕНЕРАЦИЯ СООБЩЕНИЯ КЛИЕНТУ ===
        client_message_text = (
            f"✅ Ваша бронь кабинета {room.name} подтверждена!\n"
            f"📍 Адрес: {address}\n"
            f"🔑 Код двери: {door_code}\n"
            f"🗓 {date_str} | с {start_time_str} до {end_time_str}\n"
        )

        # Если это абонемент — вставляем остаток СРАЗУ ПОСЛЕ времени
        if is_subscription:
            client_message_text += f"📉 Ваш остаток часов: {current_balance_display}\n"

        # Далее инструкции по открытию
        if enter_instr:
            client_message_text += f"\n🚪 Как открыть:\n{enter_instr}\n"
        
        # Общие правила (Про уборку и т.д.)
        if general_info:
            client_message_text += f"\n{general_info}\n"

        # Футер
        client_message_text += f"\n📞 По вопросам обращайтесь к администратору: {admin_phone}"
        # ====================================

        # === ГЕНЕРАЦИЯ СООБЩЕНИЯ ДЛЯ ГРУППЫ АДМИНОВ ===
        if is_subscription:
            group_payment_text = f"💳 Абонемент (Списано {duration_hours}ч)\n📉 Остаток: {current_balance_display}"
        else:
            group_payment_text = f"💰 Оплата: {data.get('price', 0)} ₸\n{payment_info_text}"

        group_message_text = (
            "〰〰〰〰〰〰〰〰〰〰\n"
            "📅 Новая бронь (Сайт)\n\n"
            f"🏠 Кабинет: {room.name}\n"
            f"🗓 Дата: {date_str} | с {start_time_str} до {end_time_str}\n"
            f"⏳ Длительность: {duration_hours} ч\n"
            f"👥 Гостей: {people_count}\n"
            f"👤 Клиент: {client_name} ({client_phone})\n"
            f"{group_payment_text}\n"
            "〰〰〰〰〰〰〰〰〰〰"
        )


        # Отправка через API
        bot_api_url = getattr(settings, 'BOT_WHATSAPP_API_URL', None)
        group_chat_id = getattr(settings, 'GROUP_CHAT_ID', None)

        if bot_api_url:
            # Клиенту
            client_chat_id = ''.join(filter(str.isdigit, client_phone)) + '@c.us'
            if client_chat_id.startswith('8'): client_chat_id = '7' + client_chat_id[1:]
            elif not client_chat_id.startswith('7') and len(client_chat_id.split('@')[0]) == 10:
                client_chat_id = '7' + client_chat_id
            
            try: requests.post(bot_api_url, json={'chat_id': client_chat_id, 'message': client_message_text}, timeout=10)
            except Exception as req_err: print(f"Error sending to client API: {req_err}")
            
            # Группе
            if group_chat_id:
                try: requests.post(bot_api_url, json={'chat_id': group_chat_id, 'message': group_message_text}, timeout=10)
                except Exception as req_err: print(f"Error sending to group API: {req_err}")

    except Exception as e:
        print(f"Error preparing/sending WhatsApp notifications: {e}")

    # Возвращаем баланс на фронтенд
    return JsonResponse({
            'success': True, 
            'studio_details': studio_details,
            'new_balance': current_balance_display if is_subscription else None
    })


# ========================================
# admin_confirm_booking — Подтверждение оплаты администратором
# ========================================
def admin_confirm_booking(pending_booking):
    """
    Вызывается из админки. Запускает полную логику бронирования:
    календарь, Google Sheets, WhatsApp уведомления.
    Возвращает (True, 'OK') или (False, 'описание ошибки').
    """
    try:
        room = pending_booking.room
        client_name = pending_booking.client_name
        client_phone = pending_booking.client_phone
        start_dt_aware = timezone.localtime(pending_booking.start_time)
        end_dt_aware = timezone.localtime(pending_booking.end_time)
        date_str = start_dt_aware.strftime('%Y-%m-%d')
        start_time_str = start_dt_aware.strftime('%H:%M')
        duration_hours = (end_dt_aware - start_dt_aware).total_seconds() / 3600
        payment_info_text = "Подтверждено администратором"

        # 1. ОБНОВЛЕНИЕ КАЛЕНДАРЯ
        try:
            service = get_calendar_service()
            calendar_id = str(room.google_calendar_id).strip().replace('"', '').replace("'", "").replace(' ', '')
            
            desc_payment = payment_info_text

            if room.hide_phone_in_calendar:
                event_summary = f'Сайт:{client_name}'
                event_description = (
                    f'Клиент: {client_name}\n'
                    f'Длительность: {duration_hours} ч.\n'
                    f'Оплата: {desc_payment}\nИсточник: Сайт (Админ)'
                )
            else:
                event_summary = f'Сайт:{client_name} ({client_phone})'
                event_description = (
                    f'Клиент: {client_name}\nТел: {client_phone}\n'
                    f'Длительность: {duration_hours} ч.\n'
                    f'Оплата: {desc_payment}\nИсточник: Сайт (Админ)'
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
                    print(f"Admin confirm: Failed to patch event, creating new: {e}")
                    event_patch['start'] = {'dateTime': start_dt_aware.isoformat(), 'timeZone': settings.TIME_ZONE}
                    event_patch['end'] = {'dateTime': end_dt_aware.isoformat(), 'timeZone': settings.TIME_ZONE}
                    service.events().insert(calendarId=calendar_id, body=event_patch).execute()
            else:
                event_patch['start'] = {'dateTime': start_dt_aware.isoformat(), 'timeZone': settings.TIME_ZONE}
                event_patch['end'] = {'dateTime': end_dt_aware.isoformat(), 'timeZone': settings.TIME_ZONE}
                service.events().insert(calendarId=calendar_id, body=event_patch).execute()
        except Exception as e:
            print(f"Admin confirm: Calendar error (non-blocking): {e}")

        # 2. ЗАПИСЬ В ИСТОРИЮ (Google Sheets)
        try:
            sheet_booking_data = {
                "client_name": client_name,
                "client_phone": client_phone,
                "date": date_str,
                "start_time": start_time_str,
                "duration_hours": duration_hours,
                "people_count": 1,
                "room_name": room.name,
                "price": "Подтверждено админом",
                "is_client_new": True
            }
            save_booking_to_sheet(sheet_booking_data)
        except Exception as e:
            print(f"Admin confirm: Sheet error (non-blocking): {e}")

        # 3. УВЕДОМЛЕНИЯ (WhatsApp)
        try:
            door_code, _, studio_details = get_door_code_and_instructions(room.name)
            address = studio_details.get("address", "Адрес уточняется")
            enter_instr = studio_details.get("enter_instruction", "")
            general_info = studio_details.get("general_info", "")
            
            end_time_dt = start_dt_aware + datetime.timedelta(hours=duration_hours)
            end_time_str = end_time_dt.strftime('%H:%M')
            
            settings_obj = SiteSettings.objects.first()
            admin_phone = settings_obj.phone if settings_obj else "77073910808"
            duration_display = format_hours_text(duration_hours)

            client_message_text = (
                f"✅ Ваша бронь кабинета {room.name} подтверждена!\n"
                f"📍 Адрес: {address}\n"
                f"🔑 Код двери: {door_code}\n"
                f"🗓 {date_str} | с {start_time_str} до {end_time_str}\n"
            )
            if enter_instr:
                client_message_text += f"\n🚪 Как открыть:\n{enter_instr}\n"
            if general_info:
                client_message_text += f"\n{general_info}\n"
            client_message_text += f"\n📞 По вопросам обращайтесь к администратору: {admin_phone}"

            group_message_text = (
                "〰〰〰〰〰〰〰〰〰〰\n"
                "📅 Бронь подтверждена (Админ)\n\n"
                f"🏠 Кабинет: {room.name}\n"
                f"🗓 Дата: {date_str} | с {start_time_str} до {end_time_str}\n"
                f"⏳ Длительность: {duration_hours} ч\n"
                f"👤 Клиент: {client_name} ({client_phone})\n"
                f"💰 Оплата: {payment_info_text}\n"
                "〰〰〰〰〰〰〰〰〰〰"
            )

            bot_api_url = getattr(settings, 'BOT_WHATSAPP_API_URL', None)
            group_chat_id = getattr(settings, 'GROUP_CHAT_ID', None)

            if bot_api_url:
                client_chat_id = ''.join(filter(str.isdigit, client_phone)) + '@c.us'
                if client_chat_id.startswith('8'): client_chat_id = '7' + client_chat_id[1:]
                elif not client_chat_id.startswith('7') and len(client_chat_id.split('@')[0]) == 10:
                    client_chat_id = '7' + client_chat_id

                try: requests.post(bot_api_url, json={'chat_id': client_chat_id, 'message': client_message_text}, timeout=10)
                except Exception as req_err: print(f"Admin confirm: WA client error: {req_err}")
                
                if group_chat_id:
                    try: requests.post(bot_api_url, json={'chat_id': group_chat_id, 'message': group_message_text}, timeout=10)
                    except Exception as req_err: print(f"Admin confirm: WA group error: {req_err}")
        except Exception as e:
            print(f"Admin confirm: WhatsApp error (non-blocking): {e}")

        # 4. ПОМЕЧАЕМ КАК ПОДТВЕРЖДЁННУЮ (фронтенд увидит через polling)
        pending_booking.is_confirmed = True
        pending_booking.save()
        
        return (True, 'OK')

    except Exception as e:
        print(f"admin_confirm_booking error: {e}")
        import traceback
        traceback.print_exc()
        return (False, str(e))


# ========================================
# api_check_hold_status — Проверка статуса резерва (polling с фронтенда)
# ========================================
@require_GET
def api_check_hold_status(request):
    """
    Фронтенд опрашивает этот endpoint чтобы узнать, подтвердил ли админ оплату.
    GET /api/check_hold_status/?hold_id=xxx
    Возвращает: {status: 'active'|'confirmed'|'expired'|'not_found'}
    """
    hold_id_str = request.GET.get('hold_id')
    if not hold_id_str:
        return JsonResponse({'status': 'not_found'})
    
    try:
        hold_uuid = uuid.UUID(hold_id_str)
        pending = PendingBooking.objects.get(hold_id=hold_uuid)
        
        if pending.is_confirmed:
            return JsonResponse({'status': 'confirmed'})
        elif pending.is_expired():
            return JsonResponse({'status': 'expired'})
        else:
            return JsonResponse({'status': 'active', 'expires_at': timezone.localtime(pending.expires_at).isoformat()})
    
    except PendingBooking.DoesNotExist:
        return JsonResponse({'status': 'not_found'})
    except Exception:
        return JsonResponse({'status': 'not_found'})

@require_GET
def check_balance_api(request):
    """API: Проверяет баланс по номеру телефона."""
    phone = request.GET.get('phone')
    if not phone:
        return JsonResponse({'success': False, 'error': 'Нет номера'})
        
    sub_data = get_subscription_client(phone)
    
    if not sub_data:
        # Клиент не найден в базе абонементов
        return JsonResponse({
            'success': True, 
            'found': False, 
            'balance': 0
        })
    
    # Проверяем срок действия
    is_expired = False
    try:
        # Формат в таблице: YYYY-MM-DD
        expire_date = datetime.datetime.strptime(sub_data['expires'], "%Y-%m-%d").date()
        if expire_date < datetime.date.today():
            is_expired = True
    except:
        pass # Если дата кривая, считаем что не истек (или можно наоборот)

    return JsonResponse({
        'success': True,
        'found': True,
        'balance': sub_data['balance'],
        'is_expired': is_expired,
        'expires_date': sub_data['expires']
    })


@require_POST
@csrf_exempt
def buy_subscription_api(request):
    """API: Покупка абонемента + Уведомление в группу + Уведомление клиенту."""
    try:
        data = json.loads(request.body.decode("utf-8"))
        
        name = data.get('client_name')
        phone = data.get('client_phone')
        hours = data.get('hours')
        hours_display = format_hours_text(hours)
        price = data.get('price')
        
        # Данные чека
        file_data = data.get('receipt_file_data') 
        file_name = data.get('receipt_file_name', 'receipt.jpg')
        receipt_num = data.get('receipt_number')
        
        if not all([name, phone, hours, price]):
             return JsonResponse({'success': False, 'error': 'Неполные данные'}, status=400)

        # 1. ВАЛИДАЦИЯ ЧЕКА
        receipt_status_text = ""
        if file_data:
             is_valid, msg = _validate_receipt(file_name, file_data)
             if not is_valid:
                 return JsonResponse({'success': False, 'error': f'Ошибка чека: {msg}'}, status=400)
             receipt_status_text = "Файл загружен (проверен)"
        elif receipt_num:
             receipt_status_text = f"Номер: {receipt_num}"
        else:
             return JsonResponse({'success': False, 'error': 'Прикрепите чек или введите его номер'}, status=400)
             
        # 2. Запись в Гугл Таблицу
        success = add_new_subscription_to_sheet({
            'name': name,
            'phone': phone,
            'hours': hours,
            'price': price
        })
        
        if not success:
            return JsonResponse({'success': False, 'error': 'Ошибка записи в таблицу'}, status=500)
            
        # 3. Уведомление в WhatsApp (ГРУППА АДМИНОВ)
# 3. Уведомление в WhatsApp (ГРУППА АДМИНОВ)
        msg_group = (
            "〰〰〰〰〰〰〰〰〰〰\n"
            "🎉 *ПРОДАН АБОНЕМЕНТ* (Сайт)\n\n"
            f"👤 Клиент: {name}\n"
            f"📱 Телефон: {phone}\n"
            f"📦 Пакет: {hours_display}\n" # <--- ИСПОЛЬЗУЕМ hours_display
            f"💰 Сумма: {price} ₸\n"
            f"🧾 Чек: {receipt_status_text}\n"
            "〰〰〰〰〰〰〰〰〰〰"
        )
        send_whatsapp_group(msg_group)

        # 4. Уведомление в WhatsApp (КЛИЕНТ)
        msg_client = (
            f"🎉 Здравствуйте, {name}!\n\n"
            f"Ваша заявка на покупку абонемента принята.\n\n"
            f"📦 Пакет: *{hours_display}*\n" # <--- ИСПОЛЬЗУЕМ hours_display
            f"💰 Сумма: {price} ₸\n"
            f"📅 Срок действия: 90 дней\n\n"
            "⏳ Мы проверяем вашу оплату. Часы будут зачислены на баланс в ближайшее время.\n\n"
            "С уважением, Zen Studio"
        )
        send_whatsapp_client(phone, msg_client)
        
        return JsonResponse({'success': True})

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

def add_new_subscription_to_sheet(data):
    """
    Добавляет/обновляет абонемент, учитывая ПЕРЕМЕЩЕНИЕ КОЛОНОК.
    """
    try:
        creds = Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        client = gspread.authorize(creds)
        sh = client.open_by_url(settings.CLIENTS_HISTORY_SPREADSHEET_URL)
        ws = sh.worksheet("Абонементы")
        
        # 1. Читаем заголовки, чтобы понять, где какая колонка
        headers = ws.row_values(1)
        header_map = {name.lower().strip(): i for i, name in enumerate(headers)}
        
        # Проверяем наличие ключевых колонок (названия должны совпадать с таблицей!)
        col_phone = header_map.get('телефон')
        col_balance = header_map.get('остаток') or header_map.get('баланс')
        col_expires = header_map.get('действует до')
        col_name = header_map.get('имя')
        
        if col_phone is None or col_balance is None:
            print("Ошибка: Не найдены обязательные колонки (Телефон, Остаток) в таблице")
            return False

        # Данные для записи
        phone_norm = normalize_phone_for_sheet(data['phone'])
        new_hours = float(data['hours'])
        name = data['name']
        
        now = datetime.datetime.now(ALMATY_TZ)
        valid_until = (now + datetime.timedelta(days=90)).strftime("%Y-%m-%d")
        
        # 2. Поиск клиента (по колонке телефона)
        try:
            # col_phone + 1, так как gspread нумерует с 1, а python с 0
            cell = ws.find(phone_norm, in_column=(col_phone + 1))
        except gspread.exceptions.CellNotFound:
            cell = None

        # --- ОБНОВЛЕНИЕ СУЩЕСТВУЮЩЕГО ---
        if cell:
            row_idx = cell.row
            
            # Читаем текущий баланс
            current_val = ws.cell(row_idx, col_balance + 1).value
            try:
                current_balance = float(str(current_val).replace(',', '.')) if current_val else 0.0
            except:
                current_balance = 0.0
            
            total_hours = current_balance + new_hours
            
            # Обновляем ячейки (используем найденные индексы)
            ws.update_cell(row_idx, col_balance + 1, total_hours)
            if col_expires is not None:
                ws.update_cell(row_idx, col_expires + 1, valid_until)
            if col_name is not None:
                ws.update_cell(row_idx, col_name + 1, name)
                
            return True

        # --- СОЗДАНИЕ НОВОГО ---
        else:
            # Создаем пустой список размером с количество заголовков
            new_row = [""] * len(headers)
            
            # Заполняем известные поля по индексам из header_map
            if col_phone is not None: new_row[col_phone] = phone_norm
            if col_balance is not None: new_row[col_balance] = new_hours
            if col_expires is not None: new_row[col_expires] = valid_until
            if col_name is not None: new_row[col_name] = name
            
            # Доп. поля (если есть такие колонки)
            if 'id' in header_map: new_row[header_map['id']] = f"{phone_norm}@c.us"
            if 'цена' in header_map: new_row[header_map['цена']] = data['price']
            if 'куплено' in header_map: new_row[header_map['куплено']] = new_hours
            if 'дата' in header_map: new_row[header_map['дата']] = now.strftime("%Y-%m-%d")

            ws.append_row(new_row)
            return True

    except Exception as e:
        print(f"Error adding/updating subscription: {e}")
        return False

# main/views.py
from django.db.models import Count, Q # Убедитесь, что это импортировано

class BookingPageView(TemplateView):
    template_name = 'main/booking_page.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Загружаем кабинеты с оптимизацией (select_related для филиала и старой локации)
        context['rooms'] = Room.objects.filter(
            is_active=True, show_in_booking=True
        ).select_related('branch', 'location').order_by('order')
        
        # Загружаем филиалы, в которых есть хотя бы один активный кабинет для брони
        context['branches'] = Branch.objects.filter(
            is_active=True
        ).annotate(
            active_rooms_count=Count('rooms', filter=Q(
                rooms__is_active=True, rooms__show_in_booking=True
            ))
        ).filter(active_rooms_count__gt=0).order_by('order')
        
        context['settings'] = SiteSettings.objects.first()
        context['user_is_logged_in'] = self.request.session.get('user_logged_in', False)
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
        
        # Получаем настройки и текущую страницу
        settings = SiteSettings.objects.first()
        page = self.object
        request = self.request

        # Передаем настройки в шаблон
        ctx["settings"] = settings

        # === SEO БЛОК ===
        
        # 1. Текстовые мета-теги (используем методы модели)
        ctx["seo_title"] = page.get_meta_title(settings)
        ctx["seo_description"] = page.get_meta_description(settings)
        ctx["seo_robots"] = page.get_meta_robots(settings)
        
        # 2. Каноническая ссылка
        # Если задана вручную - берем её, если нет - берем текущий URL
        ctx["seo_canonical"] = page.canonical_url or request.build_absolute_uri()
        
        # 3. Текущий URL (для og:url)
        ctx["seo_url"] = request.build_absolute_uri()

        # 4. OG Image (Картинка для соцсетей) - САМОЕ ВАЖНОЕ
        # Нам нужна абсолютная ссылка (начинается с https://...)
        seo_image_url = None
        
        if page.og_image:
            # Если есть картинка у страницы
            seo_image_url = page.og_image.url
        elif settings and settings.default_og_image:
            # Если нет, берем дефолтную из настроек
            seo_image_url = settings.default_og_image.url
            
        # Превращаем /media/img.jpg в https://site.kz/media/img.jpg
        if seo_image_url:
            ctx["seo_og_image"] = request.build_absolute_uri(seo_image_url)
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
    

"""
Добавить в views.py — Логика отмены подтверждённых бронирований.

Добавить в urls.py:
    path('api/cancel_booking_init/', views.cancel_booking_init, name='cancel_booking_init'),
    path('api/cancel_booking_confirm/', views.cancel_booking_confirm, name='cancel_booking_confirm'),
"""

# ========================================
# cancel_booking_init — Проверка возможности отмены + расчёт штрафа
# ========================================
@require_POST
@csrf_exempt
def cancel_booking_init(request):
    """
    Принимает данные брони, определяет штрафной тариф:
    < 3ч  → отмена невозможна
    3-12ч → штраф 25%
    > 12ч → бесплатная отмена
    """
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
        date_str = data.get('date')       # "2025-02-15"
        time_str = data.get('time')       # "14:00"
        duration = data.get('duration')   # 2 (часы, float)
        room_name = data.get('room')      # "Зелёный кабинет"
        price_str = data.get('price', '') # "15000" или "Абонемент"
        people = data.get('people', 1)

        if not date_str or not time_str:
            return JsonResponse({'success': False, 'error': 'Неполные данные'}, status=400)

        # Парсим дату+время брони
        try:
            booking_dt = datetime.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            booking_dt_aware = ALMATY_TZ.localize(booking_dt)
        except Exception:
            return JsonResponse({'success': False, 'error': 'Некорректная дата/время'}, status=400)

        now = timezone.now()
        hours_until = (booking_dt_aware - now).total_seconds() / 3600

        # --- ОПРЕДЕЛЯЕМ ТАРИФ ---
        if hours_until < 3:
            return JsonResponse({
                'success': True,
                'can_cancel': False,
                'reason': 'too_late',
                'hours_until': round(hours_until, 1),
                'message': 'Отмена невозможна менее чем за 3 часа до начала.'
            })

        # Определяем цену для штрафа
        is_subscription_booking = False
        base_price = 0

        try:
            base_price = float(str(price_str).replace(' ', '').replace(',', '.'))
        except (ValueError, TypeError):
            is_subscription_booking = True
            try:
                dur_hours = float(duration) if duration else 1
                ppl = int(people) if people else 1
                calculated = get_price_from_sheet(Decimal(str(dur_hours)), ppl)
                if calculated:
                    base_price = float(calculated)
                else:
                    base_price = 0
            except Exception as e:
                print(f"Price calculation error: {e}")
                base_price = 0

        penalty_amount = 0
        has_penalty = False

        if hours_until < 12:
            has_penalty = True
            penalty_amount = round(base_price * 0.25)

        dur_h = float(duration) if duration else 1

        return JsonResponse({
            'success': True,
            'can_cancel': True,
            'has_penalty': has_penalty,
            'penalty_amount': penalty_amount,
            'base_price': base_price,
            'is_subscription_booking': is_subscription_booking,
            'hours_until': round(hours_until, 1),
            'refund_hours': dur_h,
            'duration_display': format_hours_text(dur_h),
        })

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ========================================
# Вспомогательные функции для отмены
# ========================================

def _cancel_dates_match(date_a, date_b):
    """Гибкое сравнение дат — учитывает разные форматы Google Sheets."""
    if not date_a or not date_b:
        return False
    a = str(date_a).strip()
    b = str(date_b).strip()
    if a == b:
        return True
    def _parse(s):
        s = s.split()[0] if ' ' in s else s
        for fmt in ('%Y-%m-%d', '%d.%m.%Y', '%d/%m/%Y', '%m/%d/%Y'):
            try:
                return datetime.datetime.strptime(s, fmt).date()
            except:
                continue
        return None
    pa, pb = _parse(a), _parse(b)
    if pa and pb:
        return pa == pb
    return a in b or b in a


def _cancel_times_match(time_a, time_b):
    """Гибкое сравнение времени: '14:00' == '14:00:00'."""
    if not time_a or not time_b:
        return False
    a = str(time_a).strip()
    b = str(time_b).strip()
    if a == b:
        return True
    if a.startswith(b) or b.startswith(a):
        return True
    return False


def _find_sub_columns(ws_sub):
    """Находит колонки телефона и баланса в листе Абонементы."""
    try:
        headers = ws_sub.row_values(1)
        headers_lower = [h.lower().strip() for h in headers]
        phone_col = None
        balance_col = None
        if 'телефон' in headers_lower:
            phone_col = headers_lower.index('телефон') + 1
        if 'остаток' in headers_lower:
            balance_col = headers_lower.index('остаток') + 1
        elif 'баланс' in headers_lower:
            balance_col = headers_lower.index('баланс') + 1
        return phone_col, balance_col
    except:
        return None, None


# ========================================
# cancel_booking_confirm — Выполнение отмены
# ========================================
@require_POST
@csrf_exempt
def cancel_booking_confirm(request):
    """
    Подтверждение отмены (ОДНО подключение к Google Sheets):
    1. Пометка в Google Sheets (ПЕРВЫМ ДЕЛОМ)
    2. Штраф абонементом (если нужно)
    3. Возврат часов в абонемент
    4. Удаление из Google Calendar
    5. WhatsApp уведомления
    """
    import time as _time

    try:
        data = json.loads(request.body.decode("utf-8") or "{}")

        date_str = str(data.get('date', '')).strip()
        time_str = str(data.get('time', '')).strip()
        duration = float(data.get('duration', 1))
        room_name = str(data.get('room', '')).strip()
        phone = str(data.get('phone', '')).strip()
        client_name = str(data.get('client_name', '')).strip()
        has_penalty = data.get('has_penalty', False)
        penalty_amount = data.get('penalty_amount', 0)
        base_price = data.get('base_price', 0)

        receipt_file_data = data.get('receipt_file_data')
        receipt_file_name = data.get('receipt_file_name')
        receipt_number = data.get('receipt_number')
        payment_method = data.get('payment_method', 'single')

        if not all([date_str, time_str, room_name, phone]):
            return JsonResponse({'success': False, 'error': 'Неполные данные'}, status=400)

        phone_norm = normalize_phone_for_sheet(phone)
        refund_hours = duration
        refund_display = format_hours_text(refund_hours)

        print(f"[CANCEL] === Начало отмены ===")
        print(f"[CANCEL] phone={phone_norm}, date={date_str}, time={time_str}, room={room_name}")
        print(f"[CANCEL] has_penalty={has_penalty}, penalty={penalty_amount}, duration={duration}")

        # --- 0. ВАЛИДАЦИЯ ЧЕКА (если штраф переводом) ---
        receipt_info = ""
        if has_penalty and payment_method != 'subscription':
            if not receipt_file_data and not receipt_number:
                return JsonResponse({'success': False, 'error': 'Прикрепите чек или номер квитанции'}, status=400)
            if receipt_file_data:
                is_valid, message = _validate_receipt(receipt_file_name, receipt_file_data)
                if not is_valid:
                    return JsonResponse({'success': False, 'error': message}, status=400)
                receipt_info = f"Чек: {receipt_file_name}"
            else:
                receipt_info = f"Номер чека: {receipt_number}"

        # === ОДНО ПОДКЛЮЧЕНИЕ К GOOGLE SHEETS (с retry) ===
        sh = None
        for attempt in range(3):
            try:
                creds = Credentials.from_service_account_file(
                    SERVICE_ACCOUNT_FILE,
                    scopes=["https://www.googleapis.com/auth/spreadsheets"]
                )
                gc = gspread.authorize(creds)
                sh = gc.open_by_url(settings.CLIENTS_HISTORY_SPREADSHEET_URL)
                print(f"[CANCEL] Подключение OK (попытка {attempt+1})")
                break
            except Exception as e:
                print(f"[CANCEL] Подключение failed ({attempt+1}/3): {e}")
                if attempt < 2:
                    _time.sleep(2 * (attempt + 1))
                else:
                    return JsonResponse({'success': False, 'error': 'Ошибка подключения к таблице. Попробуйте через минуту.'}, status=500)

        # ============================================================
        # --- 1. ПОМЕЧАЕМ БРОНЬ КАК ОТМЕНЁННУЮ (самый важный шаг) ---
        # ============================================================
        sheet_updated = False
        try:
            ws_hist = sh.worksheet("История клиентов")
            all_data = ws_hist.get_all_values()
            headers = all_data[0] if all_data else []
            headers_lower = [h.lower().strip() for h in headers]

            hdr_map = {h: i for i, h in enumerate(headers_lower)}

            phone_col = hdr_map.get('телефон')
            date_col = hdr_map.get('дата')
            time_col = hdr_map.get('время')

            # Цена — безопасный поиск (None вместо 0 при отсутствии)
            price_col = None
            for pname in ['цена', 'стоимость', 'оплата']:
                if pname in hdr_map:
                    price_col = hdr_map[pname]
                    break

            # Статус / примечание
            status_col = None
            for h_name, h_i in hdr_map.items():
                if 'статус' in h_name or 'примечан' in h_name:
                    status_col = h_i
                    break

            print(f"[CANCEL] Колонки: phone={phone_col}, date={date_col}, time={time_col}, price={price_col}, status={status_col}")

            if phone_col is not None and date_col is not None:
                for row_i, row in enumerate(all_data[1:], start=2):
                    if date_col >= len(row) or phone_col >= len(row):
                        continue

                    row_phone_raw = row[phone_col]
                    row_date_raw = str(row[date_col]).strip()
                    row_time_raw = str(row[time_col]).strip() if time_col is not None and time_col < len(row) else ''

                    row_phone_norm = normalize_phone_for_sheet(row_phone_raw)

                    phone_ok = (phone_norm == row_phone_norm)
                    date_ok = _cancel_dates_match(date_str, row_date_raw)
                    time_ok = _cancel_times_match(time_str, row_time_raw)

                    if phone_ok and date_ok and time_ok:
                        print(f"[CANCEL] ✅ Найдена бронь в строке {row_i}")

                        if price_col is not None and price_col < len(row):
                            old_price = row[price_col]
                            if 'отменен' not in str(old_price).lower():
                                ws_hist.update_cell(row_i, price_col + 1, f"ОТМЕНЕНА ({old_price})")
                                sheet_updated = True
                                print(f"[CANCEL] Цена: ОТМЕНЕНА ({old_price})")
                            else:
                                sheet_updated = True
                                print(f"[CANCEL] Уже отменена ранее")

                        if status_col is not None:
                            penalty_note = f"Штраф: {penalty_amount}₸" if has_penalty else "Без штрафа"
                            ws_hist.update_cell(row_i, status_col + 1, f"Отменена. {penalty_note}. Возврат: {refund_display}")
                            sheet_updated = True

                        if not sheet_updated and price_col is not None:
                            ws_hist.update_cell(row_i, price_col + 1, "ОТМЕНЕНА")
                            sheet_updated = True

                        break

                if not sheet_updated:
                    print(f"[CANCEL] ⚠ Строка НЕ найдена! Строк: {len(all_data)-1}")
                    found = 0
                    for ri, r in enumerate(all_data[1:], start=2):
                        if phone_col < len(r) and normalize_phone_for_sheet(r[phone_col]) == phone_norm:
                            found += 1
                            rd = r[date_col] if date_col < len(r) else '?'
                            rt = r[time_col] if time_col is not None and time_col < len(r) else '?'
                            if found <= 5:
                                print(f"[CANCEL]   row {ri}: date='{rd}' (match={_cancel_dates_match(date_str, rd)}), time='{rt}' (match={_cancel_times_match(time_str, rt)})")
                    print(f"[CANCEL]   Всего строк с телефоном: {found}")
            else:
                print(f"[CANCEL] ❌ Колонки не найдены! headers={headers_lower}")

        except Exception as e:
            print(f"[CANCEL] ❌ Ошибка обновления: {e}")
            import traceback
            traceback.print_exc()

        # ============================================================
        # --- 2. ШТРАФ АБОНЕМЕНТОМ (если нужно) ---
        # ============================================================
        if has_penalty and payment_method == 'subscription':
            try:
                _time.sleep(1)
                ws_sub = sh.worksheet("Абонементы")
                sub_phone_col, sub_balance_col = _find_sub_columns(ws_sub)

                if sub_phone_col and sub_balance_col:
                    phones_list = ws_sub.col_values(sub_phone_col)
                    if phone_norm in phones_list:
                        sub_row = phones_list.index(phone_norm) + 1
                        balance_str = str(ws_sub.cell(sub_row, sub_balance_col).value or '0').replace(',', '.')
                        try:
                            current_balance = float(balance_str)
                        except:
                            current_balance = 0

                        if base_price > 0:
                            penalty_hours = round((penalty_amount / base_price) * duration, 2)
                        else:
                            penalty_hours = round(duration * 0.25, 2)

                        if current_balance < penalty_hours:
                            return JsonResponse({
                                'success': False,
                                'error': f'Недостаточно часов. Нужно: {format_hours_text(penalty_hours)}, баланс: {format_hours_text(current_balance)}'
                            }, status=400)

                        new_balance = current_balance - penalty_hours
                        ws_sub.update_cell(sub_row, sub_balance_col, new_balance)
                        receipt_info = f"Штраф абонементом: {format_hours_text(penalty_hours)}"
                        print(f"[CANCEL] Штраф списан: {penalty_hours}ч")
                    else:
                        return JsonResponse({'success': False, 'error': 'Абонемент не найден'}, status=400)
                else:
                    return JsonResponse({'success': False, 'error': 'Ошибка структуры таблицы абонементов'}, status=500)
            except Exception as e:
                print(f"[CANCEL] Ошибка штрафа: {e}")
                return JsonResponse({'success': False, 'error': f'Ошибка списания штрафа: {e}'}, status=500)

        # ============================================================
        # --- 3. ВОЗВРАТ ЧАСОВ В АБОНЕМЕНТ ---
        # ============================================================
        new_balance_display = ""
        try:
            _time.sleep(1)
            ws_sub = sh.worksheet("Абонементы")
            sub_phone_col, sub_balance_col = _find_sub_columns(ws_sub)

            if sub_phone_col and sub_balance_col:
                phones_list = ws_sub.col_values(sub_phone_col)
                if phone_norm in phones_list:
                    sub_row = phones_list.index(phone_norm) + 1
                    balance_str = str(ws_sub.cell(sub_row, sub_balance_col).value or '0').replace(',', '.')
                    try:
                        current_balance = float(balance_str)
                    except:
                        current_balance = 0

                    new_balance = current_balance + refund_hours
                    ws_sub.update_cell(sub_row, sub_balance_col, new_balance)
                    new_balance_display = format_hours_text(new_balance)
                    print(f"[CANCEL] Возврат: +{refund_hours}ч, баланс: {new_balance}")
                else:
                    success = add_new_subscription_to_sheet({
                        'name': client_name or 'Клиент',
                        'phone': phone,
                        'hours': refund_hours,
                        'price': 0
                    })
                    new_balance_display = format_hours_text(refund_hours) if success else "ошибка записи"
            else:
                new_balance_display = "ошибка"
        except Exception as e:
            print(f"[CANCEL] Ошибка возврата: {e}")
            new_balance_display = "ошибка"

        # ============================================================
        # --- 4. УДАЛЯЕМ ИЗ GOOGLE CALENDAR ---
        # ============================================================
        gcal_deleted = False
        try:
            booking_dt = datetime.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            booking_dt_aware = ALMATY_TZ.localize(booking_dt)

            room = Room.objects.filter(name=room_name).first()
            if not room:
                room = Room.objects.filter(name__icontains=room_name).first()
            if not room:
                room = Room.objects.filter(name__icontains=room_name.split()[0]).first()
            if room and room.google_calendar_id:
                service = get_calendar_service()
                calendar_id = str(room.google_calendar_id).strip().replace('"', '').replace("'", "").replace(' ', '')

                time_min = (booking_dt_aware - datetime.timedelta(minutes=5)).isoformat()
                time_max = (booking_dt_aware + datetime.timedelta(minutes=5)).isoformat()

                events_result = service.events().list(
                    calendarId=calendar_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy='startTime'
                ).execute()

                for event in events_result.get('items', []):
                    try:
                        event_start = event.get('start', {}).get('dateTime', '')
                        if event_start:
                            event_dt = datetime.datetime.fromisoformat(event_start.replace('Z', '+00:00'))
                            diff = abs((event_dt - booking_dt_aware).total_seconds())
                            if diff < 300:
                                service.events().delete(calendarId=calendar_id, eventId=event['id']).execute()
                                gcal_deleted = True
                                print(f"[CANCEL] Событие удалено: {event['id']}")
                                break
                    except Exception:
                        continue
        except Exception as e:
            print(f"[CANCEL] Ошибка календаря: {e}")

        # ============================================================
        # --- 5. WHATSAPP УВЕДОМЛЕНИЯ ---
        # ============================================================
        duration_display = format_hours_text(duration)
        penalty_text = f"💸 Штраф: {penalty_amount} ₸ ({receipt_info})" if has_penalty else "✅ Без штрафа"

        try:
            group_msg = (
                "〰〰〰〰〰〰〰〰〰〰\n"
                "🔄 ОТМЕНА БРОНИ (Сайт)\n\n"
                f"🏠 Кабинет: {room_name}\n"
                f"🗓 Дата: {date_str} | {time_str}\n"
                f"⏳ Длительность: {duration_display}\n"
                f"👤 Клиент: {client_name} ({phone})\n\n"
                f"{penalty_text}\n"
                f"🔁 Возврат в абонемент: {refund_display}\n"
                f"💰 Новый баланс: {new_balance_display}\n"
                "〰〰〰〰〰〰〰〰〰〰"
            )
            send_whatsapp_group(group_msg)

            client_msg = (
                "〰〰〰〰〰〰〰〰〰〰\n"
                "🔄 Ваша бронь отменена\n\n"
                f"🏠 Кабинет: {room_name}\n"
                f"🗓 Дата: {date_str} | {time_str}\n"
                f"⏳ Длительность: {duration_display}\n\n"
                f"{penalty_text}\n"
                f"🔁 Возвращено в абонемент: {refund_display}\n"
                f"💰 Ваш баланс: {new_balance_display}\n\n"
                "Если хотите забронировать другое время — выберите слот на сайте.\n"
                "〰〰〰〰〰〰〰〰〰〰"
            )
            send_whatsapp_client(phone, client_msg)
        except Exception as e:
            print(f"[CANCEL] Ошибка WhatsApp: {e}")

        print(f"[CANCEL] === Завершено. sheet={sheet_updated}, gcal={gcal_deleted} ===")

        return JsonResponse({
            'success': True,
            'gcal_deleted': gcal_deleted,
            'sheet_updated': sheet_updated,
            'refund_hours': refund_display,
            'new_balance': new_balance_display,
            'had_penalty': has_penalty,
            'penalty_amount': penalty_amount,
        })

    except Exception as e:
        print(f"[CANCEL] Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        return JsonResponse({'success': False, 'error': str(e)}, status=500)