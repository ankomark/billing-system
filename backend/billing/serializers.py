from django.contrib.auth.password_validation import validate_password
from django.utils.text import slugify
from rest_framework import serializers
from .tenancy import get_current_tenant_id
from .models import (
    Customer, Package, Subscription, Invoice, Payment,
    MpesaTransaction, User, SystemSetting, Voucher, RouterDevice,
    PlatformPlan, Tenant, TenantSubscription, TenantInvoice, TenantPayment,
    Station,
)


class CustomerSerializer(serializers.ModelSerializer):
    # The identifier a hotspot subscriber actually has. Without it the list
    # shows a name, a phone and a PPPoE username that is always blank for
    # them — searching for someone by the code on their receipt found them,
    # and then the row gave no sign of why.
    voucher_code = serializers.SerializerMethodField()

    class Meta:
        model = Customer
        # Explicit field list — never auto-expose new model fields without review
        fields = [
            "id",
            "full_name",
            "phone",
            "connection_type",
            "status",
            "pppoe_username",
            "pppoe_password",
            "hotspot_username",
            "voucher_code",
            "router",
            "custom_data_cap_gb",
            "created_at",
        ]
        extra_kwargs = {
            "pppoe_password": {"write_only": True},
        }

    def get_voucher_code(self, obj):
        if obj.connection_type != "hotspot":
            return None
        # Prefetched by the list view, so this costs no query per row.
        vouchers = [
            v
            for sub in obj.subscriptions.all()
            for v in sub.vouchers.all()
            if v.is_active
        ]
        if not vouchers:
            return None
        return max(vouchers, key=lambda v: v.created_at).code

    def validate_hotspot_username(self, value):
        """
        Surface the device-uniqueness constraint as a 400 rather than a 500.

        DRF derives validators from model UniqueConstraints automatically, but
        only when every field of the constraint is exposed by the serializer.
        The constraint is (tenant, hotspot_username) and `tenant` is not a
        serializer field, so no validator is generated and the ValidationError
        raised by Customer.save()'s full_clean() would escape as a 500.
        """
        mac = (value or "").strip()
        if not mac:
            return value

        # Phase 1: the tenant comes from the instance on update, or from the
        # single-tenant bridge on create. Phase 2 replaces this with the
        # request-scoped tenant.
        tenant_id = getattr(self.instance, "tenant_id", None)
        if tenant_id is None:
            from .models import default_tenant
            tenant_id = default_tenant()

        clash = Customer.objects.filter(tenant_id=tenant_id, hotspot_username=mac)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)

        if clash.exists():
            raise serializers.ValidationError(
                "This device is already registered to another customer."
            )
        return value

    def update(self, instance, validated_data):
        # Omitting or blanking pppoe_password keeps the existing value.
        # Supply a non-blank string to change it.
        if not validated_data.get("pppoe_password"):
            validated_data.pop("pppoe_password", None)
        return super().update(instance, validated_data)


class CustomerSubscriptionSerializer(serializers.ModelSerializer):
    """Compact subscription row for the customer detail page."""
    package_name = serializers.CharField(source="package.name", read_only=True)

    class Meta:
        model = Subscription
        fields = ("id", "package", "package_name", "status", "start_date", "expiry_date")


class CustomerVoucherSerializer(serializers.ModelSerializer):
    """Compact voucher row for the customer detail page."""

    class Meta:
        model = Voucher
        fields = ("code", "is_active", "expires_at", "created_at")


