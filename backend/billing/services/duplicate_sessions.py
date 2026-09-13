"""
One device, one session. Clear the sessions a handset left behind.

A hotspot session belongs to a MAC and an IP address together, so a device that
picks up a new DHCP lease starts a second session while the first stays open --
and `shared-users=2` on a two-device profile means RouterOS is perfectly happy
to keep both. Nothing ever closes the first. Keepalive would, and it is
deliberately off: turning it on is what caused 44 subscribers to be logged out
in a day, so it stays off and this cleans up instead.

87 MACs were carrying a duplicate on 2026-09-13, and the cost is not only an
address in the pool. RouterOS counts every session's uptime against the
account's `limit-uptime`, so a dead session keeps spending the customer's
window while it sits there. Customer 729 had 29 hours of session time against a
package bought 90 minutes earlier -- two sessions, one of them idle since it
started.

Which one to keep is not a guess: `idle-time` says which the device is actually
using. In every duplicate found, the stale session's idle-time was close to its
whole uptime -- dead since the moment it opened -- while the live one was idle
seconds. So the newest-looking session is not the rule; the least idle one is.

Only ever removes a session that has been idle a good while, and never the
least-idle one of a pair. Two genuinely busy sessions on one MAC would be
strange, and this leaves that alone for somebody to look at rather than
disconnecting a device that is working.
"""

import logging

from billing.models import RouterDevice
from billing.router_service import ros_duration_seconds, safe_connect_router
from billing.utils import normalize_mac

logger = logging.getLogger(__name__)

# A session must have been idle at least this long before it is a candidate.
#
# The observed stale sessions were idle for hours -- most of their uptime --
# so this is nowhere near them. It is here for the case this is not built for:
# two sessions both carrying traffic, where removing either would cut off
# something in use.
MIN_IDLE_SECONDS = 600


def find_duplicate_sessions(*, routers=None, min_idle=MIN_IDLE_SECONDS):
    """
    Sessions worth closing, as (router, mac, keep, drop) per duplicated MAC.

    `keep` is the least-idle session, `drop` the ones idle beyond the floor.
    A MAC whose extra sessions are all busy yields no drops and is reported by
    the caller rather than acted on.
    """
    found = []

    for router in (routers if routers is not None
                   else RouterDevice.objects.all_tenants().filter(is_active=True)):
        api = safe_connect_router(router)
        if not api:
            logger.info("[sessions] %s unreachable — skipped", router)
            continue

        by_mac = {}
        for a in api.path("ip", "hotspot", "active"):
            by_mac.setdefault(normalize_mac(a.get("mac-address")), []).append(a)

        for mac, rows in by_mac.items():
            if len(rows) < 2:
                continue

            # Least idle first. That is the session the device is on.
            rows = sorted(
                rows, key=lambda r: ros_duration_seconds(r.get("idle-time")) or 0)
            keep, rest = rows[0], rows[1:]
            drop = [r for r in rest
                    if (ros_duration_seconds(r.get("idle-time")) or 0) >= min_idle]
            found.append((router, api, mac, keep, rest, drop))

    return found


def clear_duplicate_sessions(*, apply=False, routers=None,
                             min_idle=MIN_IDLE_SECONDS):
    """
    Close the stale half of every duplicated session. Reports unless `apply`.

    Returns (closed, kept_busy) -- how many were removed, and how many extra
    sessions were left alone because they were still in use.
    """
    closed = 0
    busy = []

    for router, api, mac, keep, rest, drop in find_duplicate_sessions(
            routers=routers, min_idle=min_idle):
        still_busy = [r for r in rest if r not in drop]
        for r in still_busy:
            busy.append((router.name, mac, r))
            logger.warning(
                "[sessions] %s on %s has a second session idle only %s — "
                "left alone; two busy sessions on one address is not what "
                "this cleans up",
                mac, router.name, r.get("idle-time"))

        for r in drop:
            if not apply:
                closed += 1
                continue
            try:
                api.path("ip", "hotspot", "active").remove(r[".id"])
                closed += 1
                logger.info(
                    "[sessions] closed a session on %s for %s: address %s, "
                    "up %s, idle %s (keeping %s, idle %s)",
                    router.name, mac, r.get("address"), r.get("uptime"),
                    r.get("idle-time"), keep.get("address"),
                    keep.get("idle-time"))
            except Exception as exc:
                logger.warning("[sessions] could not close %s on %s: %s",
                               mac, router.name, exc)

    return closed, busy


