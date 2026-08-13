"""
Hotspot sharing: one paid connection feeding a second network.

A subscriber buys access for their phone, turns on the phone's own hotspot, and
three friends browse for free. Nothing else in this system can see it. The
router only ever talks to the phone, so it is one MAC, one session, one device
against the package's limit — and shared-users, which stops a code being typed
into four phones, says nothing about what is behind the one phone that used it.

What gives it away is TTL. Every IP packet carries a hop counter its sender
sets to a round number — 64 on Android, iOS, Linux and macOS, 128 on Windows,
255 on some appliances — and every router it crosses subtracts one. A phone in
the subscriber's hand reaches the MikroTik with the number it started with. A
laptop behind that phone's hotspot has crossed the phone, so it arrives one
lower.

That is the whole signal, and it is worth being plain about what it is not:

  * It says "this packet crossed a router", not "this person is cheating". A
    subscriber who plugs the WiFi into their own travel router, or runs a VM
    with a NATted adapter, looks identical.
  * It can be defeated. Pinning the outgoing TTL back to 64 is one line on a
    rooted Android or any Linux box, and there are apps that do it. Anyone who
    has bothered will not appear here at all.
  * The starting values are conventions, not rules.

So this treats TTL as evidence rather than proof, and the shape of the code
follows from that. One odd packet does nothing. It takes a sustained mismatch —
several sweeps, minutes apart — before anything happens to a paying customer,
and what happens is the operator's choice, from writing it down through texting
them to ending the session. The default is off, and no setting touches anybody's
subscription.

The strongest evidence is the mixed case. If one address is sending traffic at
both a full starting value and one below it in the same window, that is the
phone itself browsing while something else browses through it. Hence the third
address list, which records the normal values rather than the suspicious ones.

BLOCK, AND WHY IT IS NOT LIKE THE OTHERS
----------------------------------------

Every policy above `block` is decided here, in the sweep, which runs every five
minutes. That is a floor: an operator who wants sharing stopped before it is
useful cannot have it from a server that only looks twice in ten minutes.

`block` therefore does not act from here at all. It installs a filter rule on
the router that rejects forward traffic from any address the mangle rules have
listed, so enforcement lands on the first packet and needs nothing from us. The
address list entry carries a timeout, so the block lifts itself once the traffic
stops; while it continues, the client's own retries keep refreshing the entry
and the block holds. No queue is left behind, and an unreachable billing server
cannot strand anybody.

Two things about it are worth stating plainly rather than discovering.

  * It blocks the subscriber, because there is nothing else to block. The
    tethered device's traffic is NATted by the phone, so it arrives with the
    subscriber's source address and the subscriber's MAC — one address, one
    session. "Stop the sharing" and "cut off the customer who paid" are the
    same action at this layer.
  * It acts on evidence that is only evidence, with none of the patience the
    other policies have: no threshold, no attribution, no check that the person
    on that lease now is the person who earned it. A travel router, a NATted VM
    and a corporate VPN all land in the same list. Those subscribers lose their
    connection within a second of a packet arriving, and get an explanation only
    when the next sweep texts them.

The rule is in chain=forward rather than raw prerouting so that the router
itself stays reachable: DNS and the hotspot login page are chain=input traffic,
and a subscriber who can still load a page is a subscriber who can be told why.
It is placed at the top of the chain, because the stock forward chain accepts
established connections first and a block underneath that would leave every
open connection running.

Two things about RouterOS that cost us if forgotten:

  * The ttl matcher exists only in /ip firewall mangle. The same rule under
    /ip firewall filter is rejected — so the "mark in mangle, match the mark in
    filter" shape you see in most recipes is not a style choice.
  * In chain=forward the value has already been decremented by this router. In
    chain=prerouting it is the value as it arrived, which is the one every
    number above refers to. Rules here are prerouting.
  * hotspot=auth is not decoration. Without it the rule matches everything
    crossing prerouting, including replies coming back from the internet, which
    arrive with whatever is left after a dozen hops — and the address list
    fills with the addresses of web servers.

The same rules are written out in mikrotik-hotspot/tethering-detection.rsc for
an operator who would rather paste them in themselves.
"""

import logging

from django.utils import timezone

from billing.config import get_setting

logger = logging.getLogger(__name__)


# =====================================================
# WHAT GOES ON THE ROUTER
# =====================================================

# Ours, and identifiable as ours. Everything this module writes to a router
# carries it, which is what makes installing idempotent and removing possible.
RULE_COMMENT = "AUTO | WIFI BILLING SYSTEM | tether"

NORMAL_LIST = "tether-normal"
HOP1_LIST = "tether-hop1"
HOP2_LIST = "tether-hop2"
BUSY_LIST = "tether-busy"

# How many routers the traffic crossed before it reached ours.
LIST_HOPS = {NORMAL_LIST: 0, HOP1_LIST: 1, HOP2_LIST: 2}

# Every list this module owns, including the one that is not about hops.
ALL_LISTS = (NORMAL_LIST, HOP1_LIST, HOP2_LIST, BUSY_LIST)

# The round numbers senders start with.
INITIAL_TTLS = (64, 128, 255)

TTL_LISTS = {
    NORMAL_LIST: INITIAL_TTLS,
    HOP1_LIST: tuple(t - 1 for t in INITIAL_TTLS),
    HOP2_LIST: tuple(t - 2 for t in INITIAL_TTLS),
}

# Deliberately not "anything below 64". A VPN, a distant proxy or a mangled
# packet can leave any value at all, and a rule that treats every one of them
# as sharing will accuse people who are doing nothing wrong. Two hops is as far
# as this goes; past that the signal is not worth the false positives.

