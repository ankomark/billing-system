from django.db.models import Sum, Count
from django.utils import timezone

from .models import Payment, Invoice, Subscription


def revenue_summary():
    today = timezone.now().date()
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
    return {
        "active_subscriptions": Subscription.objects.filter(status="active").count(),
        "expired_subscriptions": Subscription.objects.filter(status="expired").count(),
        "unpaid_invoices": Invoice.objects.filter(payment_status="unpaid").count(),
        "pending_invoices": Invoice.objects.filter(payment_status="pending").count(),
    }
