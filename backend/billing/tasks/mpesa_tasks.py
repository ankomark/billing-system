import logging
from celery import shared_task
from django.db import transaction

from billing.models import Invoice
from billing.mpesa_client import PaymentsNotConfigured, initiate_stk_push
from billing.tenancy import tenant_context

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

    The invoice decides which operator this is for. A worker has no request, so
    without entering that operator's context the Daraja credentials would come
    from whichever operator get_setting() happened to find — pushing the charge
    through the wrong till and crediting the wrong business.
    """

    with transaction.atomic():
        try:
            # Unscoped by pk: no tenant context exists yet, the row supplies it.
            invoice = (
                Invoice.objects.all_tenants()
                .select_for_update()
                .select_related("tenant")
                .get(id=invoice_id)
            )
        except Invoice.DoesNotExist:
            logger.error(f"[stk_task] Invoice {invoice_id} not found")
            return {"success": False, "error": "Invoice not found"}

        if invoice.payment_status in ("pending", "paid"):
            logger.warning(
                f"[stk_task] Duplicate STK prevented for {invoice.invoice_number}"
            )
            return {"success": False, "error": "Duplicate STK prevented"}

        invoice.payment_status = "pending"
        invoice.save(update_fields=["payment_status"])

    try:
        with tenant_context(invoice.tenant_id):
            response = initiate_stk_push(
                phone_number=phone_number,
                amount=invoice.total_amount,
                account_reference=invoice.invoice_number,
                description="WiFi Subscription Payment",
                tenant=invoice.tenant,
            )
    except PaymentsNotConfigured as exc:
        # Release the invoice so the customer can retry once the operator has
        # finished onboarding with Safaricom. Leaving it "pending" would block
        # every later attempt via the duplicate guard above.
        Invoice.objects.all_tenants().filter(id=invoice_id).update(
            payment_status="unpaid"
        )
        logger.error(f"[stk_task] {invoice.invoice_number}: {exc}")
        # Not retryable — waiting on a human, not a transient failure.
        return {"success": False, "error": str(exc)}

    # Recorded before anything else can happen, because the callback may
    # arrive before this function has finished returning. Without it a failed
    # push comes back carrying no reference to the invoice — Safaricom omits
    # CallbackMetadata entirely unless ResultCode is 0 — and the result is
    # unattributable.
    checkout_id = (response or {}).get("CheckoutRequestID")
    if checkout_id:
        Invoice.objects.all_tenants().filter(id=invoice_id).update(
            mpesa_checkout_request_id=checkout_id
        )

    logger.info(
        "[stk_task] STK sent for invoice %s (CheckoutRequestID %s)",
        invoice.invoice_number, checkout_id or "none returned",
    )
    return response