def find_stranded_sessions(*, routers=None, min_idle=MIN_IDLE_SECONDS):
    """
    Sessions authorising an address the device no longer holds.

    A hotspot session is keyed by MAC and address together, so when a device's
    DHCP lease moves the authorisation stays behind on the old address and the
    device's traffic arrives from the new one, unauthorised. The customer sees
    "connected, no internet" while holding a perfectly valid voucher, and the
    dead session sits there indefinitely because nothing times it out --
    keepalive is off, deliberately, and the duplicate sweep does not fire
    because there is only ONE session and it is simply on the wrong address.

    A power cut does this to the whole estate at once: both routers come back,
    every device re-leases, and any whose address changed is stranded. Ten were
    in that state on 2026-09-13 after a 7-hour-old reboot, idle between 55
    minutes and 5 hours, every one of them entitled.

    Only where the MAC has a BOUND lease to compare against -- no lease means
    no evidence, and a device on a static address would otherwise be cut off
    for not appearing in a table it was never in. And only past the idle floor,
    so a lease renewal caught mid-flight is left alone.
    """
    found = []

    for router in (routers if routers is not None
                   else RouterDevice.objects.all_tenants().filter(is_active=True)):
        api = safe_connect_router(router)
        if not api:
            logger.info("[sessions] %s unreachable — skipped", router)
            continue

        try:
            leases = {
                normalize_mac(l.get("mac-address")): str(l.get("address"))
                for l in api.path("ip", "dhcp-server", "lease")
                if str(l.get("status")) == "bound"
            }
        except Exception as exc:
            # Without the lease table there is nothing to compare against, and
            # guessing would mean disconnecting people on no evidence at all.
            logger.warning(
                "[sessions] could not read leases on %s (%s) — skipped rather "
                "than guess", router, exc)
            continue

        # Who holds each address now, so a session can be checked against the
        # address as well as against its own device.
        holder = {}
        try:
            for l in api.path("ip", "dhcp-server", "lease"):
                holder[str(l.get("address"))] = normalize_mac(
                    l.get("mac-address"))
        except Exception:
            holder = {}

        for a in list(api.path("ip", "hotspot", "active")):
            mac = normalize_mac(a.get("mac-address"))
            addr = str(a.get("address"))
            lease = leases.get(mac)

            idle = ros_duration_seconds(a.get("idle-time")) or 0
            if idle < min_idle:
                continue

            # Two ways to know a session is authorising the wrong address.
            #
            # The device has a bound lease somewhere else -- it moved, and the
            # authorisation stayed behind.
            moved = lease is not None and addr != lease

            # Or the address it authorises now belongs to a DIFFERENT handset.
            # That one is unambiguous however little is known about the device
            # itself: 59 sessions were in this state on 2026-09-13, every one
            # of them with no lease of its own, because the device went away,
            # its 15-minute lease expired, and the address was handed to
            # somebody else while nothing reaped the session -- keepalive and
            # idle-timeout are both off, deliberately.
            #
            # It matters more than a device losing its own internet: two
            # handsets disagree about who owns an address, and when the first
            # comes back it lands on a new address with this session still
            # standing, which is exactly the stranding above.
            recycled = (holder.get(addr) not in (None, mac))

            if not (moved or recycled):
                continue

            # The reason travels with the row rather than a value whose
            # meaning changes by branch. The first version passed the new
            # holder's MAC through the same slot as the lease address, so the
            # log read "its lease is C2:41:D9:5F:D0:A3" -- a MAC where it says
            # lease, which is worse than saying nothing.
            if moved:
                why = f"its lease is now {lease}"
            else:
                why = f"that address now belongs to {holder.get(addr)}"
            found.append((router, api, mac, a, why))

    return found


def clear_stranded_sessions(*, apply=False, routers=None,
                            min_idle=MIN_IDLE_SECONDS):
    """
    Free the devices whose authorisation is pinned to an address they have lost.

    Removes the session AND its host entry. The host is not tidiness: RouterOS
    will not re-run MAC authentication while a stale one is standing, which is
    the whole reason retry_mac_login exists. Removing only the session leaves
    the device exactly as stuck as it was.

    The account is untouched. This ends a session already carrying nothing; it
    takes nothing away from anybody.
    """
    freed = 0

    for router, api, mac, a, why in find_stranded_sessions(
            routers=routers, min_idle=min_idle):
        if not apply:
            freed += 1
            continue
        try:
            api.path("ip", "hotspot", "active").remove(a[".id"])
            hosts = api.path("ip", "hotspot", "host")
            for h in list(hosts):
                if (normalize_mac(h.get("mac-address")) == mac
                        and str(h.get("address")) == str(a.get("address"))):
                    try:
                        hosts.remove(h[".id"])
                    except Exception:
                        pass
            freed += 1
            logger.info(
                "[sessions] freed %s on %s: authorised on %s but %s; idle %s "
                "— it can log in again on the address it has",
                mac, router.name, a.get("address"), why, a.get("idle-time"))
        except Exception as exc:
            logger.warning("[sessions] could not free %s on %s: %s",
                           mac, router.name, exc)

    return freed
