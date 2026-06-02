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

    def update(self, instance, validated_data):
        # Omitting or blanking pppoe_password keeps the existing value.
        # Supply a non-blank string to change it.
        if not validated_data.get("pppoe_password"):
            validated_data.pop("pppoe_password", None)
        return super().update(instance, validated_data)


class PackageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Package
        fields = "__all__"


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
