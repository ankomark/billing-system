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
