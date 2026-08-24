from celery import shared_task
import logging
from django.utils import timezone

from billing.models import Customer
from billing.tenancy import tenant_context
from billing.router_service import (
    safe_connect_router,
    disconnect_pppoe_session,
    enable_customer_access,
    disable_customer_access,
)

logger = logging.getLogger(__name__)


# =====================================================
# INTERNAL HELPERS
# =====================================================

def _mark_router_online(router):
    # Through record_health so the transition is logged. These used to write
    # is_online directly, which is why a router could go down and come back with
    # nothing recording that it had.
    router.record_health(True)


def _mark_router_offline(router, error):
    router.record_health(False, error=error)


def _load_customer(customer_id):
    """
    Load by primary key without tenant scoping.

    A worker has no request, so nothing has set the tenant context yet — the
    row itself is what tells us which operator we are acting for. The caller
    then enters that operator's context before touching routers or credentials.
    """
    return (
        Customer.objects.all_tenants()
        .select_related("router", "tenant")
        .get(id=customer_id)
    )


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
    customer = _load_customer(customer_id)

    if customer.connection_type != "pppoe":
        logger.info(f"[disconnect_pppoe_task] Customer {customer_id} not PPPoE")
        return False

    if not customer.router:
        logger.warning(f"[disconnect_pppoe_task] No router assigned to {customer_id}")
        return False

    router = customer.router

    with tenant_context(customer.tenant_id):
        try:
            api = safe_connect_router(router)
            if not api:
                raise ConnectionError("Router unreachable")

            disconnect_pppoe_session(api, customer.pppoe_username)
            _mark_router_online(router)

            logger.info(
                f"[disconnect_pppoe_task] PPPoE disconnected for customer {customer_id}"
            )
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
    customer = _load_customer(customer_id)

    if not customer.router:
        logger.warning(f"[enable_customer_task] No router for customer {customer_id}")
        return False

    router = customer.router

    with tenant_context(customer.tenant_id):
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
    customer = _load_customer(customer_id)

    if not customer.router:
        logger.warning(f"[disable_customer_task] No router for customer {customer_id}")
        return False

    router = customer.router

    with tenant_context(customer.tenant_id):
        try:
            disable_customer_access(customer)
            _mark_router_online(router)

            logger.info(f"[disable_customer_task] Access disabled for customer {customer_id}")
            return True

        except Exception as e:
            _mark_router_offline(router, e)
            logger.error(f"[disable_customer_task] Failed for {customer_id}: {e}")
            raise


# =====================================================
# DEVICE CONTROL TASKS
# =====================================================

@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    # Longer and more patient than the tasks above, because what this is
    # waiting for is different. Those retry a router that is briefly busy;
    # this one retries a router that was *down* when an operator blocked a
    # handset, and a link that drops does not always come back inside a
    # minute. Roughly 30s, 1m, 2m, 4m, 8m then 10m apart — about an hour of
    # trying before it gives up.
    retry_backoff=30,
    retry_backoff_max=600,
    retry_kwargs={"max_retries": 10},
    retry_jitter=True,
)
def kick_device_task(self, customer_id, mac_address):
    """
    Keep trying to take one device off the hardware.

    Blocking a device wrote the block immediately and attempted the
    disconnect exactly once. When no router answered that attempt, the
    operator was told so and nothing ever tried again — and an established
    hotspot session does not end on its own until `limit-uptime` runs out,
    which enable_hotspot sets to whatever is left of the subscription. On a
    monthly package that is days of a handset staying online after it was
    blocked, with no sweep anywhere that would notice.

    So the block itself stays synchronous — the operator gets an immediate,
    honest answer about what was reached — and this carries the part that
    needs to outlive the request.

    Deliberately does not touch router health. Marking a router offline from
    here would let an hour of device-kick retries feed the consecutive-failure
    count that auto-failover migrates subscribers on, which is a much bigger
    action than this task is entitled to take. check_router_health_task
    already tracks that, on its own schedule and its own evidence.
    """
    from billing.router_service import (
        _tenant_routers, connect_router, disable_hotspot,
    )

    customer = _load_customer(customer_id)

    with tenant_context(customer.tenant_id):
        routers = list(_tenant_routers(customer.tenant_id))
        if not routers:
            logger.warning(
                "[kick_device_task] No active router for customer %s, so "
                "there is nothing holding %s online", customer_id, mac_address)
            return False

        unfinished = []
        for router in routers:
            try:
                api = connect_router(router)
                if disable_hotspot(api, mac_address):
                    logger.info(
                        "[kick_device_task] %s is off %s", mac_address, router)
                else:
                    unfinished.append(str(router))
            except Exception as exc:
                logger.warning(
                    "[kick_device_task] could not reach %s to drop %s: %s",
                    router, mac_address, exc)
                unfinished.append(str(router))

        if unfinished:
            # Raised so autoretry_for sees it. A return would look like
            # success and end the retries with the device still connected.
            raise RuntimeError(
                f"could not confirm {mac_address} is off "
                f"{', '.join(unfinished)}"
            )

        return True
