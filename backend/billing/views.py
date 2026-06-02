from decimal import Decimal
from django.http import HttpResponse
from django.db import transaction
from celery import chain
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework.filters import SearchFilter
from .permissions import IsStaffOrAdmin,IsCustomer,IsAdmin
from .throttles import LoginRateThrottle, HotspotPublicThrottle, MpesaCallbackThrottle, STKPushThrottle
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
from .pagination import StandardPagination
from billing.models import Voucher
from billing.tasks.mpesa_tasks import initiate_stk_push_task
from django.utils import timezone
from rest_framework import viewsets
from .models import Customer, Package, Subscription,Invoice, Payment,MpesaTransaction,SystemSetting,RouterFailoverLog, HotspotUsageRecord, AccessAuditLog
from .serializers import (CustomerSerializer,PackageSerializer,SubscriptionSerializer,InvoiceSerializer,  PaymentSerializer,SystemSettingSerializer,)
from billing.tasks.notification_tasks import notify_customer_task,send_sms_task, send_whatsapp_task
from .config import get_setting
from rest_framework.permissions import IsAuthenticated
from .serializers import UserProfileSerializer
from billing.router_service import enable_customer_access
from billing.tasks.router_tasks import (enable_customer_task,disable_customer_task,disconnect_pppoe_task)

class ThrottledLoginView(TokenObtainPairView):
    throttle_classes = [LoginRateThrottle]


def home(request):
    return HttpResponse("WiFi Billing Backend is Running")


def health_check(request):
    from django.db import connection
    from django.http import JsonResponse

    checks = {}
    overall = "ok"

    # Database
    try:
        connection.ensure_connection()
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = str(exc)
        overall = "error"

    # Redis / cache
    try:
        from django.core.cache import cache
        cache.set("_hc", "1", timeout=5)
        checks["redis"] = "ok" if cache.get("_hc") == "1" else "miss"
        if checks["redis"] != "ok":
            overall = "degraded"
    except Exception as exc:
        checks["redis"] = str(exc)
        overall = "degraded"  # degraded, not full error — app still runs without cache

    http_status = 200 if overall == "ok" else (503 if overall == "error" else 200)
    return JsonResponse({"status": overall, "checks": checks}, status=http_status)


class UserProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = UserProfileSerializer(request.user)
        return Response(serializer.data)


class CustomerViewSet(viewsets.ModelViewSet):
    serializer_class = CustomerSerializer
    permission_classes = [IsAdmin]
    filter_backends = [SearchFilter]
    search_fields = ["full_name", "phone", "pppoe_username"]

    def get_queryset(self):
        qs = (
            Customer.objects
            .select_related("user", "router")
            .order_by("-created_at")
        )
        status_filter = self.request.query_params.get("status")
        conn_filter   = self.request.query_params.get("connection_type")
        if status_filter:
            qs = qs.filter(status=status_filter)
        if conn_filter:
            qs = qs.filter(connection_type=conn_filter)
        return qs


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

from billing.tasks.mpesa_tasks import initiate_stk_push_task


class MpesaSTKPushView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [STKPushThrottle]

    def post(self, request):
        subscription_id = request.data.get("subscription_id")
        phone_number = request.data.get("phone_number")

        if not subscription_id or not phone_number:
            return Response(
                {"detail": "subscription_id and phone_number are required"},
                status=400,
            )

        subscription = get_object_or_404(
            Subscription,
            id=subscription_id,
            customer__user=request.user,
        )

        invoice = getattr(subscription, "invoice", None)
        if not invoice:
            return Response(
                {"detail": "No invoice found"},
                status=400,
            )

        if invoice.payment_status in ("paid", "pending"):
            return Response(
                {"detail": "Payment already initiated"},
                status=400,
            )

        # 🚀 Schedule task ONLY
        initiate_stk_push_task.delay(invoice.id, phone_number)

        return Response(
            {
                "detail": "STK Push scheduled",
                "invoice_number": invoice.invoice_number,
            },
            status=202,
        )


