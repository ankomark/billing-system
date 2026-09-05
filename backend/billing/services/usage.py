"""
What a subscriber has used, read once and the same way everywhere.

Two places needed this answer and each computed it separately: the figure a
customer is shown on the portal, and the comparison that decides whether they
are cut off. Two sums of the same thing drift, and the direction they drift in
is somebody being disconnected while their own screen says they have data left.

Both go through here now, and both read the same two sources.

    Complete days      →  UsageRecord, one row per customer per day
    Today, and the
    part-day at the
    start of a window  →  the raw five-minute deltas

The split exists so the raw rows can eventually be thrown away. One row per
subscriber per five minutes is 2.88 million a day at ten thousand subscribers;
the daily rollup of the same thing is ten thousand. Nothing is pruned yet —
that comes after these two agree on live data for a while — so today the
rollups are a second opinion rather than the only one.
"""

import logging

from django.db.models import Sum
from django.utils import timezone

logger = logging.getLogger(__name__)


# One megabyte, as the routers and the operators both mean it: 1024 * 1024.
#
# Not 1,000,000. RouterOS `limit-bytes-total` counts binary megabytes, and if
# the ceiling we enforce in Python is 4.9% smaller than the one we write onto
# the hardware, the two disagree about when a 300 MB bundle is spent — and the
# subscriber gets cut off by whichever is stingier while the other still says
# they have data left.
MB = 1024 * 1024


def cap_bytes_for(customer, subscription=None):
    """
    The ceiling that applies to one subscriber, in bytes. 0 means unlimited.

    Every caller used to spell this itself, and they did not agree. Some wrote
    `customer.custom_data_cap or package.cap`, which reads a deliberate
    unlimited override (0) as "no override" and falls back to the package's
    ceiling — the one subscriber an operator has explicitly uncapped is the
    one that spelling re-caps. Others tested `is None` and got it right. The
    two live side by side in this codebase today, which is how a subscriber
    ends up cut off by one code path while another tells them they are fine.

    There is now one answer. `None` on the customer means inherit; `0` means
    unlimited and is honoured as an override.
    """
    if customer is not None:
        override = getattr(customer, "custom_data_cap_mb", None)
        if override is not None:
            return int(override) * MB

    package = getattr(subscription, "package", None) if subscription else None
    if package is None:
        return 0

    return int(package.data_cap_mb or 0) * MB


def caps_enforced_from():
    """
    The moment data caps started applying, or None if they always have.

    Set USAGE_CAPS_ENFORCE_FROM to an ISO timestamp and a subscription bought
    before it is not measured against any cap.

    This exists because of what a cap window means. The window is the
    subscription, so switching caps on for the first time judges every bundle
    already running against a ceiling that did not exist when it was sold --
    and those bundles have been accumulating usage since nothing counted. On
    2026-09-05 that disconnected 143 paying subscribers inside six minutes,
    every one of them still inside the term they had paid for, several sitting
    at 9GB against a 500MB cap they had never been told about.

    Nobody was wrong to have used 9GB on a package sold as unlimited. The cap
    is a promise about what you get *when you buy*, so it applies to what is
    bought from here on, and the bundles already running finish on the terms
    they were sold under. The longest of those is three weeks, after which this
    setting stops mattering and can be removed.
    """
    import os

    raw = os.getenv("USAGE_CAPS_ENFORCE_FROM", "").strip()
    if not raw:
        return None

    from django.utils.dateparse import parse_datetime, parse_date

    parsed = parse_datetime(raw)
    if parsed is None:
        day = parse_date(raw)
        if day is None:
            logger.error(
                "[usage-caps] USAGE_CAPS_ENFORCE_FROM=%r is not an ISO date or "
                "datetime; ignoring it and enforcing caps on every "
                "subscription", raw,
            )
            return None
        parsed = timezone.datetime.combine(day, timezone.datetime.min.time())

    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def cap_applies_to(subscription):
    """
    Whether this subscription is subject to its package's data cap.

    False for a bundle sold before caps were switched on -- see
    caps_enforced_from(). Consulted by every path that can act on a cap: the
    polled check, the sweep behind it, and the byte ceiling written onto a
    hotspot user. Missing it in any one of them re-opens the hole in a
    different place; the router one is the quiet one, because an exhausted
    allowance there becomes a limit-bytes-total the subscriber hits without
    anything of ours having decided to cut them off.
    """
    from_when = caps_enforced_from()
    if from_when is None:
        return True

    started = window_start(subscription)
    if started is None:
        return True

    return started >= from_when


