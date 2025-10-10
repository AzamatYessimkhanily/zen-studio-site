# main/context_processors.py (дополни)
from .models import *
from django.conf import settings

def pages_menu(request):
    return {
        "pages_menu": Page.objects.filter(is_active=True).order_by("order")[:10],
        "settings": SiteSettings.objects.first(),
    }

def seo_settings(request):
    site_settings = SiteSettings.objects.first()  # Получаем первый объект настроек сайта
    return {
        'seo_site_name': site_settings.site_name if site_settings else 'Zen Studio',
        'seo_description': site_settings.default_meta_description if site_settings and site_settings.default_meta_description else 'Почасовая аренда кабинетов в Алматы. Уютные кабинеты для консультаций, терапии и обучения.',
        'seo_title': site_settings.default_meta_title if site_settings and site_settings.default_meta_title else 'Zen Studio - Почасовая аренда кабинетов в Алматы',
        'seo_robots': site_settings.default_meta_robots if site_settings and site_settings.default_meta_robots else 'index, follow',
        'seo_og_image': site_settings.default_og_image if site_settings and site_settings.default_og_image else None,
    }