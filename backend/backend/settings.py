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

SECRET_KEY = os.getenv("SECRET_KEY", "django-insecure-change-this-in-production")

DEBUG = os.getenv("DEBUG", "False") == "True"

ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

# =====================================================
# HTTPS / SECURITY HEADERS  (production only)
# =====================================================
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000          # 1 year — tells browsers to always use HTTPS
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True            # session cookie only sent over HTTPS
    CSRF_COOKIE_SECURE = True               # CSRF cookie only sent over HTTPS
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"               # clickjacking protection

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
        # Keep connections alive per-worker. Use pgBouncer in production for
        # true connection pooling when running 8+ Gunicorn workers.
        "CONN_MAX_AGE": int(os.getenv("CONN_MAX_AGE", "60")),
    }
}

# =====================================================
# CACHE (Redis — shared across all workers)
# =====================================================
# Redis DB layout:
#  0 → Celery broker
#  1 → Django cache (get_setting + any app caching)
#  2 → Celery results

REDIS_URL = os.getenv("REDIS_URL", "")

if REDIS_URL:
    # Production / staging: Redis is available — shared cache across all workers.
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": f"{REDIS_URL}/1",
            "OPTIONS": {
                "socket_connect_timeout": 3,
                "socket_timeout": 3,
            },
        }
    }
else:
    # Development: no Redis — use in-process memory cache.
    # get_setting() still works; cache is per-process (not shared across workers).
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
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
STATIC_ROOT = BASE_DIR / "staticfiles"  # target for `python manage.py collectstatic`

# =====================================================
# CORS
# =====================================================

if DEBUG:
    CORS_ALLOW_ALL_ORIGINS = True
else:
    CORS_ALLOWED_ORIGINS = os.getenv(
        "CORS_ALLOWED_ORIGINS", "http://localhost:3000"
    ).split(",")

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
    "DEFAULT_PAGINATION_CLASS": "billing.pagination.StandardPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "120/min",
        "user": "300/min",
        "login": "5/min",
        "hotspot_public": "15/min",
        "mpesa_callback": "60/min",
        "stk_push": "10/min",
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# =====================================================
# CELERY CONFIGURATION (CRITICAL)
# =====================================================

# Broker — Redis in production, memory:// in dev (env var always wins)
CELERY_BROKER_URL = os.getenv(
    "CELERY_BROKER_URL",
    f"{REDIS_URL}/0" if REDIS_URL else "memory://",
)

# Serialization
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"

# Result backend — Redis in production, django-db in dev (when no Redis URL set).
# In production Redis prevents the task result table growing 800k+ rows/day.
if REDIS_URL:
    CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", f"{REDIS_URL}/2")
    CELERY_RESULT_EXPIRES = 60 * 60 * 24  # auto-expire results after 24 hours
else:
    CELERY_RESULT_BACKEND = "django-db"
    CELERY_CACHE_BACKEND = "django-cache"

# Timezone consistency
CELERY_TIMEZONE = TIME_ZONE

# Task safety
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 300        # Hard kill after 5 min
CELERY_TASK_SOFT_TIME_LIMIT = 240   # Graceful stop at 4 min
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

# Beat scheduler — DB so schedules survive deploys and are editable in admin
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# Pre-seed schedules on fresh deploys (DatabaseScheduler syncs these to DB)
CELERY_BEAT_SCHEDULE = {
    "expire-subscriptions": {
        "task": "billing.tasks.subscription_tasks.enforce_subscription_expiry",
        "schedule": crontab(minute="*/5"),
        "options": {"expires": 240},
    },
    "send-expiry-reminders": {
        "task": "billing.tasks.reminder_tasks.send_expiry_reminders",
        "schedule": crontab(hour=8, minute=0),
    },
    "check-router-health": {
        "task": "billing.tasks.router_health.check_router_health_task",
        "schedule": crontab(minute="*/2"),
        "options": {"expires": 90},
    },
    "collect-pppoe-usage": {
        "task": "billing.tasks.usage_tasks.collect_pppoe_usage_snapshots",
        "schedule": crontab(minute="*/5"),
        "options": {"expires": 240},
    },
    "auto-failover": {
        "task": "billing.tasks.auto_failover.run_auto_failover_task",
        "schedule": crontab(minute="*/3"),
        "options": {"expires": 150},
    },
}

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

MPESA_ALLOW_LOCAL_CALLBACK = os.getenv("MPESA_ALLOW_LOCAL_CALLBACK", "False") == "True"

# =====================================================
# OPTIONAL FALLBACK ENV CONFIGS
# (Primary values loaded dynamically from DB)
# =====================================================

MPESA_SHORTCODE = os.getenv("MPESA_SHORTCODE", "")
AT_USERNAME = os.getenv("AT_USERNAME", "")
AT_API_KEY = os.getenv("AT_API_KEY", "")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID", "")

# =====================================================
# FIELD-LEVEL ENCRYPTION
# =====================================================
# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
FIELD_ENCRYPTION_KEY = os.getenv("FIELD_ENCRYPTION_KEY", "")

# =====================================================
# ERROR MONITORING (SENTRY)
# =====================================================

SENTRY_DSN = os.getenv("SENTRY_DSN", "")

if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.redis import RedisIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[
            DjangoIntegration(),
            CeleryIntegration(),
            RedisIntegration(),
        ],
        traces_sample_rate=0.2,
        send_default_pii=False,
        environment=os.getenv("ENVIRONMENT", "production"),
    )