class CustomerDetailSerializer(CustomerSerializer):
    """
    Retrieve-only serializer. The admin CustomerDetail page reads router_name,
    subscriptions and vouchers, none of which the plain CustomerSerializer
    returns — those panels rendered permanently empty.

    Kept separate from CustomerSerializer so the paginated list endpoint does
    not pay for the nested joins on every row.
    """
    router_name   = serializers.SerializerMethodField()
    subscriptions = CustomerSubscriptionSerializer(many=True, read_only=True)
    vouchers      = serializers.SerializerMethodField()

    class Meta(CustomerSerializer.Meta):
        fields = CustomerSerializer.Meta.fields + [
            "router_name",
            "subscriptions",
            "vouchers",
        ]

    def get_router_name(self, obj):
        return obj.router.name if obj.router_id else None

    def get_vouchers(self, obj):
        # Vouchers hang off Subscription, not Customer — flatten them here.
        # Relies on the view prefetching subscriptions__vouchers.
        vouchers = [v for sub in obj.subscriptions.all() for v in sub.vouchers.all()]
        vouchers.sort(key=lambda v: v.created_at, reverse=True)
        return CustomerVoucherSerializer(vouchers, many=True).data


class PackageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Package
        fields = "__all__"


class PublicPackageSerializer(serializers.ModelSerializer):
    """
    What an unauthenticated walk-up customer may see on the captive portal.

    Explicit field list rather than "__all__": this is served to anyone on the
    hotspot, so it must never start leaking internal columns because a field
    was added to the model.
    """
    duration = serializers.SerializerMethodField()

    class Meta:
        model = Package
        fields = (
            "id", "name", "price",
            "download_speed", "upload_speed",
            "duration_value", "duration_unit", "duration",
            "monthly_data_cap_gb",
        )

    def get_duration(self, obj):
        return f"{obj.duration_value} {obj.duration_unit}"


class SubscriptionSerializer(serializers.ModelSerializer):
    customer_detail = CustomerSerializer(source="customer", read_only=True)
    package_detail  = PackageSerializer(source="package",  read_only=True)

    class Meta:
        model = Subscription
        fields = "__all__"


class InvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = "__all__"


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = "__all__"


class InvoiceDashboardSerializer(serializers.ModelSerializer):
    customer_name   = serializers.CharField(source="customer.full_name", read_only=True)
    subscription_id = serializers.IntegerField(source="subscription.id", read_only=True)

    class Meta:
        model = Invoice
        fields = (
            "id",
            "invoice_number",
            "customer_name",
            "subscription_id",
            "total_amount",
            "payment_status",
            "created_at",
        )


class MpesaTransactionDashboardSerializer(serializers.ModelSerializer):
    class Meta:
        model = MpesaTransaction
        fields = (
            "id",
            "mpesa_receipt",
            "phone_number",
            "account_reference",
            "amount",
            "status",
            "processed",
            "error_message",
            "created_at",
        )


class MpesaTransactionSerializer(serializers.ModelSerializer):
    """
    A transaction with who it turned out to be.

    The dashboard serializer above carries only what a failure needs. Reading
    the ledger is a different job: the operator is usually holding a receipt
    number a customer read out and wants to know whether it arrived, what it
    paid for and which kind of connection it was.
    """
    customer = serializers.SerializerMethodField()
    customer_id = serializers.SerializerMethodField()
    connection_type = serializers.SerializerMethodField()
    invoice_number = serializers.CharField(
        source="invoice.invoice_number", read_only=True, default=None)

    class Meta:
        model = MpesaTransaction
        fields = (
            "id", "mpesa_receipt", "phone_number", "account_reference",
            "amount", "status", "processed", "error_message", "created_at",
            "customer", "customer_id", "connection_type", "invoice_number",
        )

    def _customer(self, obj):
        # Either link may be the one that resolved: a failed transaction never
        # became a Payment, and one that arrived for an already-paid invoice
        # has the invoice but no payment of its own.
        if obj.payment_id and obj.payment:
            return obj.payment.customer
        if obj.invoice_id and obj.invoice:
            return obj.invoice.customer
        return None

    def get_customer(self, obj):
        c = self._customer(obj)
        return c.full_name if c else None

    def get_customer_id(self, obj):
        c = self._customer(obj)
        return c.id if c else None

    def get_connection_type(self, obj):
        c = self._customer(obj)
        return c.connection_type if c else None


