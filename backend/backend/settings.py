import os
from pathlib import Path
from dotenv import load_dotenv
from datetime import timedelta
from celery.schedules import crontab
from corsheaders.defaults import default_headers

# =====================================================
# BASE CONFIG
# =====================================================

BASE_DIR = Path(__file__).resolve().parent.parent

# 🔴 FORCE load .env from backend directory
load_dotenv(BASE_DIR / ".env")

_INSECURE_SECRET_KEY = "django-insecure-change-this-in-production"

SECRET_KEY = os.getenv("SECRET_KEY", _INSECURE_SECRET_KEY)

DEBUG = os.getenv("DEBUG", "False") == "True"

# Refuse to serve real traffic on the placeholder key.
#
# The fallback above is convenient locally and catastrophic in production,
# because its value is public — it is committed to this repository. Everything
# signed with it becomes forgeable by anyone who reads it: session cookies,
# password-reset links, and both hotspot secrets, which are HMACs over this key
# and nothing else. A forged device token reads a stranger's access code; a
# forged poll token reads any voucher by invoice number. The gate is only as
# real as the key behind it.
#
# Nothing here fails visibly. The site comes up, the tests pass, and the hole
# is silent until somebody finds it — which is exactly the kind of mistake
# worth spending a startup check on.
if not DEBUG and SECRET_KEY == _INSECURE_SECRET_KEY:
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "SECRET_KEY is still the development placeholder, which is published "
        "in this repository. Set SECRET_KEY in the environment before running "
        "with DEBUG off — session cookies and the hotspot poll and device "
        "tokens are all signed with it, and every one of them is forgeable "
        "until you do. Generate one with:\n\n"
        "  python -c \"from django.core.management.utils import "
        "get_random_secret_key; print(get_random_secret_key())\"\n"
    )

ALLOWED_HOSTS = [
    h.strip() for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]

# Loopback is always allowed. The container healthcheck calls
# http://localhost:8000/health/, so the moment ALLOWED_HOSTS is narrowed to a
# real domain — which production requires — that request would be answered with
# 400 DisallowedHost, the healthcheck would fail forever, and an orchestrator
# would restart-loop a perfectly healthy container.
#
# No weakening of Host-header protection: these names only resolve from inside
# the container itself.
for _loopback in ("localhost", "127.0.0.1"):
    if _loopback not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_loopback)

# =====================================================
# HTTPS / SECURITY HEADERS  (production only)
# =====================================================
if not DEBUG:
    # Redirecting to HTTPS needs something in front terminating TLS. The compose
    # stack has no such proxy, so every request to http://localhost:8000 would
    # 301 to an https:// URL nothing is listening on. Defaults to True (secure);
    # set SECURE_SSL_REDIRECT=False in .env only when TLS is genuinely absent.
    # Note SESSION_COOKIE_SECURE below still applies, so Django admin login
    # requires HTTPS regardless — the JWT API is unaffected.
    SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "True") == "True"
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
    # Must sit directly after SecurityMiddleware. Serves STATIC_ROOT under
    # gunicorn, which (unlike runserver with DEBUG=True) does not serve static
    # itself — without this the Django admin loads with no CSS.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Must come after authentication: reads the JWT to decide which operator's
    # data this request may see. Without it every query runs unscoped.
    "billing.middleware.TenantMiddleware",
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
        "OPTIONS": {
            # Fail fast when the database is unreachable. Without this the
            # driver waits on the OS TCP timeout, so a network partition makes
            # every worker hang instead of erroring — and the container
            # healthcheck, which has its own 5s budget, times out and gets the
            # container restarted while nothing is actually wrong with it.
            "connect_timeout": int(os.getenv("DB_CONNECT_TIMEOUT", "5")),
        },
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

# WhiteNoise compresses static files and adds far-future cache headers.
# Deliberately the non-manifest backend: CompressedManifestStaticFilesStorage
# raises at request time for any file missing from the manifest, which turns a
# forgotten collectstatic into a hard 500 instead of a missing stylesheet.
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

# =====================================================
# CORS
# =====================================================

if DEBUG:
    CORS_ALLOW_ALL_ORIGINS = True
else:
    CORS_ALLOWED_ORIGINS = os.getenv(
        "CORS_ALLOWED_ORIGINS", "http://localhost:3000"
    ).split(",")

# The captive portal is a different origin, and there is no list to put it on.
#
# mikrotik-hotspot/login.html runs on the router and calls /hotspot/validate/
# cross-origin. Its origin is whatever address that particular MikroTik answers
# on — 10.5.50.1, 192.168.88.1, login.hotspot — which differs per operator and
# per site, so it can never be enumerated in CORS_ALLOWED_ORIGINS.
#
# Without this the preflight returns 200 with no Access-Control-Allow-Origin,
# the browser refuses, and the voucher login on every router is dead while the
# server logs look perfectly healthy. Exactly how the impersonation headers
# shipped broken. The live test suite cannot see it either — it uses axios's
# Node adapter and performs no preflight — so this is pinned by a backend test.
#
# A captive portal is always on a private address, so the rule is bounded to
# RFC1918 and the MikroTik hotspot hostnames. It grants a page on one of those
# addresses nothing it could not already reach: authentication here is a bearer
# token in a header, never a cookie (DEFAULT_AUTHENTICATION_CLASSES is JWT
# only), and cross-origin script cannot read one.
_PRIVATE_ORIGINS = [
    r"^https?://10(\.\d{1,3}){3}(:\d+)?$",
    r"^https?://172\.(1[6-9]|2\d|3[01])(\.\d{1,3}){2}(:\d+)?$",
    r"^https?://192\.168(\.\d{1,3}){2}(:\d+)?$",
    # MikroTik's own hotspot hostnames.
    r"^https?://([\w-]+\.)?hotspot(:\d+)?$",
]

