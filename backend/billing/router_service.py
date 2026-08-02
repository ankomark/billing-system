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
    users.add(**{
        "name": mac_address,
        "password": "",
        "profile": profile,
        # Hyphenated, like every RouterOS attribute. As limit_uptime this was
        # sent as a word the router does not know, so no hotspot session ever
        # expired on the router — the only thing ending them was our own
        # nightly sweep, and only if it ran.
        "limit-uptime": f"{remaining_seconds}s",
        "comment": "AUTO | WIFI BILLING SYSTEM",
    })
def disable_hotspot(api, mac_address):
    """
    Take a device off the hotspot.

    Removing the user is not enough on its own. RouterOS keeps an established
    session running until it times out of its own accord, so an expired
    customer stayed online — sometimes for hours — while the system recorded
    them as expired and the operator saw them as cut off. The session has to be
    ended as well, which is what ip/hotspot/active is for. PPPoE has always
    done this; hotspot never did.
    """
    if not mac_address:
        return

    # The live session first, so there is no window where the user is gone but
    # the session survives to be re-established.
    try:
        actives = api.path("ip", "hotspot", "active")
        for session in list(actives):
            if session.get("user") == mac_address or session.get("mac-address") == mac_address:
                actives.remove(**{".id": session[".id"]})
    except Exception:
        # An unreachable router is handled by the caller; losing the session
        # kick must not stop the account being removed.
        logger.warning("[hotspot] could not end the live session for %s", mac_address)

    users = api.path("ip", "hotspot", "user")
    for u in users:
        if u.get("name") == mac_address:
            users.remove(**{".id": u[".id"]})
            return
def enable_customer_access(customer):
    """
    Put a paying customer onto working hardware.

    Returns True when access was actually granted, False when it was not.

    The return value matters. This used to return None either way, so a caller
    could not tell the difference between "provisioned" and "no router was
    reachable, nothing happened" — and the caller that mattered was the one
    right after a payment. A customer could pay, have their invoice marked paid
    and their subscription activated, receive an SMS saying their account was
    ready, and have no access at all, with nothing retrying and nobody told.
    """
    subscription = (
        customer.subscriptions.filter(status="active")
        .order_by("-expiry_date")
        .first()
    )
    if not subscription:
        return False

    router, api = pick_working_router(customer)
    if not router or not api:
        logger.warning(f"No router online for {customer.full_name}")
        return False

    # A move, so record it. This wrote only a log line, which meant a customer
    # could be re-homed onto different hardware and Failover Logs — the page an
    # operator opens to ask exactly that — would show nothing.
    # migrate_customer_router has always recorded its moves; this path is the
    # one that happens on its own, so it needed the record more.
    if customer.router_id != router.id:
        from .models import RouterFailoverLog

        previous_id = customer.router_id
        customer.router = router
        customer.save(update_fields=["router"])
        logger.info(f"{customer.full_name} moved to router {router.name}")

        try:
            RouterFailoverLog.objects.create(
                # Explicit, not left to the ambient tenant. This runs from
                # Celery tasks where no middleware has set a context, and the
                # model default would have nothing to resolve — the row would
                # fail and the failure be swallowed by the guard below.
                tenant_id=customer.tenant_id,
                customer=customer,
                from_router_id=previous_id,
                to_router=router,
                reason="auto_recovery",
            )
        except Exception:
            # Losing the record must not cost the customer their connection,
            # which is the whole point of this function.
            logger.exception(
                "[router] could not record the move of %s to %s",
                customer, router,
            )

    package = subscription.package

    if customer.connection_type == "pppoe":
        create_pppoe_secret(api, router, customer, package)
        enable_pppoe(api, router, customer.pppoe_username, package)

    elif customer.connection_type == "hotspot":
        enable_hotspot(api, router, customer.hotspot_username, package, subscription.expiry_date)

    return True


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

    api = safe_connect_router(router)
    if not api:
        return None

    try:
        actives = api.path("ppp", "active")
        for a in actives:
            if a.get("name") == username:
                return {
                    "ip_address": a.get("address"),
                    "uptime": a.get("uptime"),
                    "caller_id": a.get("caller-id"),
                    "router": router.name,
                }
    except Exception:
        return None

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
    enable_customer_access(customer)
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
    from billing.models import RouterEvent

    if not is_router_reachable(router):
        router.record_health(
            False, error="TCP unreachable", cause=RouterEvent.CAUSE_UNREACHABLE)
        return None

    try:
        api = connect(
            host=router.ip_address,
            username=router.username,
            password=router.password,
            port=router.api_port,
            timeout=5,
        )
        router.record_health(True)
        return api

    except (LibRouterosError, TrapError, MultiTrapError, FatalError, ProtocolError) as e:
        # The router answered and refused us, which is a different problem from
        # not being able to reach it — usually wrong credentials or a disabled
        # API service, neither of which a network fix will help.
        router.record_health(False, error=e, cause=RouterEvent.CAUSE_AUTH_FAILED)
        return None

    except Exception as e:
        router.record_health(
            False, error=f"Unknown: {e}", cause=RouterEvent.CAUSE_ERROR)
        return None


# Distinguishes "no station given" from "explicitly no station", which matter
# differently: the first means do not narrow, the second would mean narrow to
# routers with no station at all.
_UNSET = object()


