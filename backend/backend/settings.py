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

# A hostname the platform only tells you after the first deploy.
#
# Railway assigns *.up.railway.app at deploy time, so it cannot be in
# ALLOWED_HOSTS before there is something deployed to read it from. Without
# this the first boot answers 400 DisallowedHost on its own URL, and the only
# way out is to deploy, read the hostname off the dashboard, set the variable
# and redeploy. The variable does not exist on a VPS, so a Hetzner install is
# unchanged.
_platform_host = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
if _platform_host and _platform_host not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(_platform_host)

# Django rejects any POST whose Origin is not a host it believes it serves.
# Same-origin admin logins need nothing here, but a second name in front of the
# same app — a custom domain, a preview URL — fails at the login form with
# "CSRF verification failed" and no log line naming the host it wanted. Must be
# scheme-qualified; bare hostnames are silently useless.
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]
if _platform_host:
    CSRF_TRUSTED_ORIGINS.append(f"https://{_platform_host}")

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

    # TLS is terminated in front of this process — by Railway's edge, or by a
    # reverse proxy on a VPS — so what reaches gunicorn is plain HTTP and
    # request.is_secure() is False. SECURE_SSL_REDIRECT then 301s a request that
    # already arrived over HTTPS, the browser follows it back to the same place,
    # and every URL including the login page dies with ERR_TOO_MANY_REDIRECTS.
    #
    # Trusting a header a client could forge is only safe because nothing but
    # the proxy can reach this process. Do not set it on a gunicorn exposed
    # directly to the internet — there, anyone can claim their request was
    # secure and collect cookies marked HTTPS-only.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

    # The platform's health probe is not a browser and does not come through
    # the edge, so it arrives without that header and would be answered with a
    # 301. Railway counts any non-2xx as a failed check and rolls the deploy
    # back, so the app is marked unhealthy for the one reason that has nothing
    # to do with its health. Exempting one unauthenticated read-only endpoint
    # from the HTTPS redirect gives away nothing.
    SECURE_REDIRECT_EXEMPT = [r"^health/$"]

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
        # Testing router credentials. Generous for the operator filling in a
        # form — they will press it two or three times while getting a password
        # right — and low enough that the endpoint is no use for looking around
        # the network the platform sits in. See RouterTestThrottle.
        "router_test": "20/min",
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

# ─── The management tunnel ───────────────────────────────────────────────────
#
# Operators' routers sit behind CGNAT, so the platform reaches them over
# WireGuard rather than dialling a public address. These let the Routers page
# provision a tunnel peer itself, instead of an admin running three commands
# over SSH for every site — see billing/services/tunnel.py.
#
# WG_SERVER_PUBLIC_KEY is the one value that cannot be defaulted: it lives in
# /etc/wireguard/server-public.key, which is root-only, so it has to be copied
# into .env once when the server is built:
#
#     sudo cat /etc/wireguard/server-public.key
#
# Without it, provisioning refuses rather than emitting a script that pastes
# cleanly and produces a tunnel that never comes up.
WG_SERVER_PUBLIC_KEY = os.getenv("WG_SERVER_PUBLIC_KEY", "").strip()

# The name routers dial. A name rather than an address, so moving hosts is a
# DNS change instead of a visit to every router already deployed. Must be
# grey-clouded in Cloudflare — UDP cannot be proxied.
WG_ENDPOINT_HOST = os.getenv("WG_ENDPOINT_HOST", "").strip()
WG_ENDPOINT_PORT = int(os.getenv("WG_ENDPOINT_PORT", "51820"))

WG_TUNNEL_SUBNET = os.getenv("WG_TUNNEL_SUBNET", "10.10.0.0/24")
WG_SERVER_TUNNEL_IP = os.getenv("WG_SERVER_TUNNEL_IP", "10.10.0.1")

# What the interface is called on the router. Only cosmetic to us, but it goes
# into the generated script and into the firewall rule that references it, so
# the two must agree.
WG_INTERFACE_NAME = os.getenv("WG_INTERFACE_NAME", "wg-smartbill")