CORS_ALLOWED_ORIGIN_REGEXES = _PRIVATE_ORIGINS + [
    r.strip() for r in os.getenv("CORS_ALLOWED_ORIGIN_REGEXES", "").split(",") if r.strip()
]

CORS_ALLOW_CREDENTIALS = True

# The impersonation headers must be listed explicitly or a browser will not
# send them.
#
# django-cors-headers answers a preflight with a fixed default list of allowed
# headers, and anything absent from it is refused — CORS_ALLOW_ALL_ORIGINS does
# not cover headers, so this failed in development too. The preflight itself
# returns 200, which makes it look fine from the server side; what fails is the
# real request, which the browser then never sends. The frontend sees a bare
# network error and reports it as a connection problem, so the symptom points
# nowhere near the cause.
#
# Nothing is loosened by naming them: the backend already ignores both headers
# for non-platform accounts and records every use in ImpersonationLog.
CORS_ALLOW_HEADERS = (
    *default_headers,
    "x-impersonate-tenant",
    "x-impersonate-reason",
)

# =====================================================
# DJANGO REST FRAMEWORK
# =====================================================

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        # Rejects a token whose operator claim no longer matches the account.
        # Without it, a demoted platform account keeps platform-wide visibility
        # until its token expires, because scoping reads the claim while
        # permissions read the database.
        "billing.authentication.TenantAwareJWTAuthentication",
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
        # Guessing surface: redeeming a code, starting a purchase. Voucher
        # codes are six characters from a 36-symbol alphabet chosen with
        # secrets — about 2.2 billion — so this bounds an attacker to a
        # rounding error while leaving room for a person who mistypes.
        "hotspot_public": "30/min",
        # What a portal polls. Higher, and bucketed per device or per purchase
        # rather than per address: every customer of a hotspot shares one NAT,
        # so an IP-only limit means one person waiting on an M-Pesa prompt
        # starves the whole site. See HotspotPollThrottle.
        "hotspot_poll": "40/min",
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
# =====================================================
# ROUTER HEALTH
# =====================================================
# Failed probes in a row before a router is declared down.
#
# Auto-failover migrates every subscriber off an offline router, so this
# number decides how much of a wobble it takes to move hundreds of people onto
# different hardware. One was the old behaviour and far too twitchy for the
# links these operators run: Starlink pauses at satellite handover and LTE
# blips, and neither is an outage. Three probes at two-minute polling is about
# six minutes of genuine silence.
#
# Raise it for operators on especially unstable backhaul; lower it only if
# failover has become fast enough that a false one is cheap, which it is not.
ROUTER_OFFLINE_AFTER_FAILURES = int(os.getenv("ROUTER_OFFLINE_AFTER_FAILURES", "3"))

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
    # Router events are transitions only, so this deletes little on a stable
    # estate — but a flapping router writes rows every two minutes and nothing
    # else would ever remove them.
    "prune-router-events": {
        "task": "billing.tasks.router_health.prune_router_events_task",
        "schedule": crontab(hour=5, minute=0),
    },
    # Refused connections are a diagnostic, not a ledger. Without this they
    # grow with every mistyped code and nothing ever removes a row.
    "prune-connection-attempts": {
        "task": "billing.tasks.router_health.prune_connection_attempts_task",
        "schedule": crontab(hour=5, minute=10),
    },
    "collect-pppoe-usage": {
        "task": "billing.tasks.usage_tasks.collect_pppoe_usage_snapshots",
        "schedule": crontab(minute="*/5"),
        "options": {"expires": 240},
    },
    # Platform billing — charges operators, not subscribers.
    "generate-platform-invoices": {
        "task": "billing.tasks.platform_billing_tasks.generate_tenant_invoices",
        "schedule": crontab(hour=2, minute=0),
    },
    "mark-overdue-operators": {
        "task": "billing.tasks.platform_billing_tasks.mark_overdue_tenants",
        "schedule": crontab(hour=3, minute=0),
    },
    "platform-billing-reminders": {
        "task": "billing.tasks.platform_billing_tasks.send_platform_billing_reminders",
        "schedule": crontab(hour=9, minute=0),
    },
    # Locks the operator dashboard only. Subscriber service is never cut off
    # automatically — see Tenant.is_restricted.
    "restrict-after-grace": {
        "task": "billing.tasks.platform_billing_tasks.restrict_expired_grace_tenants",
        "schedule": crontab(hour=4, minute=0),
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

# Public base URL of this platform, e.g. https://billing.example.com
# Used to derive each operator's own M-Pesa callback URL, which carries their
# public token so the callback loads the right operator's credentials.
PLATFORM_BASE_URL = os.getenv("PLATFORM_BASE_URL", "")

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