def window_start(subscription):
    """
    The moment a subscriber's allowance started counting.

    The subscription, not the calendar month. The cut-off used to count from
    the first of the month while the subscriber's own screen counted from the
    day they bought — so somebody who bought a 300 MB bundle on the 28th was
    measured against traffic from the 1st and cut off before using any of it,
    with the portal insisting the whole bundle was untouched.

    A bundle is sold as a bundle. What it covers is what was bought, starting
    when it was bought, and renewing creates a new row here — which is what
    makes a renewal reset the allowance without anything having to clear it.
    """
    return getattr(subscription, "start_date", None)


def _raw_model(customer):
    from billing.models import HotspotUsageRecord, PPPoEUsageRecord

    return (
        HotspotUsageRecord if customer.connection_type == "hotspot"
        else PPPoEUsageRecord
    )


def usage_since(customer, since):
    """
    Bytes used by one subscriber since a moment, up or down, as one number.

    `since` is rarely midnight — a subscription starts when it is bought — so
    the day it falls in is counted from the raw rows, and only whole days after
    it come from the rollup. Taking the whole first day from the rollup would
    charge somebody for traffic from before they paid.
    """
    from billing.models import UsageRecord

    if since is None:
        return 0

    now = timezone.now()
    today = timezone.localdate(now)

    # The first day the rollup may be used for: the one after `since` lands in,
    # unless `since` is exactly a day boundary.
    local_since = timezone.localtime(since)
    first_full_day = local_since.date()
    if (local_since.hour, local_since.minute, local_since.second) != (0, 0, 0):
        first_full_day = first_full_day + timezone.timedelta(days=1)

    # Which whole days actually have a rollup. Not "which days ought to" — the
    # rollup runs at 01:20, so between midnight and then yesterday has none,
    # and assuming otherwise silently dropped a whole day from everybody's
    # total for eighty minutes every night. The rollup is an optimisation over
    # the raw rows, not a precondition for reading them.
    rolled_rows = list(
        UsageRecord.objects.all_tenants()
        .filter(
            tenant_id=customer.tenant_id,
            customer_id=customer.id,
            connection_type=customer.connection_type,
            date__gte=first_full_day,
            date__lt=today,
        )
        .values_list("date", "rx_bytes", "tx_bytes")
    )

    covered = {row[0] for row in rolled_rows}
    total = sum((row[1] or 0) + (row[2] or 0) for row in rolled_rows)

    # Everything else comes from the raw deltas — today, the part-day at the
    # start of the window, and any day the rollup has not reached. Days it has
    # reached are excluded here, so nothing is counted twice.
    model = _raw_model(customer)
    raw = model.objects.all_tenants().filter(
        tenant_id=customer.tenant_id,
        customer_id=customer.id,
        period_start__gte=since,
    )
    if covered:
        raw = raw.exclude(period_start__date__in=covered)

    totals = raw.aggregate(down=Sum("download_bytes"), up=Sum("upload_bytes"))
    return total + (totals["down"] or 0) + (totals["up"] or 0)


def roll_up_day(day):
    """
    Fold one finished day of raw deltas into a row per subscriber.

    Idempotent, because a task that cannot be safely re-run is a task nobody
    dares re-run after it half-fails. Re-rolling a day recomputes its rows from
    the raw records rather than adding to them.

    Returns how many rows it wrote.
    """
    from billing.models import HotspotUsageRecord, PPPoEUsageRecord, UsageRecord

    start = timezone.make_aware(
        timezone.datetime.combine(day, timezone.datetime.min.time()),
        timezone.get_current_timezone(),
    )
    end = start + timezone.timedelta(days=1)

    written = 0
    for model, kind in ((PPPoEUsageRecord, "pppoe"), (HotspotUsageRecord, "hotspot")):
        rows = (
            model.objects.all_tenants()
            .filter(period_start__gte=start, period_start__lt=end)
            .values("tenant_id", "customer_id")
            .annotate(rx=Sum("download_bytes"), tx=Sum("upload_bytes"))
        )
        for row in rows:
            UsageRecord.objects.all_tenants().update_or_create(
                customer_id=row["customer_id"],
                date=day,
                connection_type=kind,
                defaults={
                    "tenant_id": row["tenant_id"],
                    "rx_bytes": row["rx"] or 0,
                    "tx_bytes": row["tx"] or 0,
                },
            )
            written += 1

    return written
