import logging
from celery import shared_task

from billing.models import Customer, RouterDevice
from billing.router_service import migrate_customer_router
from billing.tenancy import tenant_context

logger = logging.getLogger(__name__)


# How long a dispatched re-check stays worth running.
#
# Matches the expires on the beat entry that fans these out. The re-check
# exists to establish whether a router is down *now*; one that has waited
# longer than the gap between failover runs is answering a question the next
# run is about to ask again with fresher information.
RECHECK_EXPIRES_SECONDS = 150


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_kwargs={"max_retries": 2},
)
def run_auto_failover_task(self):
    """
    Detect offline routers and dispatch one re-check per router.

    Migrations were already fanned out — running them sequentially blocked a
    single worker for minutes when many customers needed moving. The re-check
    above them was not, and it is the same shape of problem: every offline
    router is asked in turn, and each one that is genuinely down costs the full
    three-second timeout inside is_router_reachable before the next is tried.
    An outage that takes twenty routers off at once is a minute of serial
    waiting, inside a task that carries expires=150 — so the run after it is
    discarded and failover is not retried but skipped.

    Fanned out, the re-check costs three seconds once per router rather than
    three seconds of every other router's budget.
    """
    # Cross-operator sweep by design — every operator's offline routers.
    router_ids = list(
        RouterDevice.objects.all_tenants()
        .filter(is_active=True, is_online=False)
        .values_list("id", flat=True)
    )

    for router_id in router_ids:
        recheck_offline_router_task.apply_async(
            (router_id,), expires=RECHECK_EXPIRES_SECONDS)

    logger.info("[auto-failover] re-checking %s offline router(s)",
                len(router_ids))
    return len(router_ids)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_kwargs={"max_retries": 2},
)
def recheck_offline_router_task(self, router_id: int):
    """
    Confirm one router is really down, then dispatch its customers' migrations.
    """
    from billing.router_service import is_router_reachable

    router = RouterDevice.objects.all_tenants().filter(id=router_id).first()
    if router is None:
        return 0

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
        return 0

    customer_ids = list(
        Customer.objects.all_tenants()
        .filter(router=router, status="active")
        .values_list("id", flat=True)
    )
    for cid in customer_ids:
        migrate_single_customer_task.delay(cid)

    logger.info("[auto-failover] Dispatched migration for %s customer(s) off %s",
                len(customer_ids), router)
    return len(customer_ids)


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
