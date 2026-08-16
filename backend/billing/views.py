import secrets
import string
from decimal import Decimal
from django.http import HttpResponse
from django.db import IntegrityError, transaction
from celery import chain
from .utils import mac_variants, normalize_mac
from .auth_tokens import TenantTokenObtainPairView, TenantTokenObtainPairSerializer
from rest_framework.filters import SearchFilter
from .permissions import (
    IsCustomer, IsPlatformOwner, IsPlatformStaff, IsTenantAdmin,
    IsTenantAdminForBilling, IsTenantAdminOrReadOnlyMember, IsTenantMember,
)
from .throttles import (
    LoginRateThrottle, HotspotPollThrottle, HotspotPublicThrottle,
    MpesaCallbackThrottle, RouterTestThrottle, STKPushThrottle,
)
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from .security import (
    device_token_for,
    device_token_matches,
    is_trusted_mpesa_ip,
    poll_token_for,
    poll_token_matches,
)
from billing.services.voucher_service import (
    REFUSED_EXPIRED, describe_refusal, mark_voucher_used, validate_voucher,
)
from billing.router_service import enable_customer_access
from .mpesa_client import initiate_stk_push
from billing.models import Customer,Subscription,PPPoEUsageRecord
from billing.notifications import send_sms, send_whatsapp, notify_customer
from billing.serializers import BroadcastSerializer
from billing.mpesa_client import get_mpesa_access_token, missing_mpesa_keys
from django.db.models import Count, Prefetch, Q, Sum
from django.db.models.functions import TruncDate, TruncMonth
from .reports import (revenue_summary,revenue_by_method,revenue_by_package,customer_stats,)
from .analytics import (
    performance_pulse, revenue_series, peak_hours, expiring_soon,
    customer_flow, by_station,
    revenue_by_package as analytics_by_package,
    revenue_by_method as analytics_by_method,
)
from django.utils.dateparse import parse_datetime
from .dashboards import (unpaid_invoices,pending_invoices,failed_mpesa_transactions,message_logs,)
from .notifications import normalise_phone
from .serializers import (
    InvoiceDashboardSerializer,
    MessageLogSerializer,
    MpesaTransactionDashboardSerializer,
    MpesaTransactionSerializer,
)
from .pagination import StandardPagination
from billing.models import Voucher
from billing.tasks.mpesa_tasks import initiate_stk_push_task
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from .models import ConnectionAttempt, Customer, CustomerDevice, Package, Subscription,Invoice, Payment,MpesaTransaction,SystemSetting,RouterFailoverLog, HotspotUsageRecord, AccessAuditLog, Tenant
from .models import PlatformPlan, TenantSubscription, TenantInvoice, TenantPayment, TenantStatusChange
from .models import RouterEvent, Station, TenantScopedModel, router_uptime
from .models import ImpersonationLog
from .models import User, AdminActionLog, record_admin_action
from .tenancy import tenant_context, get_current_tenant_id, all_tenants
import logging

logger = logging.getLogger(__name__)
from .serializers import (PlatformPlanSerializer, TenantSubscriptionSerializer,
                          TenantInvoiceSerializer, TenantPaymentSerializer,
                          OperatorCreateSerializer, OperatorUpdateSerializer,
                          ChangePasswordSerializer, TenantUserSerializer,
                          StationSerializer,)
from .serializers import (CustomerSerializer,CustomerDetailSerializer,PackageSerializer,SubscriptionSerializer,InvoiceSerializer,  PaymentSerializer,SystemSettingSerializer,)
from billing.tasks.notification_tasks import notify_customer_task,send_sms_task, send_whatsapp_task
from .config import get_setting
from rest_framework.permissions import IsAuthenticated
from .serializers import UserProfileSerializer
from billing.router_service import enable_customer_access
from billing.tasks.router_tasks import (enable_customer_task,disable_customer_task,disconnect_pppoe_task)

