"""
Make the router's clock agree with the one the customer bought.

`limit-uptime` is written at grant time as the wall-clock seconds left on the
subscription, and RouterOS then measures it against the account's *cumulative
connected time*. Those are not the same quantity, and the difference is free
service: buy a three-hour window, be online for one of those hours, and the
router still holds two hours of credit after the window has shut. Sleep the
phone overnight on a one-week package and the credit survives the week.

A customer buys a window, not a meter. Three hours means three hours from the
moment they paid, spent or not.

The wall clock is enforced by enforce_subscription_expiry, which runs every
five minutes and takes the account off every router. That is the mechanism and
it is correct. `limit-uptime` is the backstop underneath it -- what stops an
expired subscriber staying online when the server cannot reach the router at
all, which on 2026-09-10 and 11 was three simultaneous outages leaving 42
subscribers connected, several for hours. Removing it would put that back.

So the backstop stays and is taught to count the right thing. RouterOS compares `limit-uptime` against the account's cumulative uptime plus
whatever the *current* session has run. The `uptime` field on the user row
carries only the first of those -- it is updated when a session ends, so a
subscriber who is connected right now has their current session missing from
it. Verified against the router's own arithmetic on 2026-09-12:

    limit 1w5d4h14m50s - (user.uptime 13h57m15s + active.uptime 2h31m)
        = session-time-left 1w4d11h46m35s

So for an account with `uptime` banked, `session` running and `remaining`
wall-clock seconds left, the limit that expires exactly when the subscription
does is

    limit-uptime = uptime + session + remaining

Leaving out `session` is not a rounding error: it shortens the allowance by
however long the subscriber has been connected, which on a live session is
hours. That would cut off paying customers the instant it was written.

Recomputed on a schedule, because `uptime` only moves while somebody is
actually connected and `remaining` falls whether they are or not. The gap
between them is precisely the credit this exists to stop accruing.

Writes only where the difference is worth a round trip. A router carrying 400
accounts must not take 400 writes every five minutes to correct values that
are already within a minute of right.
"""

import logging

from django.utils import timezone

from billing.models import Customer, CustomerDevice, RouterDevice
from billing.router_service import ros_duration_seconds, safe_connect_router
from billing.services.entitlement import granting_subscription
from billing.utils import normalize_mac

logger = logging.getLogger(__name__)

# Below this, leave it alone. The sweep runs every five minutes, so anything
# inside a minute is noise against the interval that will correct it anyway,
# and writing it costs a round trip per account per run.
TOLERANCE_SECONDS = 60

# Never write a limit below this. RouterOS treats limit-uptime=0 as no limit
# at all on some builds -- the same trap documented for limit-bytes-total -- so
# an account whose window has just closed must not be handed "unlimited" on the
# way out. One minute, and the expiry sweep removes the account outright.
FLOOR_SECONDS = 60


def _customer_index():
    devices = {normalize_mac(d.mac_address): d.customer
               for d in CustomerDevice.objects.all_tenants()
               .select_related("customer")}
    for c in (Customer.objects.all_tenants().exclude(hotspot_username="")):
        devices.setdefault(normalize_mac(c.hotspot_username), c)
    return devices


def align_uptime_limits(*, apply=False, routers=None, now=None):
    """
    Bring every hotspot account's limit-uptime back onto the wall clock.

    Returns (checked, corrected, overdue) where `overdue` are accounts whose
    window has already closed -- those are the disable sweep's job, not this
    one, and are reported rather than touched.
    """
    now = now or timezone.now()
    by_mac = _customer_index()

    checked = corrected = 0
    overdue = []

    for router in (routers if routers is not None
                   else RouterDevice.objects.all_tenants().filter(is_active=True)):
        api = safe_connect_router(router)
        if not api:
            logger.info("[uptime] %s unreachable — skipped", router)
            continue

        # The live sessions, because the current one is not in the user row's
        # uptime yet and RouterOS counts it against the limit regardless.
        session_by_mac = {}
        try:
            for a in api.path("ip", "hotspot", "active"):
                session_by_mac[normalize_mac(a.get("mac-address"))] = (
                    ros_duration_seconds(a.get("uptime")) or 0)
        except Exception as exc:
            # Without it every connected subscriber would be short-changed by
            # the length of their session, so skip the router rather than
            # write a limit computed from half the picture.
            logger.warning(
                "[uptime] could not read live sessions on %s (%s) — skipped "
                "rather than risk shortening a live allowance", router, exc)
            continue

        users = api.path("ip", "hotspot", "user")
        for u in list(users):
            mac = normalize_mac(u.get("name"))
            customer = by_mac.get(mac)
            if customer is None:
                continue

            subscription = granting_subscription(customer)
            if subscription is None:
                # No live paid cover. Shortening the limit would only slow down
                # what disable_customer_access does properly, and this must not
                # become a second, quieter way of cutting people off.
                overdue.append((router.name, mac, customer.pk))
                continue

            checked += 1
            uptime = ros_duration_seconds(u.get("uptime")) or 0
            session = session_by_mac.get(mac, 0)
            remaining = int((subscription.expiry_date - now).total_seconds())
            wanted = max(uptime + session + remaining, FLOOR_SECONDS)

            current = ros_duration_seconds(u.get("limit-uptime"))
            if current is not None and abs(current - wanted) <= TOLERANCE_SECONDS:
                continue

            credit = (current - wanted) if current is not None else None
            logger.info(
                "[uptime] %s on %s: limit %ss -> %ss (banked %ss, session "
                "%ss, %ss of window left%s)",
                mac, router.name, current, wanted, uptime, session, remaining,
                f", {credit}s of credit reclaimed" if credit and credit > 0 else "")

            corrected += 1
            if apply:
                try:
                    users.update(**{".id": u[".id"],
                                    "limit-uptime": f"{wanted}s"})
                except Exception as exc:
                    corrected -= 1
                    logger.warning(
                        "[uptime] could not correct %s on %s: %s",
                        mac, router.name, exc)

    return checked, corrected, overdue
