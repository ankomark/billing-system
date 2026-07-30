from django.core.management.base import BaseCommand
from billing.models import MpesaTransaction, Invoice, Payment
from billing.tenancy import tenant_context


class Command(BaseCommand):
    help = "Reconcile unprocessed successful M-Pesa transactions with invoices and payments"

    def handle(self, *args, **options):
        # Cross-operator sweep by design: reconciles every operator's
        # unprocessed receipts. Each row is then handled as its own operator.
        qs = MpesaTransaction.objects.all_tenants().filter(
            status="success",
            processed=False,
        ).select_related("tenant")

        processed_count = 0
        failed_count = 0

        for tx in qs:
            try:
                # invoice_number is globally unique, so this lookup is
                # deliberately unscoped — it is what identifies the operator.
                invoice = Invoice.objects.all_tenants().select_related(
                    "customer", "subscription", "tenant"
                ).get(invoice_number=tx.account_reference)
            except Invoice.DoesNotExist:
                tx.error_message = "Invoice not found during reconcile command"
                tx.processed = True
                tx.status = "failed"
                tx.save()
                failed_count += 1
                continue

            if float(tx.amount or 0) != float(invoice.total_amount):
                tx.error_message = f"Amount mismatch. Mpesa: {tx.amount}, Invoice: {invoice.total_amount}"
                tx.processed = True
                tx.status = "failed"
                tx.save()
                failed_count += 1
                continue

            if Payment.objects.all_tenants().filter(reference=tx.mpesa_receipt).exists():
                tx.error_message = "Payment already exists for this Mpesa receipt"
                tx.processed = True
                tx.save()
                continue

            subscription = invoice.subscription
            customer = invoice.customer

            # Act as the owning operator: Payment.save() picks a router and
            # sends the welcome message using their hardware and credentials.
            with tenant_context(invoice.tenant_id):
                payment = Payment.objects.create(
                    tenant_id=invoice.tenant_id,
                    customer=customer,
                    subscription=subscription,
                    amount=tx.amount,
                    method="mpesa",
                    reference=tx.mpesa_receipt,
                )

            tx.invoice = invoice
            tx.payment = payment
            tx.processed = True
            tx.status = "success"
            tx.error_message = ""
            tx.save()

            processed_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Reconciliation complete. Processed: {processed_count}, Failed: {failed_count}"
            )
        )