def _tenant_routers(tenant_id, station_id=_UNSET):
    """
    Active routers belonging to one operator, best priority first.

    Always filters explicitly rather than relying on the ambient tenant
    context: these functions run from Celery tasks and management commands
    where no middleware has set it, and an unscoped result would provision a
    subscriber onto another operator's hardware.

    `station_id` narrows further to one site. Left unset it means "anywhere in
    this operator", which is the behaviour for every operator who has not
    divided their estate into sites.

    Passing a station id is not a preference — it is a hard filter. A router in
    Mtwapa cannot carry a subscriber in Kilifi; there is no physical path
    between them. Moving one there does not fail over, it takes the subscriber
    offline while reporting success, which is worse than doing nothing.
    """
    from .models import RouterDevice

    qs = RouterDevice.objects.all_tenants().filter(is_active=True)
    if tenant_id is None:
        raise ValueError(
            "Router selection requires a tenant. Pass a customer or tenant_id — "
            "an unscoped selection can provision onto another operator's router."
        )
    qs = qs.filter(tenant_id=tenant_id)
    if station_id is not _UNSET and station_id is not None:
        qs = qs.filter(station_id=station_id)
    return qs.order_by("priority")


def _station_of(customer):
    """
    Which site a subscriber is served from — the site of the router they are on.

    Derived rather than stored. A second column would be a second source of
    truth that could disagree with the router the subscriber is actually
    provisioned on, and the router is the one that decides whether their
    connection works.

    None means either no router yet or a router with no site, and both mean the
    same thing to the pickers: no narrowing.
    """
    router = getattr(customer, "router", None) if customer else None
    return getattr(router, "station_id", None) if router else None


def pick_working_router(customer=None, tenant_id=None):
    tenant_id = tenant_id or getattr(customer, "tenant_id", None)
    # Stay at the subscriber's own site. Falls back to the whole operator only
    # when they have no site, which is the single-location case.
    routers = list(_tenant_routers(tenant_id, _station_of(customer)))
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
def pick_failover_router(exclude_router_id=None, customer=None, tenant_id=None):

    tenant_id = tenant_id or getattr(customer, "tenant_id", None)
    qs = _tenant_routers(tenant_id, _station_of(customer))
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

    return True

def count_pppoe_sessions(api) -> int:
    """Count active PPPoE sessions on this router."""
    active = api.path("ppp", "active")
    return sum(1 for _ in active)

def pick_best_router_for_new_customer(customer=None, tenant_id=None, station_id=None):
    """
    Least-loaded router for a subscriber who does not have one yet.

    `station_id` steers a new subscriber onto the right site. An admin adding a
    customer in Mtwapa passes it; a walk-up at a captive portal usually cannot,
    because the portal request carries only the operator token — so absent a
    station this behaves exactly as it always has and considers the whole
    estate.

    When the customer already has a router, their existing site wins over the
    argument: re-homing an existing subscriber must not move them towns.
    """
    tenant_id = tenant_id or getattr(customer, "tenant_id", None)
    station = _station_of(customer)
    if station is None and station_id is not None:
        station = station_id
    routers = list(_tenant_routers(tenant_id, station))
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
    # Scan only this operator's routers — never another's
    routers = _tenant_routers(customer.tenant_id)
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
def _sessions_by_user(router, path, name_field, rx_field, tx_field):
    """
    Every live session on one router, keyed by the name it logged in with.

    The per-customer helpers below ask a router for one username, and the only
    way to answer is to read the whole session table and discard the rest. Done
    in a loop over subscribers that is one connection each — and, when the
    assigned router has no session, one connection to every other router the
    operator owns. At a few hundred subscribers that stops fitting in the five
    minutes between runs, and the task is dropped rather than finishing late:
    usage collection quietly stops, caps stop being enforced, and nothing
    reports an error.

    The table costs the same to read whether one name is wanted or a thousand,
    so read it once and match in memory.
    """
    api = safe_connect_router(router)
    if not api:
        return None

    sessions = {}
    try:
        for row in api.path(*path):
            name = row.get(name_field)
            if not name:
                continue
            sessions[name] = {
                "connected": True,
                "ip_address": row.get("address"),
                "uptime": row.get("uptime"),
                "rx_bytes": int(row.get(rx_field, 0) or 0),
                "tx_bytes": int(row.get(tx_field, 0) or 0),
                "interface": row.get("interface"),
            }
    except Exception:
        # An unreadable table is not an empty one. Returning {} would look like
        # every subscriber on this router had disconnected, and the callers
        # treat "not connected" as nothing to record — so a transient fault
        # would silently skip a collection round instead of retrying.
        return None

    return sessions


def get_pppoe_sessions(router):
    """Live PPPoE sessions on one router, keyed by username."""
    return _sessions_by_user(
        router, ("ppp", "active"), "name", "rx-bytes", "tx-bytes")


def get_hotspot_sessions(router):
    """Live hotspot sessions on one router, keyed by login name."""
    return _sessions_by_user(
        router, ("ip", "hotspot", "active"), "user", "bytes-in", "bytes-out")


def tenant_sessions(tenant_id, reader):
    """
    One operator's live sessions across all of their routers.

    Keyed by username, valued with the router the session was found on, which
    is what the usage records store. Scoped to the one operator: matching a
    subscriber against another operator's session table would attribute
    somebody else's traffic to them, and on a platform where two operators can
    both have a "john" that is not hypothetical.

    A router that cannot be read is skipped rather than treated as empty, so a
    subscriber on an unreachable router is left alone instead of being recorded
    as disconnected.
    """
    found = {}
    for router in _tenant_routers(tenant_id):
        sessions = reader(router)
        if sessions is None:
            continue
        for username, data in sessions.items():
            found.setdefault(username, (router, data))
    return found


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
    # fallback scan — this operator's routers only
    routers = _tenant_routers(customer.tenant_id)
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