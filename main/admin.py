from django.contrib import admin
from django.utils.html import format_html
from .models import *
from django.utils.safestring import mark_safe
from django import forms
import nested_admin
# Подсказки для Font Awesome (fa-...); можно дополнять
ICON_SUGGESTIONS = [
    # --- Мебель и Пространство ---
    ("fa-chair", "Стул"),
    ("fa-couch", "Диван/зона отдыха"),
    ("fa-bed", "Кушетка/кровать"),
    ("fa-table", "Стол"),
    ("fa-chair-office", "Офисное кресло (Pro)"),
    ("fa-person-shelter", "Ширма/зона приватности"),
    ("fa-box-archive", "Шкаф/хранение"),
    ("fa-boxes-stacked", "Склад/ящики"),
    ("fa-ruler-combined", "Площадь помещения"),
    ("fa-maximize", "Полезная площадь"),
    ("fa-image", "Фотозона"),
    ("fa-seedling", "Растения/зеленая зона"),

    # --- Техника и Оборудование ---
    ("fa-tv", "Экран/ТВ"),
    ("fa-display", "Монитор"),
    ("fa-projector", "Проектор (Pro)"),
    ("fa-chalkboard", "Маркерная доска/флипчарт"),
    ("fa-video", "Камера/видеозапись"),
    ("fa-microphone", "Микрофон"),
    ("fa-headphones", "Наушники"),
    ("fa-volume-high", "Акустика/колонки"),
    ("fa-podcast", "Оборудование для подкастов"),
    ("fa-ring-light", "Кольцевая лампа (Pro)"),
    ("fa-print", "Принтер/МФУ"),
    ("fa-scanner", "Сканер (Pro)"),
    ("fa-laptop", "Рабочее место с ноутбуком"),
    ("fa-vr-cardboard", "VR/AR оборудование"),

    # --- Коммуникации и Электричество ---
    ("fa-wifi", "Wi-Fi"),
    ("fa-ethernet", "LAN/проводной интернет"),
    ("fa-plug", "Розетки"),
    ("fa-power-off", "Бесперебойное питание/ИБП"),
    ("fa-bolt", "Стабильное электричество"),

    # --- Климат и Освещение ---
    ("fa-lightbulb", "Освещение"),
    ("fa-sun", "Естественный свет"),
    ("fa-fan", "Вентиляция/вентилятор"),
    ("fa-snowflake", "Кондиционер"),
    
    # --- Удобства и Аменити ---
    ("fa-toilet", "Санузел"),
    ("fa-mug-hot", "Кухня/чай/кофе"),
    ("fa-water", "Вода/кулер"),
    ("fa-utensils", "Столовые приборы"),
    ("fa-refrigerator", "Холодильник (Pro)"),
    ("fa-shower", "Душевая кабина"),
    ("fa-ban-smoking", "Зона для некурящих"),
    ("fa-dog", "Можно с животными (pet-friendly)"),
    ("fa-child", "Детская комната/зона"),

    # --- Вместимость и Люди ---
    ("fa-users", "Вместимость"),
    ("fa-person", "Индивидуальная работа"),
    ("fa-people-group", "Работа в группе"),
    ("fa-users-gear", "Коворкинг/командная работа"),

    # --- Локация и Доступность ---
    ("fa-building", "Здание/бизнес-центр"),
    ("fa-elevator", "Лифт"),
    ("fa-stairs", "Лестница/этаж"),
    ("fa-map-location-dot", "Местоположение"),
    ("fa-location-dot", "Точка на карте"),
    ("fa-route", "Схема проезда"),
    ("fa-map-pin", "Пин/метка"),
    ("fa-parking", "Парковка"),
    ("fa-car", "Личный транспорт"),
    ("fa-bus", "Автобусная остановка"),
    ("fa-train-subway", "Метро/электричка рядом"),
    ("fa-bicycle", "Велопарковка"),
    ("fa-person-walking-luggage", "Удобно для приезжих"),
    ("fa-wheelchair-move", "Доступ для инвалидных колясок"),
    ("fa-ramp-loading", "Пандус (Pro)"),

    # --- Безопасность и Сервис ---
    ("fa-shield-halved", "Охрана/пропускной пункт"),
    ("fa-key", "Доступ/ключи"),
    ("fa-lock", "Кабинет запирается"),
    ("fa-fire-extinguisher", "Пожарная безопасность"),
    ("fa-bell", "Звонок/оповещение"),
    ("fa-concierge-bell", "Ресепшн/Консьерж-сервис"),
    ("fa-user-tie", "Администратор"),
    ("fa-broom", "Клининг/уборка"),
    ("fa-box-open", "Получение посылок"),
    ("fa-envelope", "Почтовый адрес"),

    # --- Бизнес, Работа и Процессы ---
    ("fa-briefcase", "Для деловых встреч"),
    ("fa-handshake", "Переговорная комната"),
    ("fa-chalkboard-user", "Для презентаций и тренингов"),
    ("fa-comments", "Зона для созвонов"),
    ("fa-brain", "Пространство для брейншторма"),
    ("fa-camera", "Для фотосессий/съемок"),
    ("fa-file-signature", "Место для подписания документов"),
    ("fa-paperclip", "Канцелярские принадлежности"),
    ("fa-clipboard", "Планшет/клипборд"),
    ("fa-pen", "Письменные принадлежности"),
    ("fa-clock", "Режим работы/часы"),
    ("fa-calendar", "График/календарь брони"),
    ("fa-credit-card", "Оплата картой"),
    ("fa-money-bill-wave", "Наличный расчет"),
]


