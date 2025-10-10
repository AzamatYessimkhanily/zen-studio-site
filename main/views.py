# main/views.py

from django.views.generic import TemplateView, DetailView
from django.shortcuts import get_object_or_404
from .models import *
import json, requests
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt

BOT_API = "http://194.32.140.210:5001/api/webchat"

@csrf_exempt
def webchat_proxy(request):
    if request.method != "POST":
        return HttpResponseBadRequest("POST only")
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    try:
        r = requests.post(BOT_API, json=payload, timeout=15)
    except requests.RequestException as e:
        return JsonResponse({"reply": "Сервис временно недоступен."}, status=502)

    # Пробрасываем статус и JSON как есть
    try:
        data = r.json()
    except ValueError:
        return JsonResponse({"reply": "Пустой/невалидный ответ бэка."}, status=502)

    return JsonResponse(data, status=r.status_code, safe=False)
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
    
