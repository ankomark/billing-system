import os
import logging

logger = logging.getLogger(__name__)

_SETTING_CACHE_TTL = 60  # seconds — short enough to pick up updates quickly
_MISSING = object()       # sentinel that distinguishes None from "not cached"

# All known SystemSetting keys — used by clear_settings_cache() to purge all
ALL_SETTING_KEYS = [
    "MPESA_CONSUMER_KEY",
    "MPESA_CONSUMER_SECRET",
    "MPESA_SHORTCODE",
    "MPESA_PASSKEY",
    "MPESA_CALLBACK_URL",
    "MPESA_ENV",
    "AT_USERNAME",
    "AT_API_KEY",
    "WHATSAPP_TOKEN",
    "WHATSAPP_PHONE_ID",
    "WHATSAPP_API_VERSION",
]


def _cache_key(key: str) -> str:
    return f"sys_setting:{key}"


def get_setting(key: str, default: str | None = None) -> str | None:
    """
    Read a SystemSetting from DB with Redis-backed caching.

    Multi-worker safe: all Gunicorn/Celery workers share the same Redis cache,
    so calling clear_settings_cache() after a settings update invalidates it
    globally — unlike lru_cache which is process-local.

    Falls back to environment variable, then to `default`.
    """
    from django.core.cache import cache

    cache_key = _cache_key(key)
    cached = cache.get(cache_key, _MISSING)
    if cached is not _MISSING:
        return cached

    # Cache miss — hit the DB
    from .models import SystemSetting
    try:
        obj = SystemSetting.objects.only("value").get(key=key)
        value = obj.value
    except SystemSetting.DoesNotExist:
        value = os.getenv(key)

    result = value if value is not None else default

    # Store even None so repeated misses don't hammer the DB
    cache.set(cache_key, result, _SETTING_CACHE_TTL)
    return result


def clear_settings_cache(key: str | None = None) -> None:
    """
    Invalidate the settings cache across all workers.

    Pass a specific key to clear only that setting, or omit to clear all.
    Call this after any SystemSetting write so workers pick up the new value
    within at most `_SETTING_CACHE_TTL` seconds (or immediately if you call this).
    """
    from django.core.cache import cache

    if key:
        cache.delete(_cache_key(key))
    else:
        cache.delete_many([_cache_key(k) for k in ALL_SETTING_KEYS])
    logger.debug(f"[config] Settings cache cleared: {key or 'ALL'}")
