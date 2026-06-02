from django.db import models, transaction
from django.utils import timezone
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from dateutil.relativedelta import relativedelta
import secrets
import string

from billing.notifications import notify_customer
from .utils import generate_invoice_number
from .fields import EncryptedCharField


# =====================================================
# USER MODEL
# =====================================================

class User(AbstractUser):
    ROLE_CHOICES = (
        ("superadmin", "Super Admin"),
        ("admin", "Admin"),
        ("staff", "Staff"),
        ("customer", "Customer"),
    )

    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default="admin",
    )

    def __str__(self):
        return f"{self.username} ({self.role})"


# =====================================================
# ROUTER
# =====================================================

class RouterDevice(models.Model):
    name = models.CharField(max_length=100)
    ip_address = models.GenericIPAddressField()
    username = models.CharField(max_length=100)
    password = EncryptedCharField()  # encrypted at rest; decrypted transparently on read
    api_port = models.IntegerField(default=8728)

    # failover priority: 1 = best
    priority = models.PositiveIntegerField(default=1)

    # health info
    is_active = models.BooleanField(default=True)
    is_online = models.BooleanField(default=False)
    last_seen = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    max_pppoe_sessions = models.PositiveIntegerField(default=0)  # 0 = unlimited

    def __str__(self):
        return self.name


# =====================================================
# CUSTOMER
# =====================================================

class Customer(models.Model):
    CONNECTION_TYPES = (
        ("pppoe", "PPPoE"),
        ("hotspot", "Hotspot"),
    )

    user = models.OneToOneField(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customer_profile",
    )

    full_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, unique=True)

    connection_type = models.CharField(
        max_length=10,
        choices=CONNECTION_TYPES,
        default="pppoe",
    )

    router = models.ForeignKey(
        RouterDevice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    # Identifiers (only one should be used based on connection_type)
    pppoe_username = models.CharField(max_length=100, blank=True)
    pppoe_password = EncryptedCharField(blank=True)  # encrypted at rest
    hotspot_username = models.CharField(max_length=100, blank=True)

    # Optional caps
    custom_data_cap_gb = models.PositiveIntegerField(null=True, blank=True)

    status = models.CharField(
        max_length=10,
        choices=(("active", "Active"), ("expired", "Expired")),
        default="active",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["status"],           name="customer_status_idx"),
            models.Index(fields=["pppoe_username"],   name="customer_pppoe_username_idx"),
            models.Index(fields=["connection_type"],  name="customer_connection_type_idx"),
        ]

    def clean(self):
        # Enforce data integrity: only one identifier should be set
        if self.connection_type == "pppoe" and self.hotspot_username:
            raise ValidationError("Hotspot username should be empty for PPPoE customers")
        if self.connection_type == "hotspot" and self.pppoe_username:
            raise ValidationError("PPPoE username should be empty for hotspot customers")

    def save(self, *args, **kwargs):
        # Skip full validation on partial updates — only validate complete saves
        if not kwargs.get("update_fields"):
            self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.full_name


# =====================================================
# ROUTER FAILOVER LOG
# =====================================================

class RouterFailoverLog(models.Model):
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="failover_logs"
    )
    from_router = models.ForeignKey(
        RouterDevice,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="failover_from"
    )
    to_router = models.ForeignKey(
        RouterDevice,
        on_delete=models.CASCADE,
        related_name="failover_to"
    )
    reason = models.CharField(max_length=50)  # auto_failover | admin_manual
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.customer.full_name} → {self.to_router.name}"


# =====================================================
# PACKAGE
# =====================================================

class Package(models.Model):
    DURATION_UNITS = [
        ("minutes", "Minutes"),
        ("hours", "Hours"),
        ("days", "Days"),
        ("weeks", "Weeks"),
        ("months", "Months"),
        ("years", "Years"),
    ]

    name = models.CharField(max_length=100)

    download_speed = models.PositiveIntegerField(help_text="Mbps")
    upload_speed = models.PositiveIntegerField(help_text="Mbps")

    price = models.DecimalField(max_digits=10, decimal_places=2)

    duration_value = models.PositiveIntegerField(
        help_text="Number of time units (e.g. 30, 12, 1)"
    )
    duration_unit = models.CharField(
        max_length=10,
        choices=DURATION_UNITS,
        default="days",
    )

    monthly_data_cap_gb = models.PositiveIntegerField(
        default=0,
        help_text="0 = unlimited"
    )

    is_hotspot = models.BooleanField(
        default=False,
        help_text="Is this a hotspot-only package?"
    )

    def clean(self):
        if self.duration_value <= 0:
            raise ValidationError("Duration value must be greater than zero")

    def calculate_expiry(self, start_date=None):
        start = start_date or timezone.now()

        if self.duration_unit == "minutes":
            return start + timezone.timedelta(minutes=self.duration_value)
        if self.duration_unit == "hours":
            return start + timezone.timedelta(hours=self.duration_value)
        if self.duration_unit == "days":
            return start + timezone.timedelta(days=self.duration_value)
        if self.duration_unit == "weeks":
            return start + timezone.timedelta(weeks=self.duration_value)
        if self.duration_unit == "months":
            return start + relativedelta(months=self.duration_value)
        if self.duration_unit == "years":
            return start + relativedelta(years=self.duration_value)

        raise ValueError("Invalid duration unit")

    def __str__(self):
        return (
            f"{self.name} | "
            f"{self.download_speed}/{self.upload_speed} Mbps | "
            f"{self.duration_value} {self.duration_unit}"
        )
