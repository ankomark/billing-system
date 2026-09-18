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

Except where the person on it has paid. An account reads uncovered when its
own package is over and the customer's live one is bound to another address --
the shape two handsets make, and the shape one handset makes on its own when
it randomises its MAC between purchases. Nothing in a MAC tells those apart,
but being connected does, and on 2026-09-18 cutting them anyway took paying
customers off skylink3 every hour: disabled, refused when the handset landed
back on the address, re-provisioned, disabled again on the next run. So an
address its customer is connected on, with a live package behind them, is
reclaimed onto that package instead of switched off. See
reclaim_for_live_package.
"""

import datetime as dt
import logging
import os

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

    Asks `require_grant=False`, which is the difference between this and every
    other caller of that rule. They ask which devices a package would be
    granted to; this asks only whether anything live is paying, because it is
    the one that disables. Asking the stronger question here disabled 6
    accounts on skylink3 on 2026-09-18 -- every one of them a paying customer,
    two of them connected at that moment, four of them the customer's *only*
    device against a package selling one. A handset that rotates its MAC leaves
    a second address on the router, the device row follows one of the two, and
    whichever the row is not reads as uncovered -- so the sweep disabled the
    live session, the phone was refused "invalid username or password" when it
    landed back on that address, provisioning wrote it again, and the next run
    an hour later did the same. That is the churn the module docstring above
    reads as a leak: two paths fighting over the same customers, not accounts
    nobody paid for.

    The allowance is still enforced, where it always was. `_trim_to_package_limit`
    grants only what the package sells, and `shared-users` on the profile stops
    the rest being used at once -- which is why that function says in its own
    docstring that it leaves existing accounts alone rather than "cut somebody
    off mid-session to correct an allowance".
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
                customer, mac, devices_by_customer.get(customer.pk, []),
                require_grant=False):
            continue

        found.append((mac, customer, row))

    return found


def reclaim_for_live_package(router, api, mac, customer, *, live_macs):
    """
    Give an address the package its user has already paid for.

    The third answer to an account that reads uncovered while its customer is
    sitting on it. Disabling cuts somebody who has paid; leaving it runs them
    on an account whose package is over, so their usage counts against the
    wrong subscription and `limit-uptime` comes from the wrong one. Neither is
    what anybody wants, and both leave the database believing this handset is
    somewhere it is not.

    What is actually true here is simpler than either: the customer holds a
    live package, and this is the address they are using it from. So the
    binding moves to where the person is. `macs_to_grant` orders by most
    recently seen and trims to what the package sells, so re-pointing the row
    puts this address in the grant and drops the one they have rotated away
    from -- and the next sweep finds this account covered and the stale one
    closable, which is how the hourly fight between this sweep and
    provisioning ends rather than repeating.

    Refuses when another of that subscription's devices is connected right
    now: that is a genuine second handset holding the place the package sells,
    and taking it away from them to give it here would be this same fault
    pointed the other way.

    Returns True when the address was reclaimed, False to fall through to the
    disable path.
    """
    from django.utils import timezone

    from billing.models import CustomerDevice
    from billing.router_service import macs_to_grant, provision_customer_on_router
    from billing.services.entitlement import granting_subscription
    from billing.tenancy import tenant_context

    with tenant_context(router.tenant_id):
        sub = granting_subscription(customer)
        if sub is None:
            return False

        own = CustomerDevice.objects.filter(
            customer=customer, mac_address__iexact=mac).first()
        if own is not None and own.blocked:
            # Blocked is a decision somebody made about this handset. It is
            # not for a reconciler to undo.
            return False

        held = {normalize_mac(m) for m in macs_to_grant(customer, sub)}
        if held & (live_macs - {normalize_mac(mac)}):
            logger.info(
                "[uncovered] %s on %s is uncovered but the place its package "
                "sells is in use on another connected device — not reclaimed",
                mac, router)
            return False

        if own is None:
            own = CustomerDevice.objects.create(
                tenant_id=router.tenant_id, customer=customer,
                mac_address=mac, subscription=sub)
        else:
            own.subscription = sub
            own.save(update_fields=["subscription"])
        # auto_now means last_seen only moves on save(); this address is
        # connected, and the grant orders by it.
        CustomerDevice.objects.filter(pk=own.pk).update(last_seen=timezone.now())

        provision_customer_on_router(api, router, customer, sub)

    logger.warning(
        "[uncovered] %s on %s reclaimed onto subscription %s (%s) — the "
        "customer was connected on it and had paid",
        mac, router, sub.pk, sub.package.name)
    return True


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

    disabled = ended = reclaimed = 0
    for mac, customer, row in uncovered:
        # Connected, and they have paid: move the package to where they are
        # rather than take the address away. See reclaim_for_live_package.
        if customer is not None and normalize_mac(mac) in live:
            try:
                if reclaim_for_live_package(
                        router, api, mac, customer, live_macs=set(live)):
                    reclaimed += 1
                    continue
            except Exception:
                # A reclaim that fails leaves the account exactly as it was,
                # which is the state this loop is about to correct anyway. So
                # it falls through and is disabled rather than skipped: an
                # uncovered account left enabled by an error is the leak.
                logger.warning(
                    "[uncovered] could not reclaim %s on %s — disabling it "
                    "instead", mac, router, exc_info=True)

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

    if disabled or reclaimed:
        logger.warning(
            "[uncovered] %s: disabled %s account(s) no live package covers, "
            "ended %s live session(s), reclaimed %s onto a package already "
            "paid for", router, disabled, ended, reclaimed)
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

    # Routers this sweep must not touch, by name, comma separated.
    #
    # An off switch that does not need a deploy to throw or to put back, for
    # the case this is in production for: the sweep is disabling accounts it
    # should not, and the operator needs it to stop now rather than after a
    # build. Skipping is a router staying as it is, which is the state it was
    # in before this sweep existed -- so an entry here costs only the accounts
    # this would have closed, and those keep until it is taken out again.
    skip = {n.strip() for n in
            os.getenv("UNCOVERED_SWEEP_SKIP", "").split(",") if n.strip()}

    totals = [0, 0]
    for router in routers:
        if router.name in skip:
            logger.warning(
                "[uncovered] %s is in UNCOVERED_SWEEP_SKIP — not swept",
                router)
            continue
        api = safe_connect_router(router)
        if not api:
            logger.info("[uncovered] %s unreachable — skipped", router)
            continue
        disabled, ended, _ = close_uncovered_logins(
            router, api, apply=apply, max_disable=max_disable)
        totals[0] += disabled
        totals[1] += ended

    return tuple(totals)
