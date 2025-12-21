import logging
from celery import shared_task
from django.db import transaction

from billing.models import Invoice
from billing.mpesa_client import initiate_stk_push

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=20,
    retry_kwargs={"max_retries": 3},
    retry_jitter=True,
)
def initiate_stk_push_task(self, invoice_id: int, phone_number: str) -> dict:
    """
    Safely initiate M-Pesa STK push in background.
    """

    try:
        invoice = Invoice.objects.select_for_update().get(id=invoice_id)
    except Invoice.DoesNotExist:
        logger.error(f"[stk_task] Invoice {invoice_id} not found")
        return {"success": False, "error": "Invoice not found"}

    # ⛔ Idempotency guard
    if invoice.payment_status in ("pending", "paid"):
        logger.warning(
            f"[stk_task] Duplicate STK prevented for {invoice.invoice_number}"
        )
        return {"success": False, "error": "Duplicate STK prevented"}

    # 🔐 Atomic state change
    with transaction.atomic():
        invoice.payment_status = "pending"
        invoice.save(update_fields=["payment_status"])

    response = initiate_stk_push(
        phone_number=phone_number,
        amount=invoice.total_amount,
        account_reference=invoice.invoice_number,
        description="WiFi Subscription Payment",
    )

    logger.info(
        f"[stk_task] STK sent for invoice {invoice.invoice_number}"
    )

    return response
