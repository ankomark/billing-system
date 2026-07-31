import logging
from celery import shared_task

from billing.tenancy import all_tenants

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_kwargs={"max_retries": 2},
)
def check_router_health_task(self):
    """
    Probe every active router and update is_online / last_seen / last_error.
    Runs every 2 minutes via Celery Beat. Keeps is_online fresh so that
    AdminRouterListView can serve cached status without blocking HTTP workers
    with live socket probes.
    """
    from billing.models import RouterDevice
    from billing.router_service import safe_connect_router

    # Health probing is legitimately platform-wide: every operator's
    # routers must be checked, so this opts out of scoping explicitly.
    with all_tenants():
        routers = list(RouterDevice.objects.all_tenants().filter(is_active=True))
    online = offline = 0

    for router in routers:
        api = safe_connect_router(router)
        if api:
            online += 1
            logger.info(f"[router-health] {router.name} ONLINE")
        else:
            offline += 1
            logger.warning(f"[router-health] {router.name} OFFLINE — {router.last_error}")

    logger.info(f"[router-health] Check complete: {online} online, {offline} offline")
    return {"online": online, "offline": offline}


# Long enough to investigate a pattern of flapping, short enough that the table
# does not grow without bound. Only transitions are stored, so a stable estate
# writes almost nothing and this rarely deletes anything.
EVENT_RETENTION_DAYS = 90


@shared_task
def prune_router_events_task(days=EVENT_RETENTION_DAYS):
    """
    Drop router events past the retention window.

    Every sweep that finds a flapping router adds rows, and nothing else would
    ever remove them.
    """
    from django.utils import timezone
    from billing.models import RouterEvent

    cutoff = timezone.now() - timezone.timedelta(days=days)
    with all_tenants():
        deleted, _ = RouterEvent.objects.all_tenants().filter(
            created_at__lt=cutoff).delete()
    logger.info("[router-health] pruned %s router event(s) older than %s days",
                deleted, days)
    return deleted
