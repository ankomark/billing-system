"""
Celery task package.

IMPORTANT: `app.autodiscover_tasks()` imports `billing.tasks` and nothing
deeper, so a task is only registered on a worker if this module imports it.
Previously only router_health and auto_failover were imported here, which left
the other seven modules unregistered — beat dispatched
enforce_subscription_expiry, send_expiry_reminders and
collect_pppoe_usage_snapshots on schedule and workers rejected them as
"Received unregistered task of type ...". Every `.delay()` from the web layer
(disable_customer_task, notify_customer_task, initiate_stk_push_task, ...)
failed the same way, because a worker process never imports billing.views.

Add any new task module to this list.
"""

from .alert_tasks import notify_admin_task
from .auto_failover import migrate_single_customer_task, run_auto_failover_task
from .mpesa_tasks import initiate_stk_push_task
from .notification_tasks import (
    dispatch_broadcast_task,
    notify_customer_task,
    send_sms_task,
    send_whatsapp_task,
)
from .reminder_tasks import send_expiry_reminders
from .router_health import check_router_health_task
from .router_tasks import (
    disable_customer_task,
    disconnect_pppoe_task,
    enable_customer_task,
)
from .subscription_tasks import enforce_subscription_expiry
from .usage_tasks import collect_pppoe_usage_snapshots, enforce_usage_caps

__all__ = [
    "notify_admin_task",
    "migrate_single_customer_task",
    "run_auto_failover_task",
    "initiate_stk_push_task",
    "dispatch_broadcast_task",
    "notify_customer_task",
    "send_sms_task",
    "send_whatsapp_task",
    "send_expiry_reminders",
    "check_router_health_task",
    "disable_customer_task",
    "disconnect_pppoe_task",
    "enable_customer_task",
    "enforce_subscription_expiry",
    "collect_pppoe_usage_snapshots",
    "enforce_usage_caps",
    "run_failover_cycle",
]


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
