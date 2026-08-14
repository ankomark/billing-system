from librouteros import connect
from django.utils import timezone
from django.db.models import Q
import re
import socket
from .router_profiles import ensure_pppoe_profile, ensure_hotspot_profile
from .utils import normalize_mac
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

    # Compared canonically, like every other address comparison against a
    # router. active_hotspot_macs says in its own docstring that RouterOS is
    # inconsistent about the case it reports addresses in, and this loop is
    # what stops a second user being added under the same name: a miss here
    # leaves the stale one behind and the add below either duplicates it or is
    # refused, and a refusal is a customer who has paid and is not provisioned.
    wanted = normalize_mac(mac_address)

    for u in users:
        if normalize_mac(u.get("name")) == wanted:
            # Positional. librouteros' Path.remove takes ids as *args, so a
            # `.id` keyword raises TypeError — a Python error, not a router
            # error, so nothing that guards against a router being unreachable
            # catches it.
            #
            # Every removal in this codebase was written the other way, and the
            # damage was not limited to re-issuing a voucher. disable_hotspot
            # removes the user and ends the session through the same call, so
            # nobody was ever disconnected when their time ran out: expiry
            # updated the database, reported success, and left the customer
            # online indefinitely.
            users.remove(u[".id"])
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

    # Canonical on both sides. These were exact string comparisons against
    # whatever RouterOS happened to report, and a miss here does not fail
    # loudly — it silently does nothing:
    #
    #   * the session survives, so an expired customer stays online, and an
    #     evicted device keeps a live session. That last one compounds: it then
    #     shows in active_hotspot_macs forever, so it never reads as idle, is
    #     never evictable again, and its owner's other phone is refused with
    #     "this code is connected on another device" permanently.
    #   * the user survives, so the account is never actually removed.
    wanted = normalize_mac(mac_address)

    # The live session first, so there is no window where the user is gone but
    # the session survives to be re-established.
    try:
        actives = api.path("ip", "hotspot", "active")
        for session in list(actives):
            if wanted in (normalize_mac(session.get("user")),
                          normalize_mac(session.get("mac-address"))):
                actives.remove(session[".id"])
    except Exception:
        # An unreachable router is handled by the caller; losing the session
        # kick must not stop the account being removed.
        logger.warning("[hotspot] could not end the live session for %s", mac_address)

    users = api.path("ip", "hotspot", "user")
    for u in users:
        if normalize_mac(u.get("name")) == wanted:
            users.remove(u[".id"])
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
        _grant_hotspot(api, router, customer, package, subscription.expiry_date)

    return True


# RouterOS writes durations as "1d2h3m4s", dropping any unit that is zero.
_ROS_DURATION_RE = re.compile(
    r"(?:(\d+)w)?(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")
_ROS_DURATION_UNITS = (604800, 86400, 3600, 60, 1)


def ros_duration_seconds(value):
    """
    A RouterOS duration as a number of seconds, or None if it is not one.

    None rather than 0 for anything unparseable, because the caller treats
    "idle for this long" as evidence and no evidence must not read as an idle
    time of zero — that would be the safe answer inverted.
    """
    raw = (value or "").strip().lower()
    if not raw:
        return None

    # Some builds report a plain integer of seconds, and some report h:mm:ss.
    if raw.isdigit():
        return int(raw)
    if ":" in raw:
        parts = raw.split(":")
        if not all(p.isdigit() for p in parts) or len(parts) > 3:
            return None
        total = 0
        for part in parts:
            total = total * 60 + int(part)
        return total

    match = _ROS_DURATION_RE.fullmatch(raw)
    if not match or not any(match.groups()):
        return None
    return sum(
        int(group) * unit
        for group, unit in zip(match.groups(), _ROS_DURATION_UNITS)
        if group
    )


