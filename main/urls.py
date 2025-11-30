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
    
    # Старый API для чата (если он еще нужен)
    # path("api/webchat", views.webchat_proxy, name="webchat_proxy"),
]