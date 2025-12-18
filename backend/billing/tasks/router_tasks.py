from celery import shared_task
import logging
from django.utils import timezone

from billing.models import Customer
from billing.router_service import (
    safe_connect_router,
    disconnect_pppoe_session,
    enable_customer_access,
    disable_customer_access,
)

logger = logging.getLogger(__name__)


# =====================================================
# INTERNAL HELPER
# =====================================================

def _mark_router_online(router):
    router.is_online = True
    router.last_seen = timezone.now()
    router.last_error = ""
    router.save(update_fields=["is_online", "last_seen", "last_error"])


def _mark_router_offline(router, error):
    router.is_online = False
    router.last_error = str(error)
    router.save(update_fields=["is_online", "last_error"])


# =====================================================
# PPPoE CONTROL TASKS
# =====================================================

@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=10,
    retry_kwargs={"max_retries": 3},
    retry_jitter=True,
)
def disconnect_pppoe_task(self, customer_id):
    """
    Safely disconnect a PPPoE customer.
    Used by:
    - Admin disconnect
    - Customer self-service
    - Suspension workflows
    """
    customer = Customer.objects.select_related("router").get(id=customer_id)

    if customer.connection_type != "pppoe":
        logger.info(f"[disconnect_pppoe_task] Customer {customer_id} not PPPoE")
        return False

    if not customer.router:
        logger.warning(f"[disconnect_pppoe_task] No router assigned to {customer_id}")
        return False

    router = customer.router

    try:
        api = safe_connect_router(router)
        if not api:
            raise ConnectionError("Router unreachable")

        disconnect_pppoe_session(api, customer.pppoe_username)
        _mark_router_online(router)

        logger.info(f"[disconnect_pppoe_task] PPPoE disconnected for customer {customer_id}")
        return True

    except Exception as e:
        _mark_router_offline(router, e)
        logger.error(f"[disconnect_pppoe_task] Failed for {customer_id}: {e}")
        raise


# =====================================================
# CUSTOMER ACCESS CONTROL TASKS
# =====================================================

@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=15,
    retry_kwargs={"max_retries": 3},
    retry_jitter=True,
)
def enable_customer_task(self, customer_id):
    """
    Enable internet access for a customer (PPPoE or Hotspot).
    Used after:
    - Successful payment
    - Resume
    - Renewal
    """
    customer = Customer.objects.select_related("router").get(id=customer_id)

    if not customer.router:
        logger.warning(f"[enable_customer_task] No router for customer {customer_id}")
        return False

    router = customer.router

    try:
        enable_customer_access(customer)
        _mark_router_online(router)

        logger.info(f"[enable_customer_task] Access enabled for customer {customer_id}")
        return True

    except Exception as e:
        _mark_router_offline(router, e)
        logger.error(f"[enable_customer_task] Failed for {customer_id}: {e}")
        raise


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=15,
    retry_kwargs={"max_retries": 3},
    retry_jitter=True,
)
def disable_customer_task(self, customer_id):
    """
    Disable internet access for a customer.
    Used for:
    - Suspension
    - Expiry
    - Admin action
    """
    customer = Customer.objects.select_related("router").get(id=customer_id)

    if not customer.router:
        logger.warning(f"[disable_customer_task] No router for customer {customer_id}")
        return False

    router = customer.router

    try:
        disable_customer_access(customer)
        _mark_router_online(router)

        logger.info(f"[disable_customer_task] Access disabled for customer {customer_id}")
        return True

    except Exception as e:
        _mark_router_offline(router, e)
        logger.error(f"[disable_customer_task] Failed for {customer_id}: {e}")
        raise
