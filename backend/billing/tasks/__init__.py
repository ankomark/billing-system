from .router_health import check_router_health_task
from .auto_failover import run_auto_failover_task


def run_failover_cycle():
    """
    Synchronous failover cycle for the management command.
    In production this runs via Celery beat; here it's invoked directly.
    """
    from billing.models import RouterDevice
    from billing.router_service import safe_connect_router, migrate_customer_router
    from billing.models import Customer
    import logging
    logger = logging.getLogger(__name__)

    # Health check
    routers = RouterDevice.objects.filter(is_active=True)
    for router in routers:
        api = safe_connect_router(router)
        status = "ONLINE" if api else "OFFLINE"
        logger.info(f"[failover-cmd] {router.name} {status}")

    # Migrate customers off offline routers
    offline = RouterDevice.objects.filter(is_active=True, is_online=False)
    for router in offline:
        customers = Customer.objects.filter(router=router, status="active")
        for customer in customers:
            success, msg = migrate_customer_router(customer, reason="admin_manual")
            logger.info(f"[failover-cmd] {customer.full_name}: {msg}")