# =====================================================
# SUBSCRIPTION
# =====================================================

class Subscription(models.Model):
    STATUS_CHOICES = (
        ("active", "Active"),
        ("expired", "Expired"),
        ("suspended", "Suspended"),
    )

    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="subscriptions"
    )
    package = models.ForeignKey(Package, on_delete=models.CASCADE)
    start_date = models.DateTimeField(default=timezone.now)
    expiry_date = models.DateTimeField(blank=True, null=True)
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="active"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["status"],                 name="subscription_status_idx"),
            models.Index(fields=["expiry_date"],            name="subscription_expiry_date_idx"),
            models.Index(fields=["status", "expiry_date"],  name="subscription_status_expiry_idx"),
        ]

    def save(self, *args, **kwargs):
        creating = self.pk is None

        if not self.expiry_date:
            self.expiry_date = self.package.calculate_expiry(self.start_date)

        # Keep DB writes atomic
        with transaction.atomic():
            super().save(*args, **kwargs)

            # Local imports to avoid circular import issues
            from .models import Invoice
            from .services.pppoe_service import generate_pppoe_credentials

            # Auto-create invoice only on creation
            if creating:
                Invoice.objects.create(
                    customer=self.customer,
                    subscription=self,
                    invoice_number=generate_invoice_number(),
                    total_amount=self.package.price,
                    payment_status="unpaid",
                )

            # Auto-generate PPPoE credentials only once
            if creating and self.customer.connection_type == "pppoe":
                if not self.customer.pppoe_username:
                    username, password = generate_pppoe_credentials(self.customer)
                    self.customer.pppoe_username = username
                    self.customer.pppoe_password = password
                    self.customer.save()

        # External side-effects outside the DB transaction (production safety)
        if creating and self.customer.connection_type == "pppoe":
            if self.customer.pppoe_username and self.customer.pppoe_password:
                try:
                    notify_customer(
                        self.customer.phone,
                        (
                            "Your PPPoE account is ready!\n"
                            f"Username: {self.customer.pppoe_username}\n"
                            f"Password: {self.customer.pppoe_password}\n"
                            f"Package: {self.package.name}\n"
                            f"Expires: {self.expiry_date}"
                        )
                    )
                except Exception:
                    # Notification failures should not break billing state
                    pass

    def __str__(self):
        return f"{self.customer.full_name} - {self.package.name}"


# =====================================================
# INVOICE
# =====================================================

class Invoice(models.Model):
    PAYMENT_STATUS = (
        ("paid", "Paid"),
        ("unpaid", "Unpaid"),
        ("pending", "Pending"),
    )

    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="invoices"
    )
    subscription = models.OneToOneField(
        Subscription, on_delete=models.CASCADE, related_name="invoice"
    )
    invoice_number = models.CharField(max_length=50, unique=True)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_status = models.CharField(
        max_length=10, choices=PAYMENT_STATUS, default="unpaid"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["payment_status"], name="invoice_payment_status_idx"),
            models.Index(fields=["created_at"],     name="invoice_created_at_idx"),
        ]

    def __str__(self):
        return self.invoice_number


# =====================================================
# VOUCHER UTILS
# =====================================================

def generate_voucher_code():
    prefix = "WIFI"
    random_part = "".join(
        secrets.choice(string.ascii_uppercase + string.digits)
        for _ in range(6)
    )
    return f"{prefix}-{random_part}"


# =====================================================
# VOUCHER
# =====================================================

class Voucher(models.Model):
    code = models.CharField(max_length=30, unique=True)
    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="vouchers"
    )

    bound_mac = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="MAC address bound on first use",
    )

    first_used_at = models.DateTimeField(null=True, blank=True)

    expires_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def is_valid(self):
        return (
            self.is_active
            and timezone.now() <= self.expires_at
        )

    def __str__(self):
        return self.code

# =====================================================
# PAYMENT
# =====================================================

