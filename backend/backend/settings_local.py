"""
Local development settings.
Uses SQLite (no PostgreSQL needed) and synchronous Celery (no Redis needed).
Run with: python manage.py runserver --settings=backend.settings_local
"""
from .settings import *  # noqa

# ── SQLite — zero-config database for local development ──────────────────────
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db_local.sqlite3",
    }
}

# ── Celery eager mode — tasks run synchronously, no broker required ───────────
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"

# ── Disable Sentry in local dev ───────────────────────────────────────────────
SENTRY_DSN = ""

# ── Allow all hosts locally ───────────────────────────────────────────────────
ALLOWED_HOSTS = ["*"]

# ── Open CORS for frontend dev server ─────────────────────────────────────────
CORS_ALLOW_ALL_ORIGINS = True

# ── Skip encryption key requirement locally ───────────────────────────────────
FIELD_ENCRYPTION_KEY = ""

# ── Allow M-Pesa callbacks from localhost ─────────────────────────────────────
MPESA_ALLOW_LOCAL_CALLBACK = True
