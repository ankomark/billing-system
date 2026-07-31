"""
The operator's own analytics.

Everything here is scoped by the default manager, so it answers for whichever
operator is asking and never across them.

Two things shape the design.

Aggregate in SQL, one query per question. These tables only grow, and the
tempting shape — loop the days, loop the packages, count each — is the one that
looks fine on a demo and falls over on a real estate.

And a number on its own rarely answers anything. "Sh 5,390 today" is not
information until it sits next to yesterday; a package's revenue means little
without how many purchases produced it. So the figures come back paired with
their comparison rather than leaving the frontend to guess at one.
"""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import ExtractHour, TruncDate
from django.utils import timezone

from .models import Customer, Payment, Station, Subscription


def _pct_change(current, previous):
    """
    Percentage movement, or None when there is nothing to compare against.

    None rather than 0 or 100: "no change" and "nothing to compare with" are
    different statements, and showing +100% because last month was the first
    month is worse than showing nothing.
    """
    current = float(current or 0)
    previous = float(previous or 0)
    if previous == 0:
        return None
    return round((current - previous) / previous * 100, 1)


def _sum(qs, start, end, station=None):
    qs = qs.filter(paid_at__gte=start, paid_at__lt=end)
    if station:
        qs = qs.filter(customer__router__station_id=station)
    return qs.aggregate(t=Sum("amount"))["t"] or Decimal("0")


def performance_pulse(station=None):
    """
    Today, month to date, and the last 30 days — each against the comparable
    window before it, not against zero.
    """
    now = timezone.now()
    today = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    payments = Payment.objects.all()

    yesterday = today - timedelta(days=1)
    month_start = today.replace(day=1)
    # The same number of days into last month, so a comparison made on the 3rd
    # is against the first three days of last month rather than all of it.
    days_in = (today - month_start).days + 1
    last_month_start = (month_start - timedelta(days=1)).replace(day=1)
    last_month_end = min(last_month_start + timedelta(days=days_in), month_start)

    thirty_start = now - timedelta(days=30)
    prior_thirty_start = now - timedelta(days=60)

    today_now = _sum(payments, today, now, station)
    yesterday_amt = _sum(payments, yesterday, today, station)
    mtd = _sum(payments, month_start, now, station)
    last_mtd = _sum(payments, last_month_start, last_month_end, station)
    thirty = _sum(payments, thirty_start, now, station)
    prior_thirty = _sum(payments, prior_thirty_start, thirty_start, station)

    return {
        "today": {
            "amount": float(today_now),
            "delta": _pct_change(today_now, yesterday_amt),
            "against": "vs yesterday",
        },
        "month_to_date": {
            "amount": float(mtd),
            "delta": _pct_change(mtd, last_mtd),
            "against": "vs same days last month",
        },
        "last_30_days": {
            "amount": float(thirty),
            "delta": _pct_change(thirty, prior_thirty),
            "against": "vs previous 30 days",
        },
    }


def revenue_series(start, end, station=None):
    """Daily revenue and transaction count, gap-filled."""
    qs = Payment.objects.filter(paid_at__gte=start, paid_at__lt=end)
    if station:
        qs = qs.filter(customer__router__station_id=station)

    rows = {
        r["day"]: r
        for r in qs.annotate(day=TruncDate("paid_at"))
        .values("day")
        .annotate(revenue=Sum("amount"), transactions=Count("id"))
    }

    series = []
    day = timezone.localtime(start).date()
    # Inclusive of the final day. Exclusive dropped today from the chart, which
    # on a dashboard is the day anyone is actually looking for.
    last = timezone.localtime(end).date()
    while day <= last:
        row = rows.get(day)
        series.append({
            "day": day.isoformat(),
            # A missing day is zero, not absent. Absent renders as the line
            # falling to the floor, which is a different claim from "nobody
            # paid that day".
            "revenue": float(row["revenue"]) if row else 0.0,
            "transactions": row["transactions"] if row else 0,
        })
        day += timedelta(days=1)
    return series


def revenue_by_package(start, end, station=None):
    """Which packages actually earn, with the volume behind each figure."""
    qs = Payment.objects.filter(paid_at__gte=start, paid_at__lt=end)
    if station:
        qs = qs.filter(customer__router__station_id=station)
    rows = (
        qs.values("subscription__package__name")
        .annotate(
            revenue=Sum("amount"),
            purchases=Count("id"),
            customers=Count("customer", distinct=True),
        )
        .order_by("-revenue")
    )
    return [
        {
            "name": r["subscription__package__name"] or "Unknown",
            "revenue": float(r["revenue"] or 0),
            "purchases": r["purchases"],
            "customers": r["customers"],
        }
        for r in rows
    ]


