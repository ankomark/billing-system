import logging
from celery import shared_task

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

    routers = RouterDevice.objects.filter(is_active=True)
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