class ThrottledLoginView(TenantTokenObtainPairView):
    """Issues tokens carrying the operator and role claims."""
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

    def patch(self, request):
        """Change your own username or email. Role and tenant are not here."""
        old_username = request.user.username
        serializer = UserProfileSerializer(
            request.user, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        if user.username != old_username:
            record_admin_action(
                request.user, AdminActionLog.CHANGE_USERNAME,
                target_user=user, detail=f"{old_username} -> {user.username}",
            )
        return Response(serializer.data)


class ChangePasswordView(APIView):
    """
    Set your own password.

    Every other session ends. Someone changing their password because they
    think it is known to someone else gets nothing from a change that leaves
    the other sessions signed in.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)

        user = request.user
        user.set_password(serializer.validated_data["new_password"])
        user.must_change_password = False
        user.save(update_fields=["password", "must_change_password"])
        user.invalidate_sessions()

        record_admin_action(user, AdminActionLog.CHANGE_PASSWORD, target_user=user)

        # The caller's own token is now stale too, so hand back a fresh pair
        # rather than bouncing someone to the login screen for succeeding.
        refresh = TenantTokenObtainPairSerializer.get_token(user)
        return Response({
            "detail": "Password changed. Other sessions have been signed out.",
            "access": str(refresh.access_token),
            "refresh": str(refresh),
        })


class TenantUserViewSet(viewsets.ModelViewSet):
    """
    An operator admin managing their own staff.

    Hard-scoped to the caller's tenant in both directions: the queryset filters
    by it, and create/update force it. Neither is redundant — the filter stops
    reading another operator's accounts, and forcing on write stops a payload
    with someone else's tenant id from planting an account inside them.
    """

    serializer_class = TenantUserSerializer
    permission_classes = [IsTenantAdmin]

    def get_queryset(self):
        tenant_id = get_current_tenant_id()
        if tenant_id is None:
            # Platform staff reach this while impersonating; without a tenant
            # in scope there is no "their staff" to list.
            return User.objects.none()
        return (
            User.objects.filter(tenant_id=tenant_id)
            .filter(role__in=(User.TENANT_ADMIN, User.TENANT_STAFF))
            .order_by("username")
        )

    def perform_create(self, serializer):
        tenant_id = get_current_tenant_id()
        if tenant_id is None:
            raise ValidationError("No operator in scope for this request.")
        password = serializer.validated_data.pop("password")
        user = serializer.save(tenant_id=tenant_id)
        user.set_password(password)
        # Their admin chose this password, so the holder is made to replace it.
        user.must_change_password = True
        user.save(update_fields=["password", "must_change_password"])
        record_admin_action(
            self.request.user, AdminActionLog.CREATE_USER,
            target_user=user, detail=f"role={user.role}",
        )

    def perform_update(self, serializer):
        before_role = serializer.instance.role
        before_active = serializer.instance.is_active
        password = serializer.validated_data.pop("password", None)
        user = serializer.save()

        if password:
            user.set_password(password)
            user.must_change_password = True
            user.save(update_fields=["password", "must_change_password"])
            user.invalidate_sessions()
            record_admin_action(
                self.request.user, AdminActionLog.RESET_PASSWORD, target_user=user)

        if user.role != before_role:
            record_admin_action(
                self.request.user, AdminActionLog.CHANGE_ROLE,
                target_user=user, detail=f"{before_role} -> {user.role}",
            )
        if user.is_active != before_active:
            # Disabling has to end the sessions too. is_active alone only stops
            # the next sign-in; an issued token would keep working without this.
            user.invalidate_sessions()
            record_admin_action(
                self.request.user,
                AdminActionLog.DISABLE_USER if not user.is_active
                else AdminActionLog.ENABLE_USER,
                target_user=user,
            )

    def perform_destroy(self, instance):
        """
        Disable rather than delete.

        Deleting would take the account's audit trail and its foreign keys with
        it, and 'this person left' is not the same fact as 'this person never
        existed'.
        """
        if instance.pk == self.request.user.pk:
            raise ValidationError("You cannot disable your own account.")
        instance.is_active = False
        instance.save(update_fields=["is_active"])
        instance.invalidate_sessions()
        record_admin_action(
            self.request.user, AdminActionLog.DISABLE_USER, target_user=instance)


class CustomerViewSet(viewsets.ModelViewSet):
    serializer_class = CustomerSerializer
    permission_classes = [IsTenantAdminOrReadOnlyMember]
    filter_backends = [SearchFilter]
    # A hotspot subscriber has no pppoe_username, so searching by the only
    # identifiers they actually have — the code on their receipt, the device
    # they are sitting behind — matched nothing at all. Which is exactly what
    # an operator is holding when someone reads a code down the phone.
    search_fields = [
        "full_name",
        "phone",
        "pppoe_username",
        "hotspot_username",
        "subscriptions__vouchers__code",
        "payments__reference",
    ]

    def get_serializer_class(self):
        # Only the detail page needs subscriptions/vouchers. Writes keep using
        # CustomerSerializer so its pppoe_password preservation still applies.
        if self.action == "retrieve":
            return CustomerDetailSerializer
        return CustomerSerializer

    def create(self, request, *args, **kwargs):
        # Plan caps limit growth only. An operator already over their cap
        # keeps every subscriber they have — downgrading a plan must never
        # disconnect people who are already paying.
        tenant = getattr(request.user, "tenant", None)
        if tenant is not None:
            blocked = tenant.plan_limit_exceeded("customers")
            if blocked:
                return Response({"detail": blocked}, status=status.HTTP_402_PAYMENT_REQUIRED)

        package_id = request.data.get("package")
        paid_with = request.data.get("paid_with")

        response = super().create(request, *args, **kwargs)
        if response.status_code != status.HTTP_201_CREATED or not package_id:
            return response

        # Selling at the counter.
        #
        # Creating a hotspot customer used to produce a row with a MAC, no
        # subscription and no voucher — marked active with no access, and
        # nothing in the interface to give them a code. An operator taking cash
        # from someone standing in front of them had no way to finish the job.
        #
        # Optional: without a package this behaves exactly as it always did.
        customer = Customer.objects.filter(id=response.data["id"]).first()
        package = Package.objects.filter(id=package_id).first()
        if customer is None or package is None:
            response.data["provisioning_error"] = "That package is not available."
            return response

        try:
            with transaction.atomic():
                subscription = Subscription.objects.create(
                    tenant_id=customer.tenant_id,
                    customer=customer,
                    package=package,
                )
                if paid_with in ("cash", "mpesa", "bank"):
                    # Payment.save() is what mints the voucher and provisions
                    # access, so recording it is the whole point rather than
                    # bookkeeping after the fact.
                    Payment.objects.create(
                        tenant_id=customer.tenant_id,
                        customer=customer,
                        subscription=subscription,
                        amount=package.price,
                        method=paid_with,
                        reference=(request.data.get("payment_reference") or "").strip(),
                    )
        except Exception as exc:
            logger.exception("[provision] %s could not be set up", customer)
            response.data["provisioning_error"] = str(exc)
            return response

        voucher = (
            Voucher.objects.all_tenants()
            .filter(tenant_id=customer.tenant_id, subscription=subscription)
            .order_by("-created_at")
            .first()
        )
        response.data["subscription_id"] = subscription.id
        response.data["expires_at"] = subscription.expiry_date
        response.data["voucher_code"] = voucher.code if voucher else None
        return response

    def get_queryset(self):
        qs = (
            Customer.objects
            .select_related("user", "router")
            .order_by("-created_at")
        )

        if self.action in ("retrieve", "list"):
            # Prefetch what the serializers walk, so rendering stays at a fixed
            # number of queries. The list needs it too now that a row carries
            # the subscriber's voucher — without it that is one extra query per
            # customer on a paginated page.
            qs = qs.prefetch_related(
                Prefetch(
                    "subscriptions",
                    queryset=Subscription.objects
                        .select_related("package")
                        .order_by("-expiry_date"),
                ),
                "subscriptions__vouchers",
                "devices",
            )

        status_filter = self.request.query_params.get("status")
        conn_filter   = self.request.query_params.get("connection_type")
        if status_filter:
            qs = qs.filter(status=status_filter)
        if conn_filter:
            qs = qs.filter(connection_type=conn_filter)
        return qs


class PackageViewSet(viewsets.ModelViewSet):
    serializer_class = PackageSerializer
    permission_classes = [IsTenantAdminOrReadOnlyMember]

    # Must be a method, not a class attribute. A class-level
    # `queryset = Package.objects.all()` is built once at import time, when no
    # tenant is in context, so the manager's filter never applies — DRF's
    # .all() clones that queryset rather than re-consulting the manager, and
    # every operator would see every other operator's packages.
    def get_queryset(self):
        # Ordered explicitly: pagination over an unordered queryset can repeat
        # or skip rows between pages.
        return Package.objects.order_by("id")

    def destroy(self, request, *args, **kwargs):
        """
        Refuse to delete a package anybody is on, or has ever been on.

        Deleting one used to cascade through every subscription and take the
        invoices, payments and vouchers with it. A package that has been sold
        is part of the billing record; what an operator wants when they say
        "delete" is for it to stop being offered, and that is archiving.
        """
        package = self.get_object()

        active = Subscription.objects.filter(
            package=package, status="active").count()
        total = Subscription.objects.filter(package=package).count()

        if active:
            return Response(
                {
                    "detail": (
                        f"{active} customer{'s are' if active > 1 else ' is'} "
                        f"on this package. Move or suspend "
                        f"{'them' if active > 1 else 'them'} first, or archive "
                        f"the package to stop selling it."
                    ),
                    "active_subscriptions": active,
                    "can_archive": True,
                },
                status=status.HTTP_409_CONFLICT,
            )

        if total:
            return Response(
                {
                    "detail": (
                        f"This package appears on {total} past "
                        f"subscription{'s' if total > 1 else ''} and their "
                        f"invoices, so deleting it would delete the record of "
                        f"what those customers paid for. Archive it instead — "
                        f"it stops being offered and nothing is lost."
                    ),
                    "past_subscriptions": total,
                    "can_archive": True,
                },
                status=status.HTTP_409_CONFLICT,
            )

        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"], url_path="archive")
    def archive(self, request, pk=None):
        """
        Retire a package from sale without touching anybody on it.

        What "delete" nearly always means: stop offering this. Existing
        subscribers keep what they bought until it expires.
        """
        package = self.get_object()
        package.is_archived = not package.is_archived
        package.save(update_fields=["is_archived"])
        return Response({
            "detail": (
                f"{package.name} is archived and will not be offered to anyone new."
                if package.is_archived else
                f"{package.name} is on sale again."
            ),
            "is_archived": package.is_archived,
        })


class SubscriptionViewSet(viewsets.ModelViewSet):
    serializer_class = SubscriptionSerializer
    permission_classes = [IsTenantMember | IsCustomer]

    def get_queryset(self):
        user = self.request.user

        if user.role == "customer":
            return Subscription.objects.filter(customer__user=user)

        return Subscription.objects.select_related("customer", "package")


class InvoiceViewSet(viewsets.ModelViewSet):
    serializer_class = InvoiceSerializer
    permission_classes = [IsTenantAdminOrReadOnlyMember]

    # Method, not a class attribute — see PackageViewSet above.
    def get_queryset(self):
        return Invoice.objects.select_related("customer", "subscription")


class PaymentViewSet(viewsets.ModelViewSet):
    serializer_class = PaymentSerializer
    permission_classes = [IsTenantMember | IsCustomer]

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

        # Fail here rather than in the worker. An operator still waiting on
        # Safaricom would otherwise see the request accepted and the STK prompt
        # never arrive, with the reason buried in worker logs.
        from billing.mpesa_client import missing_mpesa_keys
        missing = missing_mpesa_keys(tenant=invoice.tenant)
        if missing:
            return Response(
                {
                    "detail": "This provider has not finished setting up M-Pesa payments.",
                    "missing_settings": missing,
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
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
    """
    Safaricom posts results here. There is no JWT, so the operator is resolved
    from the URL token when present, and otherwise from the invoice number —
    which stays globally unique precisely so this works.
    """
    permission_classes = [permissions.AllowAny]
    throttle_classes = [MpesaCallbackThrottle]

    def post(self, request, tenant_token=None):
        from billing.security import client_ip

        source_ip = client_ip(request)

        # Per-operator URL. Unknown tokens are rejected rather than silently
        # falling back, so a mistyped callback URL fails loudly at setup time
        # instead of quietly booking payments against the wrong operator.
        tenant = None
        if tenant_token:
            tenant = Tenant.objects.filter(public_token=tenant_token).first()
            if tenant is None:
                logger.warning(
                    "[mpesa] Callback for unknown tenant token %s from %s",
                    tenant_token, source_ip,
                )
                return Response(
                    {"detail": "Unknown callback endpoint"},
                    status=status.HTTP_404_NOT_FOUND,
                )

        body = request.data.get("Body", {}).get("stkCallback", {})
        result_code = body.get("ResultCode")
        result_desc = body.get("ResultDesc")
        checkout_id = body.get("CheckoutRequestID")
        merchant_id = body.get("MerchantRequestID")

        # ── Is this really Safaricom? ───────────────────────────────────────
        #
        # The address list is a fast path, not a gate. It held five entries
        # against a set Safaricom publishes more of and rotates without
        # telling anyone, and this view used to refuse anything outside it as
        # its very first act — before reading the body, before logging a line.
        # The result of being wrong was: customer charged, callback dropped,
        # no access granted, and nothing recorded anywhere to explain it. The
        # worst outcome this system can produce, arrived at silently.
        #
        # So an unlisted address is not enough to refuse. What actually
        # authenticates a callback is knowledge we only ever gave Safaricom:
        # the per-operator token in the URL, which went out in the
        # CallBackURL of the push, together with something in the body
        # matching a push this platform itself initiated. Neither is guessable
        # by someone spraying this endpoint, and both are stronger evidence
        # than a source address, which is asserted by the caller.
        if not is_trusted_mpesa_ip(request):
            # AccountReference is the invoice number this platform generated
            # and sent out with the push. Safaricom returns it in the metadata,
            # which is present precisely when ResultCode is 0 — that is, when
            # money has actually moved. So the case that can hurt a customer is
            # the case that carries the evidence.
            #
            # Not the URL token on its own: it ships inside config.js on every
            # router and is served to every subscriber's browser, so treating
            # it as a secret would let any customer forge a paid callback.
            claimed_ref = None
            for item in body.get("CallbackMetadata", {}).get("Item", []) or []:
                if item.get("Name") == "AccountReference":
                    claimed_ref = item.get("Value")
                    break

            # CheckoutRequestID first, because it is the only one a *failed*
            # push carries. Safaricom omits CallbackMetadata entirely unless
            # ResultCode is 0, so correlating on the account reference alone
            # left exactly the failure case unattributable — seen live on the
            # first real attempt: ResultCode 2029 from an unlisted address,
            # refused, and the invoice left pending with nothing to explain it.
            correlates = False
            if checkout_id:
                correlates = Invoice.objects.all_tenants().filter(
                    mpesa_checkout_request_id=checkout_id
                ).exists()
            if not correlates and claimed_ref:
                correlates = Invoice.objects.all_tenants().filter(
                    invoice_number=claimed_ref
                ).exists()

            if tenant is not None and correlates:
                # Accepted on evidence, and said out loud: an operator seeing
                # this repeatedly should add the address to MPESA_TRUSTED_IPS
                # so the fast path does its job again.
                logger.warning(
                    "[mpesa] Callback accepted from unlisted address %s — the "
                    "URL token is valid and invoice %s exists, so this is a "
                    "push we initiated. Add %s to MPESA_TRUSTED_IPS so the "
                    "fast path works again. (CheckoutRequestID %s)",
                    source_ip, claimed_ref, source_ip, checkout_id,
                )
            else:
                # Still refused — but never again silently. Everything needed
                # to tell a rotated Safaricom address from a stranger probing
                # the endpoint goes in the log.
                logger.warning(
                    "[mpesa] Callback REFUSED from %s — token=%s, "
                    "CheckoutRequestID=%s, correlates=%s, MerchantRequestID=%s, "
                    "ResultCode=%s, ResultDesc=%r. If this was Safaricom, a "
                    "paying customer has just been charged and not connected.",
                    source_ip, "valid" if tenant else "missing/invalid",
                    checkout_id, correlates, merchant_id, result_code,
                    result_desc,
                )
                return Response(
                    {"detail": "Unauthorized callback source"},
                    status=status.HTTP_403_FORBIDDEN,
                )

        items = body.get("CallbackMetadata", {}).get("Item", []) if result_code == 0 else []

        data = {i["Name"]: i.get("Value") for i in items}

        mpesa_receipt = data.get("MpesaReceiptNumber")
        amount = data.get("Amount")
        phone = str(data.get("PhoneNumber")) if data.get("PhoneNumber") else None
        reference = data.get("AccountReference")

        # ── Resolve the operator before writing anything ────────────────────
        # The invoice is authoritative: invoice_number is globally unique for
        # exactly this reason. The URL token is a cross-check, not the source of
        # truth, so a callback cannot be booked against the wrong operator by
        # pointing it at the wrong URL.
        # CheckoutRequestID first, because a Buy Goods callback has no account
        # reference to offer.
        #
        # A till has no account number, so Safaricom returns only Amount,
        # MpesaReceiptNumber, TransactionDate and PhoneNumber — the
        # AccountReference this used to resolve on is simply absent. Every
        # till payment therefore reached "Missing callback data": receipt
        # recorded, invoice left pending, no Payment, no access, and a
        # customer holding an M-Pesa confirmation SMS for money the platform
        # never credited. Seen on the first successful live payment, for KSh 5.
        invoice = None
        if checkout_id:
            invoice = (
                Invoice.objects.all_tenants()
                .select_related("customer", "subscription", "tenant")
                .filter(mpesa_checkout_request_id=checkout_id)
                .first()
            )
        if invoice is None and reference:
            invoice = (
                Invoice.objects.all_tenants()
                .select_related("customer", "subscription", "tenant")
                .filter(invoice_number=reference)
                .first()
            )
        # Downstream writes this onto the transaction and compares it against
        # the invoice, so give it the number the push was for.
        if invoice is not None and not reference:
            reference = invoice.invoice_number

        if invoice is not None and tenant is not None and invoice.tenant_id != tenant.id:
            logger.error(
                "[mpesa] Callback for invoice %s arrived on operator %s's endpoint "
                "but the invoice belongs to operator %s — refusing.",
                reference, tenant.id, invoice.tenant_id,
            )
            return Response(
                {"detail": "Callback does not match this endpoint"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        resolved = tenant or (invoice.tenant if invoice else None)
        if resolved is None:
            # A failed STK carries no metadata, so with neither a token nor a
            # reference there is nothing to attribute it to.
            only = Tenant.objects.first() if Tenant.objects.count() == 1 else None
            resolved = only
        if resolved is None:
            logger.error(
                "[mpesa] Cannot attribute callback to an operator "
                "(no URL token, no resolvable invoice). Payload: %s", request.data,
            )
            return Response(
                {"detail": "Cannot attribute callback"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Idempotency — use get_or_create to prevent race conditions where two
        # concurrent Safaricom retry callbacks both pass an .exists() check before
        # either has committed, resulting in duplicate MpesaTransaction rows.
        # Receipts are globally unique, so the lookup is deliberately unscoped.
        if mpesa_receipt:
            tx, created = MpesaTransaction.objects.all_tenants().get_or_create(
                mpesa_receipt=mpesa_receipt,
                defaults={
                    "tenant": resolved,
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
                tenant=resolved,
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

        # The reference is no longer required here. It is derived from the
        # invoice above when Safaricom did not send one, and a Buy Goods
        # callback never does — requiring it failed every till payment on a
        # field that product does not have.
        if not all([mpesa_receipt, amount]):
            tx.status = "failed"
            tx.error_message = "Missing callback data"
            tx.processed = True
            tx.save()
            return Response(status=400)

        # Already looked up above, before the operator was resolved.
        if invoice is None:
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

        # Act as the owning operator: Payment.save() picks a router and sends
        # the welcome message, both of which must use their hardware and their
        # messaging credentials.
        with tenant_context(invoice.tenant_id):
            with transaction.atomic():
                # Re-read under a lock and refuse an invoice that is already
                # paid. The receipt-level idempotency above only stops the SAME
                # receipt being applied twice; it does nothing about two
                # DIFFERENT receipts against one invoice — a customer who pays
                # twice, or an STK re-initiated after a timeout that then also
                # succeeds. Without this each one creates a Payment, and for a
                # hotspot customer Payment.save() mints a voucher, so one
                # purchase would hand out two.
                #
                # The manual payment path has guarded this from the start. This
                # is the automated path, which is also the one Safaricom
                # retries, so it needed it more.
                locked = (
                    Invoice.objects.all_tenants()
                    .select_for_update()
                    .select_related("customer", "subscription")
                    .get(pk=invoice.pk)
                )
                if locked.payment_status == "paid":
                    tx.invoice = locked
                    tx.processed = True
                    tx.status = "success"
                    tx.error_message = "Invoice already paid — no second payment recorded"
                    tx.save()
                    logger.warning(
                        "[mpesa] Receipt %s arrived for invoice %s which is "
                        "already paid. Recorded, not applied.",
                        mpesa_receipt, locked.invoice_number,
                    )
                    return Response({"detail": "Invoice already paid"})

                invoice = locked
                payment = Payment.objects.create(
                    tenant_id=invoice.tenant_id,
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
    # Full access for staff, not read-only: recording a cash payment taken at
    # the counter is the counter's job, and it is a POST.
    permission_classes = [IsTenantMember]

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
    permission_classes = [IsTenantMember]

    def get(self, request):
        return Response({
            "revenue_summary": revenue_summary(),
            "revenue_by_method": revenue_by_method(),
            "revenue_by_package": revenue_by_package(),
            "customer_stats": customer_stats(),
        })

class UnpaidInvoicesView(APIView):
    permission_classes = [IsTenantMember]

    def get(self, request):
        qs = unpaid_invoices()
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = InvoiceDashboardSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class PendingInvoicesView(APIView):
    permission_classes = [IsTenantMember]

    def get(self, request):
        qs = pending_invoices()
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = InvoiceDashboardSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class FailedMpesaTransactionsView(APIView):
    permission_classes = [IsTenantMember]

    def get(self, request):
        qs = failed_mpesa_transactions()
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = MpesaTransactionDashboardSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class MessageLogsView(APIView):
    """
    Every message this operator tried to send, and what came back.

    The counterpart of FailedMpesaTransactionsView above, and overdue. M-Pesa
    has had a table and a page since its callback was written; messaging had
    only logger.error, which is a file on a server an operator cannot read.
    That is how a rejected sender ID cost one of them a day — every send
    failing, nothing in the product saying so.

    ?status=errors is the default the page opens on: refused and failed
    together, because someone looking here has messages that did not arrive and
    should not have to know which of the two kinds theirs was.

    Staff may read it, like the M-Pesa ledger — "did my customer get their
    code" is a support question before it is an administrative one.
    """
    permission_classes = [IsTenantMember]

    def get(self, request):
        qs = message_logs(
            channel=request.GET.get("channel"),
            status=request.GET.get("status", "errors"),
        )

        search = (request.GET.get("search") or "").strip()
        if search:
            # The number is what an operator has in front of them, usually read
            # out by the customer, so it is matched in whichever form it was
            # stored — the provider is sent 2547…, the customer says 07…
            qs = qs.filter(
                Q(phone__icontains=search)
                | Q(phone__icontains=normalise_phone(search))
                | Q(reason__icontains=search)
            )

        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = MessageLogSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class MpesaTransactionsView(APIView):
    """
    Every M-Pesa transaction this operator has seen, searchable.

    The rows have been recorded since the callback was written — receipt,
    amount, phone, the full raw payload, whether it was applied and why not —
    and the only ones ever shown were the failures. An operator holding a
    receipt number a customer read out over the phone had nowhere to type it.

    Covers both connection types: it is one callback endpoint, and the
    connection type is a property of whoever the payment resolved to, not of
    the transaction.
    """
    permission_classes = [IsTenantMember]

    def get(self, request):
        qs = (
            MpesaTransaction.objects
            .select_related(
                "invoice", "invoice__customer", "payment", "payment__customer")
            .order_by("-created_at")
        )

        status_filter = request.GET.get("status")
        if status_filter in ("success", "failed"):
            qs = qs.filter(status=status_filter)

        processed = request.GET.get("processed")
        if processed in ("true", "false"):
            qs = qs.filter(processed=(processed == "true"))

        conn = request.GET.get("connection_type")
        if conn in ("pppoe", "hotspot"):
            # Through whichever link resolved. A transaction that never
            # attached to anybody has no connection type and is excluded, which
            # is right: filtering by one means asking about subscribers.
            qs = qs.filter(
                Q(payment__customer__connection_type=conn)
                | Q(invoice__customer__connection_type=conn)
            )

        search = (request.GET.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(mpesa_receipt__icontains=search)
                | Q(phone_number__icontains=search)
                | Q(account_reference__icontains=search)
                | Q(payment__customer__full_name__icontains=search)
                | Q(invoice__customer__full_name__icontains=search)
            )

        days = request.GET.get("days")
        if days:
            try:
                qs = qs.filter(
                    created_at__gte=timezone.now() - timezone.timedelta(days=int(days))
                )
            except (TypeError, ValueError):
                pass

        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs.distinct(), request)
        return paginator.get_paginated_response(
            MpesaTransactionSerializer(page, many=True).data
        )


# How long a hotspot session may sit idle before it stops holding a device
# place. A room genuinely passing one code around has every phone in use, so
# their idle times are seconds; a session left behind by a phone that walked
# out grows without bound.
IDLE_SESSION_SECONDS = 10 * 60


def _routers_to_ask(customer):
    """
    The routers worth asking about this subscriber's devices.

    `customer.router` is the assigned one and is normally the only one that can
    have a session for them. It is also nullable, and a tenth of the hotspot
    subscribers on this system have it null — bound by a redemption that never
    set it, or left behind by a router that was deleted.

    That mattered more than it looks. Every caller below read `customer.router`
    alone and treated None as "no answer", and "no answer" is what makes a
    device place unfreeable. A subscriber with no assigned router could
    therefore never release a stale binding, so the first time their phone
    changed address they were refused until their package expired — with the
    message that tells them to disconnect a device that is not connected.

    Falling back to the operator's own routers is the same question asked of
    the hardware that could actually answer it, and it stays inside the tenant.
    """
    from billing.router_service import _tenant_routers

    router = getattr(customer, "router", None)
    if router is not None:
        return [router]

    try:
        return list(_tenant_routers(customer.tenant_id))
    except Exception:
        logger.warning(
            "[hotspot] customer %s has no router and their operator's routers "
            "could not be listed", customer.pk)
        return []


def _online_macs(customer, *, max_idle_seconds=None):
    """
    Every address with a live session on any router that could be reached.

    Returns None when not one router answered, and that distinction is the
    whole point: the callers must be able to tell "nobody is connected" from
    "I could not find out". A partial answer counts as an answer — a router
    that replied has told us the truth about its own sessions, and holding a
    customer's place on the silence of a second router they are not on is the
    failure this is here to end.
    """
    from billing.router_service import active_hotspot_macs

    online = set()
    answered = False
    for router in _routers_to_ask(customer):
        macs = active_hotspot_macs(router, max_idle_seconds=max_idle_seconds)
        if macs is None:
            continue
        answered = True
        online.update(normalize_mac(m) for m in macs)

    return online if answered else None


def _voucher_first_used_here(subscription, mac_address):
    """
    Whether this address is the one a code on this subscription was bought on.

    `Voucher.bound_mac` is stamped on first redemption and never overwritten,
    so it names the buyer's own handset and nothing else can forge it. Read
    across the subscription's vouchers rather than the one presented, because a
    renewal mints a new code against the same subscription and the customer is
    still the same person on the same phone.
    """
    mac = normalize_mac(mac_address)
    if not mac:
        return False

    return Voucher.objects.all_tenants().filter(
        subscription=subscription,
        bound_mac__in=mac_variants(mac),
    ).exists()


def _evict_idle_device(customer, devices, *, reclaiming=False):
    """
    Free the place held by a device that is not actually connected.

    Returns the CustomerDevice released, or None if the limit should stand.

    None is returned in two cases, and they are both deliberate:

    * No router could be asked. "Nobody is online" and "I could not find out"
      must not lead to the same decision — an unreachable router would
      otherwise hand every customer unlimited devices, silently.
    * Every bound device is in use right now. That is the sharing this limit
      was built for, and the refusal is correct.

    "In use" is not "has a session". A hotspot session outlives the phone that
    opened it, so a device that left hours ago still appears on the router and
    used to hold its owner's only place until the package expired. A session
    idle beyond IDLE_SESSION_SECONDS no longer counts.

    `reclaiming` is the one case where a device in use can still lose its
    place: the phone asking is the phone this code was first redeemed on, so
    the devices holding it are that same buyer's later ones. Somebody's second
    handset does not outrank the one that bought the code. Neither of the two
    None cases above applies then — there is no router to consult about a
    question the voucher has already answered, and nothing is given away,
    because the total stays inside what the package allows.

    The oldest binding goes first. Between two idle devices the one last seen
    longest ago is the likelier to be the phone somebody replaced.
    """
    from billing.models import AccessAuditLog
    from billing.router_service import disable_hotspot, safe_connect_router

    if not devices:
        return None

    if reclaiming:
        candidates = devices
    else:
        online = _online_macs(customer, max_idle_seconds=IDLE_SESSION_SECONDS)
        if online is None:
            logger.warning(
                "[hotspot] cannot check live sessions for customer %s, so the "
                "device limit stands — a router we cannot read must not become "
                "a router that grants everything.", customer.pk,
            )
            return None

        candidates = [
            d for d in devices if normalize_mac(d.mac_address) not in online]
        if not candidates:
            return None

    victim = sorted(candidates, key=lambda d: (d.last_seen or d.first_seen))[0]

    # Take it off the router too. Leaving the hotspot user behind would let
    # the evicted device log straight back in and retake a place it no longer
    # holds in the database — and, because a session nobody ends keeps showing
    # up as active, would make it unevictable from then on.
    for router in _routers_to_ask(customer):
        api = safe_connect_router(router)
        if api is None:
            continue
        try:
            disable_hotspot(api, victim.mac_address)
        except Exception:
            logger.warning(
                "[hotspot] evicted %s for customer %s but could not remove it "
                "from %s", victim.mac_address, customer.pk, router,
            )

    freed = victim.mac_address
    victim.delete()

    # This field is what the public status and reconnect endpoints resolve a
    # subscriber by, so it cannot be left pointing at a binding that no longer
    # exists.
    if normalize_mac(customer.hotspot_username) == normalize_mac(freed):
        customer.hotspot_username = ""
        customer.save(update_fields=["hotspot_username"])

    why = (
        "the device the code was bought on came back"
        if reclaiming else
        "no live session, and the device limit was reached by another device "
        "connecting"
    )

    try:
        AccessAuditLog.objects.create(
            customer=customer,
            action="deactivate",
            reason=f"Device {freed} released: {why}",
        )
    except Exception:
        # An operator losing the record of why a device was dropped is worth
        # a log line, not worth refusing a customer who has paid.
        logger.exception(
            "[hotspot] could not record eviction of %s for customer %s",
            freed, customer.pk,
        )

    logger.info(
        "[hotspot] released %s for customer %s — %s", freed, customer.pk, why)
    return victim


def _fold_duplicate_devices(devices):
    """
    Collapse rows that describe the same physical device.

    Addresses are canonical on the way in now, but rows written before that
    are in whatever case and separators the writer used, and a phone bound
    twice occupies two of the places its owner paid for. The oldest row wins,
    because that is the one `first_seen` orders the eviction queue by.

    Read-only: nothing is deleted here. A count that is wrong should stop
    being wrong immediately; tidying the table is the backfill's job, and a
    customer waiting at a portal should not be the one to pay for it.

    A blocked row wins over an unblocked one for the same device, whatever
    their ages. Keeping the older row when the two disagree would mean a
    blocked handset could connect by presenting the spelling that was never
    blocked.
    """
    folded = {}
    for device in devices:
        key = normalize_mac(device.mac_address)
        held = folded.get(key)
        if held is None or (device.blocked and not held.blocked):
            folded[key] = device
    return list(folded.values())


def _device_holders(customer, mac_address):
    """
    Everyone else on this operator with a claim on this address.

    Both places a claim can live: `Customer.hotspot_username`, which holds a
    subscriber's first device, and the `CustomerDevice` rows, which hold the
    rest. Checking only the first is how a MAC held as somebody's *second*
    phone stayed invisible all the way down to a unique constraint.
    """
    variants = mac_variants(mac_address)

    holder_ids = set(
        Customer.objects.all_tenants()
        .filter(tenant_id=customer.tenant_id, hotspot_username__in=variants)
        .exclude(pk=customer.pk)
        .values_list("pk", flat=True)
    )
    holder_ids.update(
        CustomerDevice.objects.all_tenants()
        .filter(tenant_id=customer.tenant_id, mac_address__in=variants)
        .exclude(customer_id=customer.pk)
        .values_list("customer_id", flat=True)
    )

    if not holder_ids:
        return []

    # Locked, so two devices cannot both decide the other one has let go.
    # Scoped to this subscriber's operator: this endpoint is public, no
    # middleware has set a tenant context, and an unscoped manager here would
    # let one operator's redemption release another operator's binding and
    # audit-log it against their customer.
    return list(
        Customer.objects.all_tenants()
        .select_for_update()
        .filter(tenant_id=customer.tenant_id, pk__in=holder_ids)
    )


def _release_device_from_others(customer, mac_address):
    """
    Take this address off every other account that holds it.

    Returns a Response to send instead, or None to carry on.

    The rule used to be: if the other account has any live subscription,
    refuse. That reads as protecting a paying customer, and mostly it refused
    one. For another account to hold this exact address, this exact handset
    must have presented a different valid code — which happens when the same
    person buys again from a second M-Pesa number (a borrowed phone, a
    mistyped one), and when a handset genuinely changes hands. Both of those
    people have paid, and the first is the person the complaint came from.

    So the question is the one the device limit already asks: is the device
    *connected* on that account right now. A binding nobody is using is not
    access anybody is losing. Anyone standing on a captive portal is by
    definition not connected, so in practice this releases — which is the
    point.

    Where this deliberately differs from `_evict_idle_device`: an unreachable
    router does not refuse here. There, granting on "I could not find out"
    hands one customer unlimited devices, so silence has to mean no. Here the
    claimant has already presented a paid, valid code of their own and is
    asking for one device — nothing is given away by letting them have it, and
    refusing costs an operator the phone call.
    """
    others = _device_holders(customer, mac_address)
    if not others:
        return None

    now = timezone.now()

    for other in others:
        still_paying = other.subscriptions.filter(
            status="active", expiry_date__gt=now,
        ).exists()

        if still_paying and _mac_is_online(other, mac_address):
            # The only case worth defending: someone is using it as we speak.
            return Response(
                {"detail": "This device is connected on another account right "
                           "now. Disconnect it and try again."},
                status=status.HTTP_409_CONFLICT,
            )

        released = CustomerDevice.objects.all_tenants().filter(
            tenant_id=other.tenant_id,
            customer=other,
            mac_address__in=mac_variants(mac_address),
        ).delete()[0]

        # Clearing this and leaving the device row behind was the whole of the
        # 500: the next statement created a row the constraint already had.
        if normalize_mac(other.hotspot_username) == mac_address:
            other.hotspot_username = ""
            other.save(update_fields=["hotspot_username"])
        elif not released:
            continue

        try:
            AccessAuditLog.objects.create(
                # Explicit: this endpoint is public, so nothing has set a
                # tenant context for the model to infer one from.
                tenant_id=other.tenant_id,
                customer=other,
                action="deactivate",
                reason=(
                    f"Hotspot device {mac_address} released to customer "
                    f"{customer.id} ({customer.full_name}) on voucher validation"
                ),
            )
        except Exception:
            # An operator losing the note of why a device moved is worth a log
            # line. It is not worth refusing the customer holding a paid code.
            logger.exception(
                "[hotspot] could not record %s moving from customer %s to %s",
                mac_address, other.pk, customer.pk,
            )

    return None


def _mac_is_online(customer, mac_address):
    """
    Whether this address has a live session on the customer's router.

    False when the router says no, and false when there is no router to ask or
    it cannot be reached — see `_release_device_from_others` for why silence
    means "not connected" here and means the opposite in `_evict_idle_device`.

    Idle sessions do not count, for the same reason they do not count there:
    the only binding worth defending against somebody holding a paid code is
    one that somebody else is using as we speak.

    Deliberately the customer's own router and not `_routers_to_ask`. Widening
    who is asked frees places in `_evict_idle_device`, where more information
    can only help the person standing at the portal. Here it would do the
    opposite — every extra router is another chance to find a reason to refuse
    them — and this function's whole design is that the claimant, who has
    presented a paid code of their own, wins anything we are unsure about.
    """
    from billing.router_service import active_hotspot_macs

    router = customer.router
    if router is None:
        return False

    online = active_hotspot_macs(
        router, max_idle_seconds=IDLE_SESSION_SECONDS)
    if not online:
        return False

    return mac_address in {normalize_mac(m) for m in online}


def _record_attempt(tenant, code, mac, outcome):
    """
    Note a refusal, without letting the noting of it become a failure.

    A portal that cannot write a diagnostic must still answer the customer, so
    this never raises.
    """
    from billing.models import ConnectionAttempt

    try:
        ConnectionAttempt.objects.create(
            tenant=tenant,
            code_tried=(code or "")[:40],
            mac_address=(mac or "")[:50],
            outcome=outcome,
        )
    except Exception:
        logger.warning("[attempt] could not record a %s for %s", outcome, mac)


class HotspotVoucherValidateView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [HotspotPublicThrottle]

    def post(self, request):
        code = request.data.get("code")
        # Canonical from here down. Every comparison below is a string
        # comparison against something bound earlier, and a phone whose
        # address arrives in a different case than it was bound in is a phone
        # its owner is told belongs to somebody else.
        mac_address = normalize_mac(request.data.get("mac_address"))

        if not code or not mac_address:
            return Response(
                {"detail": "code and mac_address are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Which operator's portal this code was presented on. Without it the
        # lookup searches every operator on the platform, and one operator's
        # voucher grants access through another's portal — the same ambiguity
        # _hotspot_customer_for() already refuses to guess at, on the same
        # single-operator fallback.
        tenant = _public_tenant(request)
        if tenant is None:
            logger.warning(
                "[hotspot] voucher validation without a resolvable operator "
                "while %s exist — refusing rather than searching all of them.",
                Tenant.objects.count(),
            )
            return Response(
                {"detail": "We couldn't identify your internet provider. "
                           "Please reconnect through the WiFi login page."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        subscription = validate_voucher(code, tenant=tenant)
        if not subscription:
            # The commonest refusal, and the one an operator most wants to see:
            # a customer holding something that does not work, giving up, and
            # saying nothing.
            #
            # Two situations were being answered with one sentence. A code that
            # ran out is not a code that does not exist, and "invalid" sends
            # somebody who has simply finished their hour back to retype it —
            # which is what a customer does thirty times before giving up.
            reason = describe_refusal(code, tenant=tenant)
            if reason == REFUSED_EXPIRED:
                _record_attempt(
                    tenant, code, mac_address, ConnectionAttempt.EXPIRED)
                return Response(
                    {"detail": "This code has run out of time. Buy another "
                               "package to get back online.",
                     "expired": True},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            _record_attempt(tenant, code, mac_address, ConnectionAttempt.INVALID)
            return Response(
                {"detail": "Invalid or expired voucher"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        customer = subscription.customer

        # Which devices may use this is decided below, by counting them against
        # the package's allowance. This used to be a single comparison against
        # the one MAC on the customer row: it enforced a limit of exactly one,
        # by overwriting rather than by counting, so a package sold for three
        # devices could not be honoured and the answer to a second phone was a
        # flat 400 with no indication of how many were allowed.

        # 🔐 Prevent this device being claimed while another customer still holds it.
        # The check above only guarded the customer's side, so nothing stopped two
        # customers ending up on the same MAC. Once that happened, the public
        # status/reconnect endpoints resolved the subscriber with .first() and
        # returned an arbitrary one of them.
        with transaction.atomic():
            conflict = _release_device_from_others(customer, mac_address)
            if conflict is not None:
                _record_attempt(
                    tenant, code, mac_address, ConnectionAttempt.DEVICE_LIMIT)
                return conflict

            # How many devices this package is good for, and which ones are
            # already using it.
            allowed = max(1, getattr(subscription.package, "max_devices", 1) or 1)
            devices = list(
                CustomerDevice.objects.all_tenants()
                .select_for_update()
                .filter(tenant_id=customer.tenant_id, customer=customer)
                .order_by("first_seen")
            )

            # A subscriber bound before the device table existed has a MAC on
            # their row and no device row, and the count reads rows — so their
            # voucher would silently be good for one more phone than it was
            # sold for. 0050 backfills these; this heals anything that slips
            # past, because the cost of missing one is free internet.
            legacy = normalize_mac(customer.hotspot_username)
            if legacy and not any(
                normalize_mac(d.mac_address) == legacy for d in devices
            ):
                try:
                    with transaction.atomic():
                        devices.append(
                            CustomerDevice.objects.create(
                                tenant_id=customer.tenant_id,
                                customer=customer,
                                mac_address=legacy,
                            )
                        )
                except IntegrityError:
                    # The address on this customer's row belongs to another
                    # subscriber's device. That makes the row stale, not the
                    # customer a device short — and it must not be a 500 on
                    # the path somebody uses after auto-connect has failed.
                    logger.warning(
                        "[hotspot] customer %s names %s, which is held "
                        "elsewhere — not counting it as theirs",
                        customer.pk, legacy,
                    )

            # One phone is one device however many spellings of its address
            # got written. Two rows for it filled two places on a two-device
            # package, and on a one-device package refused its owner outright:
            # neither row is evictable, because the phone the router reports as
            # online is both of them.
            devices = _fold_duplicate_devices(devices)

            known = next(
                (d for d in devices
                 if normalize_mac(d.mac_address) == mac_address),
                None,
            )

            if known is not None and known.blocked:
                _record_attempt(tenant, code, mac_address, ConnectionAttempt.BLOCKED)
                return Response(
                    {"detail": "This device has been blocked. Please speak to "
                               "your provider.",
                     "blocked": True},
                    status=status.HTTP_403_FORBIDDEN,
                )

            # Blocked devices are refused above and do not hold a place here:
            # blocking one should not cost the customer a slot they paid for.
            devices = [d for d in devices if not d.blocked]

            if known is None:
                # The device this very code was first redeemed on, coming back
                # after its binding went away — evicted while it was off, or
                # never rebound after its address rotated.
                #
                # `Voucher.bound_mac` is the strongest ownership evidence in
                # the system: it was written the first time this code worked,
                # by this address, and no other device can produce it. This
                # phone is the buyer, and it was being made to queue behind
                # whatever else had since taken its place — the complaint the
                # operator reported, in the customer's own words: told the code
                # is in use elsewhere, about the code they bought.
                reclaiming = (
                    len(devices) >= allowed
                    and _voucher_first_used_here(subscription, mac_address)
                )
                if reclaiming:
                    released = _evict_idle_device(
                        customer, devices, reclaiming=True)
                    if released is not None:
                        devices = [d for d in devices if d.pk != released.pk]

                if len(devices) >= allowed:
                    # Full — but full of what?
                    #
                    # The limit exists because one code bought for one phone
                    # was being passed around a room. That is devices online
                    # *at the same time*. Counting every address ever bound
                    # cannot tell it apart from a customer who changed phone,
                    # whose Android rotated its MAC, or who once opened the
                    # portal on a laptop — and those people have paid.
                    #
                    # So ask the router who is actually connected. A device
                    # with no live session is not using the package it is
                    # holding a place in, and its binding is released to the
                    # device standing here. A room genuinely sharing a code
                    # has every device online at once, nothing is evictable,
                    # and the refusal below still happens.
                    evicted = _evict_idle_device(customer, devices)
                    if evicted is None:
                        _record_attempt(
                            tenant, code, mac_address,
                            ConnectionAttempt.DEVICE_LIMIT)
                        return Response(
                            {
                                "detail": (
                                    f"This code is in use on {allowed} "
                                    f"device{'s' if allowed > 1 else ''} right "
                                    "now. Disconnect one and try again."
                                    if allowed > 1 else
                                    "This code is connected on another device "
                                    "right now. Disconnect it and try again."
                                ),
                                "devices_allowed": allowed,
                                "devices_used": len(devices),
                            },
                            status=status.HTTP_409_CONFLICT,
                        )
                    devices = [d for d in devices if d.pk != evicted.pk]

                try:
                    with transaction.atomic():
                        CustomerDevice.objects.create(
                            tenant_id=customer.tenant_id,
                            customer=customer,
                            mac_address=mac_address,
                        )
                except IntegrityError:
                    # (tenant, mac_address) is unique, and everything that
                    # could hold it has just been released above. Reaching
                    # here means something took it between that check and this
                    # write — two phones sharing a cloned address, or a second
                    # request from this same customer racing the first.
                    #
                    # It must not be an IntegrityError escaping the atomic
                    # block. That is a 500, the portal shows a 500 as "that
                    # code did not match", and the customer retries the one
                    # thing that cannot work. Say what happened instead.
                    logger.warning(
                        "[hotspot] %s was taken while customer %s was claiming it",
                        mac_address, customer.pk,
                    )
                    return Response(
                        {"detail": "This device is being registered on another "
                                   "account right now. Wait a moment and try "
                                   "again."},
                        status=status.HTTP_409_CONFLICT,
                    )
            else:
                known.save(update_fields=["last_seen"])

            # The first device stays on the customer row: the public status and
            # reconnect endpoints resolve a subscriber by it, and a great deal
            # depends on that lookup.
            if not customer.hotspot_username:
                customer.hotspot_username = mac_address
            customer.status = "active"
            customer.save(update_fields=["hotspot_username", "status"])

            # When this code was first redeemed, and by what. Inside the
            # transaction that binds the device, so the record cannot claim a
            # use that was then rolled back.
            mark_voucher_used(subscription, mac_address)

        enable_customer_access(customer)

        return Response(
            {
                "detail": "Access granted",
                "expires_at": subscription.expiry_date,
                # Proof, for later. This device has just presented a working
                # code, so it has shown it belongs to the account; the portal
                # keeps this and presents it back when asking for status, which
                # is what distinguishes it from a phone that merely knows its
                # MAC address.
                "device_token": device_token_for(mac_address),
            },
            status=status.HTTP_200_OK,
        )

def _kick_device(customer, mac_address):
    """
    Take a device off the hardware it is on, best effort.

    Blocking a device that stays connected has not blocked anything until its
    session ends, and a session can outlast a shift. An unreachable router must
    not stop the record being made, so this reports rather than raises.
    """
    from billing.router_service import _tenant_routers, connect_router, disable_hotspot

    reached = 0
    try:
        routers = _tenant_routers(customer.tenant_id)
    except Exception:
        routers = []

    for router in routers:
        try:
            api = connect_router(router)
            disable_hotspot(api, mac_address)
            reached += 1
        except Exception:
            logger.warning("[device] could not reach %s to drop %s", router, mac_address)
    return reached


class CustomerDeviceView(APIView):
    """
    One device on a subscriber's account.

    Blocking and removing answer different questions. A lost phone should be
    removed, so the replacement can take its place. A stolen one, or a
    connection being abused, should be blocked — refused even with a valid
    code, and not holding a place the customer paid for.

    Admin only: deciding who may not connect is not a support task.
    """
    permission_classes = [IsTenantAdmin]

    def _device(self, request, device_id):
        return CustomerDevice.objects.filter(id=device_id).select_related("customer").first()

    def post(self, request, device_id):
        device = self._device(request, device_id)
        if device is None:
            return Response({"detail": "Device not found"}, status=404)

        action = request.data.get("action")
        reason = (request.data.get("reason") or "").strip()

        if action == "block":
            if not reason:
                return Response(
                    {"detail": "Say why this device is blocked — it is what "
                               "answers the customer when they ask."},
                    status=400,
                )
            device.blocked = True
            device.blocked_reason = reason[:200]
            device.blocked_at = timezone.now()
            device.save(update_fields=["blocked", "blocked_reason", "blocked_at"])

            # The customer row points at one MAC, and if it is this one the
            # public lookups would still resolve through it.
            if (normalize_mac(device.customer.hotspot_username)
                    == normalize_mac(device.mac_address)):
                device.customer.hotspot_username = ""
                device.customer.save(update_fields=["hotspot_username"])

            reached = _kick_device(device.customer, device.mac_address)
            AccessAuditLog.objects.create(
                tenant_id=device.tenant_id, customer=device.customer,
                action="device_blocked",
                reason=f"{device.mac_address} — {reason}",
            )
            return Response({
                "detail": f"{device.mac_address} is blocked.",
                "routers_reached": reached,
                "blocked": True,
            })

        if action == "unblock":
            device.blocked = False
            device.blocked_reason = ""
            device.blocked_at = None
            device.save(update_fields=["blocked", "blocked_reason", "blocked_at"])
            AccessAuditLog.objects.create(
                tenant_id=device.tenant_id, customer=device.customer,
                action="device_unblocked", reason=device.mac_address,
            )
            return Response({"detail": f"{device.mac_address} may connect again.",
                             "blocked": False})

        return Response({"detail": "Unknown action."}, status=400)

    def delete(self, request, device_id):
        """Free the place. The device may connect again and claim a new one."""
        device = self._device(request, device_id)
        if device is None:
            return Response({"detail": "Device not found"}, status=404)

        mac = device.mac_address
        customer = device.customer
        # Normalised, like the block above it: a device removed while the
        # customer row still names it in another spelling leaves the public
        # status and reconnect lookups resolving through a place that is
        # supposed to be free.
        if normalize_mac(customer.hotspot_username) == normalize_mac(mac):
            customer.hotspot_username = ""
            customer.save(update_fields=["hotspot_username"])

        reached = _kick_device(customer, mac)
        device.delete()
        AccessAuditLog.objects.create(
            tenant_id=customer.tenant_id, customer=customer,
            action="device_removed", reason=mac,
        )
        return Response({"detail": f"{mac} removed. Its place is free.",
                         "routers_reached": reached})


class DeactivateVoucherView(APIView):
    """
    Stop one code working, without touching anything else.

    The existing revoke expires the whole subscription — right when somebody
    has stopped paying, wrong when a single code has leaked and the customer
    is owed a replacement. This retires the code and leaves the subscription,
    the other codes and the customer's standing alone.
    """
    permission_classes = [IsTenantAdmin]

    def post(self, request, code):
        # Case-insensitive, like redemption. Codes are minted from an uppercase
        # alphabet so two cannot differ by case alone, and an operator killing
        # a leaked code typed it or read it off a phone: "Voucher not found"
        # over letter case leaves a code working that somebody meant to stop.
        voucher = (
            Voucher.objects.filter(code__iexact=code)
            .select_related("subscription__customer")
            .first()
        )
        if voucher is None:
            return Response({"detail": "Voucher not found"}, status=404)

        reason = (request.data.get("reason") or "").strip()
        if not reason:
            return Response(
                {"detail": "Say why — a code that stops working needs an "
                           "explanation when the customer rings."},
                status=400,
            )

        if not voucher.is_active:
            return Response({"detail": "That code is already retired.",
                             "is_active": False})

        voucher.is_active = False
        voucher.save(update_fields=["is_active"])

        customer = voucher.subscription.customer
        AccessAuditLog.objects.create(
            tenant_id=voucher.tenant_id,
            customer=customer,
            subscription=voucher.subscription,
            action="voucher_deactivated",
            reason=f"{voucher.code} — {reason}",
        )
        return Response({"detail": f"{voucher.code} will not work again.",
                         "is_active": False})


class IssueVoucherView(APIView):
    """
    Sell or give a voucher to a phone number, in one step.

    The counter version of the captive portal. Somebody is standing there, or
    on the phone: pick a package, take their number, say how it was paid, hand
    over the code. No STK prompt, no waiting for a callback — the operator has
    already taken the money, or is choosing not to.

    Reuses the subscriber if that number has bought before, so a regular does
    not accumulate a new record every time they top up.

    Admin only, and a reason is required when nothing is being charged: giving
    away what the business sells is a decision about money, and in three months
    the question will be why.
    """
    permission_classes = [IsTenantAdmin]

    def post(self, request):
        tenant = getattr(request.user, "tenant", None)
        if tenant is None:
            return Response({"detail": "Operator account required."}, status=403)

        phone = _normalise_msisdn(request.data.get("phone"))
        if not phone:
            return Response(
                {"detail": "Enter a valid phone number, e.g. 0712345678."}, status=400)

        package = (
            Package.objects.all_tenants()
            .filter(id=request.data.get("package_id"), tenant=tenant,
                    is_hotspot=True, is_archived=False)
            .first()
        )
        if package is None:
            return Response({"detail": "Choose a hotspot package."}, status=400)

        paid_with = request.data.get("paid_with")
        if paid_with not in ("cash", "mpesa", "bank", "comp"):
            return Response(
                {"detail": "Say how this was paid for."}, status=400)

        reason = (request.data.get("reason") or "").strip()
        if paid_with == "comp" and not reason:
            return Response(
                {"detail": "Say why this is free — it is the answer to a question "
                           "somebody will ask later."},
                status=400,
            )

        try:
            with tenant_context(tenant), transaction.atomic():
                customer = (
                    Customer.objects.all_tenants()
                    .filter(tenant=tenant, phone=phone)
                    .first()
                )
                created_customer = customer is None
                if created_customer:
                    customer = Customer.objects.create(
                        tenant=tenant,
                        full_name=(request.data.get("full_name") or "").strip()
                                  or f"Hotspot {phone[-4:]}",
                        phone=phone,
                        connection_type="hotspot",
                    )
                elif customer.status != "active":
                    # A returning customer was left marked expired by the last
                    # sweep, and buying again never cleared it. The row then
                    # said "expired" while holding a subscription with hours
                    # left — an operator looking at their customer list would
                    # see somebody cut off who had just paid, and no amount of
                    # reconnecting would change it.
                    customer.status = "active"
                    customer.save(update_fields=["status"])

                subscription = Subscription.objects.create(
                    tenant=tenant, customer=customer, package=package,
                )
                Payment.objects.create(
                    tenant=tenant,
                    customer=customer,
                    subscription=subscription,
                    # Zero when given away: revenue is untouched and the row
                    # still exists, so free internet stays countable.
                    amount=Decimal("0.00") if paid_with == "comp" else package.price,
                    method=paid_with,
                    reference=(reason or request.data.get("payment_reference") or "")[:100],
                )
        except Exception as exc:
            logger.exception("[issue] could not issue for %s", phone)
            return Response({"detail": f"Could not issue this: {exc}"}, status=500)

        if paid_with == "comp":
            record_admin_action(
                request.user,
                AdminActionLog.COMP_VOUCHER,
                target_tenant=tenant,
                label=customer.full_name,
                detail=f"{package.name} at no charge — {reason}",
            )

        voucher = (
            Voucher.objects.all_tenants()
            .filter(tenant=tenant, subscription=subscription)
            .order_by("-created_at")
            .first()
        )
        return Response({
            "detail": f"{package.name} issued to {phone}.",
            "voucher_code": voucher.code if voucher else None,
            "expires_at": subscription.expiry_date,
            "customer_id": customer.id,
            "customer_name": customer.full_name,
            "new_customer": created_customer,
            "free": paid_with == "comp",
        }, status=201)


class CompAccessView(APIView):
    """
    Give a customer access without charging for it.

    The case this exists for: somebody paid and did not get online, or was let
    down twice, and is standing there wanting the thing they already paid for.
    Until now the only ways to help were to record a payment that never
    happened — putting money in the books that nobody received — or to do
    nothing.

    Admin only. Staff may read the console all day, but giving away what the
    business sells is a decision about money, and it belongs to whoever answers
    for the money.

    A reason is required. This is the operator writing off a sale, and in three
    months the question will be why.

    Works for both connection types because Payment.save() already does: a
    hotspot customer gets a voucher, a PPPoE line gets its access restored.
    """
    permission_classes = [IsTenantAdmin]

    def post(self, request, customer_id):
        customer = Customer.objects.filter(id=customer_id).first()
        if customer is None:
            return Response({"detail": "Customer not found"}, status=404)

        reason = (request.data.get("reason") or "").strip()
        if not reason:
            return Response(
                {"detail": "Say why this is free — it is the answer to a question "
                           "somebody will ask later."},
                status=400,
            )

        package = (
            Package.objects.filter(id=request.data.get("package_id")).first()
            if request.data.get("package_id") else None
        )
        if package is None:
            return Response({"detail": "Choose a package."}, status=400)

        try:
            with transaction.atomic():
                subscription = Subscription.objects.create(
                    tenant_id=customer.tenant_id,
                    customer=customer,
                    package=package,
                )
                # Zero, and marked as given. Revenue is untouched; the row
                # exists so the giveaway can be counted and questioned.
                Payment.objects.create(
                    tenant_id=customer.tenant_id,
                    customer=customer,
                    subscription=subscription,
                    amount=Decimal("0.00"),
                    method="comp",
                    reference=reason[:100],
                )
        except Exception as exc:
            logger.exception("[comp] could not comp %s", customer)
            return Response({"detail": f"Could not issue this: {exc}"}, status=500)

        record_admin_action(
            request.user,
            AdminActionLog.COMP_VOUCHER,
            target_tenant=customer.tenant,
            label=customer.full_name,
            detail=f"{package.name} at no charge — {reason}",
        )

        voucher = (
            Voucher.objects.all_tenants()
            .filter(tenant_id=customer.tenant_id, subscription=subscription)
            .order_by("-created_at")
            .first()
        )
        return Response({
            "detail": f"{package.name} given to {customer.full_name} at no charge.",
            "voucher_code": voucher.code if voucher else None,
            "expires_at": subscription.expiry_date,
            "connection_type": customer.connection_type,
        }, status=201)


class CustomerSuspendResumeView(APIView):
    permission_classes = [IsTenantMember]

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
    permission_classes = [IsTenantMember]

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

        # 2️⃣ Build message — signed by the operator, not the platform
        brand = customer.tenant.business_name or customer.tenant.name
        message = (
            "Your WiFi access code is:\n\n"
            f"{voucher.code}\n\n"
            f"Valid until {voucher.expires_at:%Y-%m-%d %H:%M}.\n"
            f"Thank you for choosing {brand}."
        )

        # 3️⃣ Send asynchronously, through this operator's messaging account
        notify_customer_task.delay(
            customer.phone, message, tenant_id=customer.tenant_id
        )

        # 4️⃣ Respond immediately (non-blocking)
        return Response(
            {"detail": "Voucher resend scheduled"},
            status=status.HTTP_202_ACCEPTED,
        )

    
def _subscriber_usage(customer, subscription):
    """
    What a subscriber has used, in the shape their own screen needs.

    The operator's console has had this since the data panel landed; the person
    who paid for the bundle could not see it anywhere. A cap of zero is
    unlimited, and an unlimited plan still reports consumption — "how much have
    I used" is a fair question with or without a ceiling.
    """
    from django.db.models import Sum

    from .models import HotspotUsageRecord, PPPoEUsageRecord

    cap_gb = customer.custom_data_cap_gb
    if cap_gb is None and subscription and subscription.package_id:
        cap_gb = subscription.package.monthly_data_cap_gb
    cap_gb = cap_gb or 0

    # Through the shared reader, so this and the cap check can never disagree.
    # They were two separate sums of the same thing, and the way that drift
    # shows up is somebody being cut off while their own screen says they have
    # data left.
    from .services.usage import usage_since

    used = usage_since(customer, getattr(subscription, "start_date", None))
    cap_bytes = cap_gb * 1024 ** 3 if cap_gb else 0

    return {
        "used_bytes": used,
        "cap_gb": cap_gb,
        "unlimited": cap_gb == 0,
        "percent_used": (
            round(min(used / cap_bytes * 100, 999), 1) if cap_bytes else None
        ),
    }


def _hotspot_customer_for(request, mac, **extra):
    """
    Resolve a hotspot subscriber from a device MAC on a public endpoint.

    These endpoints carry no JWT, so the operator comes from the `t` parameter
    the captive portal appends — each operator deploys their own login page and
    already configures its API base, so carrying their token costs nothing.

    MAC uniqueness is per operator. Without the token the lookup is ambiguous
    across operators, which is how one operator's subscriber status leaked to
    another and access was granted against the wrong subscription.

    Falls back to an unscoped lookup only when the platform has a single
    operator, so existing portals keep working until they are updated.
    """
    # Query string for GET (status), request body for POST (reconnect).
    token = request.GET.get("t")
    if not token:
        data = getattr(request, "data", None)
        if isinstance(data, dict):
            token = data.get("t")

    # Any of the subscriber's devices, not only the first one bound. A second
    # phone on a multi-device package is as much theirs as the first, and
    # resolving only the first would tell it that it is not registered.
    # A blocked device resolves to nobody. Leaving it resolvable would let it
    # keep reading status and calling reconnect on the account it was blocked
    # from.
    # Every spelling the address might be stored in. A device bound in one
    # case and asking in another resolved to nobody, which the portal shows as
    # "not registered" to a subscriber who is.
    variants = mac_variants(mac)

    qs = Customer.objects.all_tenants().filter(
        Q(hotspot_username__in=variants)
        | Q(devices__mac_address__in=variants, devices__blocked=False),
        **extra
    ).distinct()

    if token:
        tenant = Tenant.objects.filter(public_token=token).first()
        if tenant is None:
            return None
        return qs.filter(tenant=tenant).first()

    if Tenant.objects.count() > 1:
        logger.warning(
            "[hotspot] MAC lookup without a tenant token while %s operators "
            "exist — refusing rather than guessing.", Tenant.objects.count(),
        )
        return None

    return qs.first()


class HotspotProviderView(APIView):
    """
    Who this portal belongs to.

    The connected, status and logout pages are static files on a router, so
    they cannot know an operator's name without asking. They used to carry it
    hardcoded — one operator's business, in a template every operator
    deploys — and after that was removed they carried nothing, which left a
    subscriber looking at the platform's branding instead of the provider they
    actually pay.

    Its own endpoint because those pages need the name and nothing else;
    fetching the package list for it would be a catalogue to render one line.
    """
    permission_classes = [permissions.AllowAny]
    throttle_classes = [HotspotPollThrottle]

    def get(self, request):
        tenant = _public_tenant(request)
        if tenant is None:
            return Response({"detail": "Unknown provider."}, status=404)

        return Response({
            "provider": tenant.business_name or tenant.name,
            "support_phones": [
                n for n in (tenant.support_phone, tenant.support_phone_2) if n
            ],
            "terms_url": get_setting("HOTSPOT_TERMS_URL", default="", tenant=tenant) or None,
        })


class HotspotStatusView(APIView):
    permission_classes = [permissions.AllowAny]
    # Polled by the portal on every load, so it shares the poll budget rather
    # than the much tighter one that bounds guessing.
    throttle_classes = [HotspotPollThrottle]

    def get(self, request):
        mac = request.GET.get("mac")

        if not mac:
            return Response(
                {"detail": "MAC address is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # A MAC is only unique within one operator, so the captive portal
        # supplies ?t=<tenant public_token>. Without it, the same device
        # registered with two operators would resolve arbitrarily.
        customer = _hotspot_customer_for(request, mac)
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
        #
        # The code comes back only to a caller holding the token issued when
        # this device redeemed it or was let back on. It used to come back to
        # anyone who named the MAC, and this endpoint takes the MAC from the
        # query string — over plain http nothing can check that the caller is
        # that device, and everyone else's MAC on a shared hotspot is a
        # scanner app away. "It is already bound to this MAC" only holds if
        # the asker is this MAC, and nothing made that true.
        #
        # The package, expiry and usage stay open: a device that knows a MAC
        # learns what plan it is on, which is worth less than the credential
        # and is what a portal recovering from a reload needs to show.
        body = {
            "status": "active",
            "expires_at": subscription.expiry_date,
            "package": getattr(subscription.package, "name", None),
            # The connected page already makes this call, so the name it
            # should be showing costs nothing extra here.
            "provider": customer.tenant.business_name or customer.tenant.name,
            # What they have used, and what they are allowed. The operator
            # could see this and the person paying for it could not.
            "usage": _subscriber_usage(customer, subscription),
        }

        if device_token_matches(mac, request.GET.get("dt")):
            voucher = (
                Voucher.objects.all_tenants()
                .filter(tenant_id=customer.tenant_id, subscription=subscription)
                .order_by("-created_at")
                .first()
            )
            body["voucher_code"] = voucher.code if voucher else None

        return Response(body)
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
            # The operator's console has shown this since the data panel
            # landed; the person paying for the bundle could not see it
            # anywhere.
            "usage": _subscriber_usage(customer, subscription),
        })


class PPPoEPackagesView(APIView):
    """
    What a subscriber may renew onto.

    The renew page fetched /api/packages/, which is operator staff only, so
    every subscriber got a 403 and — because nothing caught it — an empty list
    with a disabled button and no error. A PPPoE customer could not renew at
    all.

    Its own endpoint rather than widening that one: the admin serializer is
    fields = "__all__", so pointing subscribers at it would hand them every
    column the model ever grows. This returns the same explicit, public field
    list the hotspot portal uses, scoped to the operator the subscriber
    belongs to.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        customer = getattr(request.user, "customer_profile", None)
        if customer is None:
            return Response({"detail": "Customer profile not found"}, status=404)

        from .serializers import PublicPackageSerializer

        packages = (
            Package.objects.all_tenants()
            .filter(tenant_id=customer.tenant_id, is_hotspot=False, is_archived=False)
            .order_by("price")
        )
        return Response({"results": PublicPackageSerializer(packages, many=True).data})


class PPPoERenewalStatusView(APIView):
    """
    Whether a renewal has been paid.

    Scoped to the caller's own invoices, so unlike the hotspot equivalent it
    needs no token — a subscriber is authenticated, and one subscriber cannot
    address another's invoice.
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [HotspotPollThrottle]

    def get(self, request):
        customer = getattr(request.user, "customer_profile", None)
        reference = request.GET.get("ref")
        if customer is None or not reference:
            return Response({"status": "not_found"})

        invoice = (
            Invoice.objects.all_tenants()
            .select_related("subscription")
            .filter(tenant_id=customer.tenant_id,
                    customer_id=customer.id,
                    invoice_number=reference)
            .first()
        )
        if invoice is None:
            return Response({"status": "not_found"})

        if invoice.payment_status != "paid":
            return Response({"status": invoice.payment_status})

        return Response({
            "status": "paid",
            "expires_at": invoice.subscription.expiry_date,
            "package": getattr(invoice.subscription.package, "name", None),
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

        # Scoped to this subscriber's operator, and PPPoE only. Neither was
        # checked: a package id from the hotspot catalogue renewed a PPPoE
        # line onto an hour of hotspot access, at hotspot pricing.
        package = (
            Package.objects.all_tenants()
            .filter(id=package_id, tenant_id=customer.tenant_id,
                    is_hotspot=False, is_archived=False)
            .first()
        )
        if package is None:
            return Response({"detail": "That package is not available."}, status=404)

        # === 1️⃣ CREATE RENEWAL SUBSCRIPTION ===
        subscription = Subscription.objects.create(
            customer=customer,
            package=package,
            start_date=timezone.now(),
            status="active"
        )

        # === 2️⃣ GET THE AUTOMATICALLY CREATED INVOICE ===
        invoice = subscription.invoice

        # Deliberately NOT marked pending here. The task does that itself,
        # inside select_for_update, because the same flag is its duplicate
        # guard — an invoice already "pending" is one it refuses to push
        # again. Setting it first therefore meant every renewal was blocked by
        # a guard against itself and no prompt was ever sent.

        # === 3️⃣ TRIGGER DARAJA STK PUSH ===
        #
        # Queued, not called here. This was a synchronous request to Safaricom
        # inside the customer's own request: a gunicorn worker held for however
        # long Daraja took, on a page someone is watching, and a slow response
        # became a timeout with the subscription already created. Every other
        # payment path in this codebase already goes through the task, which
        # also retries.
        #
        # Configuration is still checked up front, because "cannot take
        # payments" is an answer the customer should get now rather than after
        # waiting for a prompt that will never arrive.
        from billing.mpesa_client import missing_mpesa_keys

        missing = missing_mpesa_keys(tenant=customer.tenant)
        if missing:
            subscription.delete()  # cascade-deletes the invoice, no ghost record
            logger.warning("[pppoe] %s cannot take payments: missing %s",
                           customer.tenant, missing)
            return Response(
                {"detail": "This provider cannot accept payments yet."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        initiate_stk_push_task.delay(invoice.id, phone)
        
        return Response({
            "detail": "Check your phone for the M-Pesa prompt.",
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
            "usage": _subscriber_usage(customer, subscription),
        })
        
class SystemSettingsView(APIView):
    permission_classes = [IsTenantAdmin]

    SENSITIVE_KEYS = {
        "MPESA_CONSUMER_KEY",
        "MPESA_CONSUMER_SECRET",
        "MPESA_PASSKEY",
        "BLESSEDTEXTS_API_KEY",
        "WHATSAPP_TOKEN",
    }

    ALL_KEYS = [
        "MPESA_ENV",
        "MPESA_CONSUMER_KEY",
        "MPESA_CONSUMER_SECRET",
        "MPESA_SHORTCODE",
        "MPESA_SHORTCODE_TYPE",
        "MPESA_STORE_NUMBER",
        "MPESA_PASSKEY",
        "MPESA_CALLBACK_URL",
        "BLESSEDTEXTS_API_KEY",
        "BLESSEDTEXTS_SENDER_ID",
        "WHATSAPP_TOKEN",
        "WHATSAPP_PHONE_ID",
        "HOTSPOT_TERMS_URL",
        "SMS_TEMPLATE_VOUCHER",
        "SMS_TEMPLATE_PPPOE",
        "SMS_TEMPLATE_WELCOME_HOTSPOT",
        "SMS_TEMPLATE_WELCOME_PPPOE",
    ]

    def get(self, request):
        tenant = getattr(request.user, "tenant", None)

        data = {}
        for key in self.ALL_KEYS:
            value = get_setting(key, default="", tenant=tenant)

            if key in self.SENSITIVE_KEYS and value not in ("", None):
                data[key] = "********"
            else:
                data[key] = value or ""

        # Read-only operator identity. The token is what the MikroTik captive
        # portal must carry, and the callback URL is what gets registered with
        # Safaricom — both are needed to finish setup, so surface them here
        # rather than making an operator ask for them.
        if tenant is not None:
            from billing.mpesa_client import (
                callback_url_for, missing_mpesa_keys, shortcode_config,
            )

            data["TENANT_TOKEN"] = tenant.public_token
            data["BUSINESS_NAME"] = tenant.business_name or tenant.name
            # Editable here, and nowhere else an operator can reach: these
            # lived on the tenant and only the platform owner could set them,
            # so an operator could not publish their own support number.
            data["SUPPORT_PHONE"] = tenant.support_phone
            data["SUPPORT_PHONE_2"] = tenant.support_phone_2
            data["MPESA_MISSING"] = missing_mpesa_keys(tenant=tenant)
            try:
                data["MPESA_CALLBACK_URL_EFFECTIVE"] = callback_url_for(tenant=tenant)
            except Exception as exc:
                data["MPESA_CALLBACK_URL_EFFECTIVE"] = ""
                data["MPESA_CALLBACK_HINT"] = str(exc)

            # What the two numbers actually become in a push, resolved by the
            # same function that builds one. Sent rather than worked out again
            # in the frontend, for the reason SMS_TEMPLATES is: the page and
            # the code that talks to Safaricom must not be able to disagree.
            #
            # It is the *roles* that need showing. A Buy Goods till has two
            # numbers, only one of which signs the password, and swapping them
            # produces a push Safaricom rejects with no prompt on the phone and
            # nothing in the operator's dashboard. Seen in production: two
            # operators issued the same pair in opposite order, one working and
            # one silently dead. See shortcode_config.
            cfg = shortcode_config(tenant=tenant)
            data["MPESA_RESOLVED"] = {
                "business_shortcode": cfg["business_shortcode"],
                "party_b": cfg["party_b"],
                "transaction_type": cfg["transaction_type"],
            }

        # What the page needs to offer an editor rather than a bare textarea:
        # the wording used when the operator has set none, and what each
        # message is able to refer to. Sent rather than duplicated in the
        # frontend, so the two cannot disagree about what {support} means.
        from billing import message_templates as templates

        data["SMS_TEMPLATES"] = {
            key: {
                "label": templates.LABELS[key],
                "default": default,
                "placeholders": sorted(templates.PLACEHOLDERS[key]),
                "required": sorted(templates.REQUIRED[key]),
                # So the page counts a message rather than a template. See
                # SAMPLE — {expiry} is eight characters and sends as twenty.
                "sample": {
                    name: templates.SAMPLE[name]
                    for name in templates.PLACEHOLDERS[key]
                },
            }
            for key, default in templates.DEFAULTS.items()
        }

        return Response(data)

    def put(self, request):
        serializer = SystemSettingSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        tenant = getattr(request.user, "tenant", None)

        # Support numbers live on the tenant, not in SystemSetting, so they are
        # applied here rather than in the key/value loop below.
        support_fields = {
            f: serializer.validated_data.pop(f)
            for f in ("SUPPORT_PHONE", "SUPPORT_PHONE_2")
            if f in serializer.validated_data
        }
        if support_fields and tenant is not None:
            if "SUPPORT_PHONE" in support_fields:
                tenant.support_phone = support_fields["SUPPORT_PHONE"].strip()
            if "SUPPORT_PHONE_2" in support_fields:
                tenant.support_phone_2 = support_fields["SUPPORT_PHONE_2"].strip()
            tenant.save(update_fields=["support_phone", "support_phone_2"])

        for key, value in serializer.validated_data.items():
            if value == "********":
                continue  # keep old secret

            # Scoped explicitly: these are the operator's own M-Pesa and
            # messaging credentials, and writing them unscoped would overwrite
            # another operator's.
            SystemSetting.objects.update_or_create(
                tenant=tenant,
                key=key,
                defaults={"value": value},
            )

        # Invalidate Redis cache across all workers so new values apply immediately
        from .config import clear_settings_cache
        clear_settings_cache(tenant=tenant)

        return Response({"detail": "Settings updated successfully"})

class TestMpesaView(APIView):
    """Verifies the credentials of the operator making the request."""
    permission_classes = [IsTenantAdmin]

    def get(self, request):
        tenant = getattr(request.user, "tenant", None)
        try:
            token = get_mpesa_access_token(tenant=tenant)
            return Response({"success": True, "token": token})
        except Exception as e:
            return Response({"success": False, "error": str(e)}, status=400)


class _TestMessageView(APIView):
    """
    Sends a test message through the requesting operator's own account.

    The recipient is supplied by the caller — the old placeholder
    "2547XXXXXXXX" is not a real number, so the test always reported success
    while the send silently failed.
    """
    permission_classes = [IsTenantAdmin]
    task = None
    label = ""

    def get(self, request):
        tenant = getattr(request.user, "tenant", None)
        phone = request.query_params.get("phone") or getattr(
            tenant, "contact_phone", ""
        )
        if not phone:
            return Response(
                {
                    "success": False,
                    "error": "No recipient. Pass ?phone=2547XXXXXXXX, or set a "
                             "contact phone on your business profile.",
                },
                status=400,
            )

        self.task.delay(phone, f"{self.label} test OK", tenant_id=getattr(tenant, "pk", None))
        return Response({"success": True, "sent_to": phone})


class TestSmsView(APIView):
    """
    Send a real test SMS and report what actually happened.

    Not queued. The other channels queue and answer "success" for having
    dispatched a task, which is all they can honestly claim — but send_sms now
    returns whether the provider accepted the message, and a test that reports
    delivery it has not confirmed is worse than no test. This one waits.

    It also returns the remaining credit, because the most common reason an
    operator's messages stop arriving is an empty account, and nothing else in
    the product would show that.
    """

    permission_classes = [IsTenantAdmin]

    def get(self, request):
        from billing.notifications import send_sms, sms_balance

        tenant = getattr(request.user, "tenant", None)
        phone = request.query_params.get("phone") or getattr(tenant, "contact_phone", "")
        if not phone:
            return Response(
                {
                    "success": False,
                    "error": "No recipient. Pass ?phone=2547XXXXXXXX, or set a "
                             "contact phone on your business profile.",
                },
                status=400,
            )

        credit = sms_balance(tenant=tenant)
        sent = send_sms(phone, "SMS test OK", tenant=tenant)

        if not sent:
            return Response({
                "success": False,
                "sent_to": phone,
                "balance": credit.get("balance"),
                "error": credit.get("error")
                or "The SMS provider refused the message. Check the log for the reason.",
            }, status=400)

        return Response({
            "success": True,
            "sent_to": phone,
            "balance": credit.get("balance"),
        })


class SmsBalanceView(APIView):
    """How much SMS credit is left. Staff may read it; it is a support question."""

    permission_classes = [IsTenantMember]

    def get(self, request):
        from billing.notifications import sms_balance

        return Response(sms_balance(tenant=getattr(request.user, "tenant", None)))


class TestWhatsappView(_TestMessageView):
    task = send_whatsapp_task
    label = "WhatsApp"
    
class AdminBroadcastView(APIView):
    permission_classes = [IsTenantAdmin]

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
        # The worker has no request, so the operator must travel with the task —
        # otherwise a broadcast would fan out across every operator's customers.
        task = dispatch_broadcast_task.delay(
            audience, channel, message, customer_ids,
            tenant_id=getattr(request.user, "tenant_id", None),
        )

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
    """
    Who is connected right now, asked of the hardware itself.

    Cached for a few seconds per operator. The page polls this every ten
    seconds and the answer costs a conversation with every router the operator
    owns — so two people watching it doubled the load on their network
    equipment, a third tripled it, and a tab left open on a wall display
    carried on all night. The cache is shorter than the poll interval, so a
    single viewer still sees fresh data; what it removes is the same question
    being asked of the same routers several times at once.
    """

    permission_classes = [IsTenantMember]

    # Under the 10s the dashboard polls at, so one viewer is never served
    # something staler than they would have got anyway.
    CACHE_SECONDS = 8

    def get(self, request):
        from django.core.cache import cache

        tenant_id = getattr(request.user, "tenant_id", None)
        cache_key = f"pppoe-sessions:{tenant_id}"
        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)

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

        cache.set(cache_key, data, self.CACHE_SECONDS)
        return Response(data)
    
class AdminDisconnectPPPoEView(APIView):
    permission_classes = [IsTenantAdmin]

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
    permission_classes = [IsTenantMember]

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
    permission_classes = [IsTenantMember]

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
    """
    An operator's routers, and where they register new ones.

    Registering hardware used to be the platform owner's job in the Django
    admin, because that was the only place the API password was actually
    written — see RouterSerializer. An operator who bought a second router had
    to ask someone else to type it in for them.
    """

    permission_classes = [IsTenantAdminOrReadOnlyMember]

    def get(self, request):
        # Use cached is_online from the background health task — do NOT make
        # live socket probes here. Probing N routers synchronously during an
        # HTTP request blocks a Gunicorn worker for N × timeout seconds.
        from .serializers import RouterSerializer

        routers = RouterDevice.objects.all().select_related("station").order_by("priority")
        return Response(RouterSerializer(routers, many=True).data)

    def post(self, request):
        tenant = getattr(request.user, "tenant", None)
        if tenant is not None:
            blocked = tenant.plan_limit_exceeded("routers")
            if blocked:
                return Response({"detail": blocked}, status=status.HTTP_402_PAYMENT_REQUIRED)

        from .serializers import RouterSerializer
        serializer = RouterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        router = serializer.save()
        return Response(RouterSerializer(router).data, status=status.HTTP_201_CREATED)


class AdminRouterProvisionView(APIView):
    """
    Register a router and hand back the commands that finish the job.

    What this replaces: an admin reading the server's WireGuard key out of a
    root-owned file over SSH, generating a keypair on the router, copying its
    public half back, running wg-add-peer.sh, and only then filling in the form
    here. Five context switches between a browser, a terminal and WinBox, for
    every site an operator opens.

    Now the platform picks the address, makes the keys, asks the host to add
    the peer, and returns one block to paste into WinBox. The operator's whole
    job is: fill this in, paste that, press Test connection.

    The generated script carries the router's private key, so this returns it
    once and stores none of it. It travels over the same TLS the API password
    and the tenant token already use. If it is lost, provision again — a
    replaced peer costs one paste, and nothing else references it.
    """

    permission_classes = [IsTenantAdmin]

    def post(self, request):
        from billing.services import tunnel
        from .serializers import RouterSerializer

        tenant = getattr(request.user, "tenant", None)
        if tenant is not None:
            blocked = tenant.plan_limit_exceeded("routers")
            if blocked:
                return Response({"detail": blocked},
                                status=status.HTTP_402_PAYMENT_REQUIRED)

        name = (request.data.get("name") or "").strip()
        username = (request.data.get("username") or "").strip()
        password = request.data.get("password") or ""
        if not name or not username or not password:
            return Response(
                {"detail": "A name, an API username and an API password are needed."},
                status=400,
            )

        try:
            api_port = int(request.data.get("api_port") or 8728)
        except (TypeError, ValueError):
            return Response({"detail": "api_port must be a number."}, status=400)

        payload = {
            "name": name,
            "username": username,
            "password": password,
            "api_port": api_port,
            "priority": request.data.get("priority", 1),
            "max_pppoe_sessions": request.data.get("max_pppoe_sessions", 0),
            "is_active": request.data.get("is_active", True),
        }
        if request.data.get("station"):
            payload["station"] = request.data["station"]

        try:
            # Allocation and creation share one transaction, so the advisory
            # lock taken while picking an address is still held when the row
            # claiming it is written. Split across two, two admins
            # provisioning at the same moment are both handed 10.10.0.7 — and
            # the serializer will not catch it, because it only enforces
            # uniqueness across operators for *public* addresses. Private ones
            # genuinely repeat: every operator has a 192.168.88.1. Tunnel
            # addresses are the exception, and this is what keeps them unique.
            with transaction.atomic():
                tunnel_ip = tunnel.allocate_tunnel_ip()
                private_key, public_key = tunnel.generate_keypair()
                # Built before anything is saved: a platform with no tunnel
                # configured should return an explanation, not a stored router
                # whose address leads nowhere.
                script = tunnel.build_router_script(
                    tunnel_ip=tunnel_ip,
                    private_key=private_key,
                    api_username=username,
                    api_password=password,
                    api_port=api_port,
                )
                serializer = RouterSerializer(data={**payload, "ip_address": tunnel_ip})
                serializer.is_valid(raise_exception=True)
                router = serializer.save()
        except tunnel.TunnelNotConfigured as exc:
            # A misconfigured platform, not a misfilled form. Say which,
            # because the operator can do nothing about the former and will
            # otherwise spend the afternoon re-typing the latter.
            return Response({"detail": str(exc)}, status=503)
        except tunnel.TunnelAddressExhausted as exc:
            return Response({"detail": str(exc)}, status=507)

        # Queued last. A peer for a router that failed validation would sit in
        # the server's wg0.conf forever with nothing referencing it.
        try:
            request_id = tunnel.queue_peer(name, public_key, tunnel_ip)
        except Exception as exc:
            logger.exception("[tunnel] could not queue peer for %s", name)
            router.delete()
            return Response(
                {"detail": f"Could not reach the tunnel service: {exc}"}, status=503
            )

        return Response(
            {
                "router": RouterSerializer(router).data,
                "tunnel_ip": tunnel_ip,
                "request_id": request_id,
                "script": script,
            },
            status=status.HTTP_201_CREATED,
        )


class AdminRouterTestView(APIView):
    """
    Dial a router and say whether these credentials work.

    The point of a form an operator fills in themselves is that they find out
    now. Without this, a mistyped password is discovered by a subscriber who
    cannot get online, hours later, with nothing in the interface suggesting
    where to look — the router shows as offline exactly as it would if the
    power were out.

    Takes either an unsaved set of details, so it can be tried before anything
    is written, or `router_id` to re-test one already stored. Only an admin may
    call it: it makes the platform open a connection to an address the caller
    chose, and it distinguishes a closed port from a refused login, which is
    more than an operator's staff account needs.
    """

    permission_classes = [IsTenantAdmin]
    throttle_classes = [RouterTestThrottle]

    def post(self, request):
        from billing.router_service import probe_credentials

        router = None
        router_id = request.data.get("router_id")
        if router_id:
            # Scoped manager: an id from another operator is simply not found.
            router = RouterDevice.objects.filter(pk=router_id).first()
            if router is None:
                return Response({"detail": "Router not found"}, status=404)

        host = request.data.get("ip_address") or getattr(router, "ip_address", "")
        username = request.data.get("username") or getattr(router, "username", "")
        # A saved router's password is not sent back to the browser, so an
        # operator re-testing an existing one has nothing to type. Falling back
        # to the stored value is what makes the button work at all.
        password = request.data.get("password") or getattr(router, "password", "")
        port = request.data.get("api_port") or getattr(router, "api_port", 8728)

        if not host or not username:
            return Response(
                {"detail": "An address and a username are needed to test a router."},
                status=400,
            )

        try:
            port = int(port)
        except (TypeError, ValueError):
            return Response({"detail": "api_port must be a number."}, status=400)

        result = probe_credentials(host, username, password, port=port)

        # A test against a saved router is as good a health observation as the
        # sweep's, so record it rather than letting the interface show offline
        # next to a test that just succeeded.
        #
        # Only when what was dialled is what is stored. An operator moving a
        # router to a new address tests the new one first, from the edit form,
        # with the old row still saved — and a failure there says nothing about
        # the box currently serving their subscribers. Recording it would mark
        # a working router offline and hand every subscriber on it to failover.
        describes_saved_router = (
            router is not None
            and str(host) == router.ip_address
            and str(username) == router.username
            and port == router.api_port
        )
        if describes_saved_router:
            if result["authenticated"]:
                router.record_health(True)
                router.record_identity(
                    identity=result["identity"], serial=result["serial"])
            else:
                cause = (RouterEvent.CAUSE_AUTH_FAILED if result["reachable"]
                         else RouterEvent.CAUSE_UNREACHABLE)
                router.record_health(False, error=result["error"], cause=cause)
            result["identity"] = router.identity
            result["serial"] = router.serial_number

        return Response({
            "ok": result["authenticated"],
            "reachable": result["reachable"],
            "authenticated": result["authenticated"],
            "identity": result["identity"],
            "serial_number": result["serial"],
            "detail": result["error"] or "Connected. These credentials work.",
        })


class AdminRouterDetailView(APIView):
    permission_classes = [IsTenantAdminOrReadOnlyMember]

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

    # Same handler: the body has always been treated as partial, so the two
    # verbs already meant the same thing here. Naming it stops a client that
    # sends the conventional one from getting a 405.
    patch = put

    def delete(self, request, pk):
        """
        Remove a router, unless subscribers are still on it.

        Customer.router is SET_NULL, so deleting a router in use does not fail
        — it quietly detaches everyone attached to it. They keep their
        subscription and their credentials and stop being provisioned anywhere,
        which looks like nothing happened until they next need reconnecting.

        Harmless while only a platform owner could delete one. Now that the
        button is in the operator's own interface, next to hardware they are
        replacing, it needs to say no.
        """
        router = self._get(pk)
        if not router:
            return Response({"detail": "Not found"}, status=404)

        attached = Customer.objects.filter(router=router).count()
        if attached:
            return Response(
                {"detail": f"{attached} subscriber{'s are' if attached > 1 else ' is'} "
                           f"still on this router. Move them to another router first, "
                           f"or deactivate this one to take it out of service without "
                           f"deleting it."},
                status=status.HTTP_409_CONFLICT,
            )

        router.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
from billing.router_service import safe_connect_router, provision_customer_on_router,migrate_customer_router  


class AdminMigrateCustomerView(APIView):
    permission_classes = [IsTenantAdmin]

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
    permission_classes = [IsTenantMember]

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
    permission_classes = [IsTenantMember]

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

class ConnectionAttemptsView(APIView):
    """
    Who could not get on, and why.

    An operator hears about the customer who complains. This is the rest of
    them: the mistyped codes, the codes passed to a second phone, the blocked
    device that keeps trying.
    """
    permission_classes = [IsTenantMember]

    def get(self, request):
        qs = ConnectionAttempt.objects.all()

        outcome = request.GET.get("outcome")
        if outcome in dict(ConnectionAttempt.OUTCOMES):
            qs = qs.filter(outcome=outcome)

        days = request.GET.get("days")
        if days:
            try:
                qs = qs.filter(
                    created_at__gte=timezone.now() - timezone.timedelta(days=int(days)))
            except (TypeError, ValueError):
                pass

        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response([
            {
                "id": a.id,
                "code_tried": a.code_tried,
                "mac_address": a.mac_address,
                "outcome": a.outcome,
                "outcome_label": a.get_outcome_display(),
                "created_at": a.created_at,
            }
            for a in page
        ])


class AdminAccessLookupView(APIView):
    permission_classes = [IsTenantMember]

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
        # Case-insensitive: the operator running this search is reading the
        # code off a customer's phone or hearing it down a line, and matching
        # exactly only ever hid a code that exists.
        voucher = (
            Voucher.objects
            .select_related("subscription__customer", "subscription__package")
            .filter(code__iexact=query)
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
        # 3️⃣ Phone number, PPPoE username, or device MAC
        # --------------------------------------------------
        # The username and the MAC were missing. This page exists for someone
        # standing at a counter reading out whatever they have, and for a
        # PPPoE subscriber — most subscribers — that is their username. The
        # page could not find the commonest kind of access it is named after.
        customer = (
            Customer.objects.filter(phone=query).first()
            or Customer.objects.filter(pppoe_username__iexact=query).first()
            or Customer.objects.filter(hotspot_username__iexact=query).first()
        )

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

            matched = "phone"
            if customer.pppoe_username and customer.pppoe_username.lower() == query.lower():
                matched = "pppoe_username"
            elif customer.hotspot_username and customer.hotspot_username.lower() == query.lower():
                matched = "device"

            return Response({
                "type": matched,
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
# AdminDeactivateVoucherView was removed: it was never routed in urls.py and is
# superseded by AdminDeactivateAccessView below, which expires the subscription,
# deactivates every voucher on it, marks the customer expired AND writes an
# AccessAuditLog. The removed view accepted a `reason` and discarded it, so
# wiring it up would have created a revoke path with no audit trail.


class AdminDeactivateAccessView(APIView):
    permission_classes = [IsTenantAdmin]

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
class OperatorAnalyticsView(APIView):
    """
    One request, everything the analytics page shows.

    Deliberately one endpoint rather than eight. The page shows a single period
    across every panel, and eight calls would let them disagree — a pulse from
    one moment beside a chart from another is worse than a slower page.

    Reading is the day job, so staff may see it.
    """

    permission_classes = [IsTenantMember]

    MAX_DAYS = 366

    def get(self, request):
        now = timezone.now()

        # Explicit range wins; otherwise a day count, defaulting to a month.
        raw_from = request.query_params.get("from")
        raw_to = request.query_params.get("to")
        if raw_from and raw_to:
            start = parse_datetime(f"{raw_from}T00:00:00") or parse_datetime(raw_from)
            end = parse_datetime(f"{raw_to}T23:59:59") or parse_datetime(raw_to)
            if start is None or end is None:
                return Response({"detail": "Dates must look like 2026-07-01."}, status=400)
            start = timezone.make_aware(start) if timezone.is_naive(start) else start
            end = timezone.make_aware(end) if timezone.is_naive(end) else end
            if end <= start:
                return Response({"detail": "The end date must be after the start."}, status=400)
            if (end - start).days > self.MAX_DAYS:
                return Response(
                    {"detail": f"Ranges are limited to {self.MAX_DAYS} days."}, status=400)
        else:
            days = max(1, min(int(request.query_params.get("days", 30) or 30), 90))
            start = now - timezone.timedelta(days=days - 1)
            start = timezone.localtime(start).replace(
                hour=0, minute=0, second=0, microsecond=0)
            end = now

        station = request.query_params.get("station") or None
        if station:
            # Scoped, so a station id belonging to another operator resolves to
            # nothing rather than leaking their figures.
            if not Station.objects.filter(id=station).exists():
                return Response({"detail": "Unknown station."}, status=404)

        total_revenue = float(
            _analytics_total(start, end, station)
        )
        active_customers = Customer.objects.filter(status="active")
        if station:
            active_customers = active_customers.filter(router__station_id=station)
        active_count = active_customers.count()

        series = revenue_series(start, end, station)
        transactions = sum(p["transactions"] for p in series)

        return Response({
            "range": {
                "from": timezone.localtime(start).date().isoformat(),
                "to": timezone.localtime(end).date().isoformat(),
                "days": len(series),
            },
            "station": int(station) if station else None,
            "pulse": performance_pulse(station),
            "totals": {
                "revenue": total_revenue,
                "transactions": transactions,
                "active_customers": active_count,
                # Revenue per paying customer. A total says how big the month
                # was; this says whether each customer is worth more or less
                # than they were, which is the number that moves a decision.
                "arpu": round(total_revenue / active_count, 2) if active_count else 0.0,
            },
            "series": series,
            "by_package": analytics_by_package(start, end, station),
            "by_method": analytics_by_method(start, end, station),
            "peak_hours": peak_hours(start, end, station),
            "expiring": expiring_soon(station),
            "flow": customer_flow(start, end, station),
            "by_station": by_station(start, end),
        })


def _analytics_total(start, end, station=None):
    qs = Payment.objects.filter(paid_at__gte=start, paid_at__lt=end)
    if station:
        qs = qs.filter(customer__router__station_id=station)
    return qs.aggregate(t=Sum("amount"))["t"] or 0


class DailyRevenueView(APIView):
    permission_classes = [IsTenantMember]

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

        customer = _hotspot_customer_for(request, mac, status="active")

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

        # Deliberately no device token here, though it would be convenient.
        # This endpoint takes the MAC from the request body and cannot check
        # it either, so handing out proof would let anyone who named a
        # stranger's MAC collect a token and walk back through the gate on
        # /hotspot/status/ with it. Redeeming a code is the only thing on the
        # public surface that demonstrates anything, so it is the only thing
        # that issues one.
        return Response({
            "status": "allowed",
            "expires_at": subscription.expiry_date,
        })

# =====================================================
# PUBLIC HOTSPOT PURCHASE FLOW
# =====================================================
# A walk-up customer on the captive portal has no account and no JWT. Every
# step below was previously behind IsAdmin or IsAuthenticated, so the entire
# self-service purchase path returned 403 and nobody could buy anything.
#
# The operator is identified by the `t` token the portal carries. On a
# single-operator install the token may be omitted, so existing portals keep
# working until they are updated.

def _public_tenant(request):
    """Resolve the operator for an unauthenticated portal request."""
    token = request.GET.get("t")
    if not token:
        data = getattr(request, "data", None)
        if isinstance(data, dict):
            token = data.get("t")

    if token:
        return Tenant.objects.filter(public_token=token).first()

    # Single-operator fallback, matching _hotspot_customer_for().
    if Tenant.objects.count() == 1:
        return Tenant.objects.first()
    return None


def _normalise_msisdn(phone):
    """
    Kenyan mobile number in the 2547XXXXXXXX form Daraja expects.
    Returns None if it does not look like one.
    """
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    elif digits.startswith("7") and len(digits) == 9:
        digits = "254" + digits
    elif digits.startswith("254") and len(digits) == 12:
        pass
    else:
        return None
    return digits


class HotspotPackagesView(APIView):
    """Packages a walk-up customer can buy from this operator."""
    permission_classes = [permissions.AllowAny]
    throttle_classes = [HotspotPublicThrottle]

    def get(self, request):
        tenant = _public_tenant(request)
        if tenant is None:
            return Response(
                {"detail": "Unknown provider. Reconnect through the WiFi login page."},
                status=status.HTTP_404_NOT_FOUND,
            )

        packages = (
            Package.objects.all_tenants()
            .filter(tenant=tenant, is_hotspot=True, is_archived=False)
            .order_by("price")
        )
        from .serializers import PublicPackageSerializer

        # No banner, and deliberately nothing image-shaped at all. A portal
        # visitor's only working route is the walled garden, so the page is
        # kept to markup — the fastest image is the one nobody asks for.
        # Whichever are set, in order. A list rather than two keys so the
        # portal renders what it is given instead of deciding which of two
        # fields is worth showing.
        support = [n for n in (tenant.support_phone, tenant.support_phone_2) if n]

        return Response({
            "provider": tenant.business_name or tenant.name,
            "support_phone": tenant.support_phone or "",   # kept for older portals
            "support_phones": support,
            "terms_url": get_setting("HOTSPOT_TERMS_URL", default="", tenant=tenant) or None,
            "results": PublicPackageSerializer(packages, many=True).data,
        })


class HotspotPurchaseView(APIView):
    """
    Buy a hotspot package without an account.

    Creates (or reuses) the customer by phone number within this operator,
    creates the subscription and its invoice, then triggers STK push. The
    device MAC is deliberately NOT bound here: binding happens on voucher
    validation, after payment, so an unpaid request cannot squat a MAC that
    belongs to somebody else.
    """
    permission_classes = [permissions.AllowAny]
    throttle_classes = [HotspotPublicThrottle]

    def post(self, request):
        tenant = _public_tenant(request)
        if tenant is None:
            return Response(
                {"detail": "Unknown provider. Reconnect through the WiFi login page."},
                status=status.HTTP_404_NOT_FOUND,
            )

        phone = _normalise_msisdn(request.data.get("phone"))
        if not phone:
            return Response(
                {"detail": "Enter a valid M-Pesa number, e.g. 0712345678."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        package = (
            Package.objects.all_tenants()
            .filter(id=request.data.get("package_id"), tenant=tenant,
                    is_hotspot=True, is_archived=False)
            .first()
        )
        if package is None:
            # Scoped lookup: a package id from another operator must not resolve.
            return Response(
                {"detail": "That package is not available."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Restriction is deliberately NOT checked here. It locks the operator
        # out of their own dashboard and nothing else: their subscribers keep
        # their internet, renewals keep working, money keeps reaching their
        # till, and walk-up customers can still buy.
        #
        # This used to refuse new business as well, on the argument that an
        # operator who only loses a dashboard can ignore an unpaid invoice
        # indefinitely. That was reversed deliberately: turning away a member of
        # the public standing at a hotspot is a cost paid by someone who is not
        # party to the dispute, to apply pressure to someone who is.
        #
        # The plan limit below still applies, and so does the payment
        # configuration check — those are about whether a purchase can succeed
        # at all, not about the operator's standing.

        # Only blocks a genuinely new subscriber — a returning customer with
        # this phone number already counts against the cap and can still buy.
        if not Customer.objects.all_tenants().filter(
            tenant=tenant, phone=_normalise_msisdn(request.data.get("phone"))
        ).exists():
            blocked = tenant.plan_limit_exceeded("customers")
            if blocked:
                logger.warning("[hotspot] %s at plan limit, purchase refused", tenant)
                return Response(
                    {"detail": "This provider is not accepting new customers right now."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

        missing = missing_mpesa_keys(tenant=tenant)
        if missing:
            return Response(
                {"detail": "This provider cannot accept payments yet."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        with tenant_context(tenant):
            customer = (
                Customer.objects.all_tenants()
                .filter(tenant=tenant, phone=phone)
                .first()
            )
            if customer is None:
                customer = Customer.objects.create(
                    tenant=tenant,
                    full_name=f"Hotspot {phone[-4:]}",
                    phone=phone,
                    connection_type="hotspot",
                )

            subscription = Subscription.objects.create(
                tenant=tenant, customer=customer, package=package,
            )
            invoice = subscription.invoice

        initiate_stk_push_task.delay(invoice.id, phone)

        return Response(
            {
                "detail": "Check your phone for the M-Pesa prompt.",
                "reference": invoice.invoice_number,
                "amount": str(invoice.total_amount),
                # Proof this poll belongs to whoever started the purchase. The
                # voucher is only released to a caller holding it.
                "poll_token": poll_token_for(invoice.invoice_number),
            },
            status=status.HTTP_202_ACCEPTED,
        )


class HotspotPaymentStatusView(APIView):
    """
    Poll a purchase.

    Answering with the voucher needs more than the invoice number. These look
    like INV-20260801191649-1338 — a second-resolution timestamp and four hex
    characters — so a five-minute window is around twenty million
    combinations. Not guessable by hand, but not secret either, and the only
    thing between a stranger and somebody else's voucher was the rate limit. A
    rate limit is a cost, not a boundary.

    So the code is released only to a caller holding the token `purchase`
    handed back. Without it the endpoint still answers whether the invoice is
    paid, which is what a portal recovering from a reload needs and gives away
    nothing that grants access.
    """
    permission_classes = [permissions.AllowAny]
    throttle_classes = [HotspotPollThrottle]

    def get(self, request):
        tenant = _public_tenant(request)
        reference = request.GET.get("ref")
        if tenant is None or not reference:
            return Response({"status": "not_found"})

        invoice = (
            Invoice.objects.all_tenants()
            .select_related("subscription")
            .filter(tenant=tenant, invoice_number=reference)
            .first()
        )
        if invoice is None:
            return Response({"status": "not_found"})

        if invoice.payment_status != "paid":
            return Response({"status": invoice.payment_status})

        body = {
            "status": "paid",
            "expires_at": invoice.subscription.expiry_date,
        }

        if poll_token_matches(reference, request.GET.get("token")):
            voucher = (
                Voucher.objects.all_tenants()
                .filter(subscription=invoice.subscription, is_active=True)
                .order_by("-created_at")
                .first()
            )
            body["voucher_code"] = voucher.code if voucher else None

        return Response(body)


# =====================================================
# PLATFORM BILLING API
# =====================================================
# Charges operators on behalf of the platform. Kept apart from everything
# above, which charges subscribers on behalf of an operator.
#
# Scoping comes free: these models use TenantManager, so an operator sees only
# their own bills while platform staff (NULL tenant, unscoped) see every one.

class PlatformPlanViewSet(viewsets.ModelViewSet):
    """The plan catalogue. Only the platform sells these."""
    serializer_class = PlatformPlanSerializer

    def get_permissions(self):
        # Operators need to read the catalogue to choose or compare a plan,
        # but only the platform may change what is on offer.
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsPlatformStaff()]

    def get_queryset(self):
        qs = PlatformPlan.objects.order_by("price")
        user = self.request.user
        if not getattr(user, "is_platform_staff", False):
            qs = qs.filter(is_active=True)
        return qs


class TenantInvoiceListView(APIView):
    """
    Platform invoices.

    An operator sees their own; platform staff see everyone's, which is what
    makes this the collections view for the platform owner.

    Reachable while restricted — an operator locked out of the page showing
    what they owe has no route back.
    """
    permission_classes = [IsTenantAdminForBilling]

    def get(self, request):
        qs = TenantInvoice.objects.select_related("tenant", "subscription")

        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        if request.query_params.get("overdue") == "true":
            qs = qs.filter(status="unpaid", due_date__lt=timezone.now())

        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(
            TenantInvoiceSerializer(page, many=True).data
        )


class MyPlatformSubscriptionView(APIView):
    """
    What this operator is on, and what they currently owe.

    Reachable while restricted, for the same reason as the invoice list.
    """
    permission_classes = [IsTenantAdminForBilling]

    def get(self, request):
        tenant = getattr(request.user, "tenant", None)
        if tenant is None:
            return Response(
                {"detail": "Platform accounts are not billed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        subscription = (
            TenantSubscription.objects.select_related("plan")
            .filter(tenant=tenant)
            .first()
        )
        outstanding = TenantInvoice.objects.filter(tenant=tenant, status="unpaid")

        return Response({
            "operator": tenant.business_name or tenant.name,
            "account_status": tenant.status,
            "subscription": (
                TenantSubscriptionSerializer(subscription).data
                if subscription else None
            ),
            "outstanding": TenantInvoiceSerializer(outstanding, many=True).data,
            "amount_due": sum((i.amount for i in outstanding), Decimal("0.00")),
        })


class RecordTenantPaymentView(APIView):
    """
    Record an operator's payment to the platform.

    Platform staff only: this is the platform's own ledger, so an operator
    must never be able to mark their own bill as settled.
    """
    permission_classes = [IsPlatformStaff]

    def post(self, request):
        invoice = (
            TenantInvoice.objects.all_tenants()
            .select_related("tenant", "subscription")
            .filter(number=request.data.get("number"))
            .first()
        )
        if invoice is None:
            return Response({"detail": "Invoice not found"}, status=404)

        if invoice.status == "paid":
            return Response({"detail": "Invoice already paid"}, status=400)

        amount = request.data.get("amount")
        if amount is None or Decimal(str(amount)) != invoice.amount:
            return Response(
                {"detail": f"Amount must equal {invoice.amount}"}, status=400
            )

        method = request.data.get("method", "manual")
        if method not in dict(TenantPayment.METHODS):
            return Response({"detail": "Unknown payment method"}, status=400)

        # TenantPayment.save() settles the invoice, rolls the billing period
        # forward and lifts any restriction.
        payment = TenantPayment.objects.create(
            tenant=invoice.tenant,
            invoice=invoice,
            amount=amount,
            method=method,
            reference=request.data.get("reference", ""),
        )

        return Response(
            {
                "detail": "Payment recorded",
                "invoice": invoice.number,
                "operator": str(invoice.tenant),
                "payment_id": payment.id,
            },
            status=status.HTTP_201_CREATED,
        )


class TenantStatusView(APIView):
    """
    Change an operator's standing by hand.

    Platform staff only, and every change is recorded with who, when and why —
    restriction is a commercial action against a business, so "you cut us off
    without warning" needs an answer with dates on it.

    Note what restriction does NOT do: subscribers keep their internet,
    renewals keep working, and every background task keeps running. Cutting
    subscribers off is not offered here at all. It would punish people who paid
    the operator in good faith and are not party to the dispute, and if it is
    ever genuinely needed it should be a deliberate, separate operation.
    """
    permission_classes = [IsPlatformStaff]

    ALLOWED = {"active", "past_due", "restricted", "cancelled"}

    def get(self, request, tenant_id):
        tenant = Tenant.objects.filter(id=tenant_id).first()
        if tenant is None:
            return Response({"detail": "Operator not found"}, status=404)

        history = tenant.status_changes.select_related("changed_by")[:50]
        return Response({
            "operator": tenant.business_name or tenant.name,
            "status": tenant.status,
            "is_restricted": tenant.is_restricted,
            "history": [
                {
                    "from": h.from_status,
                    "to": h.to_status,
                    "reason": h.reason,
                    "by": h.changed_by.username if h.changed_by else None,
                    "automatic": h.automatic,
                    "at": h.created_at,
                }
                for h in history
            ],
        })

    def post(self, request, tenant_id):
        tenant = Tenant.objects.filter(id=tenant_id).first()
        if tenant is None:
            return Response({"detail": "Operator not found"}, status=404)

        new_status = request.data.get("status")
        if new_status not in self.ALLOWED:
            return Response(
                {"detail": f"status must be one of {sorted(self.ALLOWED)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reason = (request.data.get("reason") or "").strip()
        if new_status in ("restricted", "cancelled") and not reason:
            # Restricting without a stated reason is what makes a dispute
            # unanswerable months later.
            return Response(
                {"detail": "A reason is required when restricting or cancelling."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from billing.models import set_tenant_status
        changed = set_tenant_status(
            tenant, new_status, reason=reason, changed_by=request.user, automatic=False,
        )

        return Response({
            "detail": "Status updated" if changed else "Status unchanged",
            "operator": tenant.business_name or tenant.name,
            "status": tenant.status,
        })


# =====================================================
# MASTER DASHBOARD
# =====================================================
# Cross-operator views for the platform owner. Every query here is deliberately
# unscoped via .all_tenants(), which is why they are platform-staff only.
#
# Written as bulk aggregates rather than a loop over operators: at 50 operators
# a per-operator query per statistic is 200+ round trips for one page.

class PlatformOverviewView(APIView):
    """Headline numbers across the whole platform."""
    permission_classes = [IsPlatformStaff]

    def get(self, request):
        from django.db.models import Count, Sum

        now = timezone.now()

        operators_by_status = dict(
            Tenant.objects.values_list("status")
            .annotate(n=Count("id"))
            .values_list("status", "n")
        )

        # Monthly recurring revenue: what active plans are worth per period.
        mrr = (
            TenantSubscription.objects.all_tenants()
            .filter(status__in=("active", "trialing"))
            .aggregate(total=Sum("plan__price"))["total"] or Decimal("0.00")
        )

        outstanding = TenantInvoice.objects.all_tenants().filter(status="unpaid")

        return Response({
            "operators": {
                "total": Tenant.objects.count(),
                "by_status": operators_by_status,
                "restricted": Tenant.objects.filter(
                    status__in=("restricted", "cancelled")).count(),
            },
            "platform_revenue": {
                "mrr": mrr,
                "outstanding_total": outstanding.aggregate(
                    t=Sum("amount"))["t"] or Decimal("0.00"),
                "outstanding_count": outstanding.count(),
                "overdue_count": outstanding.filter(due_date__lt=now).count(),
            },
            "network": {
                "subscribers": Customer.objects.all_tenants().count(),
                "active_subscribers": Customer.objects.all_tenants().filter(
                    status="active").count(),
                "routers": RouterDevice.objects.all_tenants().count(),
                "routers_offline": RouterDevice.objects.all_tenants().filter(
                    is_active=True, is_online=False).count(),
            },
        })


class PlatformOperatorListView(APIView):
    """
    Every operator with the numbers that matter, in a fixed number of queries.

    Counts are gathered as bulk aggregates keyed by tenant and joined in
    Python, rather than queried per operator.

    POST onboards a new operator. Reading is open to all platform staff;
    creating one is not — see the permission split on post().
    """
    permission_classes = [IsPlatformStaff]

    def get_permissions(self):
        # Onboarding mints a tenant and a working login for it, and there is no
        # delete endpoint to undo either. That is an owner-level action, so
        # platform_staff can look but not create.
        if self.request.method == "POST":
            return [IsPlatformOwner()]
        return super().get_permissions()

    def post(self, request):
        serializer = OperatorCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        plan = None
        if data.get("plan"):
            plan = PlatformPlan.objects.filter(slug=data["plan"], is_active=True).first()

        # One transaction: an operator that exists without its admin account is
        # the half-finished state this endpoint is meant to prevent.
        with transaction.atomic():
            tenant = Tenant.objects.create(
                name=data["name"],
                slug=data["slug"],
                status="trial",
                business_name=data.get("business_name") or data["name"],
                support_phone=data.get("support_phone", ""),
                pppoe_prefix=data.get("pppoe_prefix") or "NET",
                contact_email=data.get("contact_email", ""),
                contact_phone=data.get("contact_phone", ""),
            )

            User.objects.create_user(
                username=data["admin_username"],
                password=data["admin_password"],
                email=data.get("admin_email", ""),
                role=User.TENANT_ADMIN,
                tenant=tenant,
            )

            # Payment credentials, when the owner had them to hand. Same
            # transaction as the tenant and its admin: an operator recorded as
            # configured while the credentials failed to save would be a worse
            # state than one plainly not set up yet.
            mpesa = {
                "MPESA_ENV": data.get("mpesa_env"),
                "MPESA_CONSUMER_KEY": data.get("mpesa_consumer_key"),
                "MPESA_CONSUMER_SECRET": data.get("mpesa_consumer_secret"),
                "MPESA_SHORTCODE": data.get("mpesa_shortcode"),
                # Asked at creation, because it cannot be inferred from the
                # number and getting it wrong is not visible until a customer
                # tries to pay and no prompt arrives.
                "MPESA_SHORTCODE_TYPE": data.get("mpesa_shortcode_type"),
                "MPESA_STORE_NUMBER": data.get("mpesa_store_number"),
                "MPESA_PASSKEY": data.get("mpesa_passkey"),
            }
            supplied = {k: v for k, v in mpesa.items() if v}
            for key, value in supplied.items():
                SystemSetting.objects.update_or_create(
                    tenant=tenant, key=key, defaults={"value": value})
            if supplied:
                record_admin_action(
                    request.user, AdminActionLog.CONFIGURE_PAYMENTS,
                    target_tenant=tenant, detail=", ".join(sorted(supplied)),
                )

            if plan is not None:
                now = timezone.now()
                TenantSubscription.objects.create(
                    tenant=tenant,
                    plan=plan,
                    status="trialing",
                    current_period_start=now,
                    current_period_end=now + timezone.timedelta(days=plan.billing_period_days),
                )

        return Response(
            {
                "id": tenant.id,
                "name": tenant.business_name or tenant.name,
                "slug": tenant.slug,
                "status": tenant.status,
                "is_restricted": tenant.is_restricted,
                "plan": plan.name if plan else None,
                "subscribers": 0,
                "active_subscribers": 0,
                "routers": 0,
                "routers_offline": 0,
                "amount_owed": Decimal("0.00"),
                "created_at": tenant.created_at,
                "admin_username": data["admin_username"],
                # So the caller knows whether onboarding is actually finished.
                "payments_missing": missing_mpesa_keys(tenant=tenant),
            },
            status=status.HTTP_201_CREATED,
        )

    def get(self, request):
        from django.db.models import Count, Sum

        tenants = Tenant.objects.all()
        status_filter = request.query_params.get("status")
        if status_filter:
            tenants = tenants.filter(status=status_filter)
        tenants = list(tenants)
        ids = [t.id for t in tenants]

        def tally(model, **filters):
            return dict(
                model.objects.all_tenants()
                .filter(tenant_id__in=ids, **filters)
                .values("tenant_id")
                .annotate(n=Count("id"))
                .values_list("tenant_id", "n")
            )

        subscribers = tally(Customer)
        active_subs = tally(Customer, status="active")
        routers = tally(RouterDevice)
        offline = tally(RouterDevice, is_active=True, is_online=False)

        owed = dict(
            TenantInvoice.objects.all_tenants()
            .filter(tenant_id__in=ids, status="unpaid")
            .values("tenant_id")
            .annotate(t=Sum("amount"))
            .values_list("tenant_id", "t")
        )

        plans = dict(
            TenantSubscription.objects.all_tenants()
            .filter(tenant_id__in=ids)
            .select_related("plan")
            .values_list("tenant_id", "plan__name")
        )

        return Response([
            {
                "id": t.id,
                "name": t.business_name or t.name,
                "slug": t.slug,
                "status": t.status,
                "is_restricted": t.is_restricted,
                "plan": plans.get(t.id),
                "subscribers": subscribers.get(t.id, 0),
                "active_subscribers": active_subs.get(t.id, 0),
                "routers": routers.get(t.id, 0),
                "routers_offline": offline.get(t.id, 0),
                "amount_owed": owed.get(t.id) or Decimal("0.00"),
                "created_at": t.created_at,
            }
            for t in tenants
        ])


class StationViewSet(viewsets.ModelViewSet):
    """
    An operator's sites.

    Scoped to the caller's operator by the default manager, and the tenant is
    taken from context on write rather than from the request body — the same
    two-sided scoping as the user endpoint, for the same reason.
    """

    serializer_class = StationSerializer
    permission_classes = [IsTenantAdmin]

    def get_queryset(self):
        return Station.objects.all().order_by("name")

    def perform_destroy(self, instance):
        """
        Refuse to delete a site that still has hardware in it.

        SET_NULL on the router would quietly ungroup every box at the site
        instead, and the first anyone would notice is failover no longer being
        held to a location.
        """
        count = instance.routers.count()
        if count:
            raise ValidationError(
                f"{instance.name} still has {count} router(s). Move them to "
                "another station first."
            )
        instance.delete()


class RouterEventsView(APIView):
    """
    One router's history of going down and coming back, with availability.

    The existing health view shows current state and a single last_error that
    each new failure overwrites. This is what that field could never answer:
    whether a router is flapping, and how long it was actually down.
    """

    permission_classes = [IsTenantMember]

    def get(self, request):
        days = max(1, min(int(request.query_params.get("days", 7) or 7), 90))
        since = timezone.now() - timezone.timedelta(days=days)

        routers = list(
            RouterDevice.objects.filter(is_active=True)
            .select_related("station").order_by("station__name", "priority")
        )
        router_id = request.query_params.get("router")
        if router_id:
            routers = [r for r in routers if str(r.id) == str(router_id)]

        # Bulk-loaded and grouped in Python rather than a query per router.
        events = list(
            RouterEvent.objects.filter(
                router__in=routers, created_at__gte=since
            ).select_related("router").order_by("-created_at")[:500]
        )
        by_router = {}
        for e in events:
            by_router.setdefault(e.router_id, []).append(e)

        # Uptime needs every transition in the window, not the 500 most recent
        # kept for display, and it needs the last one before the window to know
        # which state the router started in. Both are loaded here for all the
        # routers at once: router_uptime fetches its own otherwise, which was
        # two queries per router and — since it is called again for the
        # per-router list below — four in total, on a page the dashboard polls
        # every ten seconds. An operator with a dozen boxes was paying fifty
        # queries for a number that comes out of rows already in memory.
        uptime_events = {}
        for e in (
            RouterEvent.objects.filter(router__in=routers, created_at__gte=since)
            .order_by("created_at")
            .only("router_id", "kind", "created_at")
        ):
            uptime_events.setdefault(e.router_id, []).append(e)

        # DISTINCT ON is PostgreSQL's, which is what this runs on — one row per
        # router, the newest before the window.
        prior_events = {
            e.router_id: e
            for e in (
                RouterEvent.objects.filter(router__in=routers, created_at__lt=since)
                .order_by("router_id", "-created_at")
                .distinct("router_id")
                .only("router_id", "kind", "created_at")
            )
        }

        def availability_for(router):
            return router_uptime(
                router, since,
                events=uptime_events.get(router.id, []),
                prior=prior_events.get(router.id),
            )

        # Rolled up per site. A router-by-router list answers "is this box up";
        # an operator with two towns is usually asking "is Kilifi up", which is
        # a different question and was not answerable before.
        stations = {}
        for r in routers:
            key = r.station_id
            bucket = stations.setdefault(key, {
                "id": key,
                "name": r.station.name if r.station_id else None,
                "routers": 0,
                "routers_offline": 0,
                "downtime_seconds": 0,
                "outages": 0,
                "_uptimes": [],
            })
            availability = availability_for(r)
            bucket["routers"] += 1
            bucket["routers_offline"] += 0 if r.is_online else 1
            bucket["downtime_seconds"] += availability["downtime_seconds"]
            bucket["outages"] += availability["outages"]
            bucket["_uptimes"].append(availability["uptime_percent"])

        station_rows = []
        for bucket in stations.values():
            ups = bucket.pop("_uptimes")
            # Mean across the site's routers. Not a claim about the site being
            # reachable — two routers half down is not the same as one fully
            # down — so outages and offline counts are reported beside it.
            bucket["uptime_percent"] = round(sum(ups) / len(ups), 2) if ups else 100.0
            station_rows.append(bucket)
        station_rows.sort(key=lambda b: (b["name"] is None, b["name"] or ""))

        return Response({
            "days": days,
            "stations": station_rows,
            "routers": [
                {
                    "id": r.id,
                    "name": r.name,
                    "station": r.station_id,
                    "station_name": r.station.name if r.station_id else None,
                    "ip_address": r.ip_address,
                    "is_online": r.is_online,
                    "last_seen": r.last_seen,
                    "last_error": r.last_error,
                    "availability": availability_for(r),
                    "events": [
                        {
                            "kind": e.kind,
                            "cause": e.cause,
                            "detail": e.detail,
                            "at": e.created_at,
                        }
                        for e in by_router.get(r.id, [])
                    ],
                }
                for r in routers
            ],
        })


class PlatformAnalyticsView(APIView):
    """
    Time series across every operator.

    Nothing platform-wide had a time dimension before this: reports.py returns
    three scalars, and the only date-bucketed aggregation in the codebase
    (AdminUsageDailyView) is scoped to one operator. So the platform could say
    what its revenue is and never whether it is going up.

    Every series is bucketed in SQL and gap-filled in one pass here, because a
    chart with holes in it reads as a fall to zero rather than as a day with no
    rows. Query count is fixed — four, regardless of how many days or operators
    are in play — which is the property worth protecting as those tables grow.

    `tenant` narrows everything to one operator, which is what the operator
    detail page uses.
    """

    permission_classes = [IsPlatformStaff]

    MAX_DAYS = 365

    def get(self, request):
        days = max(1, min(int(request.query_params.get("days", 30) or 30), self.MAX_DAYS))
        since = timezone.now() - timezone.timedelta(days=days - 1)
        start_date = timezone.localtime(since).date()

        tenant_id = request.query_params.get("tenant")
        tenant = None
        if tenant_id:
            tenant = Tenant.objects.filter(id=tenant_id).first()
            if tenant is None:
                return Response({"detail": "Operator not found"}, status=404)

        def bucket(qs, date_field, value=None):
            """One row per day: SUM(value) or COUNT(*), keyed by local date."""
            qs = qs.annotate(day=TruncDate(date_field)).values("day")
            qs = qs.annotate(v=Sum(value) if value else Count("id"))
            return {r["day"]: r["v"] or 0 for r in qs}

        platform_payments = TenantPayment.objects.all_tenants().filter(paid_at__gte=since)
        subscriber_payments = Payment.objects.all_tenants().filter(paid_at__gte=since)
        new_customers = Customer.objects.all_tenants().filter(created_at__gte=since)
        if tenant is not None:
            platform_payments = platform_payments.filter(tenant=tenant)
            subscriber_payments = subscriber_payments.filter(tenant=tenant)
            new_customers = new_customers.filter(tenant=tenant)

        platform_revenue = bucket(platform_payments, "paid_at", "amount")
        subscriber_revenue = bucket(subscriber_payments, "paid_at", "amount")
        subscribers_added = bucket(new_customers, "created_at")
        operators_added = (
            {} if tenant is not None
            else bucket(Tenant.objects.filter(created_at__gte=since), "created_at")
        )

        # Cumulative operator count needs the population before the window, or
        # the line starts at zero and implies the platform was founded on the
        # first day of whatever range happens to be selected.
        running = (
            0 if tenant is not None
            else Tenant.objects.filter(created_at__lt=since).count()
        )

        series = []
        for offset in range(days):
            day = start_date + timezone.timedelta(days=offset)
            running += operators_added.get(day, 0)
            series.append({
                "day": day.isoformat(),
                "platform_revenue": float(platform_revenue.get(day, 0) or 0),
                "subscriber_revenue": float(subscriber_revenue.get(day, 0) or 0),
                "subscribers_added": subscribers_added.get(day, 0),
                "operators": running,
            })

        # Per-site breakdown, only when looking at one operator — across the
        # whole platform it would be a list of every site of every business,
        # which answers nothing.
        stations = []
        if tenant is not None:
            station_rows = (
                Customer.objects.all_tenants()
                .filter(tenant=tenant, router__station__isnull=False)
                .values("router__station__id", "router__station__name")
                .annotate(subscribers=Count("id"))
                .order_by("-subscribers")
            )
            stations = [
                {
                    "id": r["router__station__id"],
                    "name": r["router__station__name"],
                    "subscribers": r["subscribers"],
                }
                for r in station_rows
            ]

        return Response({
            "days": days,
            "operator": tenant.business_name or tenant.name if tenant else None,
            "stations": stations,
            "series": series,
            "totals": {
                "platform_revenue": sum(p["platform_revenue"] for p in series),
                "subscriber_revenue": sum(p["subscriber_revenue"] for p in series),
                "subscribers_added": sum(p["subscribers_added"] for p in series),
            },
        })


class PlatformHealthView(APIView):
    """
    What is wrong across the whole platform, in one request.

    The overview carried a single failure number — routers offline — so
    everything else needing attention had to be found by visiting each operator
    in turn. Each entry names the operator, because on this side of the product
    "a router is down" is not actionable without knowing whose.
    """

    permission_classes = [IsPlatformStaff]

    def get(self, request):
        tenants = {t.id: t for t in Tenant.objects.all()}

        offline = [
            {
                "operator": tenants[r.tenant_id].business_name or tenants[r.tenant_id].name,
                "operator_id": r.tenant_id,
                "router": r.name,
                "ip_address": r.ip_address,
                "last_seen": r.last_seen,
                "last_error": r.last_error,
            }
            for r in RouterDevice.objects.all_tenants()
            .filter(is_active=True, is_online=False)
            .order_by("tenant_id", "priority")
            if r.tenant_id in tenants
        ]

        # An operator who cannot take money is stuck in a way that looks like
        # nothing being wrong — no errors, just no revenue.
        unconfigured = [
            {"operator": t.business_name or t.name, "operator_id": t.id}
            for t in tenants.values()
            if not t.is_restricted and missing_mpesa_keys(tenant=t)
        ]

        past_due = [
            {
                "operator": t.business_name or t.name,
                "operator_id": t.id,
                "status": t.status,
            }
            for t in tenants.values()
            if t.status in ("past_due", "restricted")
        ]

        since = timezone.now() - timezone.timedelta(days=1)
        failed_payments = (
            MpesaTransaction.objects.all_tenants()
            .filter(Q(status="failed") | Q(processed=False), created_at__gte=since)
            .count()
        )

        return Response({
            "routers_offline": offline,
            "payments_unconfigured": unconfigured,
            "operators_owing": past_due,
            "failed_payments_24h": failed_payments,
            "all_clear": not (offline or unconfigured or past_due or failed_payments),
        })


class OperatorWarningView(APIView):
    """
    Send an operator a notice without changing their standing.

    The gap this fills: the only warnings that existed were the automated
    billing reminders on a fixed schedule. An owner with a reason to say "sort
    this out" had nothing between saying nothing and restricting — and
    restricting locks a business out of its own dashboard, which is a heavy
    answer to a first conversation.

    Recorded as a status change from the current status to itself, so it lands
    in the same timeline as the restrictions and appears in the history the
    operator can be shown later. set_tenant_status returns False for a no-op
    move, so the row is written directly here.
    """

    permission_classes = [IsPlatformStaff]

    def post(self, request, tenant_id):
        tenant = Tenant.objects.filter(id=tenant_id).first()
        if tenant is None:
            return Response({"detail": "Operator not found"}, status=404)

        message = (request.data.get("message") or "").strip()
        if not message:
            return Response({"message": ["Say what the warning is about."]}, status=400)

        TenantStatusChange.objects.create(
            tenant=tenant,
            from_status=tenant.status,
            to_status=tenant.status,
            reason=f"Warning: {message}",
            changed_by=request.user,
            automatic=False,
        )

        # Best effort. A warning that failed to send is still a warning that was
        # issued and recorded, and losing the record because an SMS gateway was
        # down would be the worse outcome.
        delivered = False
        if tenant.contact_phone:
            try:
                notify_customer(tenant.contact_phone, message)
                delivered = True
            except Exception:
                logger.warning("[platform] warning SMS to %s failed", tenant, exc_info=True)

        return Response({
            "detail": "Warning recorded" + (" and sent" if delivered else ""),
            "delivered": delivered,
            "reason_no_delivery": (
                None if delivered
                else "No contact phone on file for this operator"
                if not tenant.contact_phone else "Sending failed"
            ),
        })


class AdminActionLogView(APIView):
    """
    Who did what to which account.

    The log has been written since accounts management landed and was readable
    nowhere — the same defect this codebase already had with the operator status
    history: recorded faithfully, surfaced never. An audit trail nobody can read
    is a cost with no benefit.

    Scoped by who is asking. Platform staff see every operator, because acting
    across operators is their job. An operator admin sees only actions against
    their own business — including ones a platform account took on them, which
    is exactly the part they have a right to see.
    """

    permission_classes = [IsTenantAdmin]

    def get(self, request):
        user = request.user
        qs = (
            AdminActionLog.objects
            .select_related("actor", "target_user", "target_tenant")
            .all()
        )

        if not user.is_platform_staff:
            # Their own business only. Rows with no target_tenant are platform
            # housekeeping and belong to nobody here.
            qs = qs.filter(target_tenant_id=user.tenant_id)
        else:
            tenant_id = request.query_params.get("tenant")
            if tenant_id:
                qs = qs.filter(target_tenant_id=tenant_id)

        action = request.query_params.get("action")
        if action:
            qs = qs.filter(action=action)

        limit = max(1, min(int(request.query_params.get("limit", 100) or 100), 500))

        return Response({
            "actions": [
                {
                    "id": row.id,
                    "action": row.action,
                    "label": row.get_action_display(),
                    "by": row.actor.username if row.actor_id else None,
                    "by_platform": bool(row.actor_id and row.actor.is_platform_staff),
                    "target": row.target_label or (
                        row.target_user.username if row.target_user_id else None),
                    "operator": (
                        row.target_tenant.business_name or row.target_tenant.name
                        if row.target_tenant_id else None
                    ),
                    "operator_id": row.target_tenant_id,
                    "detail": row.detail,
                    "at": row.created_at,
                }
                for row in qs[:limit]
            ],
        })


class OperatorPlanView(APIView):
    """
    Move an operator onto a plan, or change the one they are on.

    A plan could only be chosen during onboarding, so an operator who grew out
    of theirs — or was created without one — could not be moved without going
    into the database. Owner-only: this decides what they are billed.
    """

    permission_classes = [IsPlatformOwner]

    def post(self, request, tenant_id):
        tenant = Tenant.objects.filter(id=tenant_id).first()
        if tenant is None:
            return Response({"detail": "Operator not found"}, status=404)

        slug = (request.data.get("plan") or "").strip()
        if not slug:
            return Response({"plan": ["Choose a plan."]}, status=400)

        plan = PlatformPlan.objects.filter(slug=slug, is_active=True).first()
        if plan is None:
            return Response({"plan": ["No active plan with that slug."]}, status=400)

        with tenant_context(tenant):
            subscription = (
                TenantSubscription.objects.all_tenants().filter(tenant=tenant).first()
            )
            now = timezone.now()
            if subscription is None:
                subscription = TenantSubscription.objects.create(
                    tenant=tenant, plan=plan, status="trialing",
                    current_period_start=now,
                    current_period_end=now + timezone.timedelta(
                        days=plan.billing_period_days),
                )
                was = None
            else:
                was = subscription.plan.name
                # The current period is left alone deliberately. Re-dating it
                # would either bill them twice for the same days or hand them a
                # free period, depending on which direction they moved.
                subscription.plan = plan
                subscription.save(update_fields=["plan"])

        record_admin_action(
            request.user, AdminActionLog.CHANGE_PLAN,
            target_tenant=tenant,
            detail=f"{was or 'no plan'} -> {plan.name}",
        )

        return Response({
            "detail": f"{tenant.business_name or tenant.name} is now on {plan.name}.",
            "plan": plan.name,
            "previous": was,
            "note": "Takes effect from their next invoice; the current period is unchanged.",
        })


class OperatorMpesaSetupView(APIView):
    """
    Set up an operator's M-Pesa till from the platform side.

    Onboarding an operator is not finished when the account exists — it is
    finished when money can reach them, and that step is gated on Safaricom
    rather than on us. An operator waiting on Daraja looks completely healthy
    from their own dashboard: nothing errors, there is simply no revenue. This
    is the endpoint that lets whoever is helping them finish the job without
    asking them to read credentials down a phone line.

    Reading is open to platform staff because every secret comes back masked.
    Writing is owner-only: these are the credentials that decide whose bank
    account a subscriber's money lands in.
    """

    permission_classes = [IsPlatformStaff]

    KEYS = [
        "MPESA_ENV",
        "MPESA_CONSUMER_KEY",
        "MPESA_CONSUMER_SECRET",
        "MPESA_SHORTCODE",
        # Whether that number is a PayBill or a Buy Goods till, and the store
        # number a till is paired with. Absent from this list the page cannot
        # write them, which is a whole product an operator cannot be onboarded
        # for — and the failure is a customer getting no prompt.
        "MPESA_SHORTCODE_TYPE",
        "MPESA_STORE_NUMBER",
        "MPESA_PASSKEY",
        "MPESA_CALLBACK_URL",
    ]

    def get_permissions(self):
        if self.request.method in ("PUT", "PATCH"):
            return [IsPlatformOwner()]
        return super().get_permissions()

    def _tenant(self, tenant_id):
        return Tenant.objects.filter(id=tenant_id).first()

    def get(self, request, tenant_id):
        tenant = self._tenant(tenant_id)
        if tenant is None:
            return Response({"detail": "Operator not found"}, status=404)

        from billing.mpesa_client import callback_url_for

        data = {}
        for key in self.KEYS:
            value = get_setting(key, default="", tenant=tenant)
            # Same masking rule as the operator's own settings page. A secret
            # is never returned in readable form once it has been set, not even
            # to the platform owner — there is no reason to read one back.
            if key in SystemSettingsView.SENSITIVE_KEYS and value not in ("", None):
                data[key] = "********"
            else:
                data[key] = value or ""

        data["operator"] = tenant.business_name or tenant.name
        data["missing"] = missing_mpesa_keys(tenant=tenant)
        data["configured"] = not data["missing"]
        # What has to be pasted into the Daraja portal. Per-operator, and
        # unguessable, so it cannot be worked out from the operator's name.
        try:
            data["callback_url"] = callback_url_for(tenant=tenant)
        except Exception as exc:
            data["callback_url"] = ""
            data["callback_hint"] = str(exc)

        return Response(data)

    def put(self, request, tenant_id):
        tenant = self._tenant(tenant_id)
        if tenant is None:
            return Response({"detail": "Operator not found"}, status=404)

        serializer = SystemSettingSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        written = []
        for key, value in serializer.validated_data.items():
            if key not in self.KEYS:
                # This endpoint is about payments. SMS and WhatsApp credentials
                # belong to the operator's own settings page.
                continue
            if value == "********":
                continue  # unchanged secret, left alone
            SystemSetting.objects.update_or_create(
                tenant=tenant, key=key, defaults={"value": value},
            )
            written.append(key)

        from .config import clear_settings_cache
        clear_settings_cache(tenant=tenant)

        if written:
            # The names of what changed, never the values.
            record_admin_action(
                request.user, AdminActionLog.CONFIGURE_PAYMENTS,
                target_tenant=tenant, detail=", ".join(written),
            )

        return Response({
            "detail": "Payment settings updated",
            "updated": written,
            "missing": missing_mpesa_keys(tenant=tenant),
        })


class OperatorMpesaTestView(APIView):
    """
    Ask Safaricom for a token with this operator's credentials.

    The only honest test of whether setup worked: it either authenticates or it
    does not, and the error Daraja returns is far more useful than anything
    guessed from the shape of the values.
    """

    permission_classes = [IsPlatformStaff]

    def post(self, request, tenant_id):
        tenant = Tenant.objects.filter(id=tenant_id).first()
        if tenant is None:
            return Response({"detail": "Operator not found"}, status=404)

        missing = missing_mpesa_keys(tenant=tenant)
        if missing:
            return Response({
                "success": False,
                "error": f"Not set up yet — missing {', '.join(missing)}.",
                "missing": missing,
            }, status=400)

        try:
            get_mpesa_access_token(tenant=tenant)
        except Exception as exc:
            # The credentials themselves are never echoed back, only what
            # Safaricom said about them.
            return Response({"success": False, "error": str(exc)}, status=400)

        return Response({
            "success": True,
            "detail": f"{tenant.business_name or tenant.name} can take payments.",
        })


class OperatorPasswordResetView(APIView):
    """
    The forgotten-password path for an operator.

    There is no email backend and the SMS credentials belong to each operator
    rather than the platform, so there is nothing to send a reset link with.
    The owner sets a temporary password instead and passes it on directly —
    which matches how support already works here, the owner being the only
    channel an operator has.

    The temporary password is returned once, in this response, and never
    stored in plaintext or written to a log. The account is marked
    must_change_password so the holder replaces it immediately, and every
    existing session is ended — otherwise a reset prompted by a suspected
    compromise would leave the suspect session signed in for up to a day.
    """

    permission_classes = [IsPlatformOwner]

    ALPHABET = string.ascii_letters + string.digits

    def post(self, request, tenant_id):
        tenant = Tenant.objects.filter(id=tenant_id).first()
        if tenant is None:
            return Response({"detail": "Operator not found"}, status=404)

        username = request.data.get("username")
        candidates = User.objects.filter(
            tenant=tenant, role__in=(User.TENANT_ADMIN, User.TENANT_STAFF)
        )
        if username:
            target = candidates.filter(username=username).first()
        else:
            # No username given: only unambiguous when they have one admin.
            admins = list(candidates.filter(role=User.TENANT_ADMIN)[:2])
            if len(admins) > 1:
                return Response(
                    {"detail": "This operator has several admins — name the one to reset.",
                     "usernames": [u.username for u in candidates]},
                    status=400,
                )
            target = admins[0] if admins else None

        if target is None:
            return Response({"detail": "No such account for this operator."}, status=404)

        temporary = "".join(secrets.choice(self.ALPHABET) for _ in range(14))
        target.set_password(temporary)
        target.must_change_password = True
        target.save(update_fields=["password", "must_change_password"])
        target.invalidate_sessions()

        record_admin_action(
            request.user, AdminActionLog.RESET_PASSWORD,
            target_user=target, target_tenant=tenant,
            detail=request.data.get("reason", "")[:255],
        )

        return Response({
            "detail": "Password reset. Give this to them directly — it is not shown again.",
            "username": target.username,
            "temporary_password": temporary,
        })


def _erase_operator(tenant, actor=None):
    """
    Remove every row belonging to one operator, then the operator.

    Returns (counts, removed).

    Order is worked out rather than hardcoded. Every tenant-scoped model
    PROTECTs the tenant, and several protect each other, so a fixed list would
    be a guess that breaks the next time a model is added. This deletes what it
    can, repeats while it is still making progress, and stops when nothing is
    left or nothing more can go — so a new model joins the sweep by existing.

    The whole thing is one transaction. A half-erased operator — the tenant
    gone, its customers orphaned, or the reverse — is worse than either
    outcome.
    """
    from django.db.models import ProtectedError

    counts = {}
    scoped = list(TenantScopedModel.__subclasses__())

    with all_tenants(), transaction.atomic():
        # Recorded before anything goes, with the numbers, because afterwards
        # there is nothing left to count. The row survives the tenant: its
        # reference is SET_NULL and target_label keeps the name as text.
        for model in scoped:
            n = model.objects.all_tenants().filter(tenant=tenant).count()
            if n:
                counts[model.__name__] = n
        user_count = User.objects.filter(tenant=tenant).count()
        if user_count:
            counts["User"] = user_count

        record_admin_action(
            actor, AdminActionLog.DELETE_OPERATOR,
            target_tenant=tenant,
            label=tenant.business_name or tenant.name,
            detail=", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "no records",
        )

        remaining = list(scoped)
        while remaining:
            stuck = []
            progressed = False
            for model in remaining:
                try:
                    with transaction.atomic():
                        deleted, _ = model.objects.all_tenants().filter(
                            tenant=tenant).delete()
                    progressed = progressed or bool(deleted)
                except ProtectedError:
                    # Something else still points at these. Try again once that
                    # something has been removed.
                    stuck.append(model)
            if not stuck:
                break
            if not progressed:
                logger.error(
                    "[erase] stuck on %s for %s — rolling back",
                    [m.__name__ for m in stuck], tenant,
                )
                raise RuntimeError("could not erase every record")
            remaining = stuck

        User.objects.filter(tenant=tenant).delete()
        tenant.delete()

    return counts, True


class PlatformOperatorDetailView(APIView):
    """One operator in full, including how often support has viewed them."""
    permission_classes = [IsPlatformStaff]

    def delete(self, request, tenant_id):
        """
        Erase a closed operator and everything belonging to them.

        Irreversible, and it destroys billing history — invoices and payments
        that most jurisdictions require keeping for years. Closing an account
        already stops invoicing without destroying anything, so this exists for
        the cases where the record genuinely should not persist: a test
        operator, a duplicate, an onboarding that never began.

        Three gates, because there is no undo:
          - the account must already be closed, so this is never the first
            action taken against a live business;
          - only the platform owner may do it;
          - the caller must type the operator's name back, which is the
            difference between deciding and mis-clicking.

        What is destroyed is counted and written to the audit log first. That
        row survives because its tenant reference is SET_NULL and it keeps the
        name as text — so afterwards there is still an answer to "what happened
        to them".
        """
        if request.user.role != User.PLATFORM_OWNER:
            return Response(
                {"detail": "This endpoint is restricted to the platform owner."},
                status=403,
            )

        tenant = Tenant.objects.filter(id=tenant_id).first()
        if tenant is None:
            return Response({"detail": "Operator not found"}, status=404)

        if tenant.status != "cancelled":
            return Response(
                {"detail": "Close the account first. Only a closed operator can be "
                           "deleted, so this is never the first action taken "
                           "against a live business."},
                status=409,
            )

        typed = (request.data.get("confirm") or "").strip()
        expected = tenant.business_name or tenant.name
        if typed != expected:
            return Response(
                {"detail": f'Type "{expected}" to confirm.', "expected": expected},
                status=400,
            )

        try:
            counts, _ = _erase_operator(tenant, actor=request.user)
        except Exception:
            # The transaction rolled back, so the operator is untouched. Say so
            # rather than leaving the caller guessing what state it is in.
            logger.exception("[erase] failed for %s", tenant)
            return Response(
                {"detail": "Could not remove every record for this operator. "
                           "Nothing was deleted and they are unchanged."},
                status=500,
            )

        return Response({
            "detail": f"{expected} and all their records have been deleted.",
            "removed": counts,
        })

    def patch(self, request, tenant_id):
        """
        Correct an operator's details after onboarding.

        Owner-only: business_name and support_phone appear in the SMS their
        subscribers receive, and pppoe_prefix shapes generated usernames.
        """
        if request.user.role != User.PLATFORM_OWNER:
            return Response(
                {"detail": "This endpoint is restricted to the platform owner."},
                status=403,
            )

        tenant = Tenant.objects.filter(id=tenant_id).first()
        if tenant is None:
            return Response({"detail": "Operator not found"}, status=404)

        serializer = OperatorUpdateSerializer(tenant, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        changed = [
            f for f, v in serializer.validated_data.items()
            if getattr(tenant, f) != v
        ]
        serializer.save()

        if changed:
            record_admin_action(
                request.user, AdminActionLog.UPDATE_OPERATOR,
                target_tenant=tenant, detail=", ".join(changed),
            )
        return Response(serializer.data)

    def get(self, request, tenant_id):
        from django.db.models import Sum

        tenant = Tenant.objects.filter(id=tenant_id).first()
        if tenant is None:
            return Response({"detail": "Operator not found"}, status=404)

        subscription = (
            TenantSubscription.objects.all_tenants()
            .select_related("plan").filter(tenant=tenant).first()
        )
        invoices = TenantInvoice.objects.all_tenants().filter(tenant=tenant)

        # Their subscriber-side revenue — the operator's own business, shown so
        # the platform can see whether a plan still fits them.
        subscriber_revenue = (
            Payment.objects.all_tenants().filter(tenant=tenant)
            .aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
        )

        return Response({
            "id": tenant.id,
            "name": tenant.business_name or tenant.name,
            "slug": tenant.slug,
            "status": tenant.status,
            "is_restricted": tenant.is_restricted,
            "contact_email": tenant.contact_email,
            "contact_phone": tenant.contact_phone,
            "created_at": tenant.created_at,
            # The editable record behind the display name above. "name" there
            # collapses the two names into one string with nothing to say which
            # it came from, which is right for showing and wrong for an edit
            # form — prefilled from it, the first save would write the public
            # brand over the internal name this platform files them under.
            # These are the fields OperatorUpdateSerializer accepts, sent raw.
            "details": {
                "name": tenant.name,
                "business_name": tenant.business_name,
                "support_phone": tenant.support_phone,
                "support_phone_2": tenant.support_phone_2,
                "pppoe_prefix": tenant.pppoe_prefix,
                "contact_email": tenant.contact_email,
                "contact_phone": tenant.contact_phone,
            },
            "payments_configured": not missing_mpesa_keys(tenant=tenant),
            "plan": TenantSubscriptionSerializer(subscription).data if subscription else None,
            "billing": {
                "outstanding": TenantInvoiceSerializer(
                    invoices.filter(status="unpaid"), many=True).data,
                "amount_owed": invoices.filter(status="unpaid").aggregate(
                    t=Sum("amount"))["t"] or Decimal("0.00"),
                "lifetime_paid": invoices.filter(status="paid").aggregate(
                    t=Sum("amount"))["t"] or Decimal("0.00"),
            },
            "network": {
                "subscribers": Customer.objects.all_tenants().filter(tenant=tenant).count(),
                "routers": RouterDevice.objects.all_tenants().filter(tenant=tenant).count(),
                "subscriber_revenue": subscriber_revenue,
            },
            "recent_support_access": [
                {
                    "by": log.platform_user.username if log.platform_user else None,
                    "method": log.method,
                    "path": log.path,
                    "reason": log.reason,
                    "at": log.created_at,
                }
                for log in tenant.impersonations.select_related("platform_user")[:20]
            ],
        })
