import logging
from celery import shared_task

from billing.models import Customer, RouterDevice
from billing.router_service import migrate_customer_router

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_kwargs={"max_retries": 2},
)
def run_auto_failover_task(self):
    """
    Detect offline routers and dispatch individual migration tasks per customer.
    Running migrations sequentially (old pattern) blocked a single worker for
    minutes when many customers needed to be moved. Now each customer is
    migrated in parallel by the Celery worker pool.
    """
    offline_routers = RouterDevice.objects.filter(is_active=True, is_online=False)

    if not offline_routers.exists():
        return 0

    dispatched = 0
    for router in offline_routers:
        customer_ids = list(
            Customer.objects
            .filter(router=router, status="active")
            .values_list("id", flat=True)
        )
        for cid in customer_ids:
            migrate_single_customer_task.delay(cid)
            dispatched += 1

    logger.info(f"[auto-failover] Dispatched migration for {dispatched} customers")
    return dispatched


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_kwargs={"max_retries": 3},
    retry_jitter=True,
)
def migrate_single_customer_task(self, customer_id: int):
    """
    Migrate one customer to the best available router.
    Runs in parallel with other customer migrations.
    """
    try:
        customer = Customer.objects.select_related("router").get(id=customer_id)
    except Customer.DoesNotExist:
        logger.warning(f"[auto-failover] Customer {customer_id} not found")
        return

    success, message = migrate_customer_router(customer, reason="auto_failover")

    if success:
        logger.info(f"[auto-failover] Customer {customer_id}: {message}")
    else:
        logger.warning(f"[auto-failover] Customer {customer_id} failed: {message}")

    return {"success": success, "message": message, "customer_id": customer_id}
