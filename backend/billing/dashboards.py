from django.utils import timezone
from django.db.models import Q

from .models import Invoice, MessageLog, MpesaTransaction
def unpaid_invoices():
    """
    Invoices that have not been paid at all
    """
    return Invoice.objects.filter(payment_status="unpaid").select_related(
        "customer", "subscription"
    ).order_by("-created_at")


def pending_invoices():
    """
    STK push sent but payment not yet confirmed
    """
    return Invoice.objects.filter(payment_status="pending").select_related(
        "customer", "subscription"
    ).order_by("-created_at")
def failed_mpesa_transactions():
    """
    Transactions that failed or could not be processed
    """
    return MpesaTransaction.objects.filter(
        Q(status="failed") | Q(processed=False)
    ).order_by("-created_at")


def message_logs(channel=None, status=None):
    """
    Delivery records, newest first, optionally narrowed.

    `status` takes the stored values, and also "errors" — refused and failed
    together, which is what the page opens on and the reason it exists. An
    operator looking here has messages that did not arrive; making them read
    two filters to see both halves of that would be a poor greeting.
    """
    qs = MessageLog.objects.all()

    if channel in ("sms", "whatsapp"):
        qs = qs.filter(channel=channel)

    if status == "errors":
        qs = qs.filter(status__in=("refused", "failed"))
    elif status in ("sent", "refused", "failed"):
        qs = qs.filter(status=status)

    return qs.order_by("-created_at")