class UserProfileSerializer(serializers.ModelSerializer):
    tenant_name = serializers.CharField(source="tenant.business_name", read_only=True, default=None)
    is_platform_staff = serializers.BooleanField(read_only=True)
    # The app shell needs to know an operator is past due or locked out without
    # a second request; only /platform/my-account/ carried it before.
    tenant_status = serializers.CharField(source="tenant.status", read_only=True, default=None)

    class Meta:
        model = User
        # tenant is null for platform staff, which is how the frontend knows
        # to show the platform dashboard rather than an operator one.
        fields = (
            "id", "username", "email", "role", "tenant", "tenant_name",
            "tenant_status", "is_platform_staff", "must_change_password",
        )
        # Only username and email are writable — role and tenant decide what
        # this account can reach, and are never self-service.
        read_only_fields = (
            "id", "role", "tenant", "tenant_name", "tenant_status",
            "is_platform_staff", "must_change_password",
        )

    def validate_username(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("A username is required.")
        clash = User.objects.filter(username__iexact=value)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("That username is already taken.")
        return value


class ChangePasswordSerializer(serializers.Serializer):
    """
    Self-service password change.

    The current password is required even though the caller is already
    authenticated: a token in someone else's hands should not be enough to lock
    the real owner out of their own account.
    """

    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

    def validate_current_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("That is not your current password.")
        return value

    def validate_new_password(self, value):
        # Django's configured validators — length, commonness, similarity to
        # the username — rather than a length check invented here.
        validate_password(value, user=self.context["request"].user)
        return value

    def validate(self, attrs):
        if attrs["current_password"] == attrs["new_password"]:
            raise serializers.ValidationError(
                {"new_password": "The new password must be different."}
            )
        return attrs


class TenantUserSerializer(serializers.ModelSerializer):
    """
    An operator's own staff, managed by their admin.

    Role choices are narrowed to the two operator roles. The model's
    user_role_matches_tenant_presence constraint means a platform role always
    has a NULL tenant, so allowing one here would either fail at the database
    or, worse, create an unscoped account inside a tenant-scoped view.
    """

    password = serializers.CharField(write_only=True, required=False)
    role = serializers.ChoiceField(
        choices=((User.TENANT_ADMIN, "Operator Admin"), (User.TENANT_STAFF, "Operator Staff")),
        default=User.TENANT_STAFF,
    )

    class Meta:
        model = User
        fields = ("id", "username", "email", "role", "is_active", "password",
                  "must_change_password", "date_joined", "last_login")
        read_only_fields = ("id", "must_change_password", "date_joined", "last_login")

    def validate_username(self, value):
        value = value.strip()
        clash = User.objects.filter(username__iexact=value)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("That username is already taken.")
        return value

    def validate_password(self, value):
        validate_password(value)
        return value

    def validate(self, attrs):
        if self.instance is None and not attrs.get("password"):
            raise serializers.ValidationError(
                {"password": "A new user needs a password."}
            )
        return attrs


class OperatorUpdateSerializer(serializers.ModelSerializer):
    """
    Correcting an operator's details after onboarding.

    Deliberately excludes slug, status and public_token. The slug and token are
    identity other things resolve against — the M-Pesa callback URL and the
    hotspot portal both carry the token — and status has its own audited
    endpoint.
    """

    class Meta:
        model = Tenant
        fields = ("name", "business_name", "support_phone", "pppoe_prefix",
                  "contact_email", "contact_phone")
        extra_kwargs = {f: {"required": False} for f in fields}


class SystemSettingSerializer(serializers.Serializer):
    MPESA_CONSUMER_KEY    = serializers.CharField(required=False, allow_blank=True)
    MPESA_CONSUMER_SECRET = serializers.CharField(required=False, allow_blank=True)
    MPESA_SHORTCODE       = serializers.CharField(required=False, allow_blank=True)
    # Per operator: one may be live on production while another is still
    # testing on sandbox. Previously only a platform-wide env var.
    MPESA_ENV             = serializers.ChoiceField(
        choices=["sandbox", "production"], required=False
    )
    MPESA_PASSKEY         = serializers.CharField(required=False, allow_blank=True)
    MPESA_CALLBACK_URL    = serializers.CharField(required=False, allow_blank=True)
    BLESSEDTEXTS_API_KEY   = serializers.CharField(required=False, allow_blank=True)
    BLESSEDTEXTS_SENDER_ID = serializers.CharField(required=False, allow_blank=True)
    WHATSAPP_TOKEN        = serializers.CharField(required=False, allow_blank=True)
    WHATSAPP_PHONE_ID     = serializers.CharField(required=False, allow_blank=True)
    WHATSAPP_API_VERSION  = serializers.CharField(required=False, allow_blank=True)

    # The captive portal's banner. Two images because a phone held upright and
    # a laptop are different shapes, and one image stretched to both is how a
    # banner ends up looking like a mistake. Both optional — with neither set
    # the portal shows the platform's own panel instead of a broken frame.
    HOTSPOT_BANNER_PORTRAIT  = serializers.URLField(required=False, allow_blank=True)
    HOTSPOT_BANNER_LANDSCAPE = serializers.URLField(required=False, allow_blank=True)
    HOTSPOT_BANNER_LINK      = serializers.URLField(required=False, allow_blank=True)


class BroadcastSerializer(serializers.Serializer):
    channel      = serializers.ChoiceField(choices=["sms", "whatsapp"])
    audience     = serializers.ChoiceField(choices=["all", "active", "expired", "custom"])
    message      = serializers.CharField(max_length=1000)
    customer_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        default=list,
    )


