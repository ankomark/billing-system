from django.core.management.base import BaseCommand
from billing.models import MpesaTransaction, Invoice, Payment


class Command(BaseCommand):
    help = "Reconcile unprocessed successful M-Pesa transactions with invoices and payments"

    def handle(self, *args, **options):
        qs = MpesaTransaction.objects.filter(
            status="success",
            processed=False,
        )

        processed_count = 0
        failed_count = 0

        for tx in qs:
            try:
                invoice = Invoice.objects.get(invoice_number=tx.account_reference)
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

            if Payment.objects.filter(reference=tx.mpesa_receipt).exists():
                tx.error_message = "Payment already exists for this Mpesa receipt"
                tx.processed = True
                tx.save()
                continue

            subscription = invoice.subscription
            customer = invoice.customer

            payment = Payment.objects.create(
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