def active_hotspot_macs(router, max_idle_seconds=None):
    """
    Which addresses have a live hotspot session on this router right now.

    Returns None when the router could not be asked — deliberately not an
    empty set. "Nobody is online" and "I could not find out" lead to opposite
    decisions: the first frees a device slot, the second must not, or an
    unreachable router would silently hand every customer unlimited devices.

    Upper-cased, because RouterOS is inconsistent about the case it reports
    addresses in and a comparison that misses is a slot freed by mistake.

    `max_idle_seconds` drops sessions that have been idle at least that long.
    A hotspot session is not ended by the phone walking away — RouterOS keeps
    it until its own idle-timeout fires, and plenty of operators have that
    turned off — so "has a session" and "is using the connection" are not the
    same question. Counting the first as the second is how a customer who did
    disconnect is told to disconnect: their own dead session holds the only
    place their package allows, and it holds it until the package expires.

    Left None, every session counts, which is what the callers that ask "is
    this device reachable at all" want.
    """
    api = safe_connect_router(router)
    if api is None:
        return None

    macs = set()
    try:
        for session in api.path("ip", "hotspot", "active"):
            if max_idle_seconds is not None:
                idle = ros_duration_seconds(session.get("idle-time"))
                # An unreadable idle time keeps the session — see
                # ros_duration_seconds. Only a number we trust frees a place.
                if idle is not None and idle >= max_idle_seconds:
                    continue
            # Hotspot users here are named for the MAC, but the session also
            # carries the address it was seen from; take either.
            for key in ("user", "mac-address"):
                value = (session.get(key) or "").strip().upper()
                if value:
                    macs.add(value)
    except Exception as exc:
        logger.warning("[hotspot] could not list active sessions on %s: %s",
                       router, exc)
        return None
    return macs


def hotspot_macs_for(customer, *, include_blocked=True):
    """
    Every device address belonging to this customer.

    `customer.hotspot_username` holds the *first* device only. Once packages
    grew a `max_devices`, the rest went into CustomerDevice rows, and this one
    field stopped describing a customer's access — it describes one phone out
    of however many they bought.

    `include_blocked` is the difference between the two callers. Granting
    access must skip a blocked device, or blocking it would achieve nothing.
    Removing access must not: blocking is refused at redemption and never
    reaches the router, so a device blocked while already connected keeps its
    session until the uptime limit fires unless something takes it off.
    """
    from .models import CustomerDevice

    devices = (
        CustomerDevice.objects.all_tenants()
        .filter(tenant_id=customer.tenant_id, customer=customer)
    )
    if not include_blocked:
        devices = devices.filter(blocked=False)

    macs = []
    seen = set()
    for mac in [
        *devices.values_list("mac_address", flat=True),
        # Last, and only if not already accounted for: subscribers bound before
        # the device table existed have this and no device row.
        (customer.hotspot_username or "").strip(),
    ]:
        mac = (mac or "").strip()
        # Keyed canonically so one phone written two ways is provisioned once.
        key = normalize_mac(mac)
        if mac and key not in seen:
            seen.add(key)
            macs.append(mac)
    return macs


def disable_customer_access(customer):
    """
    Take a customer off the network — all of them, not one of them.

    This disabled `customer.hotspot_username` alone, which is the first device
    and nothing else — the mirror of enable_customer_access only granting to
    that one. Fixing the grant without fixing this would have been worse than
    leaving both: devices two and three would have been provisioned at
    redemption and then never removed at expiry, which is not a stale row, it
    is unmetered internet.

    Every device is attempted even if one fails. A router that rejects one
    removal must not leave the rest connected — that turns a partial failure
    into free access, silently.
    """
    if not customer.router:
        return

    api = safe_connect_router(customer.router)
    if not api:
        return

    if customer.connection_type == "pppoe":
        disable_pppoe(api, customer.pppoe_username)
        return

    if customer.connection_type == "hotspot":
        failed = []
        for mac in hotspot_macs_for(customer):
            try:
                disable_hotspot(api, mac)
            except Exception as exc:
                failed.append(mac)
                logger.warning(
                    "[hotspot] could not disable %s for customer %s: %s",
                    mac, customer.pk, exc,
                )
        if failed:
            # Raised so the caller's retry sees it. disable_customer_task marks
            # the router offline and re-raises, which is what gets this looked
            # at rather than left in a log nobody reads.
            raise RuntimeError(
                f"could not disable {len(failed)} device(s) for customer "
                f"{customer.pk}: {', '.join(failed)}"
            )


