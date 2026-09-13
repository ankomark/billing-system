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
