# main/urls.py

from django.urls import path
from . import views

app_name = 'main'

urlpatterns = [
    # Стандартные страницы
    path('', views.IndexView.as_view(), name='index'),
    path('page/<slug:slug>/', views.PageDetailView.as_view(), name='page_detail'),
    path('room/<int:pk>/', views.RoomDetailView.as_view(), name='room_detail'),
    
    # === НОВАЯ СТРАНИЦА БРОНИРОВАНИЯ ===
    path('booking/', views.BookingPageView.as_view(), name='booking_page'),
    
    # === API ДЛЯ БРОНИРОВАНИЯ ===
    path('api/get_availability/', views.get_availability, name='get_availability'),
    path('api/get_price/', views.get_price, name='get_price'),
    path('api/hold_slot/', views.hold_slot, name='hold_slot'),
    path('api/cancel_hold/', views.cancel_hold, name='cancel_hold'),
    path('api/create_booking/', views.create_booking, name='create_booking'),
    path('api/find_available_rooms/', views.find_available_rooms, name='find_available_rooms'),
    path('api/check_balance/', views.check_balance_api, name='check_balance_api'),
    path('api/buy_subscription/', views.buy_subscription_api, name='buy_subscription_api'),
    # Старый API для чата (если он еще нужен)
    # path("api/webchat", views.webchat_proxy, name="webchat_proxy"),

    path('api/calculate_sub_benefit/', views.calculate_subscription_benefit, name='calculate_sub_benefit'),
    path('api/check_wa_exists/', views.check_whatsapp_existence, name='check_wa_exists'),
    
    path('api/send_auth_code/', views.api_send_auth_code, name='api_send_auth_code'),
    path('api/verify_auth_code/', views.api_verify_auth_code, name='api_verify_auth_code'),
    path('api/get_my_bookings/', views.get_my_bookings, name='get_my_bookings'),
    path('api/cancel_booking_init/', views.cancel_booking_init, name='cancel_booking_init'),
    path('api/cancel_booking_confirm/', views.cancel_booking_confirm, name='cancel_booking_confirm'),

    
]