class Payment(models.Model):
    PAYMENT_METHODS = (
        ("cash", "Cash"),
        ("mpesa", "M-Pesa"),
        ("bank", "Bank"),
    )

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="payments")
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=10, choices=PAYMENT_METHODS)
    reference = models.CharField(max_length=100, blank=True)
    paid_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["paid_at"],           name="payment_paid_at_idx"),
            models.Index(fields=["method"],             name="payment_method_idx"),
            models.Index(fields=["paid_at", "method"], name="payment_paid_at_method_idx"),
        ]

    def save(self, *args, **kwargs):
        creating = self.pk is None
        super().save(*args, **kwargs)

        if not creating:
            return

        from billing.router_service import (
            enable_customer_access,
            pick_best_router_for_new_customer,
        )

        customer = self.customer
        subscription = self.subscription
        package = subscription.package
        voucher_code = None

        # Resolve the best router BEFORE opening a DB transaction.
        # pick_best_router_for_new_customer() makes TCP connections to every
        # router (up to 5 s per router). Holding DB row locks during that
        # blocking I/O would block concurrent payments for other customers.
        assigned_router = None
        if not customer.router:
            router, _ = pick_best_router_for_new_customer()
            assigned_router = router

        # DB-only changes inside the transaction (no blocking I/O here)
        with transaction.atomic():
            invoice = subscription.invoice
            invoice.payment_status = "paid"
            invoice.save(update_fields=["payment_status"])

            subscription.status = "active"
            subscription.save(update_fields=["status"])

            if assigned_router:
                customer.router = assigned_router
                customer.save(update_fields=["router"])

            # Voucher is a DB write — belongs inside the transaction
            if customer.connection_type == "hotspot":
                voucher = Voucher.objects.create(
                    code=generate_voucher_code(),
                    subscription=subscription,
                    expires_at=subscription.expiry_date,
                )
                voucher_code = voucher.code

        # Capture primitives for the closure (avoids stale ORM objects)
        customer_id = customer.id
        phone = customer.phone
        pkg_name = package.name
        expiry = subscription.expiry_date
        pppoe_username = customer.pppoe_username
        pppoe_password = customer.pppoe_password
        connection_type = customer.connection_type
        _voucher_code = voucher_code

        def _post_payment_effects():
            # Runs after the DB transaction commits — safe to call external systems
            from billing.models import Customer as _Customer
            fresh = _Customer.objects.select_related("router").get(id=customer_id)
            enable_customer_access(fresh)

            if connection_type == "hotspot" and _voucher_code:
                message = (
                    "Welcome to Skylink WiFi!\n\n"
                    f"Package: {pkg_name}\n"
                    f"Valid Until: {expiry:%d %b %Y %I:%M %p}\n\n"
                    f"Voucher Code: {_voucher_code}\n\n"
                    "Just stay connected — auto-login will work.\n"
                    "Support: 0700 XXX XXX"
                )
            elif connection_type == "pppoe":
                message = (
                    "Welcome to Skylink Internet!\n\n"
                    "Your PPPoE account is ready:\n"
                    f"Username: {pppoe_username}\n"
                    f"Password: {pppoe_password}\n\n"
                    f"Package: {pkg_name}\n"
                    f"Valid Until: {expiry:%d %b %Y %I:%M %p}\n\n"
                    "Use these details on your router.\n"
                    "Support: 0700 XXX XXX"
                )
            else:
                return
            try:
                notify_customer(phone, message)
            except Exception:
                pass

        transaction.on_commit(_post_payment_effects)

    def __str__(self):
        return f"{self.customer.full_name} - {self.amount}"


# =====================================================
# EXPIRY REMINDER LOG
# =====================================================

class ExpiryReminderLog(models.Model):
    REMINDER_TYPES = (
        ("3_days", "3 Days Before"),
        ("1_day", "1 Day Before"),
    )

    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="reminder_logs"
    )
    reminder_type = models.CharField(max_length=10, choices=REMINDER_TYPES)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("subscription", "reminder_type")

    def __str__(self):
        return f"{self.subscription} - {self.reminder_type}"


# =====================================================
# ACCESS AUDIT LOG
# =====================================================

class AccessAuditLog(models.Model):
    ACTION_CHOICES = (
        ("deactivate", "Deactivate"),
        ("activate", "Activate"),
    )

    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="audit_logs"
    )
    subscription = models.ForeignKey(
        "Subscription", on_delete=models.SET_NULL, null=True, blank=True
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.customer.full_name} — {self.action} — {self.created_at:%Y-%m-%d %H:%M}"


# =====================================================
# SYSTEM SETTINGS
# =====================================================

