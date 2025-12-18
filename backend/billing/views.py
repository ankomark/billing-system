from django.http import HttpResponse
from django.db import transaction
from .permissions import IsStaffOrAdmin,IsCustomer,IsAdmin
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from django.shortcuts import get_object_or_404
from .security import is_trusted_mpesa_ip
from billing.services.voucher_service import validate_voucher
from billing.router_service import enable_customer_access
from .mpesa_client import initiate_stk_push
from billing.models import Customer,Subscription,PPPoEUsageRecord
from billing.notifications import send_sms, send_whatsapp
from billing.serializers import BroadcastSerializer
from billing.mpesa_client import get_mpesa_access_token
from rest_framework.permissions import IsAdminUser
from django.db.models import Sum
from django.db.models.functions import TruncDate, TruncMonth
from .reports import (revenue_summary,revenue_by_method,revenue_by_package,customer_stats,)
from .dashboards import (unpaid_invoices,pending_invoices,failed_mpesa_transactions,)
from .serializers import (InvoiceDashboardSerializer,MpesaTransactionDashboardSerializer,)
from .permissions import IsAdmin
from billing.models import Voucher
from billing.tasks.mpesa_tasks import initiate_stk_push_task
from django.utils import timezone
from rest_framework import viewsets
from .models import Customer, Package, Subscription,Invoice, Payment,MpesaTransaction,SystemSetting,RouterFailoverLog, HotspotUsageRecord
from .serializers import (CustomerSerializer,PackageSerializer,SubscriptionSerializer,InvoiceSerializer,  PaymentSerializer,SystemSettingSerializer,)
from billing.tasks.notification_tasks import notify_customer_task,send_sms_task, send_whatsapp_task
from .config import get_setting
from rest_framework.permissions import IsAuthenticated
from .serializers import UserProfileSerializer
from billing.router_service import enable_customer_access
from billing.tasks.router_tasks import (enable_customer_task,disable_customer_task,disconnect_pppoe_task)

def home(request):
    return HttpResponse("WiFi Billing Backend is Running")


class UserProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = UserProfileSerializer(request.user)
        return Response(serializer.data)


class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.all().order_by("-created_at")
    serializer_class = CustomerSerializer
    permission_classes = [IsAdmin]


class PackageViewSet(viewsets.ModelViewSet):
    queryset = Package.objects.all()
    serializer_class = PackageSerializer
    permission_classes = [IsAdmin]


class SubscriptionViewSet(viewsets.ModelViewSet):
    serializer_class = SubscriptionSerializer
    permission_classes = [IsStaffOrAdmin | IsCustomer]

    def get_queryset(self):
        user = self.request.user

        if user.role == "customer":
            return Subscription.objects.filter(customer__user=user)

        return Subscription.objects.select_related("customer", "package")


class InvoiceViewSet(viewsets.ModelViewSet):
    queryset = Invoice.objects.select_related("customer", "subscription")
    serializer_class = InvoiceSerializer
    permission_classes = [IsAdmin]


class PaymentViewSet(viewsets.ModelViewSet):
    serializer_class = PaymentSerializer
    permission_classes = [IsStaffOrAdmin | IsCustomer]

    def get_queryset(self):
        user = self.request.user

        if user.role == "customer":
            return Payment.objects.filter(customer__user=user)

        return Payment.objects.select_related("customer", "subscription")

