from django.utils import timezone
from django.db.models import Q

from .models import Invoice, MpesaTransaction
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
