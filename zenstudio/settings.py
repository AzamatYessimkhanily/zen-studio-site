# zenstudio/settings.py
import os
from pathlib import Path
from dotenv import load_dotenv
BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = 'django-insecure-e@#4!&8*@8)7!%5&0!9_6^2!3@1#4&5*@6)8!%9&0!1_2^3!'

# DEBUG = True # <-- ЗАКОММЕНТИРУЙ ИЛИ УДАЛИ СТАРУЮ СТРОКУ
# Значение 'False' будет браться из .env файла. Если его там нет, по умолчанию будет True.
DEBUG = os.environ.get('DEBUG', 'True') == 'True'


ALLOWED_HOSTS = ['zenstudio.kz', '194.32.140.210']
# Доверенные источники для CSRF (нужно с указанием схемы!)
CSRF_TRUSTED_ORIGINS = [
    "https://zenstudio.kz",
    "http://zenstudio.kz",
]

INSTALLED_APPS = [
    "nested_admin",
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    "django.contrib.sitemaps",
    'django.contrib.messages',
    'django_cleanup.apps.CleanupConfig',
    'django.contrib.staticfiles',
    'main',  # Добавляем наше приложение
    ]


MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'zenstudio.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'django.template.context_processors.media',
                'main.context_processors.seo_settings',  # Добавьте здесь ваш контекстный процессор
                'main.context_processors.pages_menu',  # Добавьте, если нужно
            ],
        },
    },
]

WSGI_APPLICATION = 'zenstudio.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

LANGUAGE_CODE = 'ru'

TIME_ZONE = 'Asia/Almaty'

USE_I18N = True

USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [
    BASE_DIR / 'static',
]

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Добавь эти строки в конец settings.py
BOT_WHATSAPP_API_URL = 'http://194.32.140.210:5001/send_whatsapp' # URL API бота (поменяем, когда создадим)
GROUP_CHAT_ID = "120363402711453294@g.us" # ID твоей группы
# URL главной Google Таблицы (для get_door_code) - если еще не определен глобально
SPREADSHEET_URL = 'https://docs.google.com/spreadsheets/d/1sorzD7-esaHJmZHnVm276yrdYPbpyOSxvHRq77uFomA/edit'
DATA_UPLOAD_MAX_MEMORY_SIZE = 10485760
CLIENTS_HISTORY_SPREADSHEET_URL = os.environ.get('CLIENTS_HISTORY_SPREADSHEET_URL', 'https://docs.google.com/spreadsheets/d/1Q6If_TlZryuEdeupAqj5VIn4wKBXWeaAgVnpRajVxNQ/edit')
GREEN_API_INSTANCE_ID = '7105260844'
GREEN_API_TOKEN = '178daf5a389f41e78ea910b09dcf5bd51143835112364b0198'