import logging
from celery import shared_task

from billing.models import Customer, RouterDevice
from billing.router_service import migrate_customer_router
from billing.tenancy import tenant_context

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
    # Cross-operator sweep by design — every operator's offline routers.
    offline_routers = RouterDevice.objects.all_tenants().filter(
        is_active=True, is_online=False
    )

    if not offline_routers.exists():
        return 0

    from billing.router_service import is_router_reachable

    dispatched = 0
    for router in offline_routers:
        # Ask the router itself before moving anybody. is_online was written by
        # a different task up to three minutes ago, and migrating a subscriber
        # costs a reconfiguration on two pieces of hardware — far more than one
        # TCP check. A router that answers here has recovered between the sweep
        # and now, which on a satellite or mobile link is the common case
        # rather than the rare one.
        if is_router_reachable(router):
            router.record_health(True)
            logger.info(
                "[auto-failover] %s answered on re-check — nobody moved.", router)
            continue

        customer_ids = list(
            Customer.objects.all_tenants()
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
        customer = (
            Customer.objects.all_tenants()
            .select_related("router", "tenant")
            .get(id=customer_id)
        )
    except Customer.DoesNotExist:
        logger.warning(f"[auto-failover] Customer {customer_id} not found")
        return

    # Act as the owning operator so router selection and any notification
    # credentials resolve to theirs.
    with tenant_context(customer.tenant_id):
        success, message = migrate_customer_router(customer, reason="auto_failover")

    if success:
        logger.info(f"[auto-failover] Customer {customer_id}: {message}")
    else:
        logger.warning(f"[auto-failover] Customer {customer_id} failed: {message}")

    return {"success": success, "message": message, "customer_id": customer_id}