class DatalistTextInput(forms.TextInput):
    def __init__(self, datalist_id: str, options: list[tuple[str, str]], attrs=None):
        self.datalist_id = datalist_id
        self.options = options
        attrs = attrs or {}
        attrs["list"] = datalist_id
        super().__init__(attrs=attrs)

    def render(self, name, value, attrs=None, renderer=None):
        input_html = super().render(name, value, attrs, renderer)
        options_html = "".join(f'<option value="{val}">{label}</option>' for val, label in self.options)
        datalist_html = f'<datalist id="{self.datalist_id}">{options_html}</datalist>'
        return mark_safe(input_html + datalist_html)
    
class RoomInfoBlockForm(forms.ModelForm):
    icon_class = forms.CharField(
        label="Иконка (Font Awesome)",
        required=False,
        widget=DatalistTextInput("fa-icons", ICON_SUGGESTIONS, attrs={"placeholder": "напр. fa-chair"}),
        help_text="Выберите из списка или введите свой класс (fa-...).",
    )
    class Meta:
        model = RoomInfoBlock
        fields = "__all__"



# --- Inline для слайдов ---
class HeroSliderInline(admin.TabularInline):
    model = HeroSlider
    extra = 1
    fields = ['image', 'order', 'image_preview']
    readonly_fields = ['image_preview']

    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="width: 120px; height: 70px; object-fit: cover; border-radius: 6px;"/>',
                obj.image.url
            )
        return "-"
    image_preview.short_description = "Превью"
# --- Inline: тарифы ---
class TariffInline(admin.TabularInline):
    model = Tariff
    extra = 6  # сразу много строк для удобства
    fields = ["hours", "unit", "price", "persons_text", "order", "is_active"] # <--- Добавил сюда# --- Настройки сайта (слайдер внутри) ---




# 1. Создаем Inline (вставь где-то рядом с TariffInline)
class FooterLinkInline(admin.TabularInline):
    model = FooterLink
    extra = 1
    fields = ("title", "url", "icon_class", "open_in_new_tab", "order")
    
    # Подключаем твой виджет для выбора иконок
    formfield_overrides = {
        models.CharField: {'widget': DatalistTextInput("fa-icons-footer", ICON_SUGGESTIONS)},
    }

# 2. Обновляем SiteSettingsAdmin (добавляем FooterLinkInline в список)
@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    list_display = ["__str__", "phone", "email"]
    
    # === ДОБАВИЛ FooterLinkInline СЮДА ===
    inlines = [HeroSliderInline, TariffInline, FooterLinkInline]
    # =====================================
    
    fieldsets = (
        ("Базовые", {"fields": ("site_name","logo","phone","email","address")}),
        ("Линки (фиксированные)", {"fields": ("instagram_link","whatsapp_admin","whatsapp_bot","reviews_link")}),
        ("SEO по умолчанию", {
            "fields": ("default_meta_title","default_meta_description","default_meta_robots","default_og_image")
        }),
    )
    def has_add_permission(self, request):
        return self.model.objects.count() < 1

class RoomImageInline(admin.TabularInline):
    model = RoomImage
    extra = 1
