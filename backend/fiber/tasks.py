"""
Smart Mapping's scheduled work. Every entry is in CELERY_BEAT_SCHEDULE.

Polling fans out one task per OLT, as billing's router health sweep does: a
dead OLT costs its own timeout, not everyone else's, and `expires` drops a
probe that sat in a backlog past the next sweep rather than writing stale
state over fresh.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from billing.tenancy import all_tenants, tenant_context

logger = logging.getLogger(__name__)

POLL_EXPIRES_SECONDS = 240


@shared_task
def poll_all_olts_task():
    from .models import Olt

    with all_tenants():
        ids = list(Olt.objects.all_tenants().filter(is_active=True).values_list("id", flat=True))
    for olt_id in ids:
        poll_single_olt_task.apply_async((olt_id,), expires=POLL_EXPIRES_SECONDS)
    return len(ids)


@shared_task
def poll_single_olt_task(olt_id):
    from .services.collector import poll_by_id

    with all_tenants():
        return poll_by_id(olt_id)


def _per_tenant(fn):
    from .models import Olt

    with all_tenants():
        tenants = set(Olt.objects.all_tenants().values_list("tenant_id", flat=True))
    out = {}
    for tenant_id in tenants:
        with tenant_context(tenant_id):
            out[tenant_id] = fn()
    return out


@shared_task
def rollup_fiber_hourly_task(hours_back=2):
    """Fold the last completed hours into hourly rows. Re-running an hour is harmless."""
    from .services.analytics import _hour, rollup_hour

    now = timezone.now()
    done = 0
    with all_tenants():
        for h in range(hours_back, 0, -1):
            done += rollup_hour(_hour(now) - timedelta(hours=h))
        # The hour in progress too, so the dashboard's "last hour" is current.
        done += rollup_hour(_hour(now))
    return done


@shared_task
def fiber_trend_sweep_task():
    from .services.analytics import trend_sweep

    return _per_tenant(trend_sweep)


@shared_task
def fiber_maintenance_sweep_task():
    """Compensate customers once a maintenance window that promised it has ended."""
    from .models import MaintenanceWindow
    from .services.compensation import for_maintenance

    now = timezone.now()
    done = 0
    with all_tenants():
        windows = list(MaintenanceWindow.objects.all_tenants().filter(
            ends_at__lte=now, compensate=True, compensated_at__isnull=True))
    for w in windows:
        with tenant_context(w.tenant_id):
            done += for_maintenance(w, now)
    return done


@shared_task
def prune_fiber_task():
    from .services.analytics import prune
    from .services.linking import prune_stale

    with all_tenants():
        out = prune()
        out["links"] = prune_stale()
    logger.info("[fiber] pruned %s", out)
    return out