class MpesaSTKCallbackView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [MpesaCallbackThrottle]

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

        # Idempotency — use get_or_create to prevent race conditions where two
        # concurrent Safaricom retry callbacks both pass an .exists() check before
        # either has committed, resulting in duplicate MpesaTransaction rows.
        if mpesa_receipt:
            tx, created = MpesaTransaction.objects.get_or_create(
                mpesa_receipt=mpesa_receipt,
                defaults={
                    "amount": amount or 0,
                    "phone_number": phone,
                    "account_reference": reference,
                    "raw_payload": request.data,
                    "status": "success" if result_code == 0 else "failed",
                },
            )
            if not created:
                return Response({"detail": "Duplicate callback ignored"})
        else:
            tx = MpesaTransaction.objects.create(
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

        if Decimal(str(amount)) != invoice.total_amount:
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

        method = request.data.get("method", "cash")
        if method not in {"cash", "mpesa", "bank"}:
            return Response({"detail": "method must be cash, mpesa, or bank"}, status=400)

        with transaction.atomic():
            # select_for_update prevents two concurrent manual payments from
            # both passing the "already paid" check before either commits.
            invoice = (
                Invoice.objects
                .select_for_update()
                .select_related("customer", "subscription")
                .filter(invoice_number=invoice_number)
                .first()
            )
            if not invoice:
                return Response({"detail": "Invoice not found"}, status=404)

            if invoice.payment_status == "paid":
                return Response({"detail": "Invoice already paid"}, status=400)

            if Decimal(str(amount)) != invoice.total_amount:
                return Response({"detail": "Amount mismatch"}, status=400)

            Payment.objects.create(
                customer=invoice.customer,
                subscription=invoice.subscription,
                amount=amount,
                method=method,
                reference=reference,
            )

        return Response({"detail": "Payment recorded successfully"}, status=201)


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
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = InvoiceDashboardSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class PendingInvoicesView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        qs = pending_invoices()
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = InvoiceDashboardSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class FailedMpesaTransactionsView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        qs = failed_mpesa_transactions()
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = MpesaTransactionDashboardSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

class HotspotVoucherValidateView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [HotspotPublicThrottle]

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
    throttle_classes = [HotspotPublicThrottle]

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
            subscription.delete()  # cascade-deletes the invoice, prevents ghost record
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
    permission_classes = [IsAuthenticated]

    def get(self, request, customer_id):
        user = request.user

        if user.role == "customer":
            try:
                own = user.customer_profile
            except Customer.DoesNotExist:
                return Response({"detail": "Customer not found"}, status=404)
            if own.id != int(customer_id):
                return Response({"detail": "Forbidden"}, status=403)
            customer = own
        else:
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

        # Invalidate Redis cache across all workers so new values apply immediately
        from .config import clear_settings_cache
        clear_settings_cache()

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

        channel      = serializer.validated_data["channel"]
        audience     = serializer.validated_data["audience"]
        message      = serializer.validated_data["message"]
        customer_ids = serializer.validated_data.get("customer_ids", [])

        if audience == "custom" and not customer_ids:
            return Response(
                {"detail": "customer_ids is required when audience=custom"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Dispatch a single Celery task that iterates customers in chunks.
        # The old pattern loaded every Customer into the web worker's memory
        # and looped synchronously — at 10k customers this blocked the worker.
        from billing.tasks.notification_tasks import dispatch_broadcast_task
        task = dispatch_broadcast_task.delay(audience, channel, message, customer_ids)

        return Response(
            {
                "detail": "Broadcast queued for delivery",
                "task_id": task.id,
                "audience": audience,
                "channel": channel,
            },
            status=status.HTTP_202_ACCEPTED,
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

        # reconnect — chain guarantees disconnect completes before enable
        chain(
            disconnect_pppoe_task.si(customer.id),
            enable_customer_task.si(customer.id),
        ).delay()
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
        # Pre-load all PPPoE customers into a dict for O(1) lookup per session.
        # Without this, the original code fired one DB query per active PPPoE
        # session (N+1): with 300 sessions = 300 individual SELECT queries.
        customer_by_username = {
            c.pppoe_username: c
            for c in Customer.objects.filter(
                connection_type="pppoe",
                pppoe_username__isnull=False,
            ).exclude(pppoe_username="")
        }

        data = []
        routers = RouterDevice.objects.filter(is_active=True)

        for router in routers:
            try:
                sessions = get_all_pppoe_sessions(router)
            except Exception:
                continue

            for s in sessions:
                customer = customer_by_username.get(s.get("username"))
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

        chain(
            disconnect_pppoe_task.si(customer.id),
            enable_customer_task.si(customer.id),
        ).delay()

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
        # Use cached is_online from the background health task — do NOT make
        # live socket probes here. Probing N routers synchronously during an
        # HTTP request blocks a Gunicorn worker for N × timeout seconds.
        routers = RouterDevice.objects.all().order_by("priority")
        data = [
            {
                "id": r.id,
                "name": r.name,
                "ip_address": r.ip_address,
                "api_port": r.api_port,
                "priority": r.priority,
                "is_active": r.is_active,
                "max_pppoe_sessions": r.max_pppoe_sessions,
                "online": r.is_online,
                "last_seen": r.last_seen,
                "last_error": r.last_error,
            }
            for r in routers
        ]
        return Response(data)

    def post(self, request):
        from .serializers import RouterSerializer
        serializer = RouterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        router = serializer.save()
        return Response(RouterSerializer(router).data, status=status.HTTP_201_CREATED)


class AdminRouterDetailView(APIView):
    permission_classes = [IsAdminUser]

    def _get(self, pk):
        try:
            return RouterDevice.objects.get(pk=pk)
        except RouterDevice.DoesNotExist:
            return None

    def get(self, request, pk):
        router = self._get(pk)
        if not router:
            return Response({"detail": "Not found"}, status=404)
        from .serializers import RouterSerializer
        return Response(RouterSerializer(router).data)

    def put(self, request, pk):
        router = self._get(pk)
        if not router:
            return Response({"detail": "Not found"}, status=404)
        from .serializers import RouterSerializer
        serializer = RouterSerializer(router, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(RouterSerializer(router).data)

    def delete(self, request, pk):
        router = self._get(pk)
        if not router:
            return Response({"detail": "Not found"}, status=404)
        router.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
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
        days = min(max(int(request.query_params.get("days", 7)), 1), 365)

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
        months = min(max(int(request.query_params.get("months", 6)), 1), 24)

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
        days = min(max(int(request.query_params.get("days", 7)), 1), 365)
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
        from django.db.models import F

        # ── Step 1: one query — active subscriptions with customer + package ─
        # Deduplicate in Python to get one subscription per customer (most recent).
        active_subs = (
            Subscription.objects
            .filter(status="active", customer__status="active")
            .select_related("customer", "package")
            .order_by("customer_id", "-expiry_date")
        )

        # customer_id → (subscription, cap_gb)
        sub_map: dict = {}
        for sub in active_subs:
            cid = sub.customer_id
            if cid in sub_map:
                continue  # already have the most-recent active sub for this customer
            cap_gb = sub.customer.custom_data_cap_gb or sub.package.monthly_data_cap_gb
            if cap_gb:
                sub_map[cid] = (sub, cap_gb)

        if not sub_map:
            return Response([])

        customer_ids = list(sub_map.keys())

        # ── Step 2: bulk aggregate PPPoE usage (1 query) ──────────────────────
        pppoe_usage = dict(
            PPPoEUsageRecord.objects
            .filter(customer_id__in=customer_ids)
            .values("customer_id")
            .annotate(total=Sum(F("download_bytes") + F("upload_bytes")))
            .values_list("customer_id", "total")
        )

        # ── Step 3: bulk aggregate Hotspot usage (1 query) ───────────────────
        hotspot_usage = dict(
            HotspotUsageRecord.objects
            .filter(customer_id__in=customer_ids)
            .values("customer_id")
            .annotate(total=Sum(F("download_bytes") + F("upload_bytes")))
            .values_list("customer_id", "total")
        )

        # ── Step 4: join in Python — zero additional DB queries ───────────────
        nearing_limit = []
        for cid, (sub, cap_gb) in sub_map.items():
            customer = sub.customer
            if customer.connection_type == "pppoe":
                total_bytes = pppoe_usage.get(cid, 0) or 0
            else:
                total_bytes = hotspot_usage.get(cid, 0) or 0

            total_gb = total_bytes / (1024 ** 3)
            percent  = (total_gb / cap_gb) * 100

            if percent >= 80:
                nearing_limit.append({
                    "customer": customer.full_name,
                    "phone":    customer.phone,
                    "used_gb":  round(total_gb, 2),
                    "cap_gb":   cap_gb,
                    "percent":  round(percent, 1),
                })

        return Response(
            sorted(nearing_limit, key=lambda x: x["percent"], reverse=True)
        )

class AdminAccessLookupView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        query = request.query_params.get("q")

        if not query:
            return Response(
                {"detail": "Query parameter ?q= is required"},
                status=400,
            )

        # --------------------------------------------------
        # 1️⃣ Voucher lookup
        # --------------------------------------------------
        voucher = (
            Voucher.objects
            .select_related("subscription__customer", "subscription__package")
            .filter(code=query)
            .first()
        )

        if voucher:
            sub = voucher.subscription
            pkg = sub.package
            cust = sub.customer

            return Response({
                "type": "voucher",
                "customer": {
                    "id": cust.id,
                    "name": cust.full_name,
                    "phone": cust.phone,
                    "connection_type": cust.connection_type,
                    "status": cust.status,
                },
                "subscription": {
                    "id": sub.id,
                    "package": pkg.name,
                    "status": sub.status,
                    "expires_at": sub.expiry_date,
                    "duration": f"{pkg.duration_value} {pkg.duration_unit}",
                },
                "voucher": {
                    "code": voucher.code,
                    "created_at": voucher.created_at,
                    "expires_at": voucher.expires_at,
                    "is_active": voucher.is_active,
                },
            })

        # --------------------------------------------------
        # 2️⃣ M-Pesa receipt lookup (voucher by payment)
        # --------------------------------------------------
        payment = (
            Payment.objects
            .select_related("subscription__customer", "subscription__package")
            .filter(reference=query)
            .first()
        )

        if payment:
            sub = payment.subscription
            pkg = sub.package
            cust = sub.customer

            return Response({
                "type": "mpesa",
                "customer": {
                    "id": cust.id,
                    "name": cust.full_name,
                    "phone": cust.phone,
                    "connection_type": cust.connection_type,
                    "status": cust.status,
                },
                "subscription": {
                    "id": sub.id,
                    "package": pkg.name,
                    "status": sub.status,
                    "expires_at": sub.expiry_date,
                    "duration": f"{pkg.duration_value} {pkg.duration_unit}",
                },
                "voucher": None,
            })

        # --------------------------------------------------
        # 3️⃣ Phone number lookup
        # --------------------------------------------------
        customer = Customer.objects.filter(phone=query).first()

        if customer:
            sub = (
                customer.subscriptions
                .select_related("package")
                .order_by("-expiry_date")
                .first()
            )

            if not sub:
                return Response(
                    {"detail": "Customer found but no subscription"},
                    status=404,
                )

            pkg = sub.package
            voucher = sub.vouchers.filter(is_active=True).first()

            return Response({
                "type": "phone",
                "customer": {
                    "id": customer.id,
                    "name": customer.full_name,
                    "phone": customer.phone,
                    "connection_type": customer.connection_type,
                    "status": customer.status,
                },
                "subscription": {
                    "id": sub.id,
                    "package": pkg.name,
                    "status": sub.status,
                    "expires_at": sub.expiry_date,
                    "duration": f"{pkg.duration_value} {pkg.duration_unit}",
                },
                "voucher": (
                    {
                        "code": voucher.code,
                        "expires_at": voucher.expires_at,
                        "is_active": voucher.is_active,
                    }
                    if voucher else None
                ),
            })

        return Response(
            {"detail": "No access record found"},
            status=404,
        )
class AdminDeactivateVoucherView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request):
        code = request.data.get("code")
        reason = request.data.get("reason", "")

        voucher = Voucher.objects.filter(code=code, is_active=True).first()
        if not voucher:
            return Response({"detail": "Voucher not found"}, status=404)

        voucher.is_active = False
        voucher.save(update_fields=["is_active"])

        customer = voucher.subscription.customer
        disable_customer_task.delay(customer.id)

        return Response({"detail": "Voucher deactivated"})
class AdminDeactivateAccessView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request):
        subscription_id = request.data.get("subscription_id")
        reason = request.data.get("reason", "Admin deactivation")

        if not subscription_id:
            return Response(
                {"detail": "subscription_id is required"},
                status=400,
            )

        try:
            subscription = Subscription.objects.select_related(
                "customer"
            ).get(id=subscription_id)
        except Subscription.DoesNotExist:
            return Response(
                {"detail": "Subscription not found"},
                status=404,
            )

        customer = subscription.customer

        with transaction.atomic():
            # 1️⃣ Expire subscription
            subscription.status = "expired"
            subscription.expiry_date = timezone.now()
            subscription.save(update_fields=["status", "expiry_date"])

            # 2️⃣ Deactivate all vouchers linked to this subscription
            Voucher.objects.filter(
                subscription=subscription,
                is_active=True,
            ).update(is_active=False)

            # 3️⃣ Update customer status
            customer.status = "expired"
            customer.save(update_fields=["status"])

            # 4️⃣ Audit log
            AccessAuditLog.objects.create(
                customer=customer,
                subscription=subscription,
                action="deactivate",
                reason=reason,
            )

        # 5️⃣ Disable router access (async)
        disable_customer_task.delay(customer.id)

        return Response(
            {
                "detail": "Access deactivated successfully",
                "customer": customer.full_name,
                "subscription_id": subscription.id,
            },
            status=200,
        )
class DailyRevenueView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        days = min(max(int(request.query_params.get("days", 30)), 1), 90)
        since = timezone.now() - timezone.timedelta(days=days)
        from .models import Payment
        data = (
            Payment.objects
            .filter(paid_at__gte=since)
            .annotate(day=TruncDate("paid_at"))
            .values("day")
            .annotate(revenue=Sum("amount"))
            .order_by("day")
        )
        return Response([
            {"date": str(x["day"]), "revenue": float(x["revenue"] or 0)}
            for x in data
        ])


class HotspotReconnectView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        mac = request.data.get("mac")

        if not mac:
            return Response(
                {"detail": "MAC address required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        customer = Customer.objects.filter(
            hotspot_username=mac,
            status="active",
        ).first()

        if not customer:
            return Response(
                {"status": "denied", "reason": "not_registered"},
                status=403,
            )

        subscription = (
            customer.subscriptions
            .filter(status="active")
            .order_by("-expiry_date")
            .first()
        )

        if not subscription:
            return Response(
                {"status": "denied", "reason": "no_subscription"},
                status=403,
            )

        if subscription.expiry_date <= timezone.now():
            return Response(
                {"status": "expired"},
                status=403,
            )

        # ✅ Re-enable access (ASYNC)
        enable_customer_task.delay(customer.id)

        return Response({
            "status": "allowed",
            "expires_at": subscription.expiry_date,
        })