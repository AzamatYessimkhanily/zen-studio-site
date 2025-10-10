# main/sitemaps.py
from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from .models import Page

class PageSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.7

    def items(self):
        return Page.objects.filter(is_active=True)

    def location(self, obj):
        return reverse("main:page_detail", kwargs={"slug": obj.slug})