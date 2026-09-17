"""
Hotspot accounts still enabled for a device no live package covers.

A one-off clean-up cannot hold this, and the proof took two hours. On
2026-09-17 there were 91 enabled accounts on skylink3 that no paid package
stood behind, five of them online, 31.78 GB served between them. They were
disabled at 08:14. By 10:17 there were 32 again, and the comment on them was
the provisioning stamp rather than the one the clean-up writes -- so they had
been re-created, not missed.

Two paths put them there, and neither is a mistake on its own:

  * Expiry disables a customer, not a device. When one package ends while
    another of theirs is still live, `enforce_subscription_expiry` now takes
    off the devices that package was serving -- but only the ones bound to it.

  * A payment that lands before the code is typed has no device of its own
    yet, so the grant falls back to the customer's most recently seen address.
    If the code is then typed on a different handset, the first one keeps a
    working account that nothing covers. That race is ordinary: the M-Pesa
    callback and the person at the portal are seconds apart.

So this is the reconciler behind both, in the shape the orphan sweep already
uses -- and for the reason its docstring gives: "the router goes on serving
what the database stopped tracking, and nothing anywhere disagrees."

Disables. Does not delete. An account here is not a freeloader; it is usually
a paying customer's second handset, and the account carries the profile it was
sold and the bytes it has run. Disabling refuses the next login and keeps all
of it, undone by setting one flag back.

The live session goes with it. `limit-uptime` counts connected time, so a
session left running outlives the package that paid for it by however much of
that limit is unspent -- which for a three-week package is days.
"""

import datetime as dt
import logging

from billing.services.entitlement import subscription_for_device
from billing.utils import normalize_mac

logger = logging.getLogger(__name__)

# What provisioning stamps on everything it creates, and what this adds when it
# switches one off. Kept on the router rather than in a table for the reason
# disable_orphan_hotspot_users gives: it survives a database restore, an
# operator reading the account can see it, and it cannot drift out of step with
# the thing it describes.
PROVISIONED_BY_US = "AUTO | WIFI BILLING SYSTEM"
UNCOVERED_MARK = " | uncovered "

# How many is still a believable number for one router.
#
# This decides what to disable by subtracting a database answer from a router's
# account list, and the failure mode of that shape is not subtle: if the
# entitlement query returns nothing -- a broken tenant context, a migration
# mid-flight, a filter that stops matching -- then every account on the router
# is uncovered, and this would disable the whole estate while doing exactly
# what it was told.
#
# The first real run found 91 on skylink3, which was the backlog since the leak
# began. Running hourly behind a fixed expiry path should find nought or a
# handful. A hundred is above that backlog and far below the several hundred an
# empty query would produce.
DEFAULT_MAX_DISABLE = 100


def _is_mac(name):
    return len(str(name or "").replace(":", "")) == 12


def _truthy(value):
    return str(value).lower() in ("true", "yes")


def find_uncovered_logins(router, api, *, by_mac=None, devices_by_customer=None):
    """
    Enabled accounts on one router that no live paid package covers.

    Reads only, so a status run and a fixing run agree about what is there.

    Password accounts are left alone: an account not named for a device address
    is `close_unearned_logins`' business, and it judges them by a different
    rule -- whether any purchase could stand behind them at all.
    """
    from billing.models import Customer, CustomerDevice

    if by_mac is None or devices_by_customer is None:
        by_mac, devices_by_customer = {}, {}
        for device in (CustomerDevice.objects.all_tenants()
                       .filter(tenant_id=router.tenant_id)
                       .select_related("customer", "subscription__invoice",
                                       "subscription__package")):
            by_mac[normalize_mac(device.mac_address)] = device.customer
            devices_by_customer.setdefault(
                device.customer_id, []).append(device)
        for customer in (Customer.objects.all_tenants()
                         .filter(tenant_id=router.tenant_id)
                         .exclude(hotspot_username="")):
            by_mac.setdefault(
                normalize_mac(customer.hotspot_username), customer)

    found = []
    for row in api.path("ip", "hotspot", "user"):
        name = str(row.get("name") or "")
        if _truthy(row.get("disabled")) or not _is_mac(name):
            continue

        mac = normalize_mac(name)
        customer = by_mac.get(mac)
        if customer is not None and subscription_for_device(
                customer, mac, devices_by_customer.get(customer.pk, [])):
            continue

        found.append((mac, customer, row))

    return found


def close_uncovered_logins(router, api, *, apply=True,
                           max_disable=DEFAULT_MAX_DISABLE):
    """
    Switch off what this router is serving without a package behind it.

    Returns (disabled, sessions_ended, refused) where `refused` is set when the
    count was above `max_disable` and nothing was touched -- above that number
    the likelier explanation is that this function is wrong rather than that
    the router is.
    """
    uncovered = find_uncovered_logins(router, api)
    if not uncovered:
        return 0, 0, False

    if len(uncovered) > max_disable:
        logger.error(
            "[uncovered] %s reports %s accounts with no live package, which is "
            "past the %s that is believable — nothing disabled. Check the "
            "entitlement query before running this again.",
            router, len(uncovered), max_disable)
        return 0, 0, True

    if not apply:
        for mac, customer, _ in uncovered:
            logger.info("[uncovered] %s on %s belongs to %s and no live "
                        "package covers it", mac, router,
                        f"customer {customer.pk}" if customer else "nobody")
        return len(uncovered), 0, False

    today = dt.date.today().isoformat()
    users = api.path("ip", "hotspot", "user")
    actives = api.path("ip", "hotspot", "active")
    live = {normalize_mac(a.get("mac-address")): a for a in actives}

    disabled = ended = 0
    for mac, customer, row in uncovered:
        try:
            users.update(**{
                ".id": row[".id"],
                "disabled": True,
                "comment": f"{PROVISIONED_BY_US}{UNCOVERED_MARK}{today}",
            })
            disabled += 1
        except Exception:
            logger.warning("[uncovered] could not disable %s on %s",
                           mac, router, exc_info=True)
            continue

        session = live.get(mac)
        if not session:
            continue
        try:
            actives.remove(session[".id"])
            ended += 1
        except Exception:
            # The account is off, so the next login is refused either way; this
            # session simply runs until its limit rather than ending now.
            logger.warning(
                "[uncovered] %s on %s is disabled but its session would not "
                "end", mac, router, exc_info=True)

    if disabled:
        logger.warning(
            "[uncovered] %s: disabled %s account(s) no live package covers, "
            "ended %s live session(s)", router, disabled, ended)
    return disabled, ended, False


def close_uncovered_logins_everywhere(*, apply=True, routers=None,
                                      max_disable=DEFAULT_MAX_DISABLE):
    """
    The same across an operator's estate, one router at a time.

    An unreachable router is skipped rather than failing the sweep: it is
    serving whatever it had when it went away, and it will be swept on the run
    after it comes back.
    """
    from billing.models import RouterDevice
    from billing.router_service import safe_connect_router

    if routers is None:
        routers = RouterDevice.objects.all_tenants().filter(is_active=True)

    totals = [0, 0]
    for router in routers:
        api = safe_connect_router(router)
        if not api:
            logger.info("[uncovered] %s unreachable — skipped", router)
            continue
        disabled, ended, _ = close_uncovered_logins(
            router, api, apply=apply, max_disable=max_disable)
        totals[0] += disabled
        totals[1] += ended

    return tuple(totals)
