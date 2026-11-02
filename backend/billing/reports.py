from datetime import datetime, timedelta

from django.db.models import Sum, Count
from django.db.models.functions import TruncMonth
from django.utils import timezone

from .models import Payment, Invoice, Subscription


def revenue_summary():
    # localdate(), not now().date(): the latter is the UTC date, while the
    # database evaluates paid_at__date in TIME_ZONE (Africa/Nairobi, UTC+3).
    # Between 21:00 and midnight UTC those are different days, so "today's
    # revenue" silently reported the wrong figure for three hours daily —
    # invisible on SQLite, which converts differently.
    today = timezone.localdate()
    start_month = today.replace(day=1)
    start_year = today.replace(month=1, day=1)
    # The whole of last month. Without it the dashboard had no memory: at
    # midnight on the 1st "This month" went back to zero and the month that
    # had just closed was nowhere on the page.
    start_last_month = (start_month - timedelta(days=1)).replace(day=1)

    return {
        "today": Payment.objects.filter(
            paid_at__date=today
        ).aggregate(total=Sum("amount"))["total"] or 0,

        "last_month": Payment.objects.filter(
            paid_at__date__gte=start_last_month, paid_at__date__lt=start_month
        ).aggregate(total=Sum("amount"))["total"] or 0,

        "this_month": Payment.objects.filter(
            paid_at__date__gte=start_month
        ).aggregate(total=Sum("amount"))["total"] or 0,

        "this_year": Payment.objects.filter(
            paid_at__date__gte=start_year
        ).aggregate(total=Sum("amount"))["total"] or 0,
    }


def revenue_by_method():
    return (
        Payment.objects
        .values("method")
        .annotate(total=Sum("amount"), count=Count("id"))
        .order_by("-total")
    )


def revenue_by_package():
    return (
        Payment.objects
        .select_related("subscription__package")
        .values("subscription__package__name")
        .annotate(total=Sum("amount"), count=Count("id"))
        .order_by("-total")
    )


def customer_stats():
    """
    The counts on the operator's dashboard.

    Subscriptions are counted paid-only, and that is the whole subtlety here. A
    subscription is created the moment somebody starts a purchase -- born
    status="active" with an unpaid invoice -- so every walk-up who opened the
    buy page, triggered an M-Pesa prompt and changed their mind leaves one
    behind, and it stays "active" until it ages out.

    Counting those was overstating the headline figure by about a fifth: on
    2026-09-07 production held 355 active subscriptions, of which 80 had never
    been paid for. The same fault sat in the tile beside it, which is labelled
    "not paying" and included 2327 abandoned checkouts among 10974.

    Nobody is being served on an unpaid subscription -- provisioning happens in
    Payment.save() -- so an operator reading "active" as "people I am carrying"
    was reading it correctly and getting the wrong number. report_usage_caps
    already filtered this way, and says why in its own code: "every
    subscription is born active with an unpaid invoice, so an abandoned
    purchase is not what anybody is being served on."

    The invoice counts below are deliberately not filtered. Those are about
    invoices rather than subscribers, and an unpaid invoice is exactly what
    that tile exists to show.
    """
    paid = Subscription.objects.filter(invoice__payment_status="paid")

    return {
        "active_subscriptions": paid.filter(status="active").count(),
        "expired_subscriptions": paid.filter(status="expired").count(),
        "unpaid_invoices": Invoice.objects.filter(payment_status="unpaid").count(),
        "pending_invoices": Invoice.objects.filter(payment_status="pending").count(),
    }


# ─────────────────────────────────────────────────────────── month by month

def _add_months(day, n):
    """The first of the month `n` months from `day`'s month (n may be negative)."""
    years, month0 = divmod(day.month - 1 + n, 12)
    return day.replace(year=day.year + years, month=month0 + 1, day=1)


def _local_midnight(day):
    return timezone.make_aware(datetime(day.year, day.month, day.day))


def monthly_revenue(months=12):
    """
    Earnings month by month, newest first, for the last `months` months
    including the one in progress.

    Calendar months in the operator's time zone (Africa/Nairobi): a payment at
    01:00 on the 1st belongs to the new month, which a UTC month would put in
    the old one.

    Every month in the window gets a row, earning or not -- a month with
    nothing in it is information, and a gap in the table reads as a bug.

    Free access ("comp") is a payment of zero: it adds nothing to earnings and
    is not counted as a payment or a paying customer, but its count is shown,
    because free internet should be countable.

    `change` compares with the month before. For the month in progress that
    would compare a part-month with a whole one and always look like a fall,
    so it is compared with last month up to the same point -- the same day
    and time of day.
    """
    months = max(1, min(int(months), 36))
    today = timezone.localdate()
    this_month = today.replace(day=1)
    # One month further back than shown, so the oldest row has a comparison.
    first = _add_months(this_month, -months)

    rows = (
        Payment.objects
        .filter(paid_at__gte=_local_midnight(first))
        .annotate(month=TruncMonth("paid_at"))
        .values("month", "method")
        .annotate(revenue=Sum("amount"), count=Count("id"))
    )
    customers = (
        Payment.objects
        .filter(paid_at__gte=_local_midnight(first))
        .exclude(method="comp")
        .annotate(month=TruncMonth("paid_at"))
        .values("month")
        .annotate(n=Count("customer", distinct=True))
    )

    def key(dt):
        dt = timezone.localtime(dt) if timezone.is_aware(dt) else dt
        return (dt.year, dt.month)

    by_month = {}
    for r in rows:
        m = by_month.setdefault(key(r["month"]), {
            "revenue": 0.0, "payments": 0, "comps": 0, "by_method": {}})
        amount = float(r["revenue"] or 0)
        if r["method"] == "comp":
            m["comps"] += r["count"]
            continue
        m["revenue"] += amount
        m["payments"] += r["count"]
        m["by_method"][r["method"]] = m["by_method"].get(r["method"], 0.0) + amount
    paying = {key(r["month"]): r["n"] for r in customers}

    # The same point in last month, for the month in progress: as far into
    # it as we now are into this one, to the minute. Whole days would compare
    # a morning of today with all of that day last month, so the month in
    # progress would read as down every day until midnight.
    elapsed = timezone.now() - _local_midnight(this_month)
    days_in = elapsed.days + 1
    last_month = _add_months(this_month, -1)
    same_point = min(_local_midnight(last_month) + elapsed, _local_midnight(this_month))
    prior_to_date = float(
        Payment.objects.filter(
            paid_at__gte=_local_midnight(last_month),
            paid_at__lt=same_point,
        ).aggregate(t=Sum("amount"))["t"] or 0
    )

    result = []
    for i in range(months):
        start = _add_months(this_month, -i)
        prev = _add_months(start, -1)
        m = by_month.get((start.year, start.month), {})
        revenue = m.get("revenue", 0.0)
        in_progress = i == 0
        against = prior_to_date if in_progress else by_month.get(
            (prev.year, prev.month), {}).get("revenue", 0.0)
        result.append({
            "month": f"{start.year}-{start.month:02d}",
            "revenue": round(revenue, 2),
            "payments": m.get("payments", 0),
            "customers": paying.get((start.year, start.month), 0),
            "comps": m.get("comps", 0),
            "by_method": {k: round(v, 2) for k, v in m.get("by_method", {}).items()},
            "change": _pct_change(revenue, against),
            "change_against": (
                "same point last month" if in_progress else "previous month"),
            "in_progress": in_progress,
        })
    return result


def _pct_change(current, previous):
    """Percentage movement, or None when there is nothing to compare against."""
    if not previous:
        return None
    return round((float(current) - float(previous)) / float(previous) * 100, 1)
