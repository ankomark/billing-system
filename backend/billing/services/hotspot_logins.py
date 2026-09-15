"""
Hotspot logins that let somebody on without buying anything.

Provisioning names every account it writes after a device MAC, because a
hotspot user is authenticated by address alone -- see enable_hotspot. So any
account on a router whose name is not a MAC was not written by this system. On
a billing network there is no legitimate reason for one to exist: every way in
is supposed to start with a purchase.

Found on skylink3 on 2026-09-15 while checking that nobody could reach the
router without a voucher:

    ip hotspot user admin  password=<set>  profile=default  enabled

The `default` user profile carries no limit-uptime, no limit-bytes-total and
no rate-limit, so anyone who typed that name and password into the portal got
unmetered, unshaped, never-expiring internet. It is the account the MikroTik
setup wizard creates, it survives a reboot, and it comes back on a restore from
an old backup or on a router swapped for a spare -- which is why this is
asserted on a schedule rather than deleted once.

It is NOT the router's administrative login. `/ip/hotspot/user` governs who may
pass through the captive portal; `/user` governs who may configure the box.
Closing this one costs an operator no access to anything -- Winbox, SSH and the
API are all untouched.

Disables. Does not delete, for the same reason disable_orphan_hotspot_users
does not: the account is the only record of whatever it was for, disabling
refuses the next login without discarding that, and it is undone by setting one
flag back.

`default-trial` is deliberately left alone. It is a stock RouterOS row -- the
counter the trial feature keeps its totals on, marked default=yes and carrying
no password -- and it admits nobody unless a server profile actually sets
trial-uptime. So it is closed only when trial is switched on, and ignored,
loudly, when it is not.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Twelve hex digits, however they were punctuated. Anything else was typed by a
# person: provisioning writes nothing but addresses.
MAC_SHAPED = re.compile(r"^[0-9A-F]{12}$")

# The stock row. Harmless on its own; see the module docstring.
TRIAL_ACCOUNT = "default-trial"


def _is_mac(name):
    return bool(MAC_SHAPED.match(re.sub(r"[^0-9A-Fa-f]", "", name or "").upper()))


def _trial_is_on(api):
    """Whether any hotspot server profile actually hands out trial access."""
    try:
        for profile in api.path("ip", "hotspot", "profile"):
            uptime = str(profile.get("trial-uptime") or "").strip()
            # RouterOS answers an unset trial as absent, "" or "0s".
            if uptime and uptime not in ("0s", "0", "none"):
                return True
    except Exception as exc:                    # pragma: no cover - transport
        logger.warning("[hotspot-logins] could not read profiles: %s", exc)
    return False


def find_unearned_logins(api):
    """
    Enabled hotspot accounts that no purchase stands behind.

    Reads only, so the status command and the fixer agree about what is there.
    """
    trial_on = None
    found = []

    for row in api.path("ip", "hotspot", "user"):
        name = str(row.get("name") or "")
        if _is_mac(name):
            continue
        if str(row.get("disabled")).lower() == "true":
            continue

        if name.lower() == TRIAL_ACCOUNT:
            if trial_on is None:
                trial_on = _trial_is_on(api)
            if not trial_on:
                continue

        found.append(row)

    return found


def close_unearned_logins(router, api, *, apply=True):
    """Disable what find_unearned_logins found. Returns how many."""
    rows = find_unearned_logins(api)
    if not rows:
        return 0

    for row in rows:
        logger.warning(
            "[hotspot-logins] %s carries hotspot login %r (profile %s) that "
            "needs no purchase%s",
            router, row.get("name"), row.get("profile") or "default",
            "" if apply else " — reporting only")

    if not apply:
        return len(rows)

    users = api.path("ip", "hotspot", "user")
    closed = 0
    for row in rows:
        try:
            users.update(**{".id": row[".id"], "disabled": "yes"})
            closed += 1
        except Exception as exc:                # pragma: no cover - transport
            logger.warning("[hotspot-logins] %s: could not disable %r: %s",
                           router, row.get("name"), exc)

    if closed:
        logger.warning("[hotspot-logins] %s: disabled %s login(s)",
                       router, closed)
    return closed


def close_unearned_logins_everywhere(*, apply=True, routers=None):
    """
    Assert it across an operator's estate. Unreachable routers are skipped.
    """
    from billing.models import RouterDevice
    from billing.router_service import safe_connect_router

    total = 0
    for router in (routers if routers is not None
                   else RouterDevice.objects.all_tenants().filter(
                       is_active=True)):
        api = safe_connect_router(router)
        if not api:
            logger.info("[hotspot-logins] %s unreachable — skipped", router)
            continue
        total += close_unearned_logins(router, api, apply=apply)

    return total
