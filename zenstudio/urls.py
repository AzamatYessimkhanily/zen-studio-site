# zenstudio/urls.py
from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.sitemaps.views import sitemap
from django.views.generic import TemplateView
from django.views.static import serve as static_serve

from main.sitemaps import PageSitemap

sitemaps = {"pages": PageSitemap}

urlpatterns = [
    path('admin/', admin.site.urls),
    path('_nested_admin/', include('nested_admin.urls')),
    path('', include('main.urls')),
    path("sitemap.xml", sitemap, {"sitemaps": sitemaps}, name="django.contrib.sitemaps.views.sitemap"),
    path("robots.txt", TemplateView.as_view(template_name="robots.txt", content_type="text/plain")),
]

if settings.DEBUG:
    # 1) favicon.ico без редиректа (Safari доволен)
    # STATICFILES_DIRS[0] = BASE_DIR / "static"
    urlpatterns = [
        re_path(
            r"^favicon\.ico$",
            static_serve,
            {"document_root": settings.STATICFILES_DIRS[0], "path": "img/favicon.ico"},
            name="favicon"
        ),
        # Дополнительно (не обязательно) — если хочется и это отдавать без редиректа:
        re_path(
            r"^apple-touch-icon\.png$",
            static_serve,
            {"document_root": settings.STATICFILES_DIRS[0], "path": "img/apple-touch-icon.png"},
            name="apple_touch_icon"
        ),
        re_path(
            r"^site\.webmanifest$",
            static_serve,
            {"document_root": settings.STATICFILES_DIRS[0], "path": "img/site.webmanifest"},
            name="manifest"
        ),
        re_path(
            r"^browserconfig\.xml$",
            static_serve,
            {"document_root": settings.STATICFILES_DIRS[0], "path": "img/browserconfig.xml"},
            name="browserconfig"
        ),
    ] + urlpatterns

    # стандартная подача media/static в DEV
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)