class BaseInfoInline(admin.TabularInline):
    model = RoomInfoBlock
    extra = 0
    fields = ("title", "value", "icon_class", "order")
    ordering = ("order", "id")
    verbose_name_plural = ""

    # фильтруем строки по нужной категории
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(category=self.category_value)

    # фиксируем выбор категории
    def save_new_objects(self, formset, commit=True):
        # Django <5 нет; используем formset.save_new instead — ниже get_formset
        return super().save_new_objects(formset, commit)

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        orig_save_new = formset.save_new
        category_value = self.category_value

        def save_new(form, commit=True):
            instance = orig_save_new(form, commit=False)
            instance.category = category_value
            if commit:
                instance.save()
            return instance

        formset.save_new = save_new
        return formset


class DescriptionInline(admin.TabularInline):
    model = RoomInfoBlock
    form = RoomInfoBlockForm
    extra = 0
    fields = ("title", "value", "icon_class", "order")
    ordering = ("order", "id")
    verbose_name_plural = "ОПИСАНИЕ КАБИНЕТА — подпункты"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(category=RoomInfoBlock.Category.DESCRIPTION)

    def get_formset(self, request, obj=None, **kwargs):
        FormSet = super().get_formset(request, obj, **kwargs)
        category_value = RoomInfoBlock.Category.DESCRIPTION

        class CategorizedFormSet(FormSet):
            def save_new(self, form, commit=True):
                instance = super().save_new(form, commit=False)
                instance.category = category_value
                if commit:
                    instance.save()
                return instance

        return CategorizedFormSet

class RoomLocationInline(admin.StackedInline):
    model = RoomLocation
    can_delete = True
    extra = 0
    max_num = 1
    verbose_name_plural = "Локация кабинета (карта)"
    fields = ("address", "latitude", "longitude", "map_link", "route_image")

    def get_extra(self, request, obj=None, **kwargs):
        return 1 if obj and not hasattr(obj, "location") else 0

class RoomImageInline(admin.TabularInline):
    model = RoomImage
    extra = 1
class RoomVideoInline(admin.TabularInline):
    model = RoomVideo
    extra = 1
    fields = ("title", "order", "video_file", "embed_url", "poster",)
    readonly_fields = ()

@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ['name', 'branch', 'area_sq_m', 'capacity_display', 'google_calendar_id', 'order', 'is_active', 'show_in_booking', 'image_preview']   
    list_editable = ['order', 'is_active', 'show_in_booking'] # Добавил возможность менять галочку прямо из списка
    list_filter = ['branch', 'is_active', 'show_in_booking']
    search_fields = ['name', 'google_calendar_id','description']

    fieldsets = (
                (None, {
                    'fields': (
                        'name', 'branch', 'google_calendar_id',
                        ('work_time_start', 'work_time_end'),
                        
                        # === ДОБАВИЛ НОВЫЕ ГАЛОЧКИ СЮДА ===
                        ('show_in_booking', 'hide_phone_in_calendar'),
                        # ==================================
                        
                        'short_description', 'main_image',
                        'is_active', 'order'
                    )
                }),
            ("Характеристики", {
                'fields': ('area_sq_m', ('capacity_min', 'capacity_max'))
            }),
            ("Контент (без подпунктов)", {
                'fields': ('equipment_text',)
            }),
        )

    inlines = [DescriptionInline, RoomLocationInline, RoomImageInline, RoomVideoInline]

    def image_preview(self, obj):
        if obj.main_image:
            return format_html('<img src="{}" style="width: 100px; height: 60px; object-fit: cover;"/>', obj.main_image.url)
        return '-'
    image_preview.short_description = 'Превью'

    def capacity_display(self, obj):
        return obj.capacity_display or '-'
    capacity_display.short_description = 'Вместимость'





class ContentImageInline(nested_admin.NestedTabularInline):
    model = ContentImage
    extra = 1
    fields = ("image", "caption", "order")
    sortable_field_name = "order"


class PageContentBlockInline(nested_admin.NestedStackedInline):
    model = PageContentBlock
    extra = 0
    # поля для разных типов
    fieldsets = (
        (None, {
            "fields": (
                ("block_type", "is_active"),
                ("title", "order"),
                ("align",),
                "text",
                "image",  # используется только для IMG+TEXT
            )
        }),
    )
    inlines = [ContentImageInline]  # ← вот они, фото внутри блока
    sortable_field_name = "order"
    
# 1. Инлайн для кнопок (используем NestedTabularInline!)
class PageLinkInline(nested_admin.NestedTabularInline):
    model = PageLink
    extra = 1
    fields = ("text", "url", "style", "icon_class", "order")
    sortable_field_name = "order"  # <--- Обязательно для сортировки
    
    # Подсказки для иконок
    formfield_overrides = {
        models.CharField: {'widget': DatalistTextInput("fa-icons-links", ICON_SUGGESTIONS)},
    }