class SystemSetting(models.Model):
    """
    Generic key/value store for system configuration:
    - MPESA_CONSUMER_KEY
    - MPESA_CONSUMER_SECRET
    - MPESA_SHORTCODE
    - MPESA_PASSKEY
    - MPESA_CALLBACK_URL
    - AT_USERNAME
    - AT_API_KEY
    - WHATSAPP_TOKEN
    - WHATSAPP_PHONE_ID
    """
    key = models.CharField(max_length=200, unique=True)
    value = models.TextField(blank=True)

    def __str__(self):
        return self.key


# =====================================================
# MPESA TRANSACTIONS (RECONCILIATION)
# =====================================================

class MpesaTransaction(models.Model):
    RESULT_STATUS = (
        ("success", "Success"),
        ("failed", "Failed"),
    )

    mpesa_receipt = models.CharField(max_length=50, unique=True, blank=True, null=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)

    account_reference = models.CharField(max_length=100, blank=True, null=True)
    merchant_request_id = models.CharField(max_length=100, blank=True, null=True)
    checkout_request_id = models.CharField(max_length=100, blank=True, null=True)

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mpesa_transactions",
    )
    payment = models.ForeignKey(
        Payment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mpesa_transactions",
    )

    processed = models.BooleanField(default=False)
    error_message = models.TextField(blank=True)

    raw_payload = models.JSONField()
    status = models.CharField(max_length=10, choices=RESULT_STATUS)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["status"],             name="mpesa_status_idx"),
            models.Index(fields=["processed"],          name="mpesa_processed_idx"),
            models.Index(fields=["status","processed"], name="mpesa_status_processed_idx"),
            models.Index(fields=["created_at"],         name="mpesa_created_at_idx"),
        ]

    def __str__(self):
        return f"{self.mpesa_receipt or 'NO-RECEIPT'} - {self.status}"


# =====================================================
# USAGE TRACKING
# =====================================================

class PPPoEUsageSnapshot(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="usage_snapshots")
    date = models.DateField()
    rx_bytes = models.BigIntegerField(default=0)
    tx_bytes = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("customer", "date")


class PPPoEUsageState(models.Model):
    """
    Stores last seen router counters so we can compute deltas safely.
    """
    customer = models.OneToOneField("Customer", on_delete=models.CASCADE, related_name="pppoe_usage_state")
    last_rx_bytes = models.BigIntegerField(default=0)
    last_tx_bytes = models.BigIntegerField(default=0)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"UsageState({self.customer_id})"


class PPPoEUsageRecord(models.Model):
    """
    Stores usage deltas per interval (e.g., every 5 minutes).
    """
    customer = models.ForeignKey("Customer", on_delete=models.CASCADE, related_name="pppoe_usage_records")
    router = models.ForeignKey("RouterDevice", null=True, blank=True, on_delete=models.SET_NULL)
    period_start = models.DateTimeField(default=timezone.now)
    period_end = models.DateTimeField(default=timezone.now)

    download_bytes = models.BigIntegerField(default=0)  # rx delta
    upload_bytes = models.BigIntegerField(default=0)    # tx delta

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["customer", "period_start"]),
        ]

    def __str__(self):
        return f"{self.customer_id} {self.period_start:%Y-%m-%d %H:%M}"


class HotspotUsageState(models.Model):
    """
    Stores last seen counters per hotspot user
    """
    customer = models.OneToOneField(
        "Customer",
        on_delete=models.CASCADE,
        related_name="hotspot_usage_state"
    )
    last_rx_bytes = models.BigIntegerField(default=0)
    last_tx_bytes = models.BigIntegerField(default=0)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"HotspotState({self.customer_id})"


class HotspotUsageRecord(models.Model):
    """
    Delta-based usage records (safe for reconnects)
    """
    customer = models.ForeignKey(
        "Customer",
        on_delete=models.CASCADE,
        related_name="hotspot_usage_records"
    )
    router = models.ForeignKey(
        "RouterDevice",
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )

    period_start = models.DateTimeField()
    period_end = models.DateTimeField()

    download_bytes = models.BigIntegerField(default=0)
    upload_bytes = models.BigIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["customer", "period_start"]),
        ]


class UsageRecord(models.Model):
    CONNECTION_TYPES = (
        ("pppoe", "PPPoE"),
        ("hotspot", "Hotspot"),
    )

    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="usage_records",
    )

    connection_type = models.CharField(
        max_length=10,
        choices=CONNECTION_TYPES,
    )

    date = models.DateField()
    rx_bytes = models.BigIntegerField(default=0)
    tx_bytes = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("customer", "date", "connection_type")
        ordering = ["date"]

    def total_mb(self):
        return (self.rx_bytes + self.tx_bytes) / (1024 * 1024)

    def __str__(self):
        return f"{self.customer.full_name} - {self.date}"