def revenue_by_method(start, end, station=None):
    qs = Payment.objects.filter(paid_at__gte=start, paid_at__lt=end)
    if station:
        qs = qs.filter(customer__router__station_id=station)
    rows = (
        qs.values("method")
        .annotate(revenue=Sum("amount"), count=Count("id"))
        .order_by("-revenue")
    )
    return [
        {"method": r["method"], "revenue": float(r["revenue"] or 0), "count": r["count"]}
        for r in rows
    ]


def peak_hours(start, end, station=None):
    """
    When people actually buy, hour by hour.

    Worth knowing for a hotspot business: it says when to have credit loaded and
    when support is worth staffing, and neither is guessable from a daily total.
    """
    qs = Payment.objects.filter(paid_at__gte=start, paid_at__lt=end)
    if station:
        qs = qs.filter(customer__router__station_id=station)
    rows = dict(
        qs.annotate(hour=ExtractHour("paid_at"))
        .values("hour")
        .annotate(n=Count("id"))
        .values_list("hour", "n")
    )
    # Every hour present, so the shape of the day is visible rather than only
    # the hours that happened to have a sale.
    return [{"hour": h, "purchases": rows.get(h, 0)} for h in range(24)]


def expiring_soon(station=None):
    """
    Money about to stop arriving.

    Revenue already collected is history. This is the part an operator can still
    act on, which is why it sits beside the totals rather than in a report
    nobody opens.
    """
    now = timezone.now()
    end_of_today = timezone.localtime(now).replace(
        hour=23, minute=59, second=59, microsecond=999999)
    qs = Subscription.objects.filter(status="active").select_related("package")
    if station:
        qs = qs.filter(customer__router__station_id=station)

    def bucket(sub_qs):
        row = sub_qs.aggregate(n=Count("id"), value=Sum("package__price"))
        return {"count": row["n"] or 0, "value": float(row["value"] or 0)}

    return {
        "today": bucket(qs.filter(expiry_date__gte=now, expiry_date__lte=end_of_today)),
        "next_7_days": bucket(
            qs.filter(expiry_date__gt=end_of_today,
                      expiry_date__lte=now + timedelta(days=7))),
        "expired_last_7_days": bucket(
            Subscription.objects.filter(
                status="expired",
                expiry_date__gte=now - timedelta(days=7),
                expiry_date__lt=now,
            ).select_related("package")
        ),
    }


def customer_flow(start, end, station=None):
    """Who joined and who lapsed in the window, and what that was worth."""
    joined = Customer.objects.filter(created_at__gte=start, created_at__lt=end)
    lapsed = Subscription.objects.filter(
        status="expired", expiry_date__gte=start, expiry_date__lt=end)
    if station:
        joined = joined.filter(router__station_id=station)
        lapsed = lapsed.filter(customer__router__station_id=station)

    joined_value = (
        Payment.objects.filter(
            customer__in=joined.values("id"), paid_at__gte=start, paid_at__lt=end)
        .aggregate(t=Sum("amount"))["t"] or Decimal("0")
    )
    lapsed_value = lapsed.aggregate(t=Sum("package__price"))["t"] or Decimal("0")

    return {
        "joined": {"count": joined.count(), "value": float(joined_value)},
        "lapsed": {"count": lapsed.count(), "value": float(lapsed_value)},
        "net_value": float(joined_value - lapsed_value),
    }


def by_station(start, end):
    """
    Per-site performance.

    Only meaningful once an operator has more than one site; a single-site
    business gets an empty list and the page hides the panel rather than showing
    them one row that says what the totals already said.
    """
    stations = list(Station.objects.all())
    if len(stations) < 2:
        return []

    revenue = dict(
        Payment.objects.filter(paid_at__gte=start, paid_at__lt=end)
        .filter(customer__router__station__isnull=False)
        .values("customer__router__station")
        .annotate(t=Sum("amount"))
        .values_list("customer__router__station", "t")
    )
    customers = dict(
        Customer.objects.filter(router__station__isnull=False)
        .values("router__station")
        .annotate(n=Count("id"))
        .values_list("router__station", "n")
    )
    offline = dict(
        Station.objects.annotate(
            n=Count("routers", filter=Q(routers__is_active=True, routers__is_online=False))
        ).values_list("id", "n")
    )

    rows = [
        {
            "id": s.id,
            "name": s.name,
            "revenue": float(revenue.get(s.id) or 0),
            "customers": customers.get(s.id, 0),
            "routers_offline": offline.get(s.id, 0),
        }
        for s in stations
    ]
    rows.sort(key=lambda r: r["revenue"], reverse=True)
    return rows
