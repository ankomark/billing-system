import os
import logging

from .tenancy import get_current_tenant_id

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


def _resolve_tenant_id(tenant):
    """Explicit argument wins, then the tenant in context."""
    if tenant is not None:
        return getattr(tenant, "pk", tenant)
    return get_current_tenant_id()


def _cache_key(tenant_id, key: str) -> str:
    """
    The tenant is part of the key.

    Without it, operator A's MPESA_CONSUMER_SECRET is served from cache to
    operator B. That is a credential leak rather than a data leak, and Postgres
    RLS cannot prevent it — on a cache hit the database is never consulted.
    """
    return f"sys_setting:{tenant_id if tenant_id is not None else 'none'}:{key}"


def get_setting(key: str, default: str | None = None, tenant=None) -> str | None:
    """
    Read a SystemSetting for one operator, with Redis-backed caching.

    Falls back to an environment variable, then to `default`. Environment
    fallbacks are platform-wide by nature, so they are only consulted when the
    operator has no value of their own — an operator that has configured a key
    never picks up another's value or a stale process-level default.
    """
    from django.core.cache import cache

    tenant_id = _resolve_tenant_id(tenant)
    cache_key = _cache_key(tenant_id, key)

    cached = cache.get(cache_key, _MISSING)
    if cached is not _MISSING:
        return cached

    from .models import SystemSetting

    qs = SystemSetting.objects.all_tenants().only("value")
    if tenant_id is not None:
        qs = qs.filter(tenant_id=tenant_id)

    # .filter().first() rather than .get(): with several operators holding the
    # same key, .get() raised MultipleObjectsReturned and returned a 500.
    obj = qs.filter(key=key).first()
    value = obj.value if obj is not None else os.getenv(key)

    result = value if value is not None else default

    # Cache misses too, so repeated lookups for an unset key do not hit the DB
    cache.set(cache_key, result, _SETTING_CACHE_TTL)
    return result


def clear_settings_cache(key: str | None = None, tenant=None) -> None:
    """
    Invalidate cached settings for one operator.

    Pass a key to clear just that one, or omit to clear all of them. Call after
    any SystemSetting write so workers see the new value immediately rather
    than within _SETTING_CACHE_TTL.
    """
    from django.core.cache import cache

    tenant_id = _resolve_tenant_id(tenant)

    if key:
        cache.delete(_cache_key(tenant_id, key))
    else:
        cache.delete_many([_cache_key(tenant_id, k) for k in ALL_SETTING_KEYS])
    logger.debug(
        f"[config] Settings cache cleared for tenant={tenant_id}: {key or 'ALL'}"
    )


# =====================================================
# PLATFORM-LEVEL SETTINGS
# =====================================================
# The platform's own credentials — the till that collects from operators.
# Deliberately a separate store and a separate cache namespace from
# get_setting(), so an operator administering their own settings can never
# read or overwrite the platform's.

PLATFORM_SETTING_KEYS = [
    "PLATFORM_MPESA_CONSUMER_KEY",
    "PLATFORM_MPESA_CONSUMER_SECRET",
    "PLATFORM_MPESA_SHORTCODE",
    "PLATFORM_MPESA_PASSKEY",
    "PLATFORM_MPESA_CALLBACK_URL",
    "PLATFORM_MPESA_ENV",
]


def _platform_cache_key(key: str) -> str:
    return f"platform_setting:{key}"


def get_platform_setting(key: str, default: str | None = None) -> str | None:
    """Read a PlatformSetting, falling back to an environment variable."""
    from django.core.cache import cache

    cache_key = _platform_cache_key(key)
    cached = cache.get(cache_key, _MISSING)
    if cached is not _MISSING:
        return cached

    from .models import PlatformSetting

    obj = PlatformSetting.objects.filter(key=key).only("value").first()
    value = obj.value if obj is not None else os.getenv(key)
    result = value if value is not None else default

    cache.set(cache_key, result, _SETTING_CACHE_TTL)
    return result


def clear_platform_settings_cache(key: str | None = None) -> None:
    from django.core.cache import cache

    if key:
        cache.delete(_platform_cache_key(key))
    else:
        cache.delete_many([_platform_cache_key(k) for k in PLATFORM_SETTING_KEYS])
    logger.debug(f"[config] Platform settings cache cleared: {key or 'ALL'}")