@admin.register(Page)
class PageAdmin(nested_admin.NestedModelAdmin):
    list_display = ("title", "slug", "order", "is_active")
    list_editable = ("order", "is_active")
    prepopulated_fields = {"slug": ("title",)}
    
    # Добавляем PageLinkInline в список
    inlines = [PageContentBlockInline, PageLinkInline] 
    
    search_fields = ("title", "slug")
    fieldsets = (
        ("Страница", {"fields": ("title","slug","content","is_active")}),
        ("SEO", {
            "fields": ("meta_title","meta_description","meta_robots","og_image","canonical_url"),
            "classes": ("collapse",)
        }),
    )

@admin.register(PendingBooking)
class PendingBookingAdmin(admin.ModelAdmin):
    list_display = ('room', 'client_name', 'client_phone', 'price', 'start_time_local', 'end_time_local', 'expires_at_local', 'is_confirmed', 'is_expired_display')
    list_filter = ('room', 'is_confirmed', 'created_at')
    readonly_fields = ('created_at', 'expires_at', 'hold_id', 'start_time', 'end_time')
    search_fields = ('room__name', 'hold_id', 'client_name', 'client_phone')
    actions = ['confirm_payment', 'delete_expired']

    @admin.display(description='Начало', ordering='start_time')
    def start_time_local(self, obj):
        return timezone.localtime(obj.start_time).strftime('%d.%m %H:%M') if obj.start_time else '-'

    @admin.display(description='Конец', ordering='end_time')
    def end_time_local(self, obj):
        return timezone.localtime(obj.end_time).strftime('%d.%m %H:%M') if obj.end_time else '-'

    @admin.display(description='Истекает (локал.)', ordering='expires_at')
    def expires_at_local(self, obj):
        if obj.is_confirmed:
            return '✅ Подтверждено'
        return timezone.localtime(obj.expires_at).strftime('%H:%M:%S') if obj.expires_at else '-'

    @admin.display(description='Истек?', boolean=True)
    def is_expired_display(self, obj):
        return obj.is_expired()

    @admin.action(description='✅ Подтвердить оплату (зафиксировать бронь)')
    def confirm_payment(self, request, queryset):
        """Админ-действие: подтверждает оплату, запускает полную логику бронирования."""
        from .views import admin_confirm_booking  # Импорт функции-помощника
        
        confirmed_count = 0
        errors = []
        
        for pending in queryset:
            if pending.is_confirmed:
                errors.append(f"{pending.room.name} ({pending.client_name}) — уже подтверждена")
                continue
                
            if not pending.client_name or not pending.client_phone:
                errors.append(f"{pending.room.name} — нет имени/телефона клиента")
                continue
            
            try:
                success, msg = admin_confirm_booking(pending)
                if success:
                    confirmed_count += 1
                else:
                    errors.append(f"{pending.room.name} ({pending.client_name}) — {msg}")
            except Exception as e:
                errors.append(f"{pending.room.name} ({pending.client_name}) — ошибка: {str(e)}")
        
        if confirmed_count:
            self.message_user(request, f"✅ Подтверждено бронирований: {confirmed_count}")
        if errors:
            self.message_user(request, f"⚠️ Ошибки: {'; '.join(errors)}", level='warning')

    @admin.action(description='Удалить истекшие резервы')
    def delete_expired(self, request, queryset):
        expired = PendingBooking.objects.filter(expires_at__lte=timezone.now(), is_confirmed=False)
        count = expired.count()
        expired.delete()
        self.message_user(request, f"Удалено {count} истекших резервов.")


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ['name', 'short_name', 'address', 'order', 'is_active', 'rooms_count']
    list_editable = ['order', 'is_active']
    list_filter = ['is_active']
    search_fields = ['name', 'address']
    ordering = ['order', 'name']

    fieldsets = (
        ('Основное', {
            'fields': ('name', 'short_name', 'photo', 'address', 'description')
        }),
        ('Ссылки', {
            'fields': ('twogis_link',),
            'classes': ('collapse',),
        }),
        ('Настройки', {
            'fields': ('order', 'is_active'),
        }),
    )

    def rooms_count(self, obj):
        return obj.rooms.filter(is_active=True).count()
    rooms_count.short_description = 'Кабинетов' 