class MpesaSTKPushView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        subscription_id = request.data.get("subscription_id")
        phone_number = request.data.get("phone_number")

        if not subscription_id or not phone_number:
            return Response(
                {"detail": "subscription_id and phone_number are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 🔐 User can only pay for own subscription
        subscription = get_object_or_404(
            Subscription,
            id=subscription_id,
            customer__user=request.user,
        )

        invoice = getattr(subscription, "invoice", None)
        if not invoice:
            return Response(
                {"detail": "No invoice found"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ⛔ Prevent duplicate STK pushes
        if invoice.payment_status in ("paid", "pending"):
            return Response(
                {"detail": "Payment already initiated or completed"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Mark invoice pending BEFORE async call
        invoice.payment_status = "pending"
        invoice.save(update_fields=["payment_status"])

        # 🚀 ASYNC STK PUSH
        initiate_stk_push_task.delay(invoice.id, phone_number)

        return Response(
            {
                "detail": "STK Push scheduled",
                "invoice_number": invoice.invoice_number,
            },
            status=status.HTTP_202_ACCEPTED,
        )

class MpesaSTKCallbackView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        if not is_trusted_mpesa_ip(request):
            return Response(
                {"detail": "Unauthorized callback source"},
                status=status.HTTP_403_FORBIDDEN,
            )

        body = request.data.get("Body", {}).get("stkCallback", {})
        result_code = body.get("ResultCode")
        result_desc = body.get("ResultDesc")

        items = body.get("CallbackMetadata", {}).get("Item", []) if result_code == 0 else []

        data = {i["Name"]: i.get("Value") for i in items}

        mpesa_receipt = data.get("MpesaReceiptNumber")
        amount = data.get("Amount")
        phone = str(data.get("PhoneNumber")) if data.get("PhoneNumber") else None
        reference = data.get("AccountReference")

        # 🔁 Idempotency check
        if mpesa_receipt and MpesaTransaction.objects.filter(mpesa_receipt=mpesa_receipt).exists():
            return Response({"detail": "Duplicate callback ignored"})

        tx = MpesaTransaction.objects.create(
            mpesa_receipt=mpesa_receipt,
            amount=amount or 0,
            phone_number=phone,
            account_reference=reference,
            raw_payload=request.data,
            status="success" if result_code == 0 else "failed",
        )

        if result_code != 0:
            tx.error_message = result_desc
            tx.processed = True
            tx.save(update_fields=["error_message", "processed"])
            return Response({"detail": "STK failed"})

        if not all([mpesa_receipt, amount, reference]):
            tx.status = "failed"
            tx.error_message = "Missing callback data"
            tx.processed = True
            tx.save()
            return Response(status=400)

        try:
            invoice = Invoice.objects.select_related("customer", "subscription").get(
                invoice_number=reference
            )
        except Invoice.DoesNotExist:
            tx.status = "failed"
            tx.error_message = "Invoice not found"
            tx.processed = True
            tx.save()
            return Response(status=400)

        if float(amount) != float(invoice.total_amount):
            tx.status = "failed"
            tx.error_message = "Amount mismatch"
            tx.processed = True
            tx.save()
            return Response(status=400)

        # 🔐 Atomic creation (Payment.save() handles router + access)
        with transaction.atomic():
            payment = Payment.objects.create(
                customer=invoice.customer,
                subscription=invoice.subscription,
                amount=amount,
                method="mpesa",
                reference=mpesa_receipt,
            )

            tx.invoice = invoice
            tx.payment = payment
            tx.processed = True
            tx.status = "success"
            tx.save()

        return Response({"detail": "Payment processed"})


class ManualPaymentView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request):
        invoice_number = request.data.get("invoice_number")
        amount = request.data.get("amount")
        reference = request.data.get("reference", "manual")

        if not invoice_number or not amount:
            return Response(
                {"detail": "invoice_number and amount are required"},
                status=400,
            )

        invoice = get_object_or_404(Invoice, invoice_number=invoice_number)

        if invoice.payment_status == "paid":
            return Response({"detail": "Invoice already paid"}, status=400)

        if float(amount) != float(invoice.total_amount):
            return Response({"detail": "Amount mismatch"}, status=400)

        with transaction.atomic():
            Payment.objects.create(
                customer=invoice.customer,
                subscription=invoice.subscription,
                amount=amount,
                method="cash",
                reference=reference,
            )

        return Response({"detail": "Manual payment recorded"}, status=201)


class RevenueDashboardView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        return Response({
            "revenue_summary": revenue_summary(),
            "revenue_by_method": revenue_by_method(),
            "revenue_by_package": revenue_by_package(),
            "customer_stats": customer_stats(),
        })

class UnpaidInvoicesView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        qs = unpaid_invoices()
        serializer = InvoiceDashboardSerializer(qs, many=True)
        return Response(serializer.data)

class PendingInvoicesView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        qs = pending_invoices()
        serializer = InvoiceDashboardSerializer(qs, many=True)
        return Response(serializer.data)

class FailedMpesaTransactionsView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        qs = failed_mpesa_transactions()
        serializer = MpesaTransactionDashboardSerializer(qs, many=True)
        return Response(serializer.data)

class HotspotVoucherValidateView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        code = request.data.get("code")
        mac_address = request.data.get("mac_address")

        if not code or not mac_address:
            return Response(
                {"detail": "code and mac_address are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        subscription = validate_voucher(code)
        if not subscription:
            return Response(
                {"detail": "Invalid or expired voucher"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        customer = subscription.customer

        # 🔐 Prevent MAC rebinding
        if customer.hotspot_username and customer.hotspot_username != mac_address:
            return Response(
                {"detail": "Voucher already bound to another device"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        customer.hotspot_username = mac_address
        customer.status = "active"
        customer.save(update_fields=["hotspot_username", "status"])

        enable_customer_access(customer)

        return Response(
            {
                "detail": "Access granted",
                "expires_at": subscription.expiry_date,
            },
            status=status.HTTP_200_OK,
        )

class CustomerSuspendResumeView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request, customer_id):
        action = request.data.get("action")  # "suspend" | "resume"
        customer = get_object_or_404(Customer, id=customer_id)

        subscription = customer.subscriptions.filter(status="active").first()

        if action == "suspend":
            if subscription:
                subscription.status = "suspended"
                subscription.save()

            customer.status = "expired"
            customer.save()

            disable_customer_task.delay(customer.id)


            return Response({"detail": "Customer suspended"})

        if action == "resume":
            if subscription:
                subscription.status = "active"
                subscription.save()

            customer.status = "active"
            customer.save()

            enable_customer_access(customer)

            return Response({"detail": "Customer resumed"})

        return Response(
            {"detail": "Invalid action"},
            status=status.HTTP_400_BAD_REQUEST,
        )
        
class ResendVoucherView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request, customer_id):
        customer = get_object_or_404(Customer, id=customer_id)

        # 1️⃣ Find latest valid voucher
        voucher = (
            Voucher.objects.filter(
                subscription__customer=customer,
                is_active=True,
                expires_at__gte=timezone.now(),
            )
            .order_by("-created_at")
            .first()
        )

        if not voucher:
            return Response(
                {"detail": "No active voucher found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # 2️⃣ Build message
        message = (
            "Your WiFi access code is:\n\n"
            f"{voucher.code}\n\n"
            f"Valid until {voucher.expires_at:%Y-%m-%d %H:%M}.\n"
            "Thank you for choosing Skylink."
        )

        # 3️⃣ Send notification asynchronously
        notify_customer_task.delay(customer.phone, message)

        # 4️⃣ Respond immediately (non-blocking)
        return Response(
            {"detail": "Voucher resend scheduled"},
            status=status.HTTP_202_ACCEPTED,
        )

    
class HotspotStatusView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        mac = request.GET.get("mac")

        if not mac:
            return Response(
                {"detail": "MAC address is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Find customer by hotspot MAC binding
        customer = Customer.objects.filter(hotspot_username=mac).first()
        if not customer:
            return Response({"status": "not_found"})

        # Get latest subscription
        subscription = (
            customer.subscriptions.order_by("-expiry_date").first()
        )
        if not subscription:
            return Response({"status": "not_found"})

        # Check for invoice
        invoice = getattr(subscription, "invoice", None)
        if not invoice:
            return Response({"status": "pending"})

        # If invoice is unpaid → still pending
        if invoice.payment_status != "paid":
            return Response({"status": "pending"})

        # Check expiry
        if subscription.expiry_date < timezone.now():
            return Response({"status": "expired"})

        # 🎉 PAYMENT IS CONFIRMED + SUBSCRIPTION ACTIVE
        return Response(
            {
                "status": "active",
                "expires_at": subscription.expiry_date,
            }
        )
class PPPoECustomerPortalView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user

        try:
            customer = user.customer_profile
        except Customer.DoesNotExist:
            return Response(
                {"detail": "Customer profile not found"},
                status=404
            )

        if customer.connection_type != "pppoe":
            return Response(
                {"detail": "This account is not PPPoE"},
                status=400
            )

        subscription = (
            customer.subscriptions
            .filter(status="active")
            .order_by("-expiry_date")
            .first()
        )

        if not subscription:
            return Response({
                "status": "expired",
                "message": "No active PPPoE subscription"
            })

        package = subscription.package

        return Response({
            "status": subscription.status,
            "customer": {
                "full_name": customer.full_name,
                "phone": customer.phone,
            },
            "pppoe": {
                "username": customer.pppoe_username,
                "password": customer.pppoe_password,
            },
            "package": {
                "name": package.name,
                "upload": package.upload_speed,
                "download": package.download_speed,
            },
            "expiry_date": subscription.expiry_date,
            "server_time": timezone.now(),
        })
class PPPoERenewView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user
        package_id = request.data.get("package_id")
        phone = request.data.get("phone")

        if not package_id or not phone:
            return Response({"detail": "package_id and phone are required"}, status=400)

        try:
            customer = user.customer_profile
        except Customer.DoesNotExist:
            return Response({"detail": "Customer not found"}, status=404)

        package = get_object_or_404(Package, id=package_id)

        # === 1️⃣ CREATE RENEWAL SUBSCRIPTION ===
        subscription = Subscription.objects.create(
            customer=customer,
            package=package,
            start_date=timezone.now(),
            status="active"
        )

        # === 2️⃣ GET THE AUTOMATICALLY CREATED INVOICE ===
        invoice = subscription.invoice

        # Mark invoice pending
        invoice.payment_status = "pending"
        invoice.save()

        # === 3️⃣ TRIGGER DARAJA STK PUSH ===
        try:
            stk_response = initiate_stk_push(
                phone_number=phone,
                amount=invoice.total_amount,
                account_reference=invoice.invoice_number,
                description="PPPoE Subscription Renewal",
            )
        except Exception as e:
            return Response(
                {"detail": f"STK Push failed: {e}"},
                status=500
            )
        
        return Response({
            "detail": "STK Push Initiated",
            "invoice_number": invoice.invoice_number,
            "subscription_id": subscription.id,
        })
class PppoeStatusView(APIView):
    def get(self, request, customer_id):
        try:
            customer = Customer.objects.get(id=customer_id)
        except Customer.DoesNotExist:
            return Response({"detail": "Customer not found"}, status=404)

        subscription = (
            customer.subscriptions.filter(status="active")
            .order_by("-expiry_date")
            .first()
        )

        if not subscription:
            return Response({"status": "expired"})

        return Response({
            "username": customer.pppoe_username,
            "password": customer.pppoe_password,
            "package_name": subscription.package.name,
            "expires_at": subscription.expiry_date,
            "status": customer.status,
        })
        
class SystemSettingsView(APIView):
    permission_classes = [IsAdminUser]

    SENSITIVE_KEYS = {
        "MPESA_CONSUMER_KEY",
        "MPESA_CONSUMER_SECRET",
        "MPESA_PASSKEY",
        "AT_API_KEY",
        "WHATSAPP_TOKEN",
    }

    ALL_KEYS = [
        "MPESA_CONSUMER_KEY",
        "MPESA_CONSUMER_SECRET",
        "MPESA_SHORTCODE",
        "MPESA_PASSKEY",
        "MPESA_CALLBACK_URL",
        "AT_USERNAME",
        "AT_API_KEY",
        "WHATSAPP_TOKEN",
        "WHATSAPP_PHONE_ID",
    ]

    def get(self, request):
        data = {}
        for key in self.ALL_KEYS:
            value = get_setting(key, default="")

            if key in self.SENSITIVE_KEYS and value not in ("", None):
                data[key] = "********"
            else:
                data[key] = value or ""

        return Response(data)

    def put(self, request):
        serializer = SystemSettingSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        for key, value in serializer.validated_data.items():
            if value == "********":
                continue  # keep old secret

            SystemSetting.objects.update_or_create(
                key=key,
                defaults={"value": value},
            )

        # ✅ Clear cache so new values apply immediately
        get_setting.cache_clear()

        return Response({"detail": "Settings updated successfully"})

class TestMpesaView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        try:
            token = get_mpesa_access_token()
            return Response({"success": True, "token": token})
        except Exception as e:
            return Response({"success": False, "error": str(e)}, status=400)


class TestSmsView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        send_sms_task.delay("2547XXXXXXXX", "SMS Test OK ✔")
        return Response({"success": True})


class TestWhatsappView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        send_whatsapp_task.delay("2547XXXXXXXX", "WhatsApp Test OK ✔")
        return Response({"success": True})
    
class AdminBroadcastView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request):
        serializer = BroadcastSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        channel = serializer.validated_data["channel"]
        audience = serializer.validated_data["audience"]
        message = serializer.validated_data["message"]
        customer_ids = serializer.validated_data.get("customer_ids", [])

        qs = Customer.objects.all()

        if audience == "active":
            qs = qs.filter(status="active")

        elif audience == "expired":
            qs = qs.filter(status="expired")

        elif audience == "custom":
            if not customer_ids:
                return Response(
                    {"detail": "customer_ids is required when audience=custom"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            qs = qs.filter(id__in=customer_ids)

        sent = 0
        failed = 0
        errors = []

        for c in qs:
            try:
                if channel == "sms":
                   send_sms_task.delay(c.phone, message)
                else:
                    send_whatsapp_task.delay(c.phone, message)
                sent += 1
            except Exception as e:
                failed += 1
                errors.append({"customer_id": c.id, "phone": c.phone, "error": str(e)})

        return Response(
            {
                "detail": "Broadcast completed",
                "audience": audience,
                "channel": channel,
                "sent": sent,
                "failed": failed,
                "errors": errors[:20],  # don't return too much
            },
            status=status.HTTP_200_OK,
        )
        
from billing.router_service import get_pppoe_live_usage

class PPPoELiveStatusView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        try:
            customer = request.user.customer_profile
        except Customer.DoesNotExist:
            return Response({"detail": "Customer not found"}, status=404)

        if customer.connection_type != "pppoe":
            return Response({"detail": "Not a PPPoE account"}, status=400)

        usage = get_pppoe_live_usage(
            customer.router,
            customer.pppoe_username
        )

        if not usage:
            return Response({"connected": False})

        return Response(usage)
    
class PPPoEControlView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        action = request.data.get("action")

        if action not in ("disconnect", "reconnect"):
            return Response({"detail": "Invalid action"}, status=400)

        customer = getattr(request.user, "customer_profile", None)
        if not customer:
            return Response({"detail": "Customer profile not found"}, status=404)

        if customer.connection_type != "pppoe":
            return Response({"detail": "Not a PPPoE account"}, status=400)

        if not customer.pppoe_username:
            return Response({"detail": "PPPoE username missing"}, status=400)

        if action == "disconnect":
            disconnect_pppoe_task.delay(customer.id)
            return Response({"detail": "Disconnect scheduled"}, status=202)

        # reconnect
        disconnect_pppoe_task.delay(customer.id)
        enable_customer_task.delay(customer.id)
        return Response({"detail": "Reconnect scheduled"}, status=202)

from billing.router_service import get_pppoe_usage   
class PPPoEUsageView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user

        try:
            customer = user.customer_profile
        except Customer.DoesNotExist:
            return Response({"detail": "Customer not found"}, status=404)

        if customer.connection_type != "pppoe":
            return Response({"detail": "Not a PPPoE account"}, status=400)

        usage = get_pppoe_usage(
            customer.router,
            customer.pppoe_username
        )

        if not usage:
            return Response({
                "connected": False,
                "message": "Not currently connected"
            })

        return Response({
            "connected": True,
            **usage
        })
from billing.router_service import get_all_pppoe_sessions  
from billing.models import RouterDevice, Customer
      
class AdminPPPoESessionsView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        data = []

        routers = RouterDevice.objects.filter(is_active=True)

        for router in routers:
            try:
                sessions = get_all_pppoe_sessions(router)
            except Exception as e:
                # Skip router if MikroTik is unreachable
                continue

            for s in sessions:
                customer = Customer.objects.filter(
                    pppoe_username=s.get("username")
                ).first()

                data.append({
                    "router": router.name,
                    "username": s.get("username"),
                    "customer": customer.full_name if customer else "Unknown",
                    "phone": customer.phone if customer else "",
                    "ip_address": s.get("ip_address"),
                    "uptime": s.get("uptime"),
                    "rx_bytes": s.get("rx_bytes", 0),
                    "tx_bytes": s.get("tx_bytes", 0),
                })

        return Response(data)
    
class AdminDisconnectPPPoEView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request):
        username = request.data.get("username")

        customer = Customer.objects.filter(pppoe_username=username).first()
        if not customer:
            return Response({"detail": "Customer not found"}, status=404)

        disconnect_pppoe_task.delay(customer.id)

        return Response(
            {"detail": "PPPoE disconnect scheduled"},
            status=202
        )
    
class CustomerReconnectPPPoEView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        customer = getattr(request.user, "customer_profile", None)

        if not customer:
            return Response(
                {"detail": "Customer profile not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if customer.connection_type != "pppoe":
            return Response(
                {"detail": "This account is not PPPoE"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not customer.pppoe_username:
            return Response(
                {"detail": "PPPoE account not configured"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 🔁 Schedule reconnect (non-blocking)
        disconnect_pppoe_task.delay(customer.id)
        enable_customer_task.delay(customer.id)

        return Response(
            {"detail": "PPPoE reconnection scheduled"},
            status=status.HTTP_202_ACCEPTED,
        )
        
class AdminRouterHealthView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        routers = RouterDevice.objects.all().order_by("priority")
        data = []
        for r in routers:
            data.append({
                "id": r.id,
                "name": r.name,
                "ip_address": r.ip_address,
                "api_port": r.api_port,
                "priority": r.priority,
                "is_active": r.is_active,
                "is_online": r.is_online,
                "last_seen": r.last_seen,
                "last_error": r.last_error,
                "max_pppoe_sessions": r.max_pppoe_sessions,
            })
        return Response(data)

class AdminFailoverLogsView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        logs = (
            RouterFailoverLog.objects
            .select_related("customer", "from_router", "to_router")
            .order_by("-created_at")[:500]
        )

        data = []
        for log in logs:
            data.append({
                "id": log.id,
                "customer": log.customer.full_name,
                "phone": log.customer.phone,
                "from_router": log.from_router.name if log.from_router else "—",
                "to_router": log.to_router.name,
                "reason": log.reason,
                "created_at": log.created_at,
            })

        return Response(data)
        
from billing.router_service import is_router_reachable


class AdminRouterListView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        routers = RouterDevice.objects.all().order_by("priority")

        data = []
        for router in routers:
            online = is_router_reachable(router)

            data.append({
                "id": router.id,
                "name": router.name,
                "ip_address": router.ip_address,
                "api_port": router.api_port,
                "priority": router.priority,
                "is_active": router.is_active,
                "online": online,
            })

        return Response(data)
from billing.router_service import safe_connect_router, provision_customer_on_router,migrate_customer_router  


class AdminMigrateCustomerView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request):
        """
        Admin router migration.

        Modes:
        - Automatic failover: provide customer_id only
        - Manual migration: provide customer_id + router_id
        """

        customer_id = request.data.get("customer_id")
        target_router_id = request.data.get("router_id")

        if not customer_id:
            return Response(
                {"detail": "customer_id is required"},
                status=400
            )

        customer = Customer.objects.filter(id=customer_id).first()
        if not customer:
            return Response(
                {"detail": "Customer not found"},
                status=404
            )

        # --------------------------------------------------
        # 🔁 MANUAL ROUTER SELECTION
        # --------------------------------------------------
        if target_router_id:
            router = RouterDevice.objects.filter(
                id=target_router_id,
                is_active=True
            ).first()

            if not router:
                return Response(
                    {"detail": "Target router not found or inactive"},
                    status=404
                )

            api = safe_connect_router(router)
            if not api:
                return Response(
                    {"detail": "Target router is offline"},
                    status=400
                )

            subscription = (
                customer.subscriptions
                .filter(status="active")
                .order_by("-expiry_date")
                .first()
            )

            if not subscription:
                return Response(
                    {"detail": "Customer has no active subscription"},
                    status=400
                )

            # Provision on selected router
            provision_customer_on_router(
                api=api,
                router=router,
                customer=customer,
                subscription=subscription,
            )

            customer.router = router
            customer.save(update_fields=["router"])

            return Response(
                {"detail": f"Migrated to {router.name}"},
                status=200
            )

        # --------------------------------------------------
        # ⚡ AUTOMATIC FAILOVER (SYSTEM DECIDES)
        # --------------------------------------------------
        success, message = migrate_customer_router(
            customer,
            reason="admin_manual"
        )

        if not success:
            return Response(
                {"detail": message},
                status=400
            )

        return Response(
            {"detail": message},
            status=200
        )
        
class PPPoEUsageDailyView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        customer = request.user.customer_profile
        days = int(request.query_params.get("days", 7))

        since = timezone.now() - timezone.timedelta(days=days)

        qs = (
            PPPoEUsageRecord.objects
            .filter(customer=customer, period_start__gte=since)
            .annotate(day=TruncDate("period_start"))
            .values("day")
            .annotate(
                download=Sum("download_bytes"),
                upload=Sum("upload_bytes"),
            )
            .order_by("day")
        )

        data = [{
            "day": x["day"],
            "download_mb": round((x["download"] or 0) / (1024 * 1024), 2),
            "upload_mb": round((x["upload"] or 0) / (1024 * 1024), 2),
        } for x in qs]

        return Response(data)


class PPPoEUsageMonthlyView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        customer = request.user.customer_profile
        months = int(request.query_params.get("months", 6))

        since = timezone.now() - timezone.timedelta(days=months * 31)

        qs = (
            PPPoEUsageRecord.objects
            .filter(customer=customer, period_start__gte=since)
            .annotate(month=TruncMonth("period_start"))
            .values("month")
            .annotate(
                download=Sum("download_bytes"),
                upload=Sum("upload_bytes"),
            )
            .order_by("month")
        )

        data = [{
            "month": x["month"],
            "download_gb": round((x["download"] or 0) / (1024 ** 3), 2),
            "upload_gb": round((x["upload"] or 0) / (1024 ** 3), 2),
        } for x in qs]

        return Response(data)
    
class HotspotUsageDailyView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        customer = request.user.customer_profile

        qs = (
            customer.hotspot_usage_records
            .annotate(day=TruncDate("period_start"))
            .values("day")
            .annotate(
                download=Sum("download_bytes"),
                upload=Sum("upload_bytes"),
            )
            .order_by("day")
        )

        return Response([
            {
                "day": x["day"],
                "download_mb": round((x["download"] or 0) / (1024 * 1024), 2),
                "upload_mb": round((x["upload"] or 0) / (1024 * 1024), 2),
            }
            for x in qs
        ])
        
class AdminUsageDailyView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        days = int(request.query_params.get("days", 7))
        since = timezone.now() - timezone.timedelta(days=days)

        pppoe = (
            PPPoEUsageRecord.objects
            .filter(period_start__gte=since)
            .annotate(day=TruncDate("period_start"))
            .values("day")
            .annotate(
                download=Sum("download_bytes"),
                upload=Sum("upload_bytes"),
            )
        )

        hotspot = (
            HotspotUsageRecord.objects
            .filter(period_start__gte=since)
            .annotate(day=TruncDate("period_start"))
            .values("day")
            .annotate(
                download=Sum("download_bytes"),
                upload=Sum("upload_bytes"),
            )
        )

        # merge PPPoE + Hotspot by day
        data = {}
        for x in list(pppoe) + list(hotspot):
            day = x["day"]
            if day not in data:
                data[day] = {"day": day, "download": 0, "upload": 0}
            data[day]["download"] += x["download"] or 0
            data[day]["upload"] += x["upload"] or 0

        return Response([
            {
                "day": k,
                "download_gb": round(v["download"] / (1024**3), 2),
                "upload_gb": round(v["upload"] / (1024**3), 2),
            }
            for k, v in sorted(data.items())
        ])
        
class AdminUsageAlertsView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        nearing_limit = []

        for c in Customer.objects.filter(status="active"):
            # Compute % used based on your business logic
            # Example 1: If you have usage and limit fields
            if hasattr(c, 'usage') and hasattr(c, 'limit') and c.limit > 0:
                percent = (c.usage / c.limit) * 100
            # Example 2: Custom calculation method on Customer model
            elif hasattr(c, 'calculate_usage_percent'):
                percent = c.calculate_usage_percent()
            # Example 3: Placeholder - replace with your actual logic
            else:
                # You need to implement your actual percent calculation
                percent = 0  # Replace with actual calculation
            
            if percent >= 80:
                nearing_limit.append({
                    "customer": c.full_name,
                    "phone": c.phone,
                    "percent": percent,
                })

        return Response(nearing_limit)