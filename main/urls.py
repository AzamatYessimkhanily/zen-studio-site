# main/urls.py

from django.urls import path
from . import views

app_name = 'main'

urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
    path('page/<slug:slug>/', views.PageDetailView.as_view(), name='page_detail'),
    path('room/<int:pk>/', views.RoomDetailView.as_view(), name='room_detail'),
    path("api/webchat", views.webchat_proxy, name="webchat_proxy"),
]