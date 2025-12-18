# billing/config.py
from functools import lru_cache
import os


@lru_cache(maxsize=200)
def get_setting(key: str, default: str | None = None) -> str | None:
    """
    Read setting from DB (SystemSetting) first.
    If key exists in DB, return its value even if it's an empty string ("").
    If not in DB, fall back to environment variable.
    Cached for performance; call get_setting.cache_clear() after updates.
    """
    # ✅ Import lazily to avoid circular import problems
    from .models import SystemSetting

    try:
        obj = SystemSetting.objects.only("value").get(key=key)
        return obj.value  # return even if ""
    except SystemSetting.DoesNotExist:
        pass

    env_val = os.getenv(key)
    if env_val is not None:
        return env_val

    return default