# Where peer requests are dropped for the host to pick up. The web container
# has no business running `wg set` — that needs root and the host's network
# namespace, and an RCE in Django should not come with the ability to
# reconfigure the server's networking. A systemd path unit on the host watches
# this directory instead. See docker/wg-peer-watcher.sh.
#
# This is the path *inside the container*, and there is rarely a reason to
# change it. The host side is WG_SPOOL_HOST_DIR in docker-compose.yml — they
# are two ends of one bind mount, and moving one without the other means
# Django writes somewhere no watcher is looking, with no error anywhere.
WG_SPOOL_DIR = os.getenv("WG_SPOOL_DIR", "/var/spool/wg-requests")

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
    # Its hotspot twin was written, tested and never scheduled — so the data
    # figure on the connected page and every hotspot cap read zero, because
    # nothing had ever recorded a byte. Offset by two minutes so the two
    # collectors do not open connections to the same routers at once.
    "collect-hotspot-usage": {
        "task": "billing.tasks.usage_tasks.collect_hotspot_usage_snapshots",
        "schedule": crontab(minute="2-59/5"),
        "options": {"expires": 240},
    },
    # Fold finished days of five-minute deltas into one row per subscriber per
    # day. Before the platform invoicing at 02:00, so a month's totals are
    # rolled up before anything bills against them.
    "roll-up-usage": {
        "task": "billing.tasks.usage_tasks.roll_up_usage_daily",
        "schedule": crontab(hour=1, minute=20),
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
    # Hotspot sharing. Off for every operator until they set TETHERING_POLICY,
    # so on a default install this wakes up, finds nobody has asked for it, and
    # goes back to sleep without dialling a single router.
    #
    # Minute 4 of each five, so it does not open connections to the same
    # hardware as the two usage collectors on minutes 0 and 2. The five-minute
    # spacing is also what the router-side address-list timeout is set against
    # — see TETHERING_LIST_TIMEOUT.
    "detect-tethering": {
        "task": "billing.tasks.tethering_tasks.detect_tethering",
        "schedule": crontab(minute="4-59/5"),
        "options": {"expires": 240},
    },
    "prune-tethering-cases": {
        "task": "billing.tasks.tethering_tasks.prune_tethering_cases",
        "schedule": crontab(hour=5, minute=20),
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

# Safaricom's callback sources. A fast path, not a gate — see
# MpesaSTKCallbackView, which accepts an unlisted address when the callback
# correlates to a push this platform sent. That matters because this list is
# never reliably complete: 196.201.212.69 posted a real result on the first
# live payment and was not here, and Safaricom publishes more than it
# documents in any one place.
#
# Add addresses as the log reports them, so the fast path keeps doing its job.
MPESA_TRUSTED_IPS = [
    "196.201.214.200",
    "196.201.214.206",
    "196.201.213.114",
    "196.201.214.207",
    "196.201.214.208",
    "196.201.212.127",
    "196.201.212.138",
    "196.201.212.129",
    "196.201.212.136",
    "196.201.212.74",
    "196.201.212.69",
    "196.201.213.44",
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

# Refuse to serve real traffic without it.
#
# EncryptedCharField falls back to plaintext when this is unset — deliberately,
# so development and the test suite need no key. In production that fallback
# means every operator's router admin password is stored in clear text, in a
# database that gets copied to object storage every night, with nothing
# anywhere reporting a problem. A password that protects other people's network
# hardware is not something to leave to whether someone remembered.
#
# Reading is unaffected either way: the field returns legacy plaintext as-is,
# so an existing database keeps working the moment a key is added, and rows
# encrypt as they are next written.
if not DEBUG and not FIELD_ENCRYPTION_KEY:
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "FIELD_ENCRYPTION_KEY is not set. Router passwords would be stored in "
        "plain text, and nothing would say so. Set it before running with "
        "DEBUG off. Generate one with:\n\n"
        "  python -c \"from cryptography.fernet import Fernet; "
        "print(Fernet.generate_key().decode())\"\n\n"
        "Keep it somewhere separate from your database backups — a dump "
        "restored without this key leaves every router password unreadable."
    )

# =====================================================
# LOGGING
# =====================================================
# Without this block, a 500 in production leaves no trace anywhere.
#
# Django's default configuration routes django.request errors to the
# mail_admins handler, and attaches a console handler only when DEBUG is on.
# Run with DEBUG=False and no ADMINS — which is what any real deployment looks
# like — and the traceback for every unhandled exception is silently dropped.
# The access log records a 500 and nothing else: no exception type, no file, no
# line. Verified on the running stack while debugging a voucher redemption that
# threw after binding a device, where there was simply nothing to read.
#
# stderr, because these processes run in containers and that is what
# `docker compose logs` captures. Sentry, when a DSN is set, is added on top of
# this by its own integration rather than replacing it — a service that can
# have an outage should not be the only place errors go.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{levelname}] {asctime} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "loggers": {
        # The one that matters: unhandled exceptions in views land here, with
        # the traceback attached.
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        # This project's own logger.* calls — router failures, M-Pesa
        # callbacks, tenant warnings.
        "billing": {
            "handlers": ["console"],
            "level": os.getenv("BILLING_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
}

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