class AccessLookupSerializer(serializers.Serializer):
    type         = serializers.CharField()
    customer     = serializers.DictField()
    subscription = serializers.DictField()
    voucher      = serializers.DictField(allow_null=True)


class StationSerializer(serializers.ModelSerializer):
    """
    One of an operator's sites. Tenant comes from the request, never the body.
    """

    routers = serializers.IntegerField(source="routers.count", read_only=True)
    routers_offline = serializers.SerializerMethodField()
    subscribers = serializers.SerializerMethodField()

    class Meta:
        model = Station
        fields = ("id", "name", "code", "notes", "is_active",
                  "routers", "routers_offline", "subscribers", "created_at")
        read_only_fields = ("id", "created_at")

    def get_routers_offline(self, obj):
        return obj.routers.filter(is_active=True, is_online=False).count()

    def get_subscribers(self, obj):
        # Derived through the router, because that is what actually decides
        # which site serves a subscriber.
        return Customer.objects.filter(router__station=obj).count()

    def validate_name(self, value):
        """
        Enforce (tenant, name) uniqueness here, not only in the database.

        DRF derives a validator from a model UniqueConstraint only when every
        field of the constraint is exposed by the serializer. `tenant` is not —
        deliberately, since it comes from the request rather than the body — so
        no validator is generated and a duplicate would escape as an
        IntegrityError and a 500. Same trap as hotspot_username on
        CustomerSerializer, which carries the same note.
        """
        value = value.strip()
        if not value:
            raise serializers.ValidationError("A station needs a name.")

        tenant_id = get_current_tenant_id()
        clash = Station.objects.all_tenants().filter(name__iexact=value)
        if tenant_id is not None:
            clash = clash.filter(tenant_id=tenant_id)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(
                "You already have a station with that name."
            )
        return value


class RouterSerializer(serializers.ModelSerializer):
    station_name = serializers.CharField(
        source="station.name", read_only=True, default=None)

    class Meta:
        model = RouterDevice
        fields = [
            "id", "name", "ip_address", "username",
            "api_port", "priority", "is_active",
            "is_online", "last_seen", "last_error",
            "max_pppoe_sessions", "station", "station_name",
        ]
        extra_kwargs = {
            "password": {"write_only": True},
        }


# =====================================================
# PLATFORM BILLING
# =====================================================

class PlatformPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlatformPlan
        fields = (
            "id", "name", "slug", "price", "billing_period_days",
            "max_customers", "max_routers", "is_active",
        )


class TenantSubscriptionSerializer(serializers.ModelSerializer):
    plan_name = serializers.CharField(source="plan.name", read_only=True)
    plan_price = serializers.DecimalField(
        source="plan.price", max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = TenantSubscription
        fields = (
            "id", "tenant", "plan", "plan_name", "plan_price", "status",
            "current_period_start", "current_period_end", "trial_ends_at",
        )
        read_only_fields = ("status", "current_period_start", "current_period_end")


class TenantInvoiceSerializer(serializers.ModelSerializer):
    operator = serializers.CharField(source="tenant.business_name", read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)

    class Meta:
        model = TenantInvoice
        fields = (
            "id", "number", "tenant", "operator", "amount", "status",
            "period_start", "period_end", "due_date", "issued_at", "is_overdue",
        )
        read_only_fields = fields


class TenantPaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = TenantPayment
        fields = ("id", "tenant", "invoice", "amount", "method", "reference", "paid_at")
        read_only_fields = ("tenant", "paid_at")


class OperatorCreateSerializer(serializers.Serializer):
    """
    Onboarding one operator: the tenant, and the first account that can log in
    to it.

    Both together, deliberately. A tenant with no admin is a business nobody
    can reach — the platform owner would have to go to the Django admin to
    finish the job, which is exactly the manual step this endpoint exists to
    remove.
    """

    name = serializers.CharField(max_length=120)
    slug = serializers.SlugField(max_length=60, required=False, allow_blank=True)
    business_name = serializers.CharField(max_length=120, required=False, allow_blank=True)
    support_phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    pppoe_prefix = serializers.CharField(max_length=10, required=False, allow_blank=True)
    contact_email = serializers.EmailField(required=False, allow_blank=True)
    contact_phone = serializers.CharField(max_length=20, required=False, allow_blank=True)

    # The operator's first admin.
    admin_username = serializers.CharField(max_length=150)
    admin_password = serializers.CharField(min_length=8, write_only=True)
    admin_email = serializers.EmailField(required=False, allow_blank=True)

    # Optional — an operator may start with no plan and be put on one later.
    plan = serializers.SlugField(required=False, allow_blank=True)

    # Optional M-Pesa credentials, so onboarding can finish the job in one pass
    # rather than leaving an operator who exists but cannot be paid. Left out,
    # they are set later from the operator's page; an operator waiting on
    # Safaricom is the normal case, not an error.
    mpesa_env = serializers.ChoiceField(
        choices=("sandbox", "production"), required=False)
    mpesa_consumer_key = serializers.CharField(required=False, allow_blank=True)
    mpesa_consumer_secret = serializers.CharField(
        required=False, allow_blank=True, write_only=True)
    mpesa_shortcode = serializers.CharField(required=False, allow_blank=True)
    mpesa_passkey = serializers.CharField(
        required=False, allow_blank=True, write_only=True)

    def validate_slug(self, value):
        if value and Tenant.objects.filter(slug=value).exists():
            raise serializers.ValidationError("An operator with this slug already exists.")
        return value

    def validate_admin_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("That username is already taken.")
        return value

    def validate_plan(self, value):
        if value and not PlatformPlan.objects.filter(slug=value, is_active=True).exists():
            raise serializers.ValidationError("No active plan with that slug.")
        return value

    def validate(self, attrs):
        # Derive the slug from the name when one was not given, and make it
        # unique here rather than letting the database raise — the caller gets
        # a field error instead of a 500.
        if not attrs.get("slug"):
            base = slugify(attrs["name"])[:55] or "operator"
            candidate, n = base, 1
            while Tenant.objects.filter(slug=candidate).exists():
                n += 1
                candidate = f"{base}-{n}"[:60]
            attrs["slug"] = candidate
        return attrs
