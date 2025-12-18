from rest_framework import serializers
from .models import Customer, Package, Subscription,Invoice, Payment,MpesaTransaction, User,SystemSetting



class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = "__all__"


class PackageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Package
        fields = "__all__"


class SubscriptionSerializer(serializers.ModelSerializer):
    customer_detail = CustomerSerializer(source="customer", read_only=True)
    package_detail = PackageSerializer(source="package", read_only=True)

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
    customer_name = serializers.CharField(source="customer.full_name", read_only=True)
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
        fields = (
            "id",
            "username",
            "role",
        )
        
class SystemSettingSerializer(serializers.Serializer):
    MPESA_CONSUMER_KEY = serializers.CharField(required=False, allow_blank=True)
    MPESA_CONSUMER_SECRET = serializers.CharField(required=False, allow_blank=True)
    MPESA_SHORTCODE = serializers.CharField(required=False, allow_blank=True)
    MPESA_PASSKEY = serializers.CharField(required=False, allow_blank=True)
    MPESA_CALLBACK_URL = serializers.CharField(required=False, allow_blank=True)

    AT_USERNAME = serializers.CharField(required=False, allow_blank=True)
    AT_API_KEY = serializers.CharField(required=False, allow_blank=True)

    WHATSAPP_TOKEN = serializers.CharField(required=False, allow_blank=True)
    WHATSAPP_PHONE_ID = serializers.CharField(required=False, allow_blank=True)
    
class BroadcastSerializer(serializers.Serializer):
    channel = serializers.ChoiceField(choices=["sms", "whatsapp"])
    audience = serializers.ChoiceField(choices=["all", "active", "expired", "custom"])
    message = serializers.CharField(max_length=1000)

    # used only when audience="custom"
    customer_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False
    )