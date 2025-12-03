# main/models.py

from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
import datetime
from django.utils import timezone
import uuid
import datetime
class SiteSettings(models.Model):
    """Общие настройки сайта"""

    site_name = models.CharField(
        "Название сайта",
        max_length=150,
        default="Zen Studio",
        help_text=(
            "Название бренда или компании. "
            "Показывается в заголовках страниц и в поисковой выдаче. "
            "Например: «Zen Studio»."
        ),
    )

    default_meta_title = models.CharField(
        "Заголовок по умолчанию (Title)",
        max_length=255,
        null=True,
        blank=True,
        help_text=(
            "Используется на страницах, где не указан свой SEO Title. "
            "Пример: «Zen Studio — аренда кабинетов в Алматы»."
        ),
    )

    default_meta_description = models.TextField(
        "Описание по умолчанию (Description)",
        null=True,
        blank=True,
        help_text=(
            "Общее описание сайта, которое поисковики и соцсети будут показывать "
            "на страницах без собственного описания. "
            "Пример: «Zen Studio — уютные кабинеты для консультаций, обучения и терапии в центре Алматы»."
        ),
    )

    default_meta_robots = models.CharField(
        "Видимость по умолчанию (Robots)",
        max_length=50,
        null=True,
        blank=True,
        help_text=(
            "Настройка для поисковиков: "
            "<b>index,follow</b> — разрешить индексировать и переходить по ссылкам. "
            "Обычно лучше оставить так. "
            "Если сайт в разработке — поставьте <b>noindex,nofollow</b>."
        ),
    )

    default_og_image = models.ImageField(
        "Основная картинка для ссылок (OG-image)",
        upload_to="seo/",
        null=True,
        blank=True,
        help_text=(
            "Эта картинка используется, когда у конкретной страницы нет своей. "
            "Отображается в WhatsApp, Telegram и соцсетях при отправке ссылки. "
            "Рекомендуемый размер: 1200×630 px (широкий баннер)."
        ),
    )
        # Методы для получения SEO данных
    def get_meta_title(self):
        return self.default_meta_title or f"{self.site_name} — аренда кабинетов в Алматы"

    def get_meta_description(self):
        return self.default_meta_description or "Описание по умолчанию сайта"

    def get_meta_robots(self):
        return self.default_meta_robots or "index, follow"

    def get_og_image(self):
        return self.default_og_image or None

    def __str__(self):
        return 'Настройки сайта'
    logo = models.ImageField(upload_to='logo/', verbose_name='Логотип', blank=True)
    phone = models.CharField(max_length=50, verbose_name='Телефон')
    email = models.EmailField(verbose_name='Email')
    address = models.TextField(verbose_name='Адрес')
    instagram_link = models.URLField(blank=True, verbose_name='Instagram')
    whatsapp_admin = models.URLField(blank=True, verbose_name='WhatsApp Администратор')
    whatsapp_bot = models.URLField(blank=True, verbose_name='WhatsApp ИИ-ассистент')
    reviews_link = models.URLField(blank=True, verbose_name='Ссылка на отзывы')

    class Meta:
        verbose_name = 'Настройки сайта'
        verbose_name_plural = 'Настройки сайта'
        
    def __str__(self):
        return 'Настройки сайта'

class HeroSlider(models.Model):
    """Слайды на главной странице"""
    site = models.ForeignKey(
        'SiteSettings',
        related_name='sliders',
        on_delete=models.CASCADE,
        verbose_name='Сайт',
        null=True,
        blank=True
    )
    image = models.ImageField(upload_to='slider/', verbose_name='Изображение')
    order = models.IntegerField(default=0, verbose_name='Порядок')

    class Meta:
        verbose_name = 'Слайд'
        verbose_name_plural = 'Слайды'
        ordering = ['order']

    def __str__(self):
        return f'Слайд {self.order}'