# Simultaneous connections from one address above which something more than a
# handset is behind it. A phone browsing sits in the low tens; a laptop with
# thirty tabs, or four devices streaming, does not.
#
# This used to be corroboration and nothing else: an address could sit in
# tether-busy all evening without a case ever being opened, because suspects
# were drawn from the hop lists alone. That left the one signal that survives
# TTL pinning — the whole reason the rule exists — unable to catch anybody, and
# a subscriber who normalises their hop counter entirely invisible.
#
# It can now open a case on its own, but not on the same terms. One torrent
# client passes this instantly, so a busy-only case needs the longer threshold
# in busy_observations() rather than the ordinary one.
DEFAULT_CONNECTION_LIMIT = 100


def mangle_rules(timeout="10m", connection_limit=DEFAULT_CONNECTION_LIMIT):
    """The rules as RouterOS wants them. Hyphenated keys — see router_profiles."""
    rules = []
    for list_name, ttls in TTL_LISTS.items():
        for ttl in ttls:
            rules.append({
                "chain": "prerouting",
                "action": "add-src-to-address-list",
                "address-list": list_name,
                "address-list-timeout": timeout,
                # Authenticated hotspot clients only. Without this the list
                # fills with internet servers.
                "hotspot": "auth",
                # Only the first packet of each connection needs looking at,
                # and on a busy hotspot that is the difference between a rule
                # that costs nothing and one an operator notices.
                "connection-state": "new",
                "ttl": f"equal:{ttl}",
                "comment": f"{RULE_COMMENT} ttl={ttl}",
            })

    # The rule that survives evasion.
    #
    # Everything above depends on the hop counter arriving untouched, and it is
    # one line on a rooted Android to make it. Nothing above will ever see that
    # subscriber. What they cannot hide is how many connections a roomful of
    # devices holds open at once — a handset browsing sits in the low tens, and
    # four devices streaming does not.
    #
    # /32 because the limit is per address, not per subnet: without it one busy
    # subscriber would put the entire hotspot range on the list.
    rules.append({
        "chain": "prerouting",
        "action": "add-src-to-address-list",
        "address-list": BUSY_LIST,
        "address-list-timeout": timeout,
        "hotspot": "auth",
        "connection-state": "new",
        "connection-limit": f"{connection_limit},32",
        "comment": f"{RULE_COMMENT} connections>{connection_limit}",
    })
    return rules


def filter_rules(lists):
    """
    The rules that actually stop traffic, for the `block` policy.

    One per address list, matching on src-address-list so the router enforces
    what the mangle rules found without waiting for a sweep. Nothing here has a
    timeout of its own: the *list entry* expires, and the block goes with it.

    reject rather than drop, deliberately. A dropped packet gives the
    subscriber a connection that hangs for thirty seconds a time, which reads
    as "the network is broken" and arrives as a support call. A rejected one
    fails at once, which reads as "something is refusing me" — closer to the
    truth, and the SMS that follows explains the rest.
    """
    return [
        {
            "chain": "forward",
            "action": "reject",
            "reject-with": "icmp-admin-prohibited",
            "src-address-list": name,
            "comment": f"{RULE_COMMENT} block {name}",
        }
        for name in lists
    ]


