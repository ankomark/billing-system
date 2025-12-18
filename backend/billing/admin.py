from django.contrib import admin
from .models import (
    User,
    RouterDevice,  # <-- ADD THIS
    Customer, 
    Package, 
    Subscription,
    Invoice, 
    Payment,
    Voucher,  # <-- You already have this
    ExpiryReminderLog,  # <-- ADD THIS
    MpesaTransaction,
    SystemSetting
)
from django.contrib.auth.admin import UserAdmin


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    model = User
    list_display = ("username", "email", "role", "is_active", "is_staff")
    fieldsets = UserAdmin.fieldsets + (
        ("Role Info", {"fields": ("role",)}),
    )


@admin.register(RouterDevice)  # <-- ADD THIS
class RouterDeviceAdmin(admin.ModelAdmin):
    list_display = ("name", "ip_address", "api_port", "is_active")
    search_fields = ("name", "ip_address")
    list_filter = ("is_active",)


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "phone",
        "connection_type",
        "status",
        "created_at",
    )
    search_fields = ("full_name", "phone")
    list_filter = ("status", "connection_type")


@admin.register(Package)
class PackageAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "download_speed",
        "upload_speed",
        "price",
        "duration_days",
    )
    search_fields = ("name",)


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("customer", "package", "status", "expiry_date")
    list_filter = ("status",)


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "customer", "total_amount", "payment_status", "created_at")
    search_fields = ("invoice_number",)
    list_filter = ("payment_status",)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("customer", "amount", "method", "paid_at")
    list_filter = ("method",)


@admin.register(Voucher)
class VoucherAdmin(admin.ModelAdmin):
    list_display = ("code", "subscription", "expires_at", "is_active")
    search_fields = ("code",)
    list_filter = ("is_active", "expires_at")


@admin.register(ExpiryReminderLog)  # <-- ADD THIS
class ExpiryReminderLogAdmin(admin.ModelAdmin):
    list_display = ("subscription", "reminder_type", "sent_at")
    list_filter = ("reminder_type", "sent_at")


@admin.register(MpesaTransaction)
class MpesaTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "mpesa_receipt",
        "amount",
        "phone_number",
        "account_reference",
        "status",
        "processed",
        "created_at",
    )
    search_fields = ("mpesa_receipt", "phone_number", "account_reference")
    list_filter = ("status", "processed")

    actions = ["process_as_payment"]

    def process_as_payment(self, request, queryset):
        """
        Admin action to process selected successful MpesaTransactions
        and create Payment records if possible.
        """
        processed_count = 0
        failed_count = 0

        for tx in queryset:
            # Skip already processed or failed
            if tx.processed and tx.payment:
                continue

            # Only try to process successful ones
            if tx.status != "success":
                failed_count += 1
                continue

            # Try to find invoice
            try:
                invoice = Invoice.objects.get(invoice_number=tx.account_reference)
            except Invoice.DoesNotExist:
                tx.error_message = "Invoice not found during admin processing"
                tx.processed = True
                tx.status = "failed"
                tx.save()
                failed_count += 1
                continue

            # Amount check
            if float(tx.amount) != float(invoice.total_amount):
                tx.error_message = f"Amount mismatch during admin processing. Mpesa: {tx.amount}, Invoice: {invoice.total_amount}"
                tx.processed = True
                tx.status = "failed"
                tx.save()
                failed_count += 1
                continue

            # Avoid duplicate Payment
            from .models import Payment  # local import to avoid cycles

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

        self.message_user(
            request,
            f"Processed: {processed_count}, Failed: {failed_count}",
        )

    process_as_payment.short_description = "Process selected M-Pesa transactions as payments"


@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    list_display = ("key", "value")
    search_fields = ("key",)