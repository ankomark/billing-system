"""
Sessions that outlived the account behind them.

Every sweep here starts from `/ip/hotspot/user`: find the account, judge it,
disable it. Disabling refuses the *next* login and nothing more. An established
session keeps running on its own `session-time-left`, because that is what
RouterOS counts down once somebody is on -- so removing or disabling an account
while its session is live leaves the device connected, unmetered by anything
this system can see, until the clock runs out.

Found on skylink3 on 2026-09-23, hours after a sweep had cleared 149 accounts:

    C6:59:D0:A8:9B:3B  192.168.88.94  up 11h26m
    bytes-out 1.19 GB   session-time-left 4d7h55m   limit-bytes-total 16.44 GB

Its account was gone from the user table entirely. `find_uncovered_logins`
could not see it -- it iterates accounts, and there was no account. Nor could
`clear_duplicate_sessions`, which compares sessions against each other. It had
four more days to run.

WHAT COUNTS AS STALE

A session whose `user` has no row in `/ip/hotspot/user`, or whose row is
disabled. Both mean the same thing: the next login would be refused, so this
one is running on nothing but its own countdown. The disabled case is also the
backstop for `close_uncovered_logins`, which ends the sessions it disables but
logs "disabled but its session would not end" when the removal fails -- that
session is caught here on the next pass.

TWO GUARDS, AND WHY

An age floor, because a session seconds old is mid-provisioning rather than
stranded -- the same 600s floor the estate scan uses, for the same reason: a
new arrival otherwise reads as a casualty.

And a refusal to act on an empty user table. This decides what to end by
subtracting one router read from another, and the failure mode of that shape is
not subtle: if `/ip/hotspot/user` comes back empty -- a transport error
swallowed, a table read mid-restore -- then every session on the router is
stale, and this would disconnect the entire site while doing exactly what it
was told. An empty table means the read failed, not that 219 people are
freeloading, so it stops. `DEFAULT_MAX_END` catches the same class of mistake
when the read is merely wrong rather than empty.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Below this a session is mid-provisioning, not stranded. See the docstring.
MIN_UPTIME_SECONDS = 600

# How many stale sessions is still a believable number for one router. Above
# it, the likelier explanation is that this function is wrong rather than that
# the router is -- the same judgement DEFAULT_MAX_DISABLE makes next door.
DEFAULT_MAX_END = 50

_UPTIME = re.compile(r"(\d+)([wdhms])")
_UNITS = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}


def uptime_seconds(text):
    """RouterOS writes uptime as `4d7h55m42s`. Unparseable reads as 0."""
    return sum(int(n) * _UNITS[u] for n, u in _UPTIME.findall(str(text or "")))


def _is_disabled(row):
    return str(row.get("disabled")).lower() in ("true", "yes")


def find_stale_sessions(api, *, min_uptime=MIN_UPTIME_SECONDS):
    """
    Live sessions with no enabled account behind them.

    Returns (sessions, accounts_seen) so the caller can tell "nothing is
    stale" from "the account table did not come back", which are the same
    empty list and very different situations.
    """
    accounts = {str(row.get("name")).upper(): row
                for row in api.path("ip", "hotspot", "user")}
    if not accounts:
        return [], 0

    stale = []
    for session in api.path("ip", "hotspot", "active"):
        if uptime_seconds(session.get("uptime")) < min_uptime:
            continue
        account = accounts.get(str(session.get("user") or "").upper())
        if account is not None and not _is_disabled(account):
            continue
        stale.append(session)

    return stale, len(accounts)


def close_stale_sessions(router, api, *, apply=True, max_end=DEFAULT_MAX_END):
    """
    End what find_stale_sessions found. Returns (ended, refused).

    `refused` is set when the count was above `max_end` and nothing was
    touched, or when the account table came back empty.
    """
    stale, accounts_seen = find_stale_sessions(api)

    if not accounts_seen:
        logger.error(
            "[stale-sessions] %s returned no hotspot accounts at all, which "
            "reads as every session being stale — nothing ended. The user "
            "table did not come back.", router)
        return 0, True

    if not stale:
        return 0, False

    if len(stale) > max_end:
        logger.error(
            "[stale-sessions] %s reports %s sessions with no account, which "
            "is past the %s that is believable — nothing ended. Check the "
            "account read before running this again.",
            router, len(stale), max_end)
        return 0, True

    for session in stale:
        logger.warning(
            "[stale-sessions] %s: %s on %s has run %s with no enabled "
            "account%s", router, session.get("user"), session.get("address"),
            session.get("uptime"), "" if apply else " — reporting only")

    if not apply:
        return len(stale), False

    actives = api.path("ip", "hotspot", "active")
    ended = 0
    for session in stale:
        try:
            actives.remove(session[".id"])
            ended += 1
        except Exception:                       # pragma: no cover - transport
            logger.warning(
                "[stale-sessions] %s: could not end %s — it runs until its "
                "own limit", router, session.get("user"), exc_info=True)

    if ended:
        logger.warning("[stale-sessions] %s: ended %s session(s) no account "
                       "stood behind", router, ended)
    return ended, False


def close_stale_sessions_everywhere(*, apply=True, routers=None,
                                    max_end=DEFAULT_MAX_END):
    """
    The same across an operator's estate, one router at a time.

    An unreachable router is skipped rather than failing the sweep: whatever
    is running there goes on running, and it is read on the pass after it
    comes back.
    """
    from billing.models import RouterDevice
    from billing.router_service import safe_connect_router

    if routers is None:
        routers = RouterDevice.objects.all_tenants().filter(is_active=True)

    total = 0
    for router in routers:
        api = safe_connect_router(router)
        if not api:
            logger.info("[stale-sessions] %s unreachable — skipped", router)
            continue
        ended, _ = close_stale_sessions(router, api, apply=apply,
                                        max_end=max_end)
        total += ended

    return total
