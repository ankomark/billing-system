from rest_framework import serializers
from .models import (
    Customer, Package, Subscription, Invoice, Payment,
    MpesaTransaction, User, SystemSetting, Voucher, RouterDevice,
)


class CustomerSerializer(serializers.ModelSerializer):
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
            "router",
            "custom_data_cap_gb",
            "created_at",
        ]
        extra_kwargs = {
            "pppoe_password": {"write_only": True},
        }

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


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username", "role")


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
    AT_USERNAME           = serializers.CharField(required=False, allow_blank=True)
    AT_API_KEY            = serializers.CharField(required=False, allow_blank=True)
    WHATSAPP_TOKEN        = serializers.CharField(required=False, allow_blank=True)
    WHATSAPP_PHONE_ID     = serializers.CharField(required=False, allow_blank=True)
    WHATSAPP_API_VERSION  = serializers.CharField(required=False, allow_blank=True)


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


class RouterSerializer(serializers.ModelSerializer):
    class Meta:
        model = RouterDevice
        fields = [
            "id", "name", "ip_address", "username",
            "api_port", "priority", "is_active",
            "is_online", "last_seen", "last_error",
            "max_pppoe_sessions",
        ]
        extra_kwargs = {
            "password": {"write_only": True},
        }