def _grant_hotspot(api, router, customer, package, expiry_date):
    """
    Put every one of a customer's devices onto the router.

    enable_hotspot creates a user named for one MAC, and all three callers
    passed `customer.hotspot_username` — the first device. So a package sold as
    good for three phones provisioned exactly one: the other two got a
    CustomerDevice row, counted against the limit, were told they were
    accepted, and had no account on the hardware to log in with.

    Returns how many were granted, so a caller can tell "provisioned nothing"
    from "provisioned everything".
    """
    granted = 0
    for mac in hotspot_macs_for(customer, include_blocked=False):
        enable_hotspot(api, router, mac, package, expiry_date)
        granted += 1
    return granted
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
            active.remove(s[".id"])
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
        _grant_hotspot(api, router, customer, package, subscription.expiry_date)

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
        _grant_hotspot(
            new_api, new_router, customer, package, subscription.expiry_date)

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
    wanted = normalize_mac(username)
    for a in actives:
        # A hotspot user is named for the device address, so this is the same
        # comparison as everywhere else here. Missing it reported a connected
        # subscriber as offline, with no usage.
        if normalize_mac(a.get("user")) == wanted:
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
def unreachable_by_policy(host):
    """
    Why the platform must not dial this address, or "" if it may.

    Every other connection in this file goes to a router an administrator
    entered. The credential test does not: it dials whatever an operator typed,
    before anything is saved, which turns it into a way to ask the platform's
    own server to open connections on the caller's behalf. Most of what that
    could reach is uninteresting, but not all of it — 169.254.169.254 is the
    cloud metadata service, and loopback is every internal service the platform
    runs without authentication because it believed nothing outside could reach
    it.

    Private ranges stay allowed. Operators reach their hardware over a VPN or a
    management VLAN, and 192.168.88.1 is the address a MikroTik ships with —
    refusing those would refuse the normal case to prevent the odd one.
    """
    import ipaddress

    try:
        addr = ipaddress.ip_address(str(host).strip())
    except ValueError:
        return "That does not look like an IP address."

    if addr.is_loopback:
        return "Loopback addresses point at the platform's own server, not your router."
    if addr.is_link_local:
        return "Link-local addresses are not reachable from the platform."
    if addr.is_multicast or addr.is_reserved or addr.is_unspecified:
        return "That address cannot belong to a router."
    return ""


def probe_credentials(host, username, password, port=8728, timeout=4):
    """
    Ask a MikroTik whether these credentials work, and who it is.

    For the form where an operator registers their own hardware. It answers the
    question they actually have — "did I type this right?" — at the moment they
    can still fix it, rather than leaving them to find out when a subscriber
    fails to be provisioned hours later.

    Never raises: every outcome is a dict the caller can show. The distinction
    that matters is `reachable` versus `authenticated`. A box that refuses the
    login is a password problem; one that never answers is a firewall, a wrong
    address, or an API service that was never enabled — and telling an operator
    to check their password when the real problem is a closed port sends them
    looking in the wrong place.

    Bounded deliberately: one host, two short timeouts. This runs inside a
    request, and the reason the router list does not probe is that N routers
    times a timeout each holds a worker for as long as that multiplies out.
    """
    refusal = unreachable_by_policy(host)
    if refusal:
        return {"reachable": False, "authenticated": False, "error": refusal,
                "identity": "", "serial": ""}

    result = {"reachable": False, "authenticated": False, "error": "",
              "identity": "", "serial": ""}

    try:
        sock = socket.create_connection((str(host), int(port)), timeout=timeout)
        sock.close()
        result["reachable"] = True
    except OSError as e:
        result["error"] = (
            f"Could not reach {host} on port {port}. Check the address, that the "
            f"router's API service is enabled, and that its firewall allows this "
            f"platform. ({e})"
        )
        return result

    try:
        api = connect(host=str(host), username=username, password=password,
                      port=int(port), timeout=timeout + 1)
    except (LibRouterosError, TrapError, MultiTrapError, FatalError, ProtocolError) as e:
        # It answered and said no. Almost always the username or password, or
        # an API user without the permissions this platform needs.
        result["error"] = f"The router refused these credentials. ({e})"
        return result
    except Exception as e:
        result["error"] = f"Could not log in to the router. ({e})"
        return result

    result["authenticated"] = True
    result["identity"] = _read_identity(api)
    result["serial"] = _read_serial(api)

    try:
        api.close()
    except Exception:
        # Nothing depends on a clean close, and failing here would turn a
        # successful test into a reported failure.
        pass

    return result


def _read_identity(api):
    """The name the operator gave the box in RouterOS. Absent is not an error."""
    try:
        for row in api.path("system", "identity"):
            return str(row.get("name", ""))
    except Exception:
        logger.debug("[router-probe] could not read identity", exc_info=True)
    return ""


def _read_serial(api):
    """
    The board serial — the only value that is the same on two rows only when
    they are the same physical machine. A CHR or an x86 install has none, which
    is normal and not worth surfacing as a problem.
    """
    try:
        for row in api.path("system", "routerboard"):
            return str(row.get("serial-number", ""))
    except Exception:
        logger.debug("[router-probe] could not read routerboard", exc_info=True)
    return ""


def safe_disconnect_pppoe(customer):
    if not customer.router or not customer.pppoe_username:
        return False

    api = safe_connect_router(customer.router)
    if not api:
        return False

    disconnect_pppoe_session(api, customer.pppoe_username)
    return True