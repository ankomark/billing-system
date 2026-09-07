from django.db.models import Sum, Count
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

    return {
        "today": Payment.objects.filter(
            paid_at__date=today
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
