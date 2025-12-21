import os
from pathlib import Path
from dotenv import load_dotenv
from datetime import timedelta
from celery.schedules import crontab

# =====================================================
# BASE CONFIG
# =====================================================

BASE_DIR = Path(__file__).resolve().parent.parent

# 🔴 FORCE load .env from backend directory
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = "django-insecure-change-this-in-production"

DEBUG = True  # ❗ Set False in production

ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
]

# =====================================================
# APPLICATIONS
# =====================================================

INSTALLED_APPS = [
    # Django core
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Third-party
    "rest_framework",
    "corsheaders",

    # Celery
    "django_celery_results",
    "django_celery_beat",

    # Local
    "billing",
]

AUTH_USER_MODEL = "billing.User"

# =====================================================
# MIDDLEWARE
# =====================================================

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# =====================================================
# URLS / WSGI
# =====================================================

ROOT_URLCONF = "backend.urls"
WSGI_APPLICATION = "backend.wsgi.application"

# =====================================================
# TEMPLATES
# =====================================================

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# =====================================================
# DATABASE
# =====================================================

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "wifi_billing"),
        "USER": os.getenv("POSTGRES_USER", "wifi_user"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "wifi_pass"),
        "HOST": os.getenv("POSTGRES_HOST", "127.0.0.1"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
    }
}



# =====================================================
# AUTH / PASSWORDS
# =====================================================

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# =====================================================
# INTERNATIONALIZATION / TIME
# =====================================================

LANGUAGE_CODE = "en-us"

TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True

# =====================================================
# STATIC FILES
# =====================================================

STATIC_URL = "/static/"

# =====================================================
# CORS (DEV ONLY)
# =====================================================

CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = True

# =====================================================
# DJANGO REST FRAMEWORK
# =====================================================

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# =====================================================
# CELERY CONFIGURATION (CRITICAL)
# =====================================================

# Broker (Redis)
CELERY_BROKER_URL = os.getenv(
    "CELERY_BROKER_URL",
    "redis://127.0.0.1:6379/0"
)


# Serialization
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"

# ✅ RESULT BACKEND (Django DB)
CELERY_RESULT_BACKEND = "django-db"
CELERY_CACHE_BACKEND = "django-cache"

# Timezone consistency
CELERY_TIMEZONE = TIME_ZONE

# Task safety
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 300        # Hard kill
CELERY_TASK_SOFT_TIME_LIMIT = 240   # Graceful stop
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

# Beat (DB scheduler)
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# =====================================================
# M-PESA CONFIG
# =====================================================

MPESA_ENV = os.getenv("MPESA_ENV", "sandbox")

MPESA_TRUSTED_IPS = [
    "196.201.214.200",
    "196.201.214.206",
    "196.201.213.114",
    "196.201.214.207",
    "196.201.214.208",
]

MPESA_ALLOW_LOCAL_CALLBACK = True  # ❗ Disable in production

# =====================================================
# OPTIONAL FALLBACK ENV CONFIGS
# (Primary values loaded dynamically from DB)
# =====================================================

MPESA_SHORTCODE = os.getenv("MPESA_SHORTCODE", "")
AT_USERNAME = os.getenv("AT_USERNAME", "")
AT_API_KEY = os.getenv("AT_API_KEY", "")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID", "")