class Room(models.Model):
    # ... твои существующие поля ...
    name = models.CharField(max_length=255, verbose_name="Название")
    description = models.TextField(blank=True, verbose_name="Описание (старое)")
    short_description = models.TextField(blank=True, verbose_name="Короткое описание")
    area_sq_m = models.DecimalField(
        max_digits=6, decimal_places=1, null=True, blank=True,
        verbose_name="Площадь, м²"
    )
    main_image = models.ImageField(upload_to='rooms/', blank=True, null=True, verbose_name="Главное фото")
    order = models.PositiveIntegerField(default=0, verbose_name="Порядок")
    is_active = models.BooleanField(default=True, verbose_name="Активен")

    # НОВОЕ: простые текстовые блоки без подпунктов
    equipment_text = models.TextField(blank=True, verbose_name="Оснащение (текст)") 
    google_calendar_id = models.CharField(
            max_length=255,
            blank=True,
            verbose_name="ID Google Календаря",
            help_text="Скопируйте сюда ID календаря из настроек Google Calendar (вида xxx@group.calendar.google.com)"
        )   
    work_time_start = models.TimeField(
        "Начало рабочего дня",
        null=True, blank=True, # Разрешаем не указывать (будут дефолтные)
        default=datetime.time(7, 0), # Значение по умолчанию 07:00
        help_text="Время начала работы кабинета (ЧЧ:ММ). Если пусто, используется 07:00."
    )
    work_time_end = models.TimeField(
        "Конец рабочего дня",
        null=True, blank=True,
        default=datetime.time(23, 0), # Значение по умолчанию 23:00
        help_text="Время окончания работы кабинета (ЧЧ:ММ). Если пусто, используется 23:00."
    )
    class Meta:
        verbose_name = 'Кабинет'
        verbose_name_plural = 'Кабинеты'
        ordering = ['order']
    
    def __str__(self):
        return self.name

class RoomImage(models.Model):
    """Дополнительные изображения кабинета"""
    room = models.ForeignKey(Room, related_name='images', on_delete=models.CASCADE)
    image = models.ImageField(upload_to='rooms/gallery/', verbose_name='Изображение')
    order = models.IntegerField(default=0, verbose_name='Порядок')
    
    class Meta:
        verbose_name = 'Изображение кабинета'
        verbose_name_plural = 'Изображения кабинетов'
        ordering = ['order']
class RoomVideo(models.Model):
    """Видео кабинета (файл или внешняя ссылка)"""
    room = models.ForeignKey(Room, related_name='videos', on_delete=models.CASCADE, verbose_name="Кабинет")

    title = models.CharField(max_length=200, blank=True, verbose_name="Заголовок (необязательно)")
    order = models.IntegerField(default=0, verbose_name="Порядок")

    # ВАРИАНТ 1: загрузить файл
    video_file = models.FileField(
        upload_to='rooms/videos/',
        blank=True, null=True,
        verbose_name="Видео-файл (MP4)"
    )

    # ВАРИАНТ 2: указать ссылку (YouTube/Vimeo/другое)
    embed_url = models.URLField(
        blank=True, null=True,
        verbose_name="Ссылка на видео (YouTube/Vimeo)"
    )

    # Обложка (кадр) — опционально
    poster = models.ImageField(
        upload_to='rooms/videos/posters/',
        blank=True, null=True,
        verbose_name="Обложка (постер)"
    )

    class Meta:
        verbose_name = "Видео кабинета"
        verbose_name_plural = "Видео кабинетов"
        ordering = ["order", "id"]

    def __str__(self):
        return self.title or f"Видео #{self.pk}"

    # Приводим youtube/vimeo url к embed-формату
    def get_embed_src(self):
        if not self.embed_url:
            return None
        url = self.embed_url
        # YouTube
        if "youtube.com/watch" in url:
            # https://www.youtube.com/watch?v=ID -> /embed/ID
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(url).query)
            vid = (q.get("v") or [""])[0]
            return f"https://www.youtube.com/embed/{vid}" if vid else url
        if "youtu.be/" in url:
            vid = url.split("youtu.be/")[-1].split("?")[0]
            return f"https://www.youtube.com/embed/{vid}"
        # Vimeo
        if "vimeo.com/" in url and "player.vimeo.com" not in url:
            vid = url.split("vimeo.com/")[-1].split("?")[0]
            return f"https://player.vimeo.com/video/{vid}"
        return url

class RoomInfoBlock(models.Model):
    class Category(models.TextChoices):
        DESCRIPTION = "description", "Описание кабинета"
        # (equipment/location больше не используем для подпунктов)

    room = models.ForeignKey('Room', related_name='info_blocks', on_delete=models.CASCADE, verbose_name="Кабинет")
    category = models.CharField(max_length=20, choices=Category.choices, verbose_name="Раздел")
    title = models.CharField(max_length=120, verbose_name="Подраздел / Заголовок")
    value = models.TextField(blank=True, verbose_name="Значение / Описание")
    icon_class = models.CharField(max_length=100, blank=True, verbose_name="Иконка (Font Awesome)")
    order = models.IntegerField(default=0, verbose_name="Порядок")

    class Meta:
        verbose_name = "Инфо-пункт кабинета"
        verbose_name_plural = "Инфо-пункты кабинета"
        ordering = ["category", "order", "id"]

    def __str__(self):
        return f"{self.get_category_display()}: {self.title}"



