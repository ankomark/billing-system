"""
Keep Customer.status agreeing with what the customer actually holds.

The flag is written by several paths and cleared by others, and they do not
cover each other. On 2026-09-13 it was wrong in both directions at once: 19
customers flagged `active` holding nothing live, and 3 flagged `expired` while
holding a paid subscription they had just bought.

Neither direction is cosmetic.

  * A stale `expired` on a paying customer is skipped by enforce_usage_caps,
    which scans `customer__status="active"`. That customer can spend an
    unlimited allowance on a capped package, because the sweep that would cut
    them off never looks at them.
  * A stale `active` on somebody holding nothing inflates every dashboard
    counting the flag, and puts them in the set that auto-failover migrates
    when a router goes down -- work done on behalf of a customer with nothing
    to serve.

The paths that clear it are the ones that expire a subscription *and* notice
nothing else covers the customer: enforce_subscription_expiry and check_cap.
Anything else that ends a subscription leaves the flag behind -- the
abandoned-checkout sweep, the window trim, an operator expiring a row by hand,
a subscription that was already expired when the sweep ran and so never
appeared in its query. Each of those is correct in itself; none of them owns
this flag.

So rather than add the same three lines to every path that might end a
subscription, this derives the flag from the one question that decides it --
does the customer hold a live paid subscription -- and corrects whatever
disagrees. A reconciler behind the writers, for the same reason
disable_orphan_hotspot_users sits behind eviction.
"""

import logging

from django.utils import timezone

from billing.models import Customer, Subscription

logger = logging.getLogger(__name__)


def sync_customer_status(*, apply=False, now=None):
    """
    Bring every customer's status into line with what they hold.

    Returns (activated, expired) -- the customers whose flag was wrong in each
    direction. Reports unless `apply`.

    Deliberately narrow: it only ever moves between "active" and "expired".
    A customer an operator has suspended or blocked by hand holds a status
    this does not recognise, and guessing at those would undo a decision.
    """
    now = now or timezone.now()

    entitled = set(
        Subscription.objects.all_tenants()
        .filter(status="active", invoice__payment_status="paid",
                expiry_date__gt=now)
        .values_list("customer_id", flat=True)
    )

    # Only the two statuses this owns. Anything else on a customer row was put
    # there by a person and is left alone.
    rows = (Customer.objects.all_tenants()
            .filter(status__in=("active", "expired"))
            .only("id", "status", "full_name", "phone"))

    activate, expire = [], []
    for c in rows:
        holds = c.pk in entitled
        if holds and c.status != "active":
            activate.append(c)
        elif not holds and c.status != "expired":
            expire.append(c)

    if apply:
        if activate:
            Customer.objects.all_tenants().filter(
                pk__in=[c.pk for c in activate]).update(status="active")
        if expire:
            Customer.objects.all_tenants().filter(
                pk__in=[c.pk for c in expire]).update(status="expired")
        logger.info(
            "[status] corrected %s customer(s) to active and %s to expired",
            len(activate), len(expire))

    for c in activate:
        logger.warning(
            "[status] customer %s (%s) holds a live paid subscription but was "
            "flagged %r — cap enforcement skips those",
            c.pk, c.phone, c.status)

    return activate, expire