def _timeout_seconds(value):
    """
    Seconds from either form RouterOS uses: "10m" going in, "00:10:00" coming
    back. Returns None for anything unrecognised, which reads as "do not touch
    it" rather than "replace it", because a rule rewritten on every sweep is
    worse than one whose timeout drifted.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if ":" in text:
        try:
            parts = [int(p) for p in text.split(":")]
        except ValueError:
            return None
        seconds = 0
        for part in parts:
            seconds = seconds * 60 + part
        return seconds
    units = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    total = number = 0
    seen = False
    for char in text:
        if char.isdigit():
            number = number * 10 + int(char)
            seen = True
        elif char in units:
            total += number * units[char]
            number = 0
        else:
            return None
    return total + number if seen else None


def _ours(path):
    """Rows in this table that carry our comment, keyed by it."""
    found = {}
    for row in path:
        comment = row.get("comment", "") or ""
        if comment.startswith(RULE_COMMENT):
            found[comment] = row
    return found


def _reconcile(path, rules, drift, *, at_top=False):
    """
    Make one table hold exactly `rules`, and nothing else of ours.

    Idempotent by comment. Anything carrying our comment that is not in the
    current rule set is removed — that is how a change to the values we look
    for reaches routers configured by an earlier release, rather than leaving
    both generations firing.

    `drift` decides what counts as a difference worth writing. RouterOS
    normalises values on the way back, so comparing everything verbatim would
    rewrite every rule on every sweep for no change at all.

    A rule the router rejects is logged and skipped. One unsupported matcher
    should not stop the others going in, and it should certainly not stop the
    sweep that called this.
    """
    existing = _ours(path)
    added = updated = removed = 0

    # Where the top of the chain is, for rules that have to sit above whatever
    # the operator already has. Read before anything is added so the anchor is
    # not one of our own rules.
    anchor = None
    if at_top:
        for row in path:
            anchor = row.get(".id")
            break

    for rule in rules:
        current = existing.pop(rule["comment"], None)

        if current is None:
            payload = dict(rule)
            if at_top and anchor is not None:
                payload["place-before"] = anchor
            try:
                path.add(**payload)
                added += 1
            except Exception as e:
                logger.warning("[tether] router rejected %s: %s",
                               rule["comment"], e)
            continue

        changes = drift(current, rule)
        if str(current.get("disabled", "false")).lower() in ("true", "yes"):
            changes["disabled"] = "no"

        if changes:
            try:
                path.update(**{".id": current[".id"]}, **changes)
                updated += 1
            except Exception as e:
                logger.warning("[tether] could not update %s: %s",
                               rule["comment"], e)

    for comment, row in existing.items():
        try:
            path.remove(row[".id"])
            removed += 1
        except Exception as e:
            logger.warning("[tether] could not remove stale rule %s: %s",
                           comment, e)

    return added, updated, removed


def _mangle_drift(current, rule):
    changes = {}
    if current.get("address-list") != rule["address-list"]:
        changes["address-list"] = rule["address-list"]

    wanted = _timeout_seconds(rule["address-list-timeout"])
    actual = _timeout_seconds(current.get("address-list-timeout"))
    if wanted is not None and actual is not None and wanted != actual:
        changes["address-list-timeout"] = rule["address-list-timeout"]
    return changes


def _filter_drift(current, rule):
    changes = {}
    for key in ("action", "src-address-list"):
        if current.get(key) != rule[key]:
            changes[key] = rule[key]
    return changes


def ensure_rules(api, *, timeout="10m", connections=DEFAULT_CONNECTION_LIMIT,
                 block_lists=()):
    """
    Install the detection rules, or leave them alone if they are already right.

    `block_lists` names the address lists whose traffic should be rejected
    outright, and is empty for every policy but `block`. Empty is not "leave
    the filter table alone" — it is "there should be none of ours in it", which
    is what takes the blocks back off when an operator steps down from `block`
    to something gentler. A rule that only disappears when the feature is
    switched off entirely would leave people cut off by a policy no longer in
    force.
    """
    added, updated, removed = _reconcile(
        api.path("ip", "firewall", "mangle"),
        mangle_rules(timeout, connections),
        _mangle_drift,
    )

    # Top of the chain: the stock forward chain accepts established connections
    # first, and a reject underneath that would stop new connections while
    # every download already running carried on.
    a, u, r = _reconcile(
        api.path("ip", "firewall", "filter"),
        filter_rules(block_lists),
        _filter_drift,
        at_top=True,
    )
    added, updated, removed = added + a, updated + u, removed + r

    if added or updated or removed:
        logger.info("[tether] rules: +%s ~%s -%s", added, updated, removed)
    return {"added": added, "updated": updated, "removed": removed}


# Set for an operator once a router of theirs has been given rules that stop
# traffic, and cleared once every one of those rules is off again.
#
# It exists because "off" does not dial anybody. That is deliberate and worth
# keeping — an operator who never asked for this should not cost a connection
# every five minutes — but it means switching off is the one moment nothing
# runs to undo what was done. For every other policy that costs nothing: the
# mangle rules only write to a list, and a stranded one is invisible. A
# stranded reject rule is a subscriber with no internet, permanently, under a
# policy that is no longer in force and with nothing anywhere that says why.
#
# So the off path reads this one cached setting, and dials only when the answer
# is yes.
BLOCK_MARKER = "TETHERING_BLOCK_INSTALLED"


def set_block_marker(tenant_id, installed):
    from billing.models import SystemSetting

    value = "true" if installed else "false"
    if (get_setting(BLOCK_MARKER, "false", tenant=tenant_id) or "") == value:
        return
    SystemSetting.objects.update_or_create(
        tenant_id=tenant_id, key=BLOCK_MARKER, defaults={"value": value})

    from billing.config import clear_settings_cache
    clear_settings_cache(BLOCK_MARKER, tenant=tenant_id)


def blocks_may_be_installed(tenant=None):
    return str(get_setting(BLOCK_MARKER, "false", tenant=tenant)
               or "").strip().lower() == "true"


def remove_block_rules(api):
    """
    Take off only the rules that stop traffic, leaving detection in place.

    The narrow half of remove_rules, for the operator who has stepped down from
    `block` rather than switched the feature off. Returns how many came off.
    """
    path = api.path("ip", "firewall", "filter")
    removed = 0
    for row in list(path):
        if (row.get("comment") or "").startswith(RULE_COMMENT):
            path.remove(row[".id"])
            removed += 1
    return removed


def remove_rules(api):
    """
    Take everything this module put on a router back off it.

    The rules, the address lists it fills, and any queue it created. Turning
    the feature off should leave the router as it was found, and an operator
    who cannot see that happen will not trust the feature that did it.

    The filter rules come off first. They are the only thing here that stops
    traffic, so if this run fails halfway the half that ran is the half that
    gave people their connection back.
    """
    removed = 0

    for table in (("ip", "firewall", "filter"), ("ip", "firewall", "mangle")):
        path = api.path(*table)
        for row in list(path):
            if (row.get("comment") or "").startswith(RULE_COMMENT):
                path.remove(row[".id"])
                removed += 1

    entries = api.path("ip", "firewall", "address-list")
    for row in list(entries):
        if row.get("list") in ALL_LISTS:
            try:
                entries.remove(row[".id"])
            except Exception:
                # Dynamic entries expire on their own; a failure to delete one
                # is not worth abandoning the rest of the cleanup for.
                pass

    queues = api.path("queue", "simple")
    for row in list(queues):
        if (row.get("comment") or "").startswith(RULE_COMMENT):
            queues.remove(row[".id"])
            removed += 1

    return removed


# =====================================================
# WHAT THE ROUTER TELLS US
# =====================================================

def read_lists(api):
    """
    The address lists, each as {address: entry}.

    Returns None if the table could not be read. An unreadable router is not a
    router with nothing on it — treating the two the same would clear every
    open case on the estate the first time a link wobbled.
    """
    found = {name: {} for name in ALL_LISTS}
    try:
        for row in api.path("ip", "firewall", "address-list"):
            name = row.get("list")
            address = row.get("address")
            if name in found and address:
                found[name][address] = row
    except Exception as e:
        logger.warning("[tether] could not read address lists: %s", e)
        return None
    return found


def sessions_by_ip(api):
    """
    Live hotspot sessions keyed by address, which is what a list entry is.

    Returns None if the table could not be read, for a sharper reason than the
    one above. Every judgement here rests on tying an address to a subscriber,
    and this table is the only thing that does it. An empty dict from a failed
    read does not look like a failure downstream — it looks like a router full
    of addresses that belong to nobody, which is the one state where a case
    cannot be attributed, cannot be acted on, and gets filed against the
    address instead of the person. A run that concluded that about an entire
    hotspot would be indistinguishable, in the table afterwards, from a real
    night of anonymous sharing.
    """
    sessions = {}
    try:
        for row in api.path("ip", "hotspot", "active"):
            address = row.get("address")
            if not address:
                continue
            sessions[address] = {
                "id": row.get(".id"),
                "user": row.get("user", "") or "",
                "mac": row.get("mac-address", "") or "",
                "uptime": row.get("uptime", ""),
            }
    except Exception as e:
        logger.warning("[tether] could not read hotspot sessions: %s", e)
        return None
    return sessions


# The two tables are read a moment apart, and both durations are reported to
# the second. Enough slack that ordinary skew never reads as a stale entry;
# small enough that it cannot excuse one.
FRESHNESS_SLACK_SECONDS = 30


def caused_by_this_session(entry, session, configured_seconds,
                          slack=FRESHNESS_SLACK_SECONDS):
    """
    Could the session on this address have produced this list entry?

    The question exists because a list entry outlives the traffic that made it.
    Somebody tethers, disconnects, and the entry sits there for the rest of its
    timeout — while the address goes back in the pool and is handed to the next
    person to log in. Without asking this, that person inherits the evidence:
    their sightings tick up on traffic that was never theirs, and at the
    threshold they are throttled for it.

    Answered without trusting any clock. RouterOS reports what is left of the
    entry's timeout, so its age is the timeout it was given minus what remains;
    the session reports its own uptime. An entry older than the session on that
    address cannot have come from it. Both numbers come from the same router,
    so no comparison crosses a machine.

    An address that is still being used to share refreshes its own entry on
    every new connection, so the age of a live case's entry stays near zero and
    this never gets in the way of catching anybody.

    Returns None when the router did not report enough to decide.
    """
    remaining = _timeout_seconds(entry.get("timeout"))
    uptime = _timeout_seconds(session.get("uptime"))
    if remaining is None or uptime is None or not configured_seconds:
        return None
    return (configured_seconds - remaining) <= uptime + slack


def ipv6_is_open(api):
    """
    Whether this router is forwarding IPv6, which would go around all of this.

    MikroTik's hotspot is IPv4 only. It does not intercept IPv6, does not
    authenticate it, and these rules do not see it — the hop counter lives in a
    different header matched by a different firewall. So on a router handing
    out global IPv6, a tethered device that prefers IPv6 is invisible here, and
    a device that has never logged in at all may not need to.

    That is a hole in the hotspot, not only in this detector, and it is not
    ours to close by writing rules of our own scope — so it is reported rather
    than patched. Returns None when the question cannot be answered.
    """
    try:
        for row in api.path("ipv6", "address"):
            if str(row.get("disabled", "false")).lower() in ("true", "yes"):
                continue
            address = row.get("address", "") or ""
            # Link-local is unroutable and present on every interface whether
            # IPv6 is in use or not. A global address is the one that means
            # traffic can leave.
            if address and not address.lower().startswith("fe80"):
                return True
        return False
    except Exception:
        # RouterOS without the ipv6 package has no such path at all, which is
        # the safest answer there is. Not knowing is not a warning.
        return None


def customer_for(tenant_id, session):
    """
    Whose session this is.

    A hotspot user is named after a MAC, and which MAC depends on how the
    subscriber got on: the one their code was bound to, or one of the devices
    since added to their package. Both are checked, and case is not trusted —
    RouterOS reports uppercase, and what is stored is whatever was sent.
    """
    from billing.models import Customer, CustomerDevice
    from billing.utils import mac_variants

    # Separators as well as case now. This built its own case variants, which
    # covered what RouterOS varies and not what an operator typing an address
    # into the admin varies, and the two lookups below are exact matches.
    names = []
    for value in (session.get("user"), session.get("mac")):
        if value:
            names.extend(n for n in mac_variants(value) if n not in names)
    if not names:
        return None

    customer = (
        Customer.objects.all_tenants()
        .filter(tenant_id=tenant_id, hotspot_username__in=names)
        .first()
    )
    if customer:
        return customer

    device = (
        CustomerDevice.objects.all_tenants()
        .select_related("customer")
        .filter(tenant_id=tenant_id, mac_address__in=names)
        .first()
    )
    return device.customer if device else None


# =====================================================
# WHAT WE DO ABOUT IT
# =====================================================

OFF = "off"
LOG = "log"
WARN = "warn"
THROTTLE = "throttle"
KICK = "kick"
BLOCK = "block"

POLICIES = (OFF, LOG, WARN, THROTTLE, KICK, BLOCK)

# Escalating, and ordered so it can be said in one place that block is the only
# one that does not wait for a sweep — see the module docstring.
IMMEDIATE = (BLOCK,)

DEFAULT_MESSAGE = (
    "We have noticed your connection being shared to other devices through "
    "your phone's hotspot. Your package covers your own devices only. Please "
    "turn hotspot sharing off, or buy a package that covers more devices."
)


def policy(tenant=None):
    """
    What this operator wants done. Off unless they have said otherwise.

    Off means no rules are installed and no router is dialled — an operator who
    has not asked for this gets nothing on their hardware and no extra load.
    """
    value = (get_setting("TETHERING_POLICY", OFF, tenant=tenant) or OFF).strip().lower()
    if value not in POLICIES:
        logger.warning("[tether] unknown policy %r — treating as off", value)
        return OFF
    return value


def _int_setting(key, default, tenant=None, minimum=1):
    raw = get_setting(key, str(default), tenant=tenant)
    try:
        return max(minimum, int(str(raw).strip()))
    except (TypeError, ValueError):
        return default


def min_observations(tenant=None):
    """
    Sweeps in a row before a paying customer is touched.

    Three, at a five-minute sweep, is roughly a quarter of an hour of sustained
    mismatch. Low enough to catch an evening of sharing, high enough that one
    reconnecting device or one odd packet cannot cost somebody their session.
    """
    return _int_setting("TETHERING_MIN_OBSERVATIONS", 3, tenant, minimum=2)


def busy_observations(tenant=None):
    """
    Sweeps in a row before connection count alone is acted on.

    Twice the ordinary threshold by default — half an hour of an address
    holding more connections than a handset does, rather than the quarter of an
    hour a hop-counter mismatch needs. The gap is the false-positive rate: a
    hop count one below the starting value has one innocent explanation and it
    is uncommon, while a hundred simultaneous connections has several and one
    of them is a torrent client that will pass the threshold in a burst. Making
    it survive six sweeps is what separates that burst from a household.

    Never below the ordinary threshold, whatever is typed. Busy-only evidence
    is the weaker of the two, and a configuration where it acted sooner than a
    TTL mismatch would be backwards.
    """
    floor = min_observations(tenant)
    return max(floor, _int_setting("TETHERING_BUSY_OBSERVATIONS", floor * 2,
                                   tenant, minimum=3))


def block_busy(tenant=None):
    """
    Whether `block` also rejects on connection count alone. Off by default.

    The hop lists and this one are not the same kind of evidence, and under a
    policy with no threshold and no appeal that difference decides who gets cut
    off. One subscriber with a torrent client, or a laptop with a hundred tabs,
    trips the connection limit inside a second and — with this on — loses their
    connection for it, with no sweep having judged anything.

    An operator who wants the subscribers who defeat the hop counter caught
    anyway can switch it on. It should be a decision, not a default.
    """
    raw = get_setting("TETHERING_BLOCK_BUSY", "false", tenant=tenant)
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def block_lists_for(tenant=None):
    """Which address lists the router should reject outright."""
    lists = [HOP1_LIST, HOP2_LIST]
    if block_busy(tenant):
        lists.append(BUSY_LIST)
    return tuple(lists)


def throttle_kbps(tenant=None):
    return _int_setting("TETHERING_THROTTLE_KBPS", 512, tenant, minimum=64)


def connection_limit(tenant=None):
    return _int_setting("TETHERING_CONNECTION_LIMIT", DEFAULT_CONNECTION_LIMIT,
                        tenant, minimum=20)


# The sweep runs every five minutes (see CELERY_BEAT_SCHEDULE). An address the
# router has already forgotten by the time we look is an address we never saw,
# so the router must remember for longer than the gap between sweeps — with
# enough margin that one retried or delayed run does not lose the evidence.
MIN_LIST_TIMEOUT_SECONDS = 600


def list_timeout(tenant=None):
    """
    How long the router remembers an address.

    Floored, and deliberately not trusted to whoever typed it. Set below the
    sweep interval this fails in the worst way available to it: the rules match,
    the entries appear, they expire before anything reads them, and the sweep
    finds an empty list every time. No error, no log line, no cases — a feature
    that is switched on, installed, running, and blind.
    """
    raw = get_setting("TETHERING_LIST_TIMEOUT", "10m", tenant=tenant) or "10m"
    seconds = _timeout_seconds(raw)

    if seconds is None:
        logger.warning(
            "[tether] could not read TETHERING_LIST_TIMEOUT %r — using 10m", raw)
        return "10m"

    if seconds < MIN_LIST_TIMEOUT_SECONDS:
        logger.warning(
            "[tether] TETHERING_LIST_TIMEOUT %r is shorter than the sweep "
            "interval — entries would expire before they were read. Using 10m.",
            raw)
        return "10m"

    return raw


def stale_after_minutes(tenant=None):
    """Silence for this long closes a case and undoes what was applied."""
    return _int_setting("TETHERING_STALE_MINUTES", 30, tenant, minimum=5)


def _queue_name(ip):
    return f"TETHER {ip}"


def throttle_address(api, ip, kbps):
    """
    Slow one address down, rather than cutting it off.

    A simple queue on the address, not a filter rule: the subscriber keeps
    working, sharing becomes unpleasant, and nobody is left staring at a dead
    connection wondering whether they have been robbed. It is also trivially
    reversible, which matters when the evidence is only evidence.
    """
    limit = f"{kbps}k/{kbps}k"
    queues = api.path("queue", "simple")
    name = _queue_name(ip)

    for row in queues:
        if row.get("name") == name:
            queues.update(**{".id": row[".id"], "max-limit": limit,
                             "disabled": "no"})
            return False

    queues.add(**{
        "name": name,
        "target": f"{ip}/32",
        "max-limit": limit,
        "comment": RULE_COMMENT,
    })
    return True


def unthrottle_address(api, ip):
    queues = api.path("queue", "simple")
    name = _queue_name(ip)
    for row in list(queues):
        if row.get("name") == name:
            queues.remove(row[".id"])
            return True
    return False


def end_session(api, ip):
    """
    End the hotspot session on this address.

    This is the answer to "redirect them to the login page". A firewall rule
    cannot do it: once a client is authenticated the hotspot lets their traffic
    through, and dropping packets gives them a broken connection, not a login
    form. Removing the session puts them back to unauthenticated, and the next
    page they open is intercepted the way a new arrival's would be.

    Their account is untouched — they can log straight back in. That is the
    point: it interrupts the sharing and tells them why, without taking away
    what they paid for.
    """
    actives = api.path("ip", "hotspot", "active")
    for row in list(actives):
        if row.get("address") == ip:
            actives.remove(row[".id"])
            return True
    return False


# =====================================================
# THE SWEEP
# =====================================================

def sweep_router(router, *, now=None):
    """
    Look at one router once: install the rules, read what they caught, and
    move each case along.

    Returns a summary, or None if the router could not be reached — which is
    not the same as a router with nothing to report, and the caller must not
    treat it as one.
    """
    from billing.router_service import safe_connect_router

    now = now or timezone.now()
    tenant_id = router.tenant_id
    action = policy(tenant_id)
    if action == OFF:
        return None

    api = safe_connect_router(router)
    if api is None:
        return None

    timeout = list_timeout(tenant_id)

    # Only `block` puts anything on the router that stops traffic. For every
    # other policy this is empty, which actively takes the reject rules off a
    # router that was on `block` yesterday.
    blocking = block_lists_for(tenant_id) if action == BLOCK else ()

    try:
        # Marked before the attempt, not after. A run that adds the rule and
        # then dies leaves the router blocking and the marker unset, and
        # nothing would ever come back for it.
        if blocking:
            set_block_marker(tenant_id, True)
        ensure_rules(api, timeout=timeout,
                     connections=connection_limit(tenant_id),
                     block_lists=blocking)
        # Deliberately not cleared here when `blocking` is empty. This is one
        # router of possibly several, and clearing on the first one that came
        # clean would strand the reject rules on a sibling that was unreachable
        # this run. Only _lift_blocks, which reaches every router or gives up,
        # is allowed to say the blocks are gone. A marker left set costs one
        # check on the off path; a marker cleared early costs somebody their
        # connection, permanently.
    except Exception as e:
        # Rules that could not be installed mean nothing will be caught, but
        # the lists from a previous run may still hold something worth reading.
        logger.warning("[tether] could not install rules on %s: %s", router, e)

    lists = read_lists(api)
    if lists is None:
        return None

    # Both tables or neither. Without the session table nothing can be tied to
    # a subscriber, and a sweep that records a hotspot's worth of anonymous
    # addresses is worse than a sweep that did not run.
    sessions = sessions_by_ip(api)
    if sessions is None:
        return None

    if ipv6_is_open(api) is True:
        # Loud, because it is the one hole none of these rules can see into,
        # and an operator reading empty case tables should know why they might
        # be empty. Per sweep rather than per case: it is a property of the
        # router, and once every five minutes in a log is already generous.
        logger.warning(
            "[tether] %s is forwarding IPv6. The hotspot does not intercept "
            "IPv6 and these rules do not see it, so sharing over IPv6 goes "
            "undetected — and unauthenticated.", router)

    threshold = min_observations(tenant_id)

    seen = 0
    acted = 0

    # One address, one sighting — taking the furthest it was seen from. A phone
    # with a laptop and a second router behind it lands in both lists, and
    # counting it twice would reach the threshold in half the time it should.
    #
    # tether-busy is in here too, at zero hops. It used to be read only to flag
    # cases the hop lists had already opened, which meant the one signal that
    # survives a subscriber pinning their TTL could never open a case of its
    # own — the evasion it exists to catch went uncaught by the rule written to
    # catch it. Zero is what marks the evidence as connection count alone, and
    # _act holds those to the longer busy_observations() threshold.
    suspects = {}
    for list_name in (HOP1_LIST, HOP2_LIST, BUSY_LIST):
        for ip, entry in lists[list_name].items():
            hops, best = suspects.get(ip, (-1, entry))
            # The entry from the list that saw it furthest away, so the
            # freshness check below reads the one the hop count came from.
            here = LIST_HOPS.get(list_name, 0)
            suspects[ip] = (here, entry) if here > hops else (hops, best)

    configured = _timeout_seconds(timeout)

    for ip, (hops, entry) in suspects.items():
        session = sessions.get(ip)
        fresh = (caused_by_this_session(entry, session, configured)
                 if session else None)
        if session and fresh is None:
            logger.warning(
                "[tether] %s did not report enough to tell whether the entry "
                "for %s predates the session on it — not counting it", router, ip)

        case = _record(
            router=router,
            ip=ip,
            hops=hops,
            mixed=ip in lists[NORMAL_LIST],
            busy=ip in lists[BUSY_LIST],
            session=session,
            fresh=fresh,
            now=now,
        )
        if case is None:
            continue
        seen += 1
        if _act(api, case, action, threshold, tenant_id, now, session):
            acted += 1

    # A throttled subscriber whose lease moved. Handled here rather than in
    # _act, because it is not an escalation and must happen whether or not
    # anything else about the case has changed.
    _follow_throttles(api, router, sessions, now)

    return {"router": router.id, "seen": seen, "acted": acted}


def _follow_throttles(api, router, sessions, now):
    """
    Keep a throttle on the person it was applied to, not on their old address.

    Leases move — a phone that reconnects overnight comes back on a different
    one, and the address it had is handed to somebody else. A queue left
    pointing at the old address stops throttling the subscriber it was meant
    for and starts throttling a stranger who has done nothing, with nothing
    anywhere that explains why their connection is slow.
    """
    from billing.models import TetheringCase

    throttled = (
        TetheringCase.objects.all_tenants()
        .filter(tenant_id=router.tenant_id, router=router,
                status=TetheringCase.THROTTLED)
        .exclude(throttled_ip="")
        .select_related("customer")
    )

    throttled = [c for c in throttled if c.customer_id is not None]
    if not throttled:
        return

    # Resolved once. Doing it per case per session is a query for every session
    # on the router multiplied by every throttled subscriber, on a table that
    # is read every five minutes.
    holder = {}
    for ip, session in sessions.items():
        customer = customer_for(router.tenant_id, session)
        if customer is not None:
            holder[customer.pk] = ip

    for case in throttled:
        current = holder.get(case.customer_id)
        if current is None or current == case.throttled_ip:
            continue

        try:
            unthrottle_address(api, case.throttled_ip)
            throttle_address(api, current, throttle_kbps(router.tenant_id))
        except Exception as e:
            logger.warning("[tether] could not move the throttle for %s: %s",
                           case.customer_id, e)
            continue

        logger.info("[tether] throttle followed customer %s from %s to %s",
                    case.customer_id, case.throttled_ip, current)
        case.throttled_ip = current
        case.ip_address = current
        case.last_seen = now
        case.save(update_fields=["throttled_ip", "ip_address", "last_seen"])


def _record(*, router, ip, hops, mixed, busy, session, fresh, now):
    """
    Open or extend the case for one suspicious address.

    Two things here are load-bearing, and both are about not charging the wrong
    person.

    A private address identifies nobody on its own. 10.5.50.14 exists on every
    router an operator owns, and on every other operator's too, so an address
    is only meaningful together with the router that reported it — every lookup
    below is scoped to both.

    And a sighting counts against a subscriber only when it can be shown to be
    theirs. Two ways it cannot: nobody is logged in on the address at all, or
    somebody is but the entry predates their session — see
    caused_by_this_session, which is the case of an address changing hands
    while the evidence stays behind. Such a sighting adds nothing to the count
    that decides whether a person is throttled or cut off. Nobody is charged
    for traffic that has not been shown to be theirs.

    It does not keep an acted-on case alive either, and that is the subtler
    half. A case stays open while it is being seen, and a throttle stays
    applied while the case is open. If traffic from whoever inherited the
    address could refresh it, the subscriber it was applied to could walk away
    and the throttle would sit on a stranger indefinitely, renewed every five
    minutes by that stranger's own traffic. So an unattributable sighting keeps
    a case alive only while nothing has been done about it.
    """
    from django.db import IntegrityError, transaction

    from billing.models import TetheringCase

    tenant_id = router.tenant_id
    session = session or {}
    customer = customer_for(tenant_id, session) if session else None

    open_cases = TetheringCase.objects.all_tenants().filter(
        tenant_id=tenant_id, status__in=TetheringCase.OPEN_STATUSES)

    attributable = customer is not None and fresh is True

    if attributable:
        case = open_cases.filter(customer=customer).first()
    else:
        # Not shown to be anybody's. Attach to whatever open case that address
        # on that router already belongs to, rather than opening a second one
        # against a person who may have walked into somebody else's evidence.
        case = open_cases.filter(router=router, ip_address=ip).first()

    if case is None:
        # No name on it. An unattributable sighting must never create a row
        # that reads as an accusation against the person who happens to hold
        # the address — the address is all that has actually been observed.
        case = TetheringCase(
            tenant_id=tenant_id, customer=customer if attributable else None)

    case.router = router
    case.ip_address = ip
    case.hops = max(case.hops or 0, hops)

    # See the docstring: an acted-on case is renewed only by its own
    # subscriber's traffic, never by that of whoever holds the address next.
    if attributable or case.status == TetheringCase.WATCHING:
        case.last_seen = now

    if attributable:
        case.mac_address = session.get("mac", "") or case.mac_address
        case.mixed_ttl = case.mixed_ttl or mixed
        case.high_connections = case.high_connections or busy
        case.observations = (case.observations or 0) + 1

    try:
        with transaction.atomic():
            case.save()
    except IntegrityError:
        # The one open case per subscriber constraint. Two sweeps overlapping —
        # a retry landing on top of a slow run — would otherwise abort the
        # whole router's sweep over a row that already exists.
        logger.info("[tether] a case for customer %s was opened concurrently",
                    case.customer_id)
        return open_cases.filter(customer=customer).first()

    return case


def _act(api, case, action, threshold, tenant_id, now, session=None):
    """
    Apply the operator's policy when the evidence has held up.

    Nothing happens below the threshold, and nothing happens to a case with no
    subscriber behind it — there is nobody to tell and nothing fair to do.

    And nothing happens to an address unless the person logged in on it right
    now is the person the case is about. Everything applied here — a queue, a
    session removal — lands on an address, and an address is a lease. Between
    the sighting that filled the list and the moment this runs, that lease can
    have been handed to somebody who has done nothing at all. Checking costs
    one dictionary lookup; not checking costs a stranger their connection.

    Warning and throttling happen once. A second text five minutes after the
    first is nagging, and a throttle stays applied until the case closes, so
    re-applying it would be a no-op with a phone call attached.

    Ending a session is the exception. It lasts a moment: log back in and carry
    on, and without another one the deterrent fired once and then never again.
    So a kicked case can earn another kick, on a further threshold's worth of
    sightings after the last one — measured from acted_observations, not from
    zero, so the evidence count stays cumulative.

    `block` reaches here having already been applied — the router rejected the
    traffic on the first packet, without asking. What is left to do is tell the
    subscriber why, and record who it was, and both of those are worth the
    ordinary threshold: the reject lifts itself when the traffic stops, so a
    single stray packet costs somebody a second of connectivity, and texting
    them about it would be the part that was hard to take back.
    """
    from billing.models import TetheringCase

    if action == LOG or case.customer_id is None:
        return False

    repeatable = action in (KICK, BLOCK)
    already = {KICK: TetheringCase.KICKED, BLOCK: TetheringCase.BLOCKED}.get(action)
    allowed = (case.status == TetheringCase.WATCHING) or (
        repeatable and case.status == already)
    if not allowed:
        return False

    # Connection count on its own is the weaker signal and answers to the
    # longer threshold. hops is 0 only when nothing but tether-busy has ever
    # seen this address.
    if not case.hops:
        threshold = busy_observations(tenant_id)

    if case.observations - case.acted_observations < threshold:
        return False

    # Who is on that address at this moment, not who was on it when the router
    # wrote the list entry.
    if session is None or customer_for(tenant_id, session) != case.customer:
        logger.info(
            "[tether] not acting on %s: the session there is not customer %s",
            case.ip_address, case.customer_id)
        return False

    from billing.tasks.notification_tasks import notify_customer_task

    message = get_setting("TETHERING_MESSAGE", DEFAULT_MESSAGE, tenant=tenant_id)
    applied = False

    if action == WARN:
        case.status = TetheringCase.WARNED
        applied = True

    elif action == THROTTLE:
        try:
            throttle_address(api, case.ip_address, throttle_kbps(tenant_id))
            case.status = TetheringCase.THROTTLED
            # What was throttled, so lifting it later is a fact rather than an
            # assumption about where this subscriber was at the time.
            case.throttled_ip = case.ip_address
            applied = True
        except Exception as e:
            logger.warning("[tether] could not throttle %s: %s",
                           case.ip_address, e)
            case.note = f"throttle failed: {e}"[:255]

    elif action == BLOCK:
        # Nothing to apply. The router rejected this address the moment the
        # mangle rule listed it, and will keep rejecting it until the entry
        # times out — which the subscriber's own retries keep pushing back for
        # as long as they carry on. All that is owed here is the record and
        # the text, so the person on the other end learns why their connection
        # died rather than filing it under "this network is broken".
        case.status = TetheringCase.BLOCKED
        applied = True

    elif action == KICK:
        try:
            # Only a session that was really there counts. An address-list
            # entry outlives the session that filled it, so a sweep can find
            # the address with nobody logged in on it — recording a kick that
            # ended nothing would spend the threshold and leave the next real
            # session untouched.
            if end_session(api, case.ip_address):
                case.status = TetheringCase.KICKED
                applied = True
        except Exception as e:
            logger.warning("[tether] could not end session for %s: %s",
                           case.ip_address, e)
            case.note = f"session end failed: {e}"[:255]

    if applied:
        case.acted_at = now
        case.acted_observations = case.observations
        # Once per case. A second kick is a deterrent; a second text saying the
        # same thing fifteen minutes later is nagging.
        if case.customer.phone and case.notified_at is None:
            # Queued rather than sent here: a slow SMS gateway must not hold
            # the sweep open against a router, and a failed one must not undo
            # what has already been applied at the router.
            notify_customer_task.delay(
                case.customer.phone, message, tenant_id=tenant_id)
            case.notified_at = now

    case.save()
    if applied:
        logger.info("[tether] %s for customer %s (%s sightings, %s hop(s)%s)",
                    case.status, case.customer_id, case.observations,
                    case.hops, ", mixed" if case.mixed_ttl else "")
    return applied


def close_stale_cases(tenant_id, *, now=None, force=False):
    """
    Close cases that have gone quiet, and undo anything applied to them.

    This is the half that is easy to leave out and expensive to have left out.
    A throttle queue nobody removes is a customer who paid for 10 Mbps and gets
    512 kbps for the rest of the month, with the reason sitting in a table
    nobody reads. Silence is the signal that the sharing stopped.

    A blocked case needs nothing undone here, and that is the point of how it
    was applied: the reject rule matches an address list, the list entry has a
    timeout, so the block has already lifted itself by the time a case looks
    stale. Nothing about a subscriber's connection depends on this function
    running, or on the billing server existing at all.

    `force` closes every open case regardless of how recently it was seen, and
    exists for one moment: the operator turning the feature off. Nothing sweeps
    for them after that, so without this the last thing the feature ever did
    would be to leave a throttle on somebody permanently — switched off, out of
    the logs, and impossible to explain from the router alone.
    """
    from billing.models import TetheringCase
    from billing.router_service import safe_connect_router

    now = now or timezone.now()

    stale = (
        TetheringCase.objects.all_tenants()
        .select_related("router")
        .filter(tenant_id=tenant_id, status__in=TetheringCase.OPEN_STATUSES)
    )
    if not force:
        cutoff = now - timezone.timedelta(
            minutes=stale_after_minutes(tenant_id))
        stale = stale.filter(last_seen__lt=cutoff)
    stale = list(stale)

    closed = 0
    for case in stale:
        fields = ["status", "cleared_at"]

        # throttled_ip, not ip_address: what was applied is what has to come
        # off. The two differ whenever a lease moved after the throttle went
        # on, and lifting the wrong one leaves a queue behind on the router
        # throttling whoever holds that address now.
        if case.status == TetheringCase.THROTTLED and case.throttled_ip:
            if not case.router_id:
                logger.warning(
                    "[tether] case %s is throttled on %s with no router on "
                    "record — the queue must be removed by hand",
                    case.pk, case.throttled_ip)
                continue

            api = safe_connect_router(case.router)
            if api is None:
                # Leave the case open. Closing it here would record the
                # throttle as lifted while the queue is still on the router,
                # and nothing would ever come back to it.
                logger.warning(
                    "[tether] cannot reach %s to lift the throttle on %s",
                    case.router, case.throttled_ip)
                continue
            try:
                unthrottle_address(api, case.throttled_ip)
            except Exception as e:
                logger.warning("[tether] could not lift throttle on %s: %s",
                               case.throttled_ip, e)
                continue

            case.throttled_ip = ""
            fields.append("throttled_ip")

        case.status = TetheringCase.CLEARED
        case.cleared_at = now
        case.save(update_fields=fields)
        closed += 1

    return closed
