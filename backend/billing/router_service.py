from librouteros import connect
from django.utils import timezone
from django.db.models import Q
import socket
from .router_profiles import ensure_pppoe_profile, ensure_hotspot_profile
from django.db import transaction
import logging
logger = logging.getLogger(__name__)
def connect_router(router):
    """Connect to MikroTik Router."""
    return connect(
        host=router.ip_address,
        username=router.username,
        password=router.password,
        port=router.api_port,
    )
def create_pppoe_secret(api, router, customer, package):
    if not customer.pppoe_username or not customer.pppoe_password:
        return

    profile = ensure_pppoe_profile(router, package)
    secrets = api.path("ppp", "secret")

    for s in secrets:
        if s.get("name") == customer.pppoe_username:
            return

    secrets.add(
        name=customer.pppoe_username,
        password=customer.pppoe_password,
        service="pppoe",
        profile=profile,
        comment="AUTO | WIFI BILLING SYSTEM",
    )
def enable_pppoe(api, router, username, package):
    if not username:
        return

    profile = ensure_pppoe_profile(router, package)
    secrets = api.path("ppp", "secret")

    for s in secrets:
        if s.get("name") == username:
            secrets.update(**{".id": s[".id"], "disabled": "no", "profile": profile})
            return
def disable_pppoe(api, username):
    if not username:
        return

    secrets = api.path("ppp", "secret")
    for s in secrets:
        if s.get("name") == username:
            secrets.update(**{".id": s[".id"], "disabled": "yes"})
            return
def enable_hotspot(api, router, mac_address, package, expiry_date):
    if not mac_address:
        return
    profile = ensure_hotspot_profile(router, package)
    users = api.path("ip", "hotspot", "user")

    remaining_seconds = max(int((expiry_date - timezone.now()).total_seconds()), 60)

    for u in users:
        if u.get("name") == mac_address:
            users.remove(**{".id": u[".id"]})
    users.add(
        name=mac_address,
        password="",
        profile=profile,
        limit_uptime=f"{remaining_seconds}s",
        comment="AUTO | WIFI BILLING SYSTEM",
    )
def disable_hotspot(api, mac_address):
    if not mac_address:
        return
    users = api.path("ip", "hotspot", "user")
    for u in users:
        if u.get("name") == mac_address:
            users.remove(**{".id": u[".id"]})
            return
def enable_customer_access(customer):
    subscription = (
        customer.subscriptions.filter(status="active")
        .order_by("-expiry_date")
        .first()
    )
    if not subscription:
        return

    router, api = pick_working_router(customer)
    if not router or not api:
        logger.warning(f"No router online for {customer.full_name}")
        return

    # ✅ IMPORTANT: if failover router differs, update customer.router
    if customer.router_id != router.id:
        customer.router = router
        customer.save(update_fields=["router"])
        logger.info(f"{customer.full_name} moved to router {router.name}")

    package = subscription.package

    if customer.connection_type == "pppoe":
        create_pppoe_secret(api, router, customer, package)
        enable_pppoe(api, router, customer.pppoe_username, package)

    elif customer.connection_type == "hotspot":
        enable_hotspot(api, router, customer.hotspot_username, package, subscription.expiry_date)


def disable_customer_access(customer):
    if not customer.router:
        return

    api = safe_connect_router(customer.router)
    if not api:
        return

    if customer.connection_type == "pppoe":
        disable_pppoe(api, customer.pppoe_username)
    elif customer.connection_type == "hotspot":
        disable_hotspot(api, customer.hotspot_username)
def get_pppoe_live_usage(router, username):
    """
    Fetch live PPPoE session stats from MikroTik
    """
    if not router or not username:
        return None

    api = safe_connect_router(router)  # ← Use safe_connect_router instead of connect_router
    if not api:
        return None

    try:
        active = api.path("ppp", "active")
        for session in active:
            if session.get("name") == username:
                return {
                    "connected": True,
                    "ip_address": session.get("address"),
                    "uptime": session.get("uptime"),
                    "rx_bytes": int(session.get("rx-bytes", 0)),
                    "tx_bytes": int(session.get("tx-bytes", 0)),
                    "interface": session.get("interface"),
                }
    except Exception:
        return None

    return {"connected": False}  
def disconnect_pppoe_session(api, username):
    if not username:
        return

    active = api.path("ppp", "active")
    for s in active:
        if s.get("name") == username:
            active.remove(**{".id": s[".id"]})
            return