class RoomLocation(models.Model):
    room = models.OneToOneField('Room', related_name='location', on_delete=models.CASCADE, verbose_name="Кабинет")
    address = models.CharField(max_length=255, verbose_name="Адрес", blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, verbose_name="Широта (lat)")
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, verbose_name="Долгота (lng)")
    map_link = models.URLField(blank=True, verbose_name="Ссылка на карту (Яндекс/Google/2GIS)")
    route_image = models.ImageField(
        upload_to="rooms/routes/",
        blank=True, null=True,
        verbose_name="Схема проезда (изображение)"
    )
    class Meta:
        verbose_name = "Локация кабинета"
        verbose_name_plural = "Локация кабинета"

    def __str__(self):
        return self.address or f"Локация для {self.room}"
    
# main/models.py
from django.db import models

class Tariff(models.Model):
    site = models.ForeignKey(
        'SiteSettings',
        related_name='tariffs',
        on_delete=models.CASCADE,
        verbose_name='Сайт',
        null=True,
        blank=True
    )

    class Unit(models.TextChoices):
        HOUR1 = "ЧАС", "ЧАС"        # 1 ЧАС
        HOUR2 = "ЧАСА", "ЧАСА"      # 2–4 ЧАСА
        HOUR5 = "ЧАСОВ", "ЧАСОВ"    # 5+ ЧАСОВ
    hours = models.PositiveIntegerField("Количество часов")
    unit = models.CharField("Ед.измерения", max_length=10, choices=Unit.choices, default=Unit.HOUR1)
    price = models.PositiveIntegerField("Цена, тг")
    persons_text = models.CharField("Кол-во человек (текст)", max_length=50, blank=True)
    order = models.PositiveIntegerField("Порядок", default=0)
    is_active = models.BooleanField("Активен", default=True)
    class Meta:
        verbose_name = "Тариф"
        verbose_name_plural = "Тарифы"
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.hours} {self.unit} — {self.price} тг"


class Page(models.Model):
    title = models.CharField(max_length=255, verbose_name="Заголовок")
    slug = models.SlugField(unique=True)
    content = models.TextField(blank=True, verbose_name="Короткое описание/вступление")
    order = models.PositiveIntegerField(default=0, db_index=True, verbose_name="Порядок")
    is_active = models.BooleanField(default=True)

    # SEO (если нужно)
    meta_title = models.CharField(
        "Заголовок для поисковиков (Title)",
        max_length=255, null=True, blank=True,
        help_text=(
            "Этот текст показывается в заголовке вкладки браузера и в Google при поиске. "
            "Пример: «Аренда кабинетов Zen Studio в Алматы». "
            "Если оставить пустым — подставится автоматически из названия страницы."
        ),
    )
    meta_description = models.TextField(
        "Описание страницы (Description)",
        null=True, blank=True,
        help_text=(
            "Короткий абзац, который появляется под заголовком в Google. "
            "Опиши в 1-2 предложениях, что человек найдёт на странице. "
            "Например: «Уютные кабинеты для консультаций, обучения и терапии в центре Алматы»."
        ),
    )
    meta_robots = models.CharField(
        "Видимость для поисковиков (Robots)",
        max_length=50, null=True, blank=True,
        help_text=(
            "Указывает, можно ли индексировать страницу. "
            "Оставь «index,follow», чтобы страница показывалась в Google. "
            "Если временно не нужно — поставь «noindex,nofollow»."
        ),
    )
    og_image = models.ImageField(
        "Картинка для предпросмотра (OG-image)",
        upload_to="seo/", blank=True, null=True,
        help_text=(
            "Эта картинка отображается, когда вы делитесь ссылкой в WhatsApp, Telegram, Instagram и т.п. "
            "Рекомендуемый размер 1200×630 пикселей (широкий баннер)."
        ),
    )
    canonical_url = models.URLField(
        "Каноническая ссылка (Canonical URL)",
        null=True, blank=True,
        help_text=(
            "Используется, если одна страница дублирует другую. "
            "Обычно можно не заполнять — сайт сам подставит правильный адрес."
        ),
    )
    class Meta:
        ordering = ("order", "id")
        verbose_name = "Страница"
        verbose_name_plural = "Страницы"

    def __str__(self):
        return self.title


    def get_meta_title(self, settings=None):
        """
        Приоритет:
        1) meta_title у самой страницы
        2) default_meta_title из настроек сайта
        3) "<page.title> — <settings.site_name|Zen Studio>"
        """
        if self.meta_title:
            return self.meta_title
        if settings and settings.default_meta_title:
            return settings.default_meta_title
        site_name = (settings.site_name if settings and settings.site_name else "Zen Studio")
        return f"{self.title} — {site_name}"

    def get_meta_description(self, settings=None):
        """
        1) meta_description у страницы
        2) дефолт из настроек
        3) пусто
        """
        if self.meta_description:
            return self.meta_description
        if settings and settings.default_meta_description:
            return settings.default_meta_description
        return ""

    def get_meta_robots(self, settings=None):
        """
        1) meta_robots у страницы
        2) дефолт из настроек
        3) 'index,follow'
        """
        if self.meta_robots:
            return self.meta_robots
        if settings and settings.default_meta_robots:
            return settings.default_meta_robots
        return "index,follow"

    def get_og_image_url(self, request, settings=None):
        """
        Абсолютный URL для og:image (критично для WhatsApp/Telegram/Facebook)
        """
        img = self.og_image or (settings.default_og_image if settings else None)
        return request.build_absolute_uri(img.url) if img else None


class PageContentBlock(models.Model):
    class BlockType(models.TextChoices):
        IMG_TEXT = "img_text", _("Картинка + текст")
        CAROUSEL = "carousel", _("Карусель изображений")
        TEXT = "text", _("Только текст")

    class Align(models.TextChoices):
        LEFT = "left", _("Картинка слева, текст справа")
        RIGHT = "right", _("Картинка справа, текст слева")
        FULL = "full", _("Широкий блок")

    page = models.ForeignKey(Page, related_name="contents", on_delete=models.CASCADE)
    block_type = models.CharField(max_length=32, choices=BlockType.choices, default=BlockType.IMG_TEXT, verbose_name="Тип блока")
    title = models.CharField(max_length=255, blank=True, verbose_name="Заголовок блока / Название карусели")
    text = models.TextField(blank=True, verbose_name="Текст")
    image = models.ImageField(upload_to="pages/blocks/", blank=True, null=True, verbose_name="Изображение (для IMG+TEXT)")
    align = models.CharField(max_length=16, choices=Align.choices, default=Align.LEFT, verbose_name="Раскладка")
    order = models.PositiveIntegerField(default=0, db_index=True, verbose_name="Порядок")
    is_active = models.BooleanField(default=True, verbose_name="Активен")

    class Meta:
        ordering = ("order", "id")
        verbose_name = "Контент-блок"
        verbose_name_plural = "Контент-блоки"

    def __str__(self):
        return f"{self.get_block_type_display()} — {self.title or 'без названия'}"


class ContentImage(models.Model):
    """Изображения для блока-карусели"""
    block = models.ForeignKey(PageContentBlock, related_name="images", on_delete=models.CASCADE)
    image = models.ImageField(upload_to="pages/blocks/carousel/", verbose_name="Файл")
    caption = models.CharField(max_length=255, blank=True, verbose_name="Подпись")
    order = models.PositiveIntegerField(default=0, db_index=True, verbose_name="Порядок")

    class Meta:
        ordering = ("order", "id")
        verbose_name = "Изображение в карусели"
        verbose_name_plural = "Изображения в карусели"

    def __str__(self):
        return self.caption or f"image #{self.pk}"
        


class PendingBooking(models.Model):
    """Модель для временного резервирования слота."""
    room = models.ForeignKey(Room, on_delete=models.CASCADE, verbose_name="Кабинет")
    start_time = models.DateTimeField("Время начала")
    end_time = models.DateTimeField("Время окончания")
    created_at = models.DateTimeField("Время создания", auto_now_add=True)
    expires_at = models.DateTimeField("Истекает в")
    # Уникальный ID для этой попытки, чтобы клиент мог ее отменить
    hold_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    google_event_id = models.CharField(max_length=255, blank=True, null=True, verbose_name="ID события в Google")
    
    # === ДОБАВЛЯЕМ ЭТИ ПОЛЯ ===
    client_name = models.CharField(max_length=255, blank=True, null=True, verbose_name="Имя клиента")
    client_phone = models.CharField(max_length=50, blank=True, null=True, verbose_name="Телефон клиента")
    # ==========================

    class Meta:
        verbose_name = "Временный резерв (15 мин)"
        verbose_name_plural = "Временные резервы (15 мин)"
        indexes = [
            models.Index(fields=['room', 'start_time', 'end_time']),
            models.Index(fields=['expires_at']),
        ]

    def is_expired(self):
        """Проверяет, истекло ли время резерва."""
        return timezone.now() >= self.expires_at

    def save(self, *args, **kwargs):
        # Автоматически устанавливаем время истечения = +15 минут от сейчас
        if not self.pk: # Только при создании
            # Используем timezone.now() для aware datetime
            self.expires_at = timezone.now() + datetime.timedelta(minutes=1)
        super().save(*args, **kwargs)

    def __str__(self):
        local_start = timezone.localtime(self.start_time)
        local_expires = timezone.localtime(self.expires_at)
        return f"Резерв {self.room.name} с {local_start.strftime('%H:%M %d.%m')} до {local_expires.strftime('%H:%M:%S')}"