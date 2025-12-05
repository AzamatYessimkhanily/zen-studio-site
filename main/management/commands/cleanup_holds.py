from django.core.management.base import BaseCommand
from django.utils import timezone
from main.views import cleanup_expired_holds
import datetime

class Command(BaseCommand):
    help = 'Удаляет просроченные брони и отправляет уведомления'

    def handle(self, *args, **kwargs):
        self.stdout.write(f"[{datetime.datetime.now()}] Запуск очистки броней...")
        try:
            # Вызываем ту самую функцию, которую мы написали в views.py
            cleanup_expired_holds()
            self.stdout.write(self.style.SUCCESS(f"[{datetime.datetime.now()}] Очистка завершена успешно."))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Ошибка при очистке: {e}"))