def get_pppoe_usage(router, username):
    """
    Returns live PPPoE session info from MikroTik
    """
    if not router or not username:
        return None

    api = connect_router(router)
    actives = api.path("ppp", "active")

    for a in actives:
        if a.get("name") == username:
            return {
                "ip_address": a.get("address"),
                "uptime": a.get("uptime"),
                "caller_id": a.get("caller-id"),
                "router": router.name,
            }

    return None  # not connected
def disconnect_pppoe_user(customer):
    if not customer.router or not customer.pppoe_username:
        return
    api = safe_connect_router(customer.router)
    if not api:
        return
    disconnect_pppoe_session(api, customer.pppoe_username)


def reconnect_pppoe_user(customer):
    """
    Reconnect PPPoE (disconnect + allow reconnect)
    """
    if not customer.router or not customer.pppoe_username:
        return

    api = safe_connect_router(customer.router)  
    if not api:
        return
        
    disconnect_pppoe_session(api, customer.pppoe_username)
def get_all_pppoe_sessions(router):
    """
    Fetch all active PPPoE sessions from MikroTik
    """
    api = connect_router(router)
    active = api.path("ppp", "active")

    sessions = []

    for s in active:
        sessions.append({
            "username": s.get("name"),
            "ip_address": s.get("address"),
            "uptime": s.get("uptime"),
            "rx_bytes": int(s.get("rx-bytes", 0)),
            "tx_bytes": int(s.get("tx-bytes", 0)),
            "interface": s.get("interface"),
            "caller_id": s.get("caller-id"),
        })

    return sessions
from librouteros.exceptions import (
    LibRouterosError,
    TrapError,
    MultiTrapError,
    FatalError,
    ProtocolError,
)
def is_router_reachable(router, timeout=3) -> bool:
    try:
        sock = socket.create_connection((router.ip_address, router.api_port), timeout=timeout)
        sock.close()
        return True
    except OSError:
        return False
    
    
def safe_connect_router(router):
    """
    Connect safely. Returns API object or None.
    Also updates router health in DB.
    """
    if not is_router_reachable(router):
        router.is_online = False
        router.last_error = "TCP unreachable"
        router.save(update_fields=["is_online", "last_error"])
        return None

    try:
        api = connect(
            host=router.ip_address,
            username=router.username,
            password=router.password,
            port=router.api_port,
            timeout=5,
        )
        router.is_online = True
        router.last_seen = timezone.now()
        router.last_error = ""
        router.save(update_fields=["is_online", "last_seen", "last_error"])
        return api

    except (LibRouterosError, TrapError, MultiTrapError, FatalError, ProtocolError) as e:
        router.is_online = False
        router.last_error = str(e)
        router.save(update_fields=["is_online", "last_error"])
        return None

    except Exception as e:
        router.is_online = False
        router.last_error = f"Unknown: {e}"
        router.save(update_fields=["is_online", "last_error"])
        return None


def pick_working_router(customer=None):
    from .models import RouterDevice  # local import avoids app-loading issues
    routers = list(RouterDevice.objects.filter(is_active=True).order_by("priority"))
    # Try assigned router first
    if customer and getattr(customer, "router_id", None):
        assigned = next((r for r in routers if r.id == customer.router_id), None)
        if assigned:
            api = safe_connect_router(assigned)
            if api:
                return assigned, api

    # Fallback priority order
    for r in routers:
        api = safe_connect_router(r)
        if api:
            return r, api

    return None, None
def pick_failover_router(exclude_router_id=None, customer=None):
   
    from .models import RouterDevice

    qs = RouterDevice.objects.filter(is_active=True).order_by("priority")
    if exclude_router_id:
        qs = qs.exclude(id=exclude_router_id)

    for r in qs:
        api = safe_connect_router(r)
        if api:
            return r, api

    return None, None
def provision_customer_on_router(api, router, customer, subscription):
   
    package = subscription.package

    if customer.connection_type == "pppoe":
        create_pppoe_secret(api, router, customer, package)
        enable_pppoe(api, router, customer.pppoe_username, package)

    elif customer.connection_type == "hotspot":
        enable_hotspot(api, router, customer.hotspot_username, package, subscription.expiry_date)

def count_pppoe_sessions(api) -> int:
    """Count active PPPoE sessions on this router."""
    active = api.path("ppp", "active")
    return sum(1 for _ in active)

def pick_best_router_for_new_customer(customer=None):
    
    from .models import RouterDevice

    routers = list(RouterDevice.objects.filter(is_active=True).order_by("priority"))
    candidates = []

    for r in routers:
        api = safe_connect_router(r)
        if not api:
            continue

        # load metric (PPPoE sessions); you can extend for hotspot too
        pppoe_load = 0
        try:
            pppoe_load = count_pppoe_sessions(api)
        except Exception:
            # if we fail to read sessions, skip this router
            continue

        # capacity rule (optional)
        if r.max_pppoe_sessions and pppoe_load >= r.max_pppoe_sessions:
            continue

        candidates.append((pppoe_load, r.priority, r, api))

    if not candidates:
        return None, None

    candidates.sort(key=lambda x: (x[0], x[1]))
    _, _, router, api = candidates[0]
    return router, api


from .models import RouterFailoverLog

def migrate_customer_router(customer, reason="manual_migration"):
    # --------------------------------------------------
    # 1️⃣ Validate active subscription
    # --------------------------------------------------
    subscription = (
        customer.subscriptions
        .filter(status="active")
        .order_by("-expiry_date")
        .first()
    )
    if not subscription:
        return False, "No active subscription"

    old_router = customer.router

    # --------------------------------------------------
    # 2️⃣ Pick best router (load-balanced + online)
    # --------------------------------------------------
    new_router, new_api = pick_best_router_for_new_customer(customer)
    if not new_router or not new_api:
        return False, "No router online for migration"

    if old_router and new_router.id == old_router.id:
        return False, "Customer already on optimal router"

    package = subscription.package

    if customer.connection_type == "pppoe":
        create_pppoe_secret(new_api, new_router, customer, package)
        enable_pppoe(new_api, new_router, customer.pppoe_username, package)

    elif customer.connection_type == "hotspot":
        enable_hotspot(
            new_api,
            new_router,
            customer.hotspot_username,
            package,
            subscription.expiry_date,
        )

    else:
        return False, "Unsupported connection type"

    if old_router and customer.connection_type == "pppoe":
        old_api = safe_connect_router(old_router)
        if old_api:
            try:
                disconnect_pppoe_session(old_api, customer.pppoe_username)
            except Exception:
                pass  # do not fail migration

    with transaction.atomic():
        customer.router = new_router
        customer.save(update_fields=["router"])

    RouterFailoverLog.objects.create(
    customer=customer,
    from_router=old_router,
    to_router=new_router,
    reason=reason,
    )

    return True, f"Migrated to {new_router.name}"


def get_pppoe_live_usage_any_router(customer):
    """
    Try assigned router first; if no session found, scan other online routers.
    """
    from .models import RouterDevice

    username = customer.pppoe_username
    if not username:
        return None, None

    # 1) assigned first
    if customer.router_id:
        router = customer.router
        api = safe_connect_router(router)  
        if api:
            data = get_pppoe_live_usage(router, username)
            if data and data.get("connected"):
                return router, data
    routers = RouterDevice.objects.filter(is_active=True).order_by("priority")
    for r in routers:
        if customer.router_id and r.id == customer.router_id:
            continue
        api = safe_connect_router(r)  
        if not api:
            continue
        data = get_pppoe_live_usage(r, username)
        if data and data.get("connected"):
            return r, data
    return None, {"connected": False}
def get_hotspot_live_usage(router, username):
    api = safe_connect_router(router)
    if not api:
        return None
    actives = api.path("ip", "hotspot", "active")
    for a in actives:
        if a.get("user") == username:
            return {
                "connected": True,
                "rx_bytes": int(a.get("bytes-in", 0)),
                "tx_bytes": int(a.get("bytes-out", 0)),
                "uptime": a.get("uptime"),
                "ip_address": a.get("address"),
            }

    return {"connected": False}
def get_hotspot_live_usage_any_router(customer):
    from .models import RouterDevice

    username = customer.hotspot_username
    if not username:
        return None, None
    # assigned router first
    if customer.router_id:
        r = customer.router
        data = get_hotspot_live_usage(r, username)
        if data and data.get("connected"):
            return r, data
    # fallback scan
    routers = RouterDevice.objects.filter(is_active=True).order_by("priority")
    for r in routers:
        data = get_hotspot_live_usage(r, username)
        if data and data.get("connected"):
            return r, data

    return None, {"connected": False}
def safe_disconnect_pppoe(customer):
    if not customer.router or not customer.pppoe_username:
        return False

    api = safe_connect_router(customer.router)
    if not api:
        return False

    disconnect_pppoe_session(api, customer.pppoe_username)
    return True