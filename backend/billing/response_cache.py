"""
A short memory for the dashboard's heaviest read-only reports.

The usage and analytics panels are aggregations over hundreds of thousands
of rows, and the dashboard asks for them every time it is opened -- by every
member of staff, and again on every revisit. Their inputs move slowly: usage
is collected every five minutes, and an analytics chart over thirty days does
not change in a way anyone could see within a minute. Recomputing them on
each view was most of the time the dashboard spent waiting.

Keyed by operator and the full request path, so one operator can never be
served another's figures, and two different date ranges never share an
entry. Only successful answers are kept. A cache that cannot be reached is
treated as empty: the report is computed as before, never refused.
"""

import hashlib
import logging
from functools import wraps

from django.conf import settings
from django.core.cache import cache
from rest_framework.response import Response

logger = logging.getLogger(__name__)


def cached_get(prefix, seconds):
    def decorate(get):
        @wraps(get)
        def wrapper(self, request, *args, **kwargs):
            if not getattr(settings, "RESPONSE_CACHE_ENABLED", True):
                return get(self, request, *args, **kwargs)
            # request.tenant_id is set by TenantMiddleware from the signed
            # token (or an audited impersonation); None means unscoped
            # platform staff, who get an entry of their own.
            tenant = getattr(request._request if hasattr(request, "_request") else request,
                             "tenant_id", None)
            path = hashlib.sha256(request.get_full_path().encode()).hexdigest()[:24]
            key = f"resp:{prefix}:{tenant if tenant is not None else 'platform'}:{path}"
            try:
                hit = cache.get(key)
            except Exception:
                logger.warning("[cache] unavailable reading %s", prefix)
                hit = None
            if hit is not None:
                return Response(hit)
            response = get(self, request, *args, **kwargs)
            if response.status_code == 200:
                try:
                    cache.set(key, response.data, seconds)
                except Exception:
                    logger.warning("[cache] unavailable writing %s", prefix)
            return response
        return wrapper
    return decorate
