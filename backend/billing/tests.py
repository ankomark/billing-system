"""
Critical path tests for the WiFi billing system.

Coverage:
  - SubscriptionCreationTests  : expiry calculation, invoice auto-creation, PPPoE credentials
  - PaymentProcessingTests     : invoice marked paid, router access, notifications, vouchers
  - MpesaCallbackTests         : webhook success/failure/duplicate/mismatch/bad-IP
  - VoucherValidationTests     : valid, expired, inactive, MAC rebind protection
  - EncryptionFieldTests       : encrypt on write, decrypt on read, passthrough, no double-encrypt
  - CustomerModelTests         : full_clean guard on partial saves, validation rules
  - LoginThrottleTests         : 5 attempts pass, 6th returns 429
"""

import json
from pathlib import Path
import re
from decimal import Decimal
from unittest import skipUnless
from unittest.mock import MagicMock, patch
from cryptography.fernet import Fernet

from django.db import connection, transaction

from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import ProtectedError
from django.db.utils import IntegrityError
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from billing.auth_tokens import TenantTokenObtainPairSerializer

from billing.config import clear_settings_cache, get_platform_setting, get_setting
from billing.mpesa_client import (
    PaymentsNotConfigured,
    callback_url_for,
    initiate_stk_push,
    missing_mpesa_keys,
    payments_configured,
)
from billing.services.pppoe_service import generate_pppoe_credentials
from billing.services.voucher_service import validate_voucher, extract_codes
from billing.tasks.mpesa_tasks import initiate_stk_push_task
from billing.tasks.platform_billing_tasks import (
    generate_tenant_invoices,
    mark_overdue_tenants,
    restrict_expired_grace_tenants,
    send_platform_billing_reminders,
)
from billing.tasks.router_health import prune_router_events_task
from billing.tasks.provisioning import ensure_customer_access_task
from billing.router_service import enable_customer_access
from billing.notifications import send_sms, send_bulk_sms, sms_balance
from celery.exceptions import Retry
from billing.router_service import (
    _station_of, _tenant_routers, pick_best_router_for_new_customer,
    pick_failover_router, pick_working_router,
)
from billing.tasks.subscription_tasks import enforce_subscription_expiry
from billing.router_service import (
    pick_best_router_for_new_customer,
    pick_working_router,
)
from billing.tenancy import TenantManager, all_tenants, tenant_context

from billing.models import (
    User, ConnectionAttempt, Customer, CustomerDevice, Package, Subscription,
    Invoice, Payment, Voucher, MpesaTransaction, RouterDevice,
    AccessAuditLog, Tenant, RouterFailoverLog, ExpiryReminderLog,
    SystemSetting, PPPoEUsageSnapshot, PPPoEUsageState, PPPoEUsageRecord,
    HotspotUsageState, HotspotUsageRecord, UsageRecord,
    PlatformPlan, PlatformSetting, TenantSubscription, TenantInvoice, TenantPayment,
    TenantStatusChange, ImpersonationLog, AdminActionLog, RouterEvent, Station,
    set_tenant_status, router_uptime,
)
from billing.fields import ENCRYPTED_PREFIX

# A fixed key used only in encryption-specific tests.
# Other test classes work without any key (plaintext passthrough).
TEST_ENCRYPTION_KEY = Fernet.generate_key().decode()


# ===========================================================
# Shared factory helpers
# ===========================================================

def _default_tenant():
    """The tenant migration 0026 creates. Every operator account needs one."""
    return Tenant.objects.get(slug="skylink")


def make_admin(username="admin_user", tenant=None):
    # is_staff too: some endpoints historically used DRF IsAdminUser.
    return User.objects.create_user(
        username=username, password="adminpass",
        role=User.TENANT_ADMIN, tenant=tenant or _default_tenant(), is_staff=True,
    )


def make_platform_owner(username="platform_owner"):
    """Platform account: NULL tenant, so queries run unscoped."""
    return User.objects.create_user(
        username=username, password="ownerpass",
        role=User.PLATFORM_OWNER, tenant=None, is_staff=True,
    )


def make_router(name="Main Router", ip="192.168.1.1", **kwargs):
    defaults = {
        "username": "admin",
        "password": "routerpass",
        "api_port": 8728,
        "priority": 1,
        "is_active": True,
        "is_online": True,
    }
    return RouterDevice.objects.create(name=name, ip_address=ip, **{**defaults, **kwargs})


def make_package(name="Basic 30d", price="500.00",
                 duration_value=30, duration_unit="days", **kwargs):
    return Package.objects.create(
        name=name, download_speed=5, upload_speed=2,
        price=Decimal(price),
        duration_value=duration_value, duration_unit=duration_unit,
        monthly_data_cap_gb=0, is_hotspot=False,
        **kwargs,
    )


def make_hotspot_package(name="Hotspot 2hr", price="50.00"):
    return Package.objects.create(
        name=name, download_speed=5, upload_speed=2,
        price=Decimal(price), duration_value=2, duration_unit="hours",
        monthly_data_cap_gb=0, is_hotspot=True,
    )


def make_pppoe_customer(router, phone="254712345678", username_suffix="01"):
    tenant = router.tenant if router is not None else _default_tenant()
    user = User.objects.create_user(
        username=f"pppoe_{username_suffix}", password="pass",
        role=User.CUSTOMER, tenant=tenant,
    )
    return Customer.objects.create(
        user=user, full_name="PPPoE Test Customer",
        phone=phone, connection_type="pppoe", router=router, tenant=tenant,
    )


def make_hotspot_customer(router, phone="254700111222", username_suffix="01"):
    tenant = router.tenant if router is not None else _default_tenant()
    user = User.objects.create_user(
        username=f"hs_{username_suffix}", password="pass",
        role=User.CUSTOMER, tenant=tenant,
    )
    return Customer.objects.create(
        user=user, full_name="Hotspot Test Customer",
        phone=phone, connection_type="hotspot", router=router, tenant=tenant,
    )


# ===========================================================
# 1. Subscription Creation
# ===========================================================

class SubscriptionCreationTests(TestCase):
    """Subscription.save() must calculate expiry correctly and create an invoice."""

    def setUp(self):
        self.router = make_router()
        self.package = make_package()  # 30 days / KES 500

    @patch("billing.models.notify_customer")
    def test_invoice_auto_created_on_subscription(self, _):
        customer = make_pppoe_customer(self.router)
        sub = Subscription.objects.create(customer=customer, package=self.package)
        self.assertTrue(Invoice.objects.filter(subscription=sub).exists())
        invoice = sub.invoice
        self.assertEqual(invoice.payment_status, "unpaid")
        self.assertEqual(invoice.total_amount, Decimal("500.00"))
        self.assertEqual(invoice.customer, customer)

    @patch("billing.models.notify_customer")
    def test_expiry_calculated_for_days(self, _):
        customer = make_pppoe_customer(self.router)
        sub = Subscription.objects.create(customer=customer, package=self.package)
        expected_seconds = 30 * 24 * 3600
        actual_seconds = (sub.expiry_date - sub.start_date).total_seconds()
        self.assertAlmostEqual(actual_seconds, expected_seconds, delta=60)

    @patch("billing.models.notify_customer")
    def test_expiry_calculated_for_months(self, _):
        monthly_pkg = make_package(name="Monthly", duration_value=1, duration_unit="months")
        customer = make_pppoe_customer(self.router, phone="254711000001", username_suffix="m1")
        sub = Subscription.objects.create(customer=customer, package=monthly_pkg)
        # relativedelta adds exactly one calendar month
        start = sub.start_date
        end = sub.expiry_date
        expected_month = (start.month % 12) + 1
        self.assertEqual(end.month, expected_month)
        self.assertEqual(end.day, start.day)

    @patch("billing.models.notify_customer")
    def test_expiry_calculated_for_hours(self, _):
        hourly_pkg = make_package(name="1hr", duration_value=1, duration_unit="hours", price="20.00")
        customer = make_pppoe_customer(self.router, phone="254711000002", username_suffix="h1")
        sub = Subscription.objects.create(customer=customer, package=hourly_pkg)
        actual = (sub.expiry_date - sub.start_date).total_seconds()
        self.assertAlmostEqual(actual, 3600, delta=10)

    @patch("billing.models.notify_customer")
    def test_expiry_calculated_for_weeks(self, _):
        weekly_pkg = make_package(name="Weekly", duration_value=2, duration_unit="weeks", price="150.00")
        customer = make_pppoe_customer(self.router, phone="254711000003", username_suffix="w1")
        sub = Subscription.objects.create(customer=customer, package=weekly_pkg)
        actual = (sub.expiry_date - sub.start_date).total_seconds()
        self.assertAlmostEqual(actual, 2 * 7 * 24 * 3600, delta=60)

    @patch("billing.models.notify_customer")
    def test_pppoe_credentials_generated_on_first_subscription(self, _):
        customer = make_pppoe_customer(self.router)
        self.assertFalse(bool(customer.pppoe_username))
        Subscription.objects.create(customer=customer, package=self.package)
        customer.refresh_from_db()
        self.assertTrue(customer.pppoe_username.startswith("SKY-"))
        self.assertTrue(len(customer.pppoe_password) >= 10)

    @patch("billing.models.notify_customer")
    def test_credentials_not_regenerated_on_renewal(self, _):
        customer = make_pppoe_customer(self.router)
        Subscription.objects.create(customer=customer, package=self.package)
        customer.refresh_from_db()
        first_username = customer.pppoe_username

        Subscription.objects.create(customer=customer, package=self.package)
        customer.refresh_from_db()
        self.assertEqual(customer.pppoe_username, first_username)

    @patch("billing.models.notify_customer")
    def test_each_subscription_gets_unique_invoice_number(self, _):
        customer = make_pppoe_customer(self.router)
        sub1 = Subscription.objects.create(customer=customer, package=self.package)
        router2 = make_router(name="R2", ip="10.0.0.2")
        customer2 = make_pppoe_customer(router2, phone="254700999888", username_suffix="02")
        sub2 = Subscription.objects.create(customer=customer2, package=self.package)
        self.assertNotEqual(sub1.invoice.invoice_number, sub2.invoice.invoice_number)


# ===========================================================
# 2. Payment Processing
# ===========================================================

class PaymentProcessingTests(TestCase):
    """Payment.save() must mark invoice paid, activate subscription, fire side effects."""

    def setUp(self):
        self.router = make_router()
        self.package = make_package()
        self.customer = make_pppoe_customer(self.router)
        with patch("billing.models.notify_customer"):
            self.sub = Subscription.objects.create(
                customer=self.customer, package=self.package,
            )
        self.invoice = self.sub.invoice

    @patch("billing.router_service.enable_customer_access")
    @patch("billing.models.notify_customer")
    def test_invoice_marked_paid(self, _, __):
        with self.captureOnCommitCallbacks(execute=True):
            Payment.objects.create(
                customer=self.customer, subscription=self.sub,
                amount=self.package.price, method="cash",
            )
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.payment_status, "paid")

    @patch("billing.router_service.enable_customer_access")
    @patch("billing.models.notify_customer")
    def test_subscription_set_to_active(self, _, __):
        with self.captureOnCommitCallbacks(execute=True):
            Payment.objects.create(
                customer=self.customer, subscription=self.sub,
                amount=self.package.price, method="cash",
            )
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, "active")

    @patch("billing.tasks.provisioning.ensure_customer_access_task.delay")
    @patch("billing.models.notify_customer")
    def test_router_access_is_requested_exactly_once(self, _, mock_provision):
        """
        A payment provisions the customer, once.

        It asserted the inline call before provisioning was made retryable. A
        router briefly unreachable used to cost the customer the access they had
        just paid for, so it now goes through a task that retries and, failing
        that, tells the operator — which means the thing to assert is that the
        work was requested, not that it finished synchronously.
        """
        with self.captureOnCommitCallbacks(execute=True):
            Payment.objects.create(
                customer=self.customer, subscription=self.sub,
                amount=self.package.price, method="cash",
            )
        mock_provision.assert_called_once()
        self.assertEqual(mock_provision.call_args.args[0], self.customer.id)

    @patch("billing.router_service.enable_customer_access")
    @patch("billing.models.notify_customer")
    def test_pppoe_notification_contains_credentials(self, mock_notify, _):
        with self.captureOnCommitCallbacks(execute=True):
            Payment.objects.create(
                customer=self.customer, subscription=self.sub,
                amount=self.package.price, method="cash",
            )
        mock_notify.assert_called_once()
        phone_arg, message_arg = mock_notify.call_args[0]
        self.assertEqual(phone_arg, self.customer.phone)
        self.assertIn("PPPoE", message_arg)

    @patch("billing.router_service.enable_customer_access")
    @patch("billing.models.notify_customer")
    def test_hotspot_voucher_created_on_payment(self, _, __):
        hotspot_pkg = make_hotspot_package()
        router2 = make_router(name="R2", ip="10.0.0.2")
        hs_customer = make_hotspot_customer(router2, phone="254700888777", username_suffix="hs02")
        with patch("billing.models.notify_customer"):
            hs_sub = Subscription.objects.create(customer=hs_customer, package=hotspot_pkg)

        with self.captureOnCommitCallbacks(execute=True):
            Payment.objects.create(
                customer=hs_customer, subscription=hs_sub,
                amount=hotspot_pkg.price, method="mpesa", reference="QK999",
            )
        voucher = Voucher.objects.filter(subscription=hs_sub).first()
        self.assertIsNotNone(voucher)
        self.assertTrue(voucher.is_active)
        self.assertTrue(voucher.code.startswith("WIFI-"))
        self.assertEqual(voucher.expires_at, hs_sub.expiry_date)

    @patch("billing.router_service.enable_customer_access")
    @patch("billing.models.notify_customer")
    def test_second_payment_does_not_double_process(self, _, __):
        """A second payment on the same subscription must not create a second invoice or voucher."""
        with self.captureOnCommitCallbacks(execute=True):
            Payment.objects.create(
                customer=self.customer, subscription=self.sub,
                amount=self.package.price, method="cash",
            )
        with self.captureOnCommitCallbacks(execute=True):
            Payment.objects.create(
                customer=self.customer, subscription=self.sub,
                amount=self.package.price, method="cash",
            )
        # Invoice still paid (not reset)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.payment_status, "paid")
        # Only one invoice for this subscription
        self.assertEqual(Invoice.objects.filter(subscription=self.sub).count(), 1)


# ===========================================================
# 3. M-Pesa STK Callback
# ===========================================================

@override_settings(MPESA_ALLOW_LOCAL_CALLBACK=True)
class MpesaCallbackTests(TestCase):
    """STK callback webhook must process payments atomically and reject bad requests."""

    URL = "/api/mpesa/stk-callback/"

    def setUp(self):
        self.client = APIClient()
        self.router = make_router()
        self.package = make_package(price="500.00")
        self.customer = make_pppoe_customer(self.router)
        with patch("billing.models.notify_customer"):
            self.sub = Subscription.objects.create(
                customer=self.customer, package=self.package,
            )
        self.invoice = self.sub.invoice

    def _build_callback(self, result_code=0, amount=500,
                        receipt="QK12345678", reference=None):
        reference = reference or self.invoice.invoice_number
        payload = {
            "Body": {
                "stkCallback": {
                    "ResultCode": result_code,
                    "ResultDesc": "Success" if result_code == 0 else "User cancelled",
                    "MerchantRequestID": "MR-001",
                    "CheckoutRequestID": "CR-001",
                }
            }
        }
        if result_code == 0:
            payload["Body"]["stkCallback"]["CallbackMetadata"] = {
                "Item": [
                    {"Name": "Amount", "Value": amount},
                    {"Name": "MpesaReceiptNumber", "Value": receipt},
                    {"Name": "PhoneNumber", "Value": 254712345678},
                    {"Name": "AccountReference", "Value": reference},
                ]
            }
        return json.dumps(payload)

    @patch("billing.router_service.enable_customer_access")
    @patch("billing.models.notify_customer")
    def test_success_marks_invoice_paid(self, _, __):
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(self.URL, data=self._build_callback(),
                                    content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.payment_status, "paid")

    @patch("billing.router_service.enable_customer_access")
    @patch("billing.models.notify_customer")
    def test_success_creates_payment_and_transaction(self, _, __):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.URL, data=self._build_callback(),
                             content_type="application/json")
        self.assertTrue(Payment.objects.filter(reference="QK12345678").exists())
        tx = MpesaTransaction.objects.get(mpesa_receipt="QK12345678")
        self.assertEqual(tx.status, "success")
        self.assertTrue(tx.processed)
        self.assertEqual(tx.invoice, self.invoice)

    def test_failed_callback_creates_transaction_but_no_payment(self):
        resp = self.client.post(
            self.URL,
            data=self._build_callback(result_code=1032),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.payment_status, "unpaid")
        self.assertFalse(Payment.objects.filter(subscription=self.sub).exists())
        self.assertTrue(MpesaTransaction.objects.filter(status="failed").exists())

    @patch("billing.router_service.enable_customer_access")
    @patch("billing.models.notify_customer")
    def test_duplicate_receipt_ignored(self, _, __):
        body = self._build_callback()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.URL, data=body, content_type="application/json")
        # Second callback with identical receipt
        resp = self.client.post(self.URL, data=body, content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        # Still only one payment
        self.assertEqual(Payment.objects.filter(subscription=self.sub).count(), 1)

    def test_amount_mismatch_rejected(self):
        resp = self.client.post(
            self.URL,
            data=self._build_callback(amount=100),  # package costs 500
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.payment_status, "unpaid")

    def test_unknown_invoice_reference_rejected(self):
        resp = self.client.post(
            self.URL,
            data=self._build_callback(reference="INV-DOES-NOT-EXIST"),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    @override_settings(MPESA_ALLOW_LOCAL_CALLBACK=False, MPESA_TRUSTED_IPS=[])
    def test_untrusted_ip_blocked(self):
        resp = self.client.post(
            self.URL,
            data=self._build_callback(),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)


# ===========================================================
# 4. Voucher Validation
# ===========================================================

class VoucherValidationTests(TestCase):
    """HotspotVoucherValidateView must enforce validity, expiry, and MAC binding."""

    URL = "/api/hotspot/validate/"

    def setUp(self):
        self.client = APIClient()
        self.router = make_router()
        self.package = make_hotspot_package()
        self.customer = make_hotspot_customer(self.router)
        with patch("billing.models.notify_customer"):
            self.sub = Subscription.objects.create(
                customer=self.customer, package=self.package,
            )
        self.voucher = Voucher.objects.create(
            code="WIFI-TEST01",
            subscription=self.sub,
            expires_at=timezone.now() + timezone.timedelta(hours=3),
            is_active=True,
        )

    @patch("billing.router_service.enable_customer_access")
    def test_valid_voucher_returns_200(self, _):
        resp = self.client.post(self.URL, {
            "code": "WIFI-TEST01",
            "mac_address": "AA:BB:CC:DD:EE:FF",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["detail"], "Access granted")
        self.assertIn("expires_at", resp.data)

    @patch("billing.router_service.enable_customer_access")
    def test_valid_voucher_binds_mac(self, _):
        self.client.post(self.URL, {
            "code": "WIFI-TEST01",
            "mac_address": "AA:BB:CC:DD:EE:FF",
        })
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.hotspot_username, "AA:BB:CC:DD:EE:FF")
        self.assertEqual(self.customer.status, "active")

    @patch("billing.router_service.enable_customer_access")
    def test_same_mac_revalidation_passes(self, _):
        self.customer.hotspot_username = "AA:BB:CC:DD:EE:FF"
        self.customer.save(update_fields=["hotspot_username"])
        resp = self.client.post(self.URL, {
            "code": "WIFI-TEST01",
            "mac_address": "AA:BB:CC:DD:EE:FF",
        })
        self.assertEqual(resp.status_code, 200)

    def test_different_mac_on_bound_voucher_rejected(self):
        """
        The guarantee is unchanged — a code bought for one phone does not work
        on another. What changed is the answer: 409 rather than a flat 400,
        carrying how many devices the package allows and how many are in use,
        because "already in use on 2 devices" is something a customer can act
        on and "bad request" is not.

        This subscriber is bound the old way, with a MAC on their row and no
        device row. That is exactly the state every existing subscriber was in
        when the device table arrived, and the count reads rows — so without
        the backfill and the healing beside it, this would answer 200 and the
        voucher would be good for one more phone than it was sold for.
        """
        self.customer.hotspot_username = "AA:BB:CC:DD:EE:FF"
        self.customer.save(update_fields=["hotspot_username"])
        resp = self.client.post(self.URL, {
            "code": "WIFI-TEST01",
            "mac_address": "11:22:33:44:55:66",
        })
        self.assertEqual(resp.status_code, 409, resp.data)
        self.assertEqual(resp.data["devices_used"], 1)

    def test_expired_voucher_rejected(self):
        self.voucher.expires_at = timezone.now() - timezone.timedelta(hours=1)
        self.voucher.save(update_fields=["expires_at"])
        resp = self.client.post(self.URL, {
            "code": "WIFI-TEST01",
            "mac_address": "AA:BB:CC:DD:EE:FF",
        })
        self.assertEqual(resp.status_code, 400)

    def test_inactive_voucher_rejected(self):
        self.voucher.is_active = False
        self.voucher.save(update_fields=["is_active"])
        resp = self.client.post(self.URL, {
            "code": "WIFI-TEST01",
            "mac_address": "AA:BB:CC:DD:EE:FF",
        })
        self.assertEqual(resp.status_code, 400)

    def test_nonexistent_code_rejected(self):
        resp = self.client.post(self.URL, {
            "code": "WIFI-BOGUS99",
            "mac_address": "AA:BB:CC:DD:EE:FF",
        })
        self.assertEqual(resp.status_code, 400)

    def test_missing_code_returns_400(self):
        resp = self.client.post(self.URL, {"mac_address": "AA:BB:CC:DD:EE:FF"})
        self.assertEqual(resp.status_code, 400)

    def test_missing_mac_returns_400(self):
        resp = self.client.post(self.URL, {"code": "WIFI-TEST01"})
        self.assertEqual(resp.status_code, 400)


# ===========================================================
# 5. Encryption Field
# ===========================================================

@override_settings(FIELD_ENCRYPTION_KEY=TEST_ENCRYPTION_KEY)
class EncryptionFieldTests(TestCase):
    """EncryptedCharField must encrypt on write and decrypt transparently on read."""

    def setUp(self):
        self.router = make_router()

    def _raw_password(self, router_id):
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute(
                "SELECT password FROM billing_routerdevice WHERE id = %s",
                [router_id],
            )
            return cur.fetchone()[0]

    def test_value_stored_encrypted_in_db(self):
        raw = self._raw_password(self.router.id)
        self.assertTrue(
            raw.startswith(ENCRYPTED_PREFIX),
            f"Expected enc: prefix, got raw: {raw[:30]}",
        )

    def test_value_decrypts_transparently_on_read(self):
        router = RouterDevice.objects.get(pk=self.router.pk)
        self.assertEqual(router.password, "routerpass")

    def test_same_plaintext_produces_different_ciphertext(self):
        """Fernet uses a random IV — identical plaintexts must produce distinct ciphertexts."""
        router2 = make_router(name="R2", ip="10.0.0.2", password="routerpass")
        raw1 = self._raw_password(self.router.id)
        raw2 = self._raw_password(router2.id)
        self.assertNotEqual(raw1, raw2)

    def test_empty_password_not_encrypted(self):
        RouterDevice.objects.filter(pk=self.router.pk).update(password="")
        raw = self._raw_password(self.router.id)
        self.assertFalse(raw.startswith(ENCRYPTED_PREFIX))

    def test_legacy_plaintext_passes_through_on_read(self):
        """Records pre-dating encryption must be readable after the field change."""
        RouterDevice.objects.filter(pk=self.router.pk).update(password="legacy_plain")
        router = RouterDevice.objects.get(pk=self.router.pk)
        self.assertEqual(router.password, "legacy_plain")

    def test_no_double_encryption_on_resave(self):
        router = RouterDevice.objects.get(pk=self.router.pk)
        router.save()
        raw = self._raw_password(router.id)
        # Must start with exactly one enc: prefix, not enc:enc:
        self.assertTrue(raw.startswith(ENCRYPTED_PREFIX))
        self.assertFalse(raw[len(ENCRYPTED_PREFIX):].startswith(ENCRYPTED_PREFIX))

    def test_pppoe_password_encrypted_on_customer(self):
        user = User.objects.create_user(username="enc_cust", password="pass",
                                        role=User.CUSTOMER, tenant=_default_tenant())
        customer = Customer.objects.create(
            user=user, full_name="Enc Customer", phone="254799990001",
            connection_type="pppoe", router=self.router,
            pppoe_username="SKY-9999-XYZ", pppoe_password="secret_pw",
        )
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute(
                "SELECT pppoe_password FROM billing_customer WHERE id = %s",
                [customer.id],
            )
            raw = cur.fetchone()[0]
        self.assertTrue(raw.startswith(ENCRYPTED_PREFIX))
        customer.refresh_from_db()
        self.assertEqual(customer.pppoe_password, "secret_pw")


# ===========================================================
# 6. Customer Model Validation Guards
# ===========================================================

class CustomerModelTests(TestCase):
    """Customer.save() must skip full_clean on partial saves but enforce it on full saves."""

    def setUp(self):
        self.router = make_router()

    def test_partial_save_skips_full_clean(self):
        """
        save(update_fields=...) must not call full_clean.
        We verify this by creating an invalid cross-field DB state via raw SQL
        and then confirming that a partial save does not raise.
        """
        customer = make_pppoe_customer(self.router)
        # Force invalid state directly in DB (bypasses Python-level validation)
        Customer.objects.filter(pk=customer.pk).update(hotspot_username="AA:BB:CC:DD")
        customer.refresh_from_db()
        # Partial save must NOT raise even though cross-field state is invalid
        customer.status = "active"
        customer.save(update_fields=["status"])  # should not raise

    def test_full_save_enforces_validation(self):
        from django.core.exceptions import ValidationError
        customer = make_pppoe_customer(self.router)
        customer.hotspot_username = "AA:BB:CC:DD:EE:FF"
        # Full save must call full_clean and raise
        with self.assertRaises(ValidationError):
            customer.save()

    def test_hotspot_customer_with_pppoe_username_invalid(self):
        from django.core.exceptions import ValidationError
        user = User.objects.create_user(username="bad_hs", password="x",
                                        role=User.CUSTOMER, tenant=_default_tenant())
        customer = Customer(
            user=user, full_name="Bad", phone="254788801001",
            connection_type="hotspot", pppoe_username="SKY-BAD-001",
        )
        with self.assertRaises(ValidationError):
            customer.full_clean()

    def test_pppoe_customer_with_hotspot_username_invalid(self):
        from django.core.exceptions import ValidationError
        user = User.objects.create_user(username="bad_pp", password="x",
                                        role=User.CUSTOMER, tenant=_default_tenant())
        customer = Customer(
            user=user, full_name="Bad", phone="254788801002",
            connection_type="pppoe", hotspot_username="AA:BB:CC:DD:EE:FF",
        )
        with self.assertRaises(ValidationError):
            customer.full_clean()

    def test_valid_pppoe_customer_passes_validation(self):
        user = User.objects.create_user(username="good_pp", password="x",
                                        role=User.CUSTOMER, tenant=_default_tenant())
        customer = Customer(
            user=user, full_name="Good", phone="254788801003",
            connection_type="pppoe",
        )
        customer.full_clean()  # must not raise

    def test_valid_hotspot_customer_passes_validation(self):
        user = User.objects.create_user(username="good_hs", password="x",
                                        role=User.CUSTOMER, tenant=_default_tenant())
        customer = Customer(
            user=user, full_name="Good", phone="254788801004",
            connection_type="hotspot",
        )
        customer.full_clean()  # must not raise


# ===========================================================
# 7. Login Rate Throttle
# ===========================================================

class LoginThrottleTests(TestCase):
    """Login endpoint must reject the 6th attempt within one minute."""

    URL = "/api/auth/login/"

    def setUp(self):
        # Reset throttle cache so tests are isolated from each other
        cache.clear()
        self.client = APIClient()
        make_admin("throttle_user")

    def test_first_five_attempts_are_not_throttled(self):
        for _ in range(5):
            resp = self.client.post(self.URL, {"username": "throttle_user", "password": "wrong"})
        # 5th attempt gets 401 (bad credentials), not 429
        self.assertEqual(resp.status_code, 401)

    def test_sixth_attempt_is_throttled(self):
        for _ in range(5):
            self.client.post(self.URL, {"username": "throttle_user", "password": "wrong"})
        resp = self.client.post(self.URL, {"username": "throttle_user", "password": "wrong"})
        self.assertEqual(resp.status_code, 429)

    def test_correct_credentials_within_limit_return_tokens(self):
        resp = self.client.post(
            self.URL,
            # make_admin() sets this password
            {"username": "throttle_user", "password": "adminpass"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("access", resp.data)
        self.assertIn("refresh", resp.data)

    def test_throttle_is_per_ip_not_global(self):
        """Two different clients must have independent throttle counters."""
        client_a = APIClient()
        client_b = APIClient()
        client_b.defaults["REMOTE_ADDR"] = "10.0.0.2"

        for _ in range(5):
            client_a.post(self.URL, {"username": "x", "password": "y"})

        # Client A is throttled
        self.assertEqual(
            client_a.post(self.URL, {"username": "x", "password": "y"}).status_code,
            429,
        )
        # Client B is on a different IP — must not be throttled yet
        self.assertNotEqual(
            client_b.post(self.URL, {"username": "x", "password": "y"}).status_code,
            429,
        )


# ===========================================================
# 8. Customer Detail Serializer
# ===========================================================

class CustomerDetailSerializerTests(TestCase):
    """
    The admin CustomerDetail page reads router_name / subscriptions / vouchers.
    CustomerSerializer never returned them, so those panels rendered empty.
    The retrieve action must supply them; list must stay lightweight.
    """

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.client.force_authenticate(user=make_admin("detail_admin"))

        self.router   = make_router(name="Edge Router 1")
        self.package  = make_package(name="Home 10Mbps")
        self.customer = make_pppoe_customer(
            self.router, phone="254799000111", username_suffix="detail",
        )
        self.subscription = Subscription.objects.create(
            customer=self.customer, package=self.package,
        )
        self.voucher = Voucher.objects.create(
            code="WIFI-DETAIL1",
            subscription=self.subscription,
            expires_at=timezone.now() + timezone.timedelta(days=30),
        )

    def _detail(self, customer=None):
        return self.client.get(f"/api/customers/{(customer or self.customer).id}/")

    def test_detail_returns_router_name(self):
        resp = self._detail()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["router_name"], "Edge Router 1")

    def test_router_name_is_null_when_no_router_assigned(self):
        orphan = Customer.objects.create(
            full_name="No Router", phone="254799000222",
            connection_type="pppoe", router=None,
        )
        resp = self._detail(orphan)
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.data["router_name"])

    def test_detail_returns_subscriptions_with_package_name(self):
        resp = self._detail()
        subs = resp.data["subscriptions"]
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["id"], self.subscription.id)
        self.assertEqual(subs[0]["package_name"], "Home 10Mbps")
        self.assertEqual(subs[0]["status"], "active")
        self.assertIsNotNone(subs[0]["expiry_date"])

    def test_detail_returns_vouchers_flattened_from_subscriptions(self):
        resp = self._detail()
        vouchers = resp.data["vouchers"]
        self.assertEqual(len(vouchers), 1)
        self.assertEqual(vouchers[0]["code"], "WIFI-DETAIL1")
        self.assertTrue(vouchers[0]["is_active"])

    def test_customer_with_no_subscriptions_returns_empty_lists(self):
        bare = Customer.objects.create(
            full_name="Bare", phone="254799000333",
            connection_type="pppoe", router=self.router,
        )
        resp = self._detail(bare)
        self.assertEqual(resp.data["subscriptions"], [])
        self.assertEqual(resp.data["vouchers"], [])

    def test_list_endpoint_stays_lightweight(self):
        """List must not carry the nested payload — 25 rows/page."""
        resp = self.client.get("/api/customers/")
        self.assertEqual(resp.status_code, 200)
        row = resp.data["results"][0]
        self.assertNotIn("subscriptions", row)
        self.assertNotIn("vouchers", row)
        self.assertNotIn("router_name", row)

    def test_detail_query_count_does_not_grow_with_subscriptions(self):
        """Prefetching must keep the detail page at a fixed query count."""
        # 5 = customer (+select_related) + subscriptions prefetch + vouchers
        # prefetch + devices prefetch + one aggregate for data used. On
        # Postgres add 2: the middleware sets the RLS scope on the connection
        # at the start of the request and clears it at the end.
        #
        # Fixed overhead — the point of this test is that it does not grow,
        # and it earned its keep: the usage and devices panels first shipped
        # querying per call and pushed this to ten.
        expected = 5 + (2 if connection.vendor == "postgresql" else 0)
        with self.assertNumQueries(expected):
            self._detail()

        for i in range(4):
            sub = Subscription.objects.create(
                customer=self.customer, package=self.package,
            )
            Voucher.objects.create(
                code=f"WIFI-EXTRA{i}", subscription=sub,
                expires_at=timezone.now() + timezone.timedelta(days=5),
            )

        # 5 = customer (+select_related) + subscriptions prefetch + vouchers
        # prefetch + devices prefetch + one aggregate for data used. On
        # Postgres add 2: the middleware sets the RLS scope on the connection
        # at the start of the request and clears it at the end.
        #
        # Fixed overhead — the point of this test is that it does not grow,
        # and it earned its keep: the usage and devices panels first shipped
        # querying per call and pushed this to ten.
        expected = 5 + (2 if connection.vendor == "postgresql" else 0)
        with self.assertNumQueries(expected):
            resp = self._detail()
        self.assertEqual(len(resp.data["subscriptions"]), 5)
        self.assertEqual(len(resp.data["vouchers"]), 5)

    def test_patch_still_preserves_pppoe_password(self):
        """Write path must keep using CustomerSerializer's password guard."""
        self.customer.pppoe_password = "originalpass"
        self.customer.save()

        resp = self.client.patch(
            f"/api/customers/{self.customer.id}/",
            {"full_name": "Renamed", "pppoe_password": ""},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.full_name, "Renamed")
        self.assertEqual(self.customer.pppoe_password, "originalpass")


# ===========================================================
# 9. Hotspot MAC Collision
# ===========================================================

class HotspotMacCollisionTests(TestCase):
    """
    A device MAC must identify exactly one customer.

    Previously nothing enforced that: the voucher endpoint only checked whether
    the *customer* was bound to a different MAC, never whether the *MAC* was
    held by a different customer. Two customers could share one MAC, after
    which /api/hotspot/status/ resolved the subscriber with .first() and
    returned an arbitrary one of them.
    """

    MAC = "AA:BB:CC:DD:EE:01"
    VALIDATE = "/api/hotspot/validate/"
    STATUS   = "/api/hotspot/status/"

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.router  = make_router(name="Hotspot Router", ip="10.10.0.1")
        self.package = make_hotspot_package()

    def _make_holder(self, suffix, phone, days, status="active"):
        """Customer holding a voucher, with a subscription expiring in `days`."""
        customer = make_hotspot_customer(self.router, phone=phone, username_suffix=suffix)
        sub = Subscription.objects.create(
            customer=customer, package=self.package, status=status,
            expiry_date=timezone.now() + timezone.timedelta(days=days),
        )
        voucher = Voucher.objects.create(
            code=f"WIFI-{suffix.upper()}", subscription=sub,
            expires_at=timezone.now() + timezone.timedelta(days=max(days, 1)),
        )
        return customer, sub, voucher

    # ---- the constraint itself -------------------------------------------

    def test_two_customers_cannot_share_a_mac(self):
        a, _, _ = self._make_holder("a1", "254700000001", 5)
        b, _, _ = self._make_holder("b1", "254700000002", 5)

        a.hotspot_username = self.MAC
        a.save(update_fields=["hotspot_username"])

        b.hotspot_username = self.MAC
        with self.assertRaises(IntegrityError):
            b.save(update_fields=["hotspot_username"])

    def test_many_customers_may_have_blank_hotspot_username(self):
        """The constraint is partial — every PPPoE customer has a blank value."""
        for i in range(3):
            Customer.objects.create(
                full_name=f"PPPoE {i}", phone=f"25471100000{i}",
                connection_type="pppoe", router=self.router,
            )
        self.assertEqual(Customer.objects.filter(hotspot_username="").count(), 3)

    # ---- the write path ---------------------------------------------------

    @patch("billing.views.enable_customer_access")
    def test_device_held_by_an_active_customer_is_refused(self, _):
        holder, _, _ = self._make_holder("h1", "254700000011", 10)
        holder.hotspot_username = self.MAC
        holder.save(update_fields=["hotspot_username"])

        _, _, voucher = self._make_holder("c1", "254700000012", 10)

        resp = self.client.post(
            self.VALIDATE, {"code": voucher.code, "mac_address": self.MAC}, format="json",
        )
        self.assertEqual(resp.status_code, 409)

        holder.refresh_from_db()
        self.assertEqual(holder.hotspot_username, self.MAC, "active holder must keep the device")

    @patch("billing.views.enable_customer_access")
    def test_device_held_by_an_expired_customer_is_released(self, _):
        stale, sub, _ = self._make_holder("s1", "254700000021", 5)
        stale.hotspot_username = self.MAC
        stale.save(update_fields=["hotspot_username"])
        # push the holder's subscription into the past
        Subscription.objects.filter(pk=sub.pk).update(
            status="expired", expiry_date=timezone.now() - timezone.timedelta(days=1),
        )

        claimant, _, voucher = self._make_holder("n1", "254700000022", 10)

        resp = self.client.post(
            self.VALIDATE, {"code": voucher.code, "mac_address": self.MAC}, format="json",
        )
        self.assertEqual(resp.status_code, 200)

        stale.refresh_from_db()
        claimant.refresh_from_db()
        self.assertEqual(stale.hotspot_username, "", "stale binding must be released")
        self.assertEqual(claimant.hotspot_username, self.MAC)

    @patch("billing.views.enable_customer_access")
    def test_releasing_a_stale_binding_is_audited(self, _):
        stale, sub, _ = self._make_holder("s2", "254700000031", 5)
        stale.hotspot_username = self.MAC
        stale.save(update_fields=["hotspot_username"])
        Subscription.objects.filter(pk=sub.pk).update(
            status="expired", expiry_date=timezone.now() - timezone.timedelta(days=1),
        )
        _, _, voucher = self._make_holder("n2", "254700000032", 10)

        self.client.post(
            self.VALIDATE, {"code": voucher.code, "mac_address": self.MAC}, format="json",
        )

        log = AccessAuditLog.objects.filter(customer=stale).first()
        self.assertIsNotNone(log, "releasing a device must leave an audit trail")
        self.assertIn(self.MAC, log.reason)

    @patch("billing.views.enable_customer_access")
    def test_same_customer_revalidating_same_mac_still_works(self, _):
        customer, _, voucher = self._make_holder("r1", "254700000041", 10)
        for _i in range(2):
            resp = self.client.post(
                self.VALIDATE, {"code": voucher.code, "mac_address": self.MAC}, format="json",
            )
            self.assertEqual(resp.status_code, 200)
        customer.refresh_from_db()
        self.assertEqual(customer.hotspot_username, self.MAC)

    # ---- the admin API path -----------------------------------------------

    def test_admin_assigning_a_taken_mac_gets_400_not_500(self):
        """
        Customer.save() runs full_clean(), and since Django 4.1 that validates
        constraints too — raising django ValidationError, which DRF does not
        translate. Without serializer-level validation the admin would get a
        500 for what is ordinary bad input.
        """
        holder, _, _ = self._make_holder("adm1", "254700000061", 10)
        holder.hotspot_username = self.MAC
        holder.save(update_fields=["hotspot_username"])

        other = make_hotspot_customer(self.router, phone="254700000062", username_suffix="adm2")

        admin_client = APIClient()
        admin_client.force_authenticate(user=make_admin("mac_admin"))
        resp = admin_client.patch(
            f"/api/customers/{other.id}/",
            {"hotspot_username": self.MAC},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("hotspot_username", resp.data)

    # ---- the read path ----------------------------------------------------

    @patch("billing.views.enable_customer_access")
    def test_status_lookup_is_unambiguous(self, _):
        """
        The original defect: with two customers on one MAC, this endpoint
        returned whichever row the database produced first.
        """
        claimant, _, voucher = self._make_holder("q1", "254700000051", 10)
        self.client.post(
            self.VALIDATE, {"code": voucher.code, "mac_address": self.MAC}, format="json",
        )
        Invoice.objects.filter(customer=claimant).update(payment_status="paid")

        self.assertEqual(
            Customer.objects.filter(hotspot_username=self.MAC).count(), 1,
            "exactly one customer may hold a MAC",
        )
        resp = self.client.get(self.STATUS, {"mac": self.MAC})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "active")


# ===========================================================
# 10. Phase 1 — Tenancy data model
# ===========================================================

# Every model carrying a tenant FK. Table-driven on purpose: a model added
# later without being scoped fails these tests instead of silently shipping.
SCOPED_MODELS = [
    RouterDevice, Customer, RouterFailoverLog, Package, Subscription, Invoice,
    Voucher, Payment, ExpiryReminderLog, AccessAuditLog, SystemSetting,
    MpesaTransaction, PPPoEUsageSnapshot, PPPoEUsageState, PPPoEUsageRecord,
    HotspotUsageState, HotspotUsageRecord, UsageRecord,
    # Platform billing — scoped so an operator sees their own bills and
    # platform staff see everyone's.
    TenantSubscription, TenantInvoice, TenantPayment,
]


class TenantBackfillTests(TestCase):
    """Migration 0026 must leave every row claimed by the default tenant."""

    def test_default_tenant_exists_after_migrations(self):
        tenant = Tenant.objects.get(slug="skylink")
        self.assertEqual(tenant.status, "active")
        self.assertEqual(tenant.business_name, "Skylink WiFi")
        self.assertEqual(tenant.pppoe_prefix, "SKY")
        self.assertTrue(tenant.public_token)

    def test_every_scoped_model_has_a_tenant_field(self):
        for model in SCOPED_MODELS:
            with self.subTest(model=model.__name__):
                field = model._meta.get_field("tenant")
                self.assertFalse(field.null, f"{model.__name__}.tenant must be NOT NULL")

    def test_no_unclaimed_rows(self):
        for model in SCOPED_MODELS:
            with self.subTest(model=model.__name__):
                self.assertEqual(model.objects.filter(tenant__isnull=True).count(), 0)

    def test_user_tenant_stays_nullable(self):
        """NULL on User means platform staff — it is never tightened."""
        self.assertTrue(User._meta.get_field("tenant").null)


class TenantIntegrityTests(TestCase):
    """Children must never disagree with their parent about the owner."""

    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.get(slug="skylink")
        self.router = make_router()
        self.package = make_package()
        self.customer = make_pppoe_customer(self.router, phone="254733000001", username_suffix="t1")
        self.sub = Subscription.objects.create(customer=self.customer, package=self.package)

    def test_child_rows_inherit_the_same_tenant(self):
        invoice = self.sub.invoice
        payment = Payment.objects.create(
            customer=self.customer, subscription=self.sub, amount=self.package.price, method="cash",
        )
        for obj in (self.router, self.package, self.customer, self.sub, invoice, payment):
            with self.subTest(obj=type(obj).__name__):
                self.assertEqual(obj.tenant_id, self.tenant.id)

    def test_tenant_cannot_be_deleted_while_rows_reference_it(self):
        """PROTECT — removing an operator must not destroy billing history."""
        with self.assertRaises(ProtectedError):
            self.tenant.delete()


class DefaultTenantBridgeTests(TestCase):
    """
    The phase 1 bridge fills `tenant` while one operator exists, and refuses
    once it becomes a guess. That refusal is what forces phase 2 to pass the
    tenant explicitly rather than silently attaching rows to the wrong operator.
    """

    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.get(slug="skylink")

    def test_tenant_is_supplied_automatically_for_a_single_operator(self):
        c = Customer.objects.create(
            full_name="Auto", phone="254733000011", connection_type="pppoe",
        )
        self.assertEqual(c.tenant_id, self.tenant.id)

    def test_ambiguous_write_is_refused_once_a_second_operator_exists(self):
        Tenant.objects.create(name="Acme WiFi", slug="acme")
        with self.assertRaises(RuntimeError):
            Customer.objects.create(
                full_name="Ambiguous", phone="254733000012", connection_type="pppoe",
            )

    def test_explicit_tenant_still_works_with_several_operators(self):
        other = Tenant.objects.create(name="Acme WiFi", slug="acme")
        c = Customer.objects.create(
            full_name="Explicit", phone="254733000013",
            connection_type="pppoe", tenant=other,
        )
        self.assertEqual(c.tenant_id, other.id)

    def test_tenant_generates_its_own_public_token(self):
        a = Tenant.objects.create(name="One", slug="one")
        b = Tenant.objects.create(name="Two", slug="two")
        self.assertTrue(a.public_token and b.public_token)
        self.assertNotEqual(a.public_token, b.public_token)


class TenantScopedUniquenessTests(TestCase):
    """
    Uniqueness that used to be global now lives inside the tenant — that is the
    whole point: one person may subscribe to two different operators.
    """

    def setUp(self):
        cache.clear()
        self.t1 = Tenant.objects.get(slug="skylink")
        self.t2 = Tenant.objects.create(name="Acme WiFi", slug="acme")

    def test_same_phone_allowed_across_operators(self):
        phone = "254744000001"
        Customer.objects.create(full_name="A", phone=phone, connection_type="pppoe", tenant=self.t1)
        Customer.objects.create(full_name="B", phone=phone, connection_type="pppoe", tenant=self.t2)
        self.assertEqual(Customer.objects.filter(phone=phone).count(), 2)

    def test_same_phone_rejected_within_one_operator(self):
        phone = "254744000002"
        Customer.objects.create(full_name="A", phone=phone, connection_type="pppoe", tenant=self.t1)
        with self.assertRaises((IntegrityError, DjangoValidationError)):
            Customer.objects.create(full_name="B", phone=phone, connection_type="pppoe", tenant=self.t1)

    def test_same_hotspot_mac_allowed_across_operators(self):
        mac = "AA:BB:CC:00:11:22"
        Customer.objects.create(full_name="A", phone="254744000011",
                                connection_type="hotspot", hotspot_username=mac, tenant=self.t1)
        Customer.objects.create(full_name="B", phone="254744000012",
                                connection_type="hotspot", hotspot_username=mac, tenant=self.t2)
        self.assertEqual(Customer.objects.filter(hotspot_username=mac).count(), 2)

    def test_same_pppoe_username_rejected_within_one_operator(self):
        Customer.objects.create(full_name="A", phone="254744000021", connection_type="pppoe",
                                pppoe_username="SKY-1111-AAA", tenant=self.t1)
        with self.assertRaises((IntegrityError, DjangoValidationError)):
            Customer.objects.create(full_name="B", phone="254744000022", connection_type="pppoe",
                                    pppoe_username="SKY-1111-AAA", tenant=self.t1)

    def test_blank_pppoe_usernames_do_not_collide(self):
        """The constraint is partial — every hotspot customer has a blank value."""
        for i in range(3):
            Customer.objects.create(full_name=f"H{i}", phone=f"25474400003{i}",
                                    connection_type="hotspot", tenant=self.t1)
        self.assertEqual(
            Customer.objects.filter(tenant=self.t1, pppoe_username="").count(), 3
        )

    def test_same_setting_key_allowed_across_operators(self):
        """This is what routes each operator's payments to their own till."""
        SystemSetting.objects.create(tenant=self.t1, key="MPESA_SHORTCODE", value="111111")
        SystemSetting.objects.create(tenant=self.t2, key="MPESA_SHORTCODE", value="222222")
        self.assertEqual(
            {s.value for s in SystemSetting.objects.filter(key="MPESA_SHORTCODE")},
            {"111111", "222222"},
        )

    def test_setting_key_rejected_twice_within_one_operator(self):
        SystemSetting.objects.create(tenant=self.t1, key="AT_API_KEY", value="a")
        with self.assertRaises((IntegrityError, DjangoValidationError)):
            SystemSetting.objects.create(tenant=self.t1, key="AT_API_KEY", value="b")

    def test_invoice_number_stays_globally_unique(self):
        """
        Deliberately NOT tenant-scoped — the M-Pesa callback carries no tenant
        context and resolves the operator from this value alone.
        """
        self.assertTrue(Invoice._meta.get_field("invoice_number").unique)
        self.assertTrue(Voucher._meta.get_field("code").unique)
        self.assertTrue(MpesaTransaction._meta.get_field("mpesa_receipt").unique)


# ===========================================================
# 11. Tenant isolation — the load-bearing tests
# ===========================================================

class TwoOperatorMixin:
    """Two operators, each with a full set of records and their own admin."""

    def build_operators(self):
        self.t1 = Tenant.objects.get(slug="skylink")
        self.t2 = Tenant.objects.create(name="Acme WiFi", slug="acme")

        # is_staff too: the app mixes two admin checks — a custom role-based
        # IsAdmin, and DRF's IsAdminUser which tests is_staff. Real operator
        # admins need to satisfy both.
        self.admin1 = User.objects.create_user(
            username="admin_one", password="pw", role=User.TENANT_ADMIN,
            tenant=self.t1, is_staff=True)
        self.admin2 = User.objects.create_user(
            username="admin_two", password="pw", role=User.TENANT_ADMIN,
            tenant=self.t2, is_staff=True)

        self.data = {}
        for tag, tenant in (("t1", self.t1), ("t2", self.t2)):
            with tenant_context(tenant):
                router = RouterDevice.objects.create(
                    name=f"{tag}-router", ip_address="10.0.0.1", username="a",
                    password="p", tenant=tenant)
                package = Package.objects.create(
                    name=f"{tag}-package", download_speed=5, upload_speed=2,
                    price=Decimal("500.00"), duration_value=30, duration_unit="days",
                    monthly_data_cap_gb=0, is_hotspot=False, tenant=tenant)
                customer = Customer.objects.create(
                    full_name=f"{tag}-customer", phone=f"2547{tag[-1]}0000001",
                    connection_type="pppoe", router=router, tenant=tenant)
                sub = Subscription.objects.create(
                    customer=customer, package=package, tenant=tenant)
            self.data[tag] = dict(
                tenant=tenant, router=router, package=package,
                customer=customer, sub=sub, invoice=sub.invoice,
            )

    def auth(self, user):
        """
        A token built exactly as the login view builds it, so the tenant
        claim is present and TenantMiddleware behaves as in production.
        RefreshToken.for_user() would skip the serializer and omit the claim.
        """
        client = APIClient()
        token = TenantTokenObtainPairSerializer.get_token(user).access_token
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return client


class TenantIsolationAPITests(TwoOperatorMixin, TestCase):
    """One operator must never see another's records through the API."""

    def setUp(self):
        cache.clear()
        self.build_operators()

    def test_customer_list_shows_only_own_records(self):
        client = self.auth(self.admin1)
        resp = client.get("/api/customers/")
        self.assertEqual(resp.status_code, 200)
        names = [r["full_name"] for r in resp.data["results"]]
        self.assertIn("t1-customer", names)
        self.assertNotIn("t2-customer", names)

    def test_package_list_shows_only_own_records(self):
        client = self.auth(self.admin1)
        resp = client.get("/api/packages/")
        names = [r["name"] for r in resp.data["results"]]
        self.assertEqual(names, ["t1-package"])

    def test_each_operator_sees_their_own_side(self):
        """The mirror case — proves scoping follows the token, not a default."""
        resp = self.auth(self.admin2).get("/api/customers/")
        names = [r["full_name"] for r in resp.data["results"]]
        self.assertEqual(names, ["t2-customer"])

    def test_cannot_retrieve_another_operators_customer(self):
        other_id = self.data["t2"]["customer"].id
        resp = self.auth(self.admin1).get(f"/api/customers/{other_id}/")
        self.assertEqual(resp.status_code, 404)

    def test_cannot_delete_another_operators_customer(self):
        other_id = self.data["t2"]["customer"].id
        resp = self.auth(self.admin1).delete(f"/api/customers/{other_id}/")
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(Customer.objects.all_tenants().filter(id=other_id).exists())

    def test_router_list_shows_only_own_hardware(self):
        resp = self.auth(self.admin1).get("/api/admin/routers/")
        names = [r["name"] for r in resp.data]
        self.assertEqual(names, ["t1-router"])

    def test_revenue_report_counts_only_own_business(self):
        for tag in ("t1", "t2"):
            d = self.data[tag]
            with tenant_context(d["tenant"]):
                Payment.objects.create(
                    customer=d["customer"], subscription=d["sub"],
                    amount=Decimal("500.00"), method="cash", tenant=d["tenant"])
        resp = self.auth(self.admin1).get("/api/reports/revenue/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Decimal(str(resp.data["revenue_summary"]["today"])),
                         Decimal("500.00"))


class TenantIsolationManagerTests(TwoOperatorMixin, TestCase):
    """Scoping must be structural, not applied model by model."""

    def setUp(self):
        cache.clear()
        self.build_operators()

    def test_every_scoped_model_uses_the_filtering_manager(self):
        for model in SCOPED_MODELS:
            with self.subTest(model=model.__name__):
                self.assertIsInstance(
                    model.objects, TenantManager,
                    f"{model.__name__}.objects must scope by tenant",
                )

    def test_queries_are_filtered_inside_a_tenant_context(self):
        for model, key in ((Customer, "customer"), (Package, "package"),
                           (RouterDevice, "router"), (Subscription, "sub")):
            with self.subTest(model=model.__name__):
                with tenant_context(self.t1):
                    ids = set(model.objects.values_list("id", flat=True))
                self.assertIn(self.data["t1"][key].id, ids)
                self.assertNotIn(self.data["t2"][key].id, ids)

    def test_all_tenants_is_the_explicit_opt_out(self):
        """
        `.all_tenants()` escapes the manager filter. With no scope in force
        that is the whole story.
        """
        self.assertEqual(Customer.objects.all_tenants().count(), 2)

    def test_all_tenants_does_not_escape_the_database_scope(self):
        """
        Inside a tenant context, `.all_tenants()` is still bounded by RLS on
        Postgres. That is the backstop doing its job, not a defect: code that
        has declared it is acting for one operator does not get to read across
        every operator by changing manager.

        Genuine cross-operator reads use the `all_tenants()` *context manager*,
        which clears both layers.
        """
        with tenant_context(self.t1):
            self.assertEqual(Customer.objects.count(), 1)

            expected = 2 if connection.vendor != "postgresql" else 1
            self.assertEqual(Customer.objects.all_tenants().count(), expected)

            with all_tenants():
                self.assertEqual(Customer.objects.all_tenants().count(), 2)

    def test_no_context_means_unscoped(self):
        """Platform staff and cross-operator sweeps rely on this."""
        self.assertEqual(Customer.objects.count(), 2)


class RouterIsolationTests(TwoOperatorMixin, TestCase):
    """
    The failure with physical consequences: provisioning a subscriber onto
    another operator's MikroTik.
    """

    def setUp(self):
        cache.clear()
        self.build_operators()

    @patch("billing.router_service.safe_connect_router")
    @patch("billing.router_service.count_pppoe_sessions", return_value=0)
    def test_selection_never_returns_another_operators_router(self, _c, mock_conn):
        mock_conn.return_value = object()
        for tag in ("t1", "t2"):
            with self.subTest(tag=tag):
                router, _api = pick_best_router_for_new_customer(
                    self.data[tag]["customer"])
                self.assertEqual(router.id, self.data[tag]["router"].id)

    @patch("billing.router_service.safe_connect_router")
    def test_working_router_selection_is_scoped(self, mock_conn):
        mock_conn.return_value = object()
        router, _api = pick_working_router(self.data["t1"]["customer"])
        self.assertEqual(router.tenant_id, self.t1.id)

    def test_selection_without_a_tenant_is_refused(self):
        """Refusing beats silently scanning every operator's hardware."""
        with self.assertRaises(ValueError):
            pick_best_router_for_new_customer(None)


class SettingsIsolationTests(TwoOperatorMixin, TestCase):
    """
    Credential isolation. A leak here is worse than a data leak, and RLS cannot
    catch it because a cache hit never reaches the database.
    """

    def setUp(self):
        cache.clear()
        self.build_operators()
        SystemSetting.objects.create(
            tenant=self.t1, key="MPESA_CONSUMER_SECRET", value="secret-one")
        SystemSetting.objects.create(
            tenant=self.t2, key="MPESA_CONSUMER_SECRET", value="secret-two")

    def test_each_operator_reads_their_own_credential(self):
        self.assertEqual(get_setting("MPESA_CONSUMER_SECRET", tenant=self.t1), "secret-one")
        self.assertEqual(get_setting("MPESA_CONSUMER_SECRET", tenant=self.t2), "secret-two")

    def test_cache_does_not_leak_between_operators(self):
        """Second read is served from cache — the poisoning path."""
        get_setting("MPESA_CONSUMER_SECRET", tenant=self.t1)
        get_setting("MPESA_CONSUMER_SECRET", tenant=self.t2)
        self.assertEqual(get_setting("MPESA_CONSUMER_SECRET", tenant=self.t1), "secret-one")
        self.assertEqual(get_setting("MPESA_CONSUMER_SECRET", tenant=self.t2), "secret-two")

    def test_context_selects_the_credential_when_no_argument_given(self):
        with tenant_context(self.t2):
            self.assertEqual(get_setting("MPESA_CONSUMER_SECRET"), "secret-two")

    def test_clearing_one_operators_cache_leaves_the_other(self):
        get_setting("MPESA_CONSUMER_SECRET", tenant=self.t1)
        get_setting("MPESA_CONSUMER_SECRET", tenant=self.t2)
        SystemSetting.objects.filter(tenant=self.t1).update(value="rotated")
        clear_settings_cache(tenant=self.t1)
        self.assertEqual(get_setting("MPESA_CONSUMER_SECRET", tenant=self.t1), "rotated")
        self.assertEqual(get_setting("MPESA_CONSUMER_SECRET", tenant=self.t2), "secret-two")


@skipUnless(connection.vendor == "postgresql", "RLS requires PostgreSQL")
class RowLevelSecurityTests(TwoOperatorMixin, TestCase):
    """
    Proves the database itself refuses cross-tenant rows.

    Deliberately uses raw SQL, bypassing the ORM entirely. Testing through the
    ORM would only re-test the application-layer manager — the whole point of
    RLS is to hold when that layer is wrong or bypassed.

    Skipped on SQLite, which has no RLS. That means it does NOT run in the
    default local/test setup: it must be exercised against Postgres before RLS
    can be considered verified.
    """

    def setUp(self):
        cache.clear()
        self.build_operators()

    def _raw_count(self, table):
        with connection.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            return cur.fetchone()[0]

    def test_force_row_level_security_is_enabled(self):
        """Without FORCE, the table owner bypasses every policy."""
        with connection.cursor() as cur:
            cur.execute(
                "SELECT relrowsecurity, relforcerowsecurity "
                "FROM pg_class WHERE relname = 'billing_customer'"
            )
            enabled, forced = cur.fetchone()
        self.assertTrue(enabled, "RLS not enabled on billing_customer")
        self.assertTrue(forced, "FORCE not set — the owner bypasses the policy")

    def test_raw_sql_cannot_see_another_operators_rows(self):
        with tenant_context(self.t1):
            self.assertEqual(self._raw_count("billing_customer"), 1)
        with tenant_context(self.t2):
            self.assertEqual(self._raw_count("billing_customer"), 1)

    def test_unscoped_connection_sees_everything(self):
        """Platform staff and cross-operator sweeps depend on this."""
        self.assertEqual(self._raw_count("billing_customer"), 2)

    def test_context_does_not_leak_across_transactions(self):
        """
        Guards the CONN_MAX_AGE trap: a plain SET would persist on the pooled
        connection and the next request would inherit the previous operator.
        """
        with tenant_context(self.t1):
            pass
        self.assertEqual(self._raw_count("billing_customer"), 2)

    def test_a_web_request_applies_the_scope_to_postgres(self):
        """
        The gap a real Postgres run exposed.

        The middleware used to set only the Python ContextVar, so during an API
        request app.current_tenant_id was unset, the policy's IS NULL branch
        matched, and the database allowed everything. RLS protected background
        tasks and no web request at all — the appearance of a backstop with
        none of the substance.
        """
        self.auth(self.admin1).get("/api/customers/")

        with connection.cursor() as cur:
            cur.execute("SELECT current_setting('app.current_tenant_id', true)")
            after = cur.fetchone()[0]

        # Cleared once the request finished, so nothing inherits this
        # connection's scope.
        self.assertIn(after, ("", None))

    def test_leaving_a_tenant_block_restores_the_database_scope(self):
        """
        Both layers must move together. Restoring only the ContextVar leaves
        Postgres still filtering to the operator whose block just ended, and
        rows go quietly missing from what reads as an unscoped query.
        """
        with tenant_context(self.t1):
            self.assertEqual(self._raw_count("billing_customer"), 1)

        self.assertEqual(
            self._raw_count("billing_customer"), 2,
            "database scope outlived the tenant_context block",
        )

    def test_all_tenants_really_is_unscoped_at_the_database(self):
        with tenant_context(self.t1):
            self.assertEqual(self._raw_count("billing_customer"), 1)
            with all_tenants():
                self.assertEqual(
                    self._raw_count("billing_customer"), 2,
                    "all_tenants() was still filtered by RLS",
                )
            # ...and the operator scope comes back afterwards.
            self.assertEqual(self._raw_count("billing_customer"), 1)

    def test_policy_covers_every_scoped_table(self):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT tablename FROM pg_policies WHERE policyname = 'tenant_isolation'"
            )
            covered = {row[0] for row in cur.fetchall()}
        expected = {m._meta.db_table for m in SCOPED_MODELS}
        self.assertEqual(expected - covered, set(), "tables missing an RLS policy")


# ===========================================================
# 12. Phase 3 — per-operator payments, endpoints and branding
# ===========================================================

MPESA_KEYS = {
    "MPESA_CONSUMER_KEY": "key",
    "MPESA_CONSUMER_SECRET": "secret",
    "MPESA_SHORTCODE": "111111",
    "MPESA_PASSKEY": "passkey",
}


class PerOperatorMpesaTests(TwoOperatorMixin, TestCase):
    """Subscriber money must settle into the operator's own till."""

    def setUp(self):
        cache.clear()
        self.build_operators()
        for key, value in MPESA_KEYS.items():
            SystemSetting.objects.create(tenant=self.t1, key=key, value=f"t1-{value}")
            SystemSetting.objects.create(tenant=self.t2, key=key, value=f"t2-{value}")

    def test_each_operator_has_their_own_shortcode(self):
        self.assertEqual(get_setting("MPESA_SHORTCODE", tenant=self.t1), "t1-111111")
        self.assertEqual(get_setting("MPESA_SHORTCODE", tenant=self.t2), "t2-111111")

    def test_configured_when_all_keys_present(self):
        self.assertTrue(payments_configured(tenant=self.t1))
        self.assertEqual(missing_mpesa_keys(tenant=self.t1), [])

    def test_missing_keys_are_reported_not_guessed(self):
        SystemSetting.objects.filter(tenant=self.t2, key="MPESA_PASSKEY").delete()
        self.assertFalse(payments_configured(tenant=self.t2))
        self.assertEqual(missing_mpesa_keys(tenant=self.t2), ["MPESA_PASSKEY"])

    def test_stk_push_uses_the_invoices_operator_credentials(self):
        """The charge must appear on the till of the operator who is owed."""
        d = self.data["t2"]
        with tenant_context(d["tenant"]):
            invoice = d["sub"].invoice

        captured = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured["shortcode"] = json["BusinessShortCode"]
            captured["callback"] = json["CallBackURL"]
            class R:
                status_code = 200
                def raise_for_status(self): pass
                def json(self): return {"ResponseCode": "0"}
            return R()

        # PLATFORM_BASE_URL is needed to derive the per-operator callback;
        # without it the task correctly refuses before reaching Daraja.
        with override_settings(PLATFORM_BASE_URL="https://billing.example.com"), \
             patch("billing.mpesa_client.get_mpesa_access_token", return_value="tok"), \
             patch("billing.mpesa_client.requests.post", side_effect=fake_post):
            initiate_stk_push_task(invoice.id, "254700000000")

        self.assertEqual(captured["shortcode"], "t2-111111",
                         "STK pushed against the wrong operator's shortcode")
        self.assertIn(self.t2.public_token, captured["callback"],
                      "callback pointed at the wrong operator")

    def test_callback_url_carries_the_operators_token(self):
        with override_settings(PLATFORM_BASE_URL="https://billing.example.com"):
            url = callback_url_for(tenant=self.t2)
        self.assertIn(self.t2.public_token, url)
        self.assertNotIn(self.t1.public_token, url)

    def test_explicit_callback_url_overrides_the_derived_one(self):
        SystemSetting.objects.create(
            tenant=self.t1, key="MPESA_CALLBACK_URL", value="https://custom/cb/")
        self.assertEqual(callback_url_for(tenant=self.t1), "https://custom/cb/")

    def test_unconfigured_operator_raises_rather_than_charging(self):
        SystemSetting.objects.filter(tenant=self.t2).delete()
        cache.clear()
        with self.assertRaises(PaymentsNotConfigured):
            initiate_stk_push("254700000000", 100, "INV-X", tenant=self.t2)

    def test_stk_task_releases_the_invoice_when_unconfigured(self):
        """
        Otherwise the invoice sticks at "pending" and the duplicate guard
        blocks every retry once the operator finishes onboarding.
        """
        SystemSetting.objects.filter(tenant=self.t2).delete()
        cache.clear()
        d = self.data["t2"]
        invoice = Invoice.objects.all_tenants().get(subscription=d["sub"])

        result = initiate_stk_push_task(invoice.id, "254700000000")

        self.assertFalse(result["success"])
        invoice.refresh_from_db()
        self.assertEqual(invoice.payment_status, "unpaid")


@override_settings(MPESA_ALLOW_LOCAL_CALLBACK=True)
class PerOperatorCallbackTests(TwoOperatorMixin, TestCase):
    """The callback must credit the operator who is actually owed."""

    def setUp(self):
        cache.clear()
        self.build_operators()
        self.client = APIClient()

    def _payload(self, invoice, receipt="RCPT001"):
        return {
            "Body": {"stkCallback": {
                "ResultCode": 0,
                "ResultDesc": "ok",
                "CallbackMetadata": {"Item": [
                    {"Name": "MpesaReceiptNumber", "Value": receipt},
                    {"Name": "Amount", "Value": float(invoice.total_amount)},
                    {"Name": "PhoneNumber", "Value": 254700000000},
                    {"Name": "AccountReference", "Value": invoice.invoice_number},
                ]},
            }}
        }

    @patch("billing.router_service.enable_customer_access")
    def test_callback_on_an_operators_url_credits_that_operator(self, _):
        invoice = Invoice.objects.all_tenants().get(subscription=self.data["t2"]["sub"])
        resp = self.client.post(
            f"/api/mpesa/callback/{self.t2.public_token}/",
            self._payload(invoice), format="json")
        self.assertEqual(resp.status_code, 200)

        tx = MpesaTransaction.objects.all_tenants().get(mpesa_receipt="RCPT001")
        self.assertEqual(tx.tenant_id, self.t2.id)
        payment = Payment.objects.all_tenants().get(reference="RCPT001")
        self.assertEqual(payment.tenant_id, self.t2.id)

    @patch("billing.router_service.enable_customer_access")
    def test_callback_arriving_on_the_wrong_operators_url_is_refused(self, _):
        """
        A misconfigured callback URL must fail loudly, not book one operator's
        payment against another.
        """
        invoice = Invoice.objects.all_tenants().get(subscription=self.data["t2"]["sub"])
        resp = self.client.post(
            f"/api/mpesa/callback/{self.t1.public_token}/",
            self._payload(invoice), format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Payment.objects.all_tenants().filter(reference="RCPT001").exists())

    def test_unknown_token_is_rejected(self):
        resp = self.client.post(
            "/api/mpesa/callback/not-a-real-token/", {}, format="json")
        self.assertEqual(resp.status_code, 404)

    @patch("billing.router_service.enable_customer_access")
    def test_legacy_url_still_resolves_via_invoice_number(self, _):
        """Kept working: changing a live callback URL needs Safaricom approval."""
        invoice = Invoice.objects.all_tenants().get(subscription=self.data["t1"]["sub"])
        resp = self.client.post(
            "/api/mpesa/stk-callback/", self._payload(invoice, "RCPT002"), format="json")
        self.assertEqual(resp.status_code, 200)
        tx = MpesaTransaction.objects.all_tenants().get(mpesa_receipt="RCPT002")
        self.assertEqual(tx.tenant_id, self.t1.id)


class HotspotTenantTokenTests(TwoOperatorMixin, TestCase):
    """
    Same device MAC, two operators. Without the token the lookup is ambiguous —
    the defect recorded in data-model-spec.md §6.
    """

    MAC = "AA:BB:CC:DD:EE:FF"

    def setUp(self):
        cache.clear()
        self.build_operators()
        self.client = APIClient()
        for tag in ("t1", "t2"):
            d = self.data[tag]
            c = d["customer"]
            c.connection_type = "hotspot"
            c.pppoe_username = ""
            c.hotspot_username = self.MAC
            c.save(update_fields=["connection_type", "pppoe_username", "hotspot_username"])
            Invoice.objects.all_tenants().filter(subscription=d["sub"]).update(
                payment_status="paid")

    def test_same_mac_can_exist_under_both_operators(self):
        self.assertEqual(
            Customer.objects.all_tenants().filter(hotspot_username=self.MAC).count(), 2)

    def test_token_selects_the_right_operators_subscriber(self):
        for tag in ("t1", "t2"):
            with self.subTest(tag=tag):
                token = self.data[tag]["tenant"].public_token
                resp = self.client.get("/api/hotspot/status/",
                                       {"mac": self.MAC, "t": token})
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.data["status"], "active")
                self.assertEqual(
                    resp.data["expires_at"].replace(tzinfo=None).date(),
                    self.data[tag]["sub"].expiry_date.date(),
                )

    def test_missing_token_with_several_operators_refuses_to_guess(self):
        resp = self.client.get("/api/hotspot/status/", {"mac": self.MAC})
        self.assertEqual(resp.data["status"], "not_found")

    def test_unknown_token_resolves_to_nothing(self):
        resp = self.client.get("/api/hotspot/status/",
                               {"mac": self.MAC, "t": "bogus"})
        self.assertEqual(resp.data["status"], "not_found")


class PerOperatorBrandingTests(TwoOperatorMixin, TestCase):
    """A subscriber belongs to their operator and has never heard of us."""

    def setUp(self):
        cache.clear()
        self.build_operators()
        self.t2.business_name = "Acme Broadband"
        self.t2.support_phone = "0722000000"
        self.t2.pppoe_prefix = "ACME"
        self.t2.save()

    @patch("billing.signals.notify_customer")
    def test_welcome_message_uses_the_operators_name(self, mock_notify):
        with tenant_context(self.t2):
            Customer.objects.create(
                full_name="New Sub", phone="254755000001",
                connection_type="hotspot", tenant=self.t2)
        message = mock_notify.call_args[0][1]
        self.assertIn("Acme Broadband", message)
        self.assertNotIn("Skylink", message)

    def test_pppoe_username_uses_the_operators_prefix(self):
        with tenant_context(self.t2):
            customer = Customer.objects.create(
                full_name="PPPoE Sub", phone="254755000002",
                connection_type="pppoe", tenant=self.t2)
            username, _password = generate_pppoe_credentials(customer)
        self.assertTrue(username.startswith("ACME-"), username)

    def test_prefixes_do_not_collide_across_operators(self):
        with tenant_context(self.t1):
            c1 = Customer.objects.create(
                full_name="A", phone="254755000011",
                connection_type="pppoe", tenant=self.t1)
            u1, _ = generate_pppoe_credentials(c1)
        with tenant_context(self.t2):
            c2 = Customer.objects.create(
                full_name="B", phone="254755000012",
                connection_type="pppoe", tenant=self.t2)
            u2, _ = generate_pppoe_credentials(c2)
        self.assertTrue(u1.startswith("SKY-"))
        self.assertTrue(u2.startswith("ACME-"))


class PublicEndpointScopingTests(TwoOperatorMixin, TestCase):
    """
    Regressions for defects found auditing phases 1–3.

    Public endpoints carry no JWT, so no middleware sets a tenant context and
    the manager runs unscoped. Anything reading customers there must scope
    explicitly or it reaches across operators.
    """

    MAC = "AA:BB:CC:11:22:33"

    def setUp(self):
        cache.clear()
        self.build_operators()
        self.client = APIClient()

    def _hotspot_customer(self, tenant, phone, suffix, days=10):
        with tenant_context(tenant):
            c = Customer.objects.create(
                full_name=f"HS {suffix}", phone=phone,
                connection_type="hotspot", hotspot_username=self.MAC, tenant=tenant)
            sub = Subscription.objects.create(
                customer=c, package=self.data["t1"]["package"] if tenant == self.t1
                else self.data["t2"]["package"],
                tenant=tenant,
                expiry_date=timezone.now() + timezone.timedelta(days=days))
            v = Voucher.objects.create(
                code=f"WIFI-{suffix}", subscription=sub, tenant=tenant,
                expires_at=timezone.now() + timezone.timedelta(days=days))
        return c, sub, v

    @patch("billing.views.enable_customer_access")
    def test_voucher_validation_ignores_another_operators_device_binding(self, _):
        """
        Operator A validating a voucher must not be blocked by — or release —
        Operator B's customer who happens to share the device MAC.
        """
        # B holds the MAC with a live subscription
        b_customer, _b_sub, _b_v = self._hotspot_customer(
            self.t2, "254766000002", "B", days=30)

        # A's own subscriber claims the same MAC on their side
        a_customer, _a_sub, a_voucher = self._hotspot_customer(
            self.t1, "254766000001", "A", days=30)
        a_customer.hotspot_username = ""
        a_customer.save(update_fields=["hotspot_username"])

        # The portal identifies whose it is. It used to be able to omit this,
        # which is precisely what let a voucher cross operators — see
        # VoucherTenantScopeTests. What this test is about is unchanged: A's
        # own voucher, on A's own portal, must not be blocked by B.
        resp = self.client.post(
            f"/api/hotspot/validate/?t={self.t1.public_token}",
            {"code": a_voucher.code, "mac_address": self.MAC},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, "blocked by another operator's customer")
        b_customer.refresh_from_db()
        self.assertEqual(
            b_customer.hotspot_username, self.MAC,
            "another operator's device binding was released",
        )
        self.assertFalse(
            AccessAuditLog.objects.all_tenants().filter(customer=b_customer).exists(),
            "audit log written against another operator's customer",
        )

    def test_pppoe_renew_derives_the_operators_callback(self):
        """
        Regression: initiate_stk_push was called without a tenant, so
        callback_url_for() had no public_token and raised PaymentsNotConfigured
        even for a fully configured operator.
        """
        for key, value in MPESA_KEYS.items():
            SystemSetting.objects.create(tenant=self.t2, key=key, value=value)

        user = User.objects.create_user(
            username="renew_cust", password="pw", role=User.CUSTOMER, tenant=self.t2)
        customer = self.data["t2"]["customer"]
        customer.user = user
        customer.save(update_fields=["user"])

        captured = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured["callback"] = json["CallBackURL"]
            class R:
                def raise_for_status(self): pass
                def json(self): return {"ResponseCode": "0"}
            return R()

        # Renewing queues the push rather than making it. It used to call
        # Safaricom inside the request — a worker held for however long Daraja
        # took, on a page a customer is watching — so the call this test cares
        # about now happens in the task, and that is where it is followed.
        client = self.auth(user)
        with patch("billing.views.initiate_stk_push_task.delay") as queued:
            resp = client.post(
                "/api/pppoe/renew/",
                {"package_id": self.data["t2"]["package"].id, "phone": "254700000000"},
                format="json",
            )

        self.assertEqual(resp.status_code, 200, getattr(resp, "data", None))
        self.assertTrue(queued.called, "the renewal never reached the payment task")
        invoice_id, phone = queued.call_args.args

        with override_settings(PLATFORM_BASE_URL="https://billing.example.com"), \
             patch("billing.mpesa_client.get_mpesa_access_token", return_value="tok"), \
             patch("billing.mpesa_client.requests.post", side_effect=fake_post):
            initiate_stk_push_task(invoice_id, phone)

        self.assertIn(self.t2.public_token, captured["callback"])


class PublicHotspotPurchaseTests(TwoOperatorMixin, TestCase):
    """
    The walk-up purchase flow. Every step was previously behind IsAdmin or
    IsAuthenticated, so a customer on the captive portal got 403 and could not
    buy anything at all.
    """

    def setUp(self):
        cache.clear()
        self.build_operators()
        self.client = APIClient()

        for tag, tenant in (("t1", self.t1), ("t2", self.t2)):
            with tenant_context(tenant):
                Package.objects.create(
                    tenant=tenant, name=f"{tag}-hotspot-2hr",
                    download_speed=5, upload_speed=2, price=Decimal("50.00"),
                    duration_value=2, duration_unit="hours",
                    monthly_data_cap_gb=0, is_hotspot=True)
            for key, value in MPESA_KEYS.items():
                SystemSetting.objects.create(tenant=tenant, key=key, value=f"{tag}-{value}")

    def _package(self, tenant):
        return Package.objects.all_tenants().get(tenant=tenant, is_hotspot=True)

    # ---- packages ---------------------------------------------------------

    def test_packages_are_readable_without_a_login(self):
        resp = self.client.get("/api/hotspot/packages/", {"t": self.t1.public_token})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([p["name"] for p in resp.data["results"]], ["t1-hotspot-2hr"])

    def test_packages_are_scoped_to_the_token(self):
        resp = self.client.get("/api/hotspot/packages/", {"t": self.t2.public_token})
        self.assertEqual([p["name"] for p in resp.data["results"]], ["t2-hotspot-2hr"])

    def test_pppoe_packages_are_not_offered_on_the_portal(self):
        resp = self.client.get("/api/hotspot/packages/", {"t": self.t1.public_token})
        names = [p["name"] for p in resp.data["results"]]
        self.assertNotIn("t1-package", names)

    def test_public_package_payload_stays_minimal(self):
        resp = self.client.get("/api/hotspot/packages/", {"t": self.t1.public_token})
        # max_devices belongs here: the portal has to be able to say how many
        # phones a package covers, and a customer deciding what to buy needs
        # it. Everything in this set is deliberate — the test exists so a
        # column cannot join the public payload by accident.
        self.assertEqual(
            set(resp.data["results"][0]),
            {"id", "name", "price", "download_speed", "upload_speed",
             "duration_value", "duration_unit", "duration",
             "monthly_data_cap_gb", "max_devices"},
        )

    def test_unknown_token_is_rejected(self):
        resp = self.client.get("/api/hotspot/packages/", {"t": "bogus"})
        self.assertEqual(resp.status_code, 404)

    # ---- purchase ---------------------------------------------------------

    @patch("billing.views.initiate_stk_push_task")
    def test_purchase_creates_customer_subscription_and_invoice(self, mock_task):
        resp = self.client.post("/api/hotspot/purchase/", {
            "t": self.t1.public_token,
            "package_id": self._package(self.t1).id,
            "phone": "0712345678",
        }, format="json")

        self.assertEqual(resp.status_code, 202, resp.data)
        self.assertIn("reference", resp.data)

        customer = Customer.objects.all_tenants().get(phone="254712345678")
        self.assertEqual(customer.tenant_id, self.t1.id)
        self.assertEqual(customer.connection_type, "hotspot")
        self.assertTrue(mock_task.delay.called, "STK push was never scheduled")

    @patch("billing.views.initiate_stk_push_task")
    def test_phone_is_normalised_for_daraja(self, _):
        for entered in ("0712345678", "712345678", "254712345678", "+254 712 345 678"):
            with self.subTest(entered=entered):
                Customer.objects.all_tenants().filter(phone="254712345678").delete()
                resp = self.client.post("/api/hotspot/purchase/", {
                    "t": self.t1.public_token,
                    "package_id": self._package(self.t1).id,
                    "phone": entered,
                }, format="json")
                self.assertEqual(resp.status_code, 202)
                self.assertTrue(
                    Customer.objects.all_tenants().filter(phone="254712345678").exists())

    def test_rubbish_phone_is_refused(self):
        resp = self.client.post("/api/hotspot/purchase/", {
            "t": self.t1.public_token,
            "package_id": self._package(self.t1).id,
            "phone": "12345",
        }, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_cannot_buy_another_operators_package(self):
        resp = self.client.post("/api/hotspot/purchase/", {
            "t": self.t1.public_token,
            "package_id": self._package(self.t2).id,
            "phone": "0712345678",
        }, format="json")
        self.assertEqual(resp.status_code, 404)

    def test_purchase_refused_while_operator_has_no_mpesa_setup(self):
        SystemSetting.objects.filter(tenant=self.t2).delete()
        cache.clear()
        resp = self.client.post("/api/hotspot/purchase/", {
            "t": self.t2.public_token,
            "package_id": self._package(self.t2).id,
            "phone": "0712345678",
        }, format="json")
        self.assertEqual(resp.status_code, 503)

    @patch("billing.views.initiate_stk_push_task")
    def test_repeat_purchase_reuses_the_same_customer(self, _):
        pkg = self._package(self.t1).id
        for _i in range(2):
            self.client.post("/api/hotspot/purchase/", {
                "t": self.t1.public_token, "package_id": pkg, "phone": "0712345678",
            }, format="json")
        self.assertEqual(
            Customer.objects.all_tenants().filter(phone="254712345678").count(), 1)

    # ---- payment status ---------------------------------------------------

    @patch("billing.views.initiate_stk_push_task")
    def test_status_withholds_the_voucher_until_paid(self, _):
        resp = self.client.post("/api/hotspot/purchase/", {
            "t": self.t1.public_token,
            "package_id": self._package(self.t1).id,
            "phone": "0712345678",
        }, format="json")
        poll = self.client.get("/api/hotspot/payment-status/",
                               {"t": self.t1.public_token, "ref": resp.data["reference"]})
        self.assertNotEqual(poll.data["status"], "paid")
        self.assertNotIn("voucher_code", poll.data)

    @patch("billing.views.initiate_stk_push_task")
    @patch("billing.router_service.enable_customer_access")
    def test_voucher_is_returned_once_payment_lands(self, _enable, _task):
        resp = self.client.post("/api/hotspot/purchase/", {
            "t": self.t1.public_token,
            "package_id": self._package(self.t1).id,
            "phone": "0712345678",
        }, format="json")
        ref = resp.data["reference"]

        invoice = Invoice.objects.all_tenants().get(invoice_number=ref)
        with tenant_context(self.t1):
            Payment.objects.create(
                tenant=self.t1, customer=invoice.customer,
                subscription=invoice.subscription,
                amount=invoice.total_amount, method="mpesa", reference="RCPT-HS")

        # The token purchase handed back is what releases the code. Polling
        # with the reference alone answers paid, and nothing more — see
        # HotspotPollTokenTests for why.
        poll = self.client.get("/api/hotspot/payment-status/", {
            "t": self.t1.public_token,
            "ref": ref,
            "token": resp.data["poll_token"],
        })
        self.assertEqual(poll.data["status"], "paid")
        self.assertTrue(poll.data["voucher_code"])

    @patch("billing.views.initiate_stk_push_task")
    def test_reference_from_another_operator_does_not_resolve(self, _):
        resp = self.client.post("/api/hotspot/purchase/", {
            "t": self.t1.public_token,
            "package_id": self._package(self.t1).id,
            "phone": "0712345678",
        }, format="json")
        poll = self.client.get("/api/hotspot/payment-status/",
                               {"t": self.t2.public_token, "ref": resp.data["reference"]})
        self.assertEqual(poll.data["status"], "not_found")


# ===========================================================
# 13. Phase 4 — roles and permissions
# ===========================================================

class RoleConstraintTests(TestCase):
    """
    A NULL tenant means platform staff, and platform staff run unscoped. The
    pairing of role and tenant is therefore a privilege boundary, enforced in
    the database rather than only in application code.
    """

    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.get(slug="skylink")

    def test_operator_account_cannot_have_a_null_tenant(self):
        with self.assertRaises((IntegrityError, DjangoValidationError)):
            User.objects.create_user(
                username="orphan", password="pw",
                role=User.TENANT_ADMIN, tenant=None)

    def test_platform_account_cannot_belong_to_an_operator(self):
        with self.assertRaises((IntegrityError, DjangoValidationError)):
            User.objects.create_user(
                username="confused", password="pw",
                role=User.PLATFORM_OWNER, tenant=self.tenant)

    def test_platform_account_with_no_tenant_is_allowed(self):
        user = make_platform_owner()
        self.assertIsNone(user.tenant_id)
        self.assertTrue(user.is_platform_staff)

    def test_createsuperuser_produces_a_platform_account(self):
        """Otherwise the constraint would make createsuperuser simply fail."""
        user = User.objects.create_superuser(username="root", password="pw")
        self.assertEqual(user.role, User.PLATFORM_OWNER)
        self.assertIsNone(user.tenant_id)

    def test_old_roles_were_migrated_to_operator_roles(self):
        """0031 maps fail-closed: nobody is promoted to platform staff."""
        self.assertEqual(
            set(User.objects.values_list("role", flat=True)) - set(dict(User.ROLE_CHOICES)),
            set(),
        )


class PermissionBoundaryTests(TwoOperatorMixin, TestCase):
    """
    Replaces DRF's IsAdminUser, which checked only `is_staff` — unrelated to
    these roles. It let any Django staff account through fourteen admin
    endpoints and locked out operator admins without the flag.
    """

    ADMIN_URL = "/api/customers/"

    def setUp(self):
        cache.clear()
        self.build_operators()

    def test_operator_admin_may_administer(self):
        resp = self.auth(self.admin1).get(self.ADMIN_URL)
        self.assertEqual(resp.status_code, 200)

    def test_customer_may_not_administer(self):
        customer_user = User.objects.create_user(
            username="sub", password="pw", role=User.CUSTOMER, tenant=self.t1)
        resp = self.auth(customer_user).get(self.ADMIN_URL)
        self.assertEqual(resp.status_code, 403)

    def test_is_staff_alone_does_not_grant_admin(self):
        """The exact hole IsAdminUser left open."""
        sneaky = User.objects.create_user(
            username="sneaky", password="pw",
            role=User.CUSTOMER, tenant=self.t1, is_staff=True)
        resp = self.auth(sneaky).get(self.ADMIN_URL)
        self.assertEqual(resp.status_code, 403)

    def test_operator_admin_without_is_staff_is_still_admitted(self):
        """The other half: IsAdminUser used to lock these accounts out."""
        plain = User.objects.create_user(
            username="plain_admin", password="pw",
            role=User.TENANT_ADMIN, tenant=self.t1, is_staff=False)
        resp = self.auth(plain).get(self.ADMIN_URL)
        self.assertEqual(resp.status_code, 200)

    def test_operator_staff_may_not_reach_admin_only_endpoints(self):
        """
        ADMIN_URL is the customer list, which staff may now READ — that is the
        point of having a staff role at all. What they must not reach is the
        configuration of the business, so this asserts against those instead,
        and against writing a customer rather than seeing one.
        """
        staffer = User.objects.create_user(
            username="clerk", password="pw", role=User.TENANT_STAFF, tenant=self.t1)
        client = self.auth(staffer)

        self.assertEqual(client.get("/api/system/settings/").status_code, 403)
        self.assertEqual(client.get("/api/users/").status_code, 403)
        self.assertEqual(
            client.post(self.ADMIN_URL, {
                "full_name": "Nope", "phone": "254700111888",
                "connection_type": "pppoe",
            }, format="json").status_code,
            403,
        )

    def test_operator_staff_may_read_the_business(self):
        """The other half of the same rule, kept beside it so neither drifts."""
        staffer = User.objects.create_user(
            username="clerk_reader", password="pw",
            role=User.TENANT_STAFF, tenant=self.t1)
        self.assertEqual(self.auth(staffer).get(self.ADMIN_URL).status_code, 200)

    def test_platform_staff_may_act_for_support(self):
        owner = make_platform_owner()
        self.assertEqual(self.auth(owner).get(self.ADMIN_URL).status_code, 200)

    def test_platform_staff_see_every_operator(self):
        owner = make_platform_owner()
        resp = self.auth(owner).get(self.ADMIN_URL)
        names = [r["full_name"] for r in resp.data["results"]]
        self.assertIn("t1-customer", names)
        self.assertIn("t2-customer", names)


class TokenClaimTests(TwoOperatorMixin, TestCase):
    """
    The tenant travels in a signed claim so the middleware can scope a request
    without a database lookup. Previously it authenticated in middleware and
    DRF authenticated again in the view: two user queries per request.
    """

    def setUp(self):
        cache.clear()
        self.build_operators()

    def test_token_carries_operator_and_role(self):
        token = TenantTokenObtainPairSerializer.get_token(self.admin1).access_token
        self.assertEqual(token["tenant_id"], self.t1.id)
        self.assertEqual(token["role"], User.TENANT_ADMIN)

    def test_platform_token_carries_a_null_operator(self):
        token = TenantTokenObtainPairSerializer.get_token(make_platform_owner()).access_token
        self.assertIsNone(token["tenant_id"])

    def test_login_response_states_the_operator(self):
        """Saves the frontend a round-trip just to choose a dashboard."""
        resp = APIClient().post(
            "/api/auth/login/",
            {"username": "admin_one", "password": "pw"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["tenant_id"], self.t1.id)
        self.assertEqual(resp.data["role"], User.TENANT_ADMIN)
        self.assertFalse(resp.data["is_platform_staff"])

    def test_scoping_still_holds_for_a_token_without_the_claim(self):
        """
        Tokens issued before this change carry no claim. Treating that as
        unscoped would hand an operator admin platform-wide visibility for the
        lifetime of their existing token, so the middleware falls back to a
        lookup instead.
        """
        # RefreshToken.for_user() bypasses the serializer, so this token has
        # no tenant claim — exactly the shape of one issued before this change.
        token = RefreshToken.for_user(self.admin1).access_token

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        resp = client.get("/api/customers/")

        self.assertEqual(resp.status_code, 200)
        names = [r["full_name"] for r in resp.data["results"]]
        self.assertEqual(names, ["t1-customer"], "legacy token leaked another operator")

    def test_profile_reports_the_operator(self):
        resp = self.auth(self.admin1).get("/api/auth/profile/")
        self.assertEqual(resp.data["tenant"], self.t1.id)
        self.assertFalse(resp.data["is_platform_staff"])


# ===========================================================
# 14. Phase 5 — platform billing
# ===========================================================

class PlatformBillingMixin(TwoOperatorMixin):
    """Two operators, each on a plan, with a period that has just ended."""

    def build_billing(self):
        self.build_operators()
        self.plan = PlatformPlan.objects.create(
            name="Starter", slug="starter", price=Decimal("2000.00"),
            billing_period_days=30, max_customers=100, max_routers=2)

        self.subs = {}
        for tag, tenant in (("t1", self.t1), ("t2", self.t2)):
            self.subs[tag] = TenantSubscription.objects.create(
                tenant=tenant, plan=self.plan, status="active",
                current_period_start=timezone.now() - timezone.timedelta(days=30),
                current_period_end=timezone.now() - timezone.timedelta(minutes=1),
            )


class PlatformInvoiceGenerationTests(PlatformBillingMixin, TestCase):

    def setUp(self):
        cache.clear()
        self.build_billing()

    def test_invoices_are_issued_when_the_period_ends(self):
        issued = generate_tenant_invoices()
        self.assertEqual(issued, 2)
        self.assertEqual(TenantInvoice.objects.all_tenants().count(), 2)

    def test_invoice_numbers_are_distinguishable_from_subscriber_invoices(self):
        """
        PINV- against INV-. Both resolve an operator from an M-Pesa callback,
        so confusing them would credit the wrong ledger entirely.
        """
        generate_tenant_invoices()
        for invoice in TenantInvoice.objects.all_tenants():
            self.assertTrue(invoice.number.startswith("PINV-"), invoice.number)

    def test_generation_is_idempotent(self):
        """Safe to re-run after a partial failure — nobody is double-billed."""
        generate_tenant_invoices()
        again = generate_tenant_invoices()
        self.assertEqual(again, 0)
        self.assertEqual(TenantInvoice.objects.all_tenants().count(), 2)

    def test_operators_still_in_trial_are_not_billed(self):
        self.subs["t2"].trial_ends_at = timezone.now() + timezone.timedelta(days=7)
        self.subs["t2"].save()
        generate_tenant_invoices()
        self.assertFalse(
            TenantInvoice.objects.all_tenants().filter(tenant=self.t2).exists())

    def test_cancelled_operators_are_not_billed(self):
        self.subs["t2"].status = "cancelled"
        self.subs["t2"].save()
        generate_tenant_invoices()
        self.assertFalse(
            TenantInvoice.objects.all_tenants().filter(tenant=self.t2).exists())

    def test_invoice_charges_the_plan_price(self):
        generate_tenant_invoices()
        invoice = TenantInvoice.objects.all_tenants().filter(tenant=self.t1).first()
        self.assertEqual(invoice.amount, self.plan.price)


class PlatformPaymentTests(PlatformBillingMixin, TestCase):

    def setUp(self):
        cache.clear()
        self.build_billing()
        generate_tenant_invoices()
        self.invoice = TenantInvoice.objects.all_tenants().get(tenant=self.t1)

    def test_payment_settles_the_invoice(self):
        TenantPayment.objects.create(
            tenant=self.t1, invoice=self.invoice,
            amount=self.invoice.amount, method="mpesa", reference="R1")
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "paid")

    def test_payment_rolls_the_billing_period_forward(self):
        old_end = self.subs["t1"].current_period_end
        TenantPayment.objects.create(
            tenant=self.t1, invoice=self.invoice,
            amount=self.invoice.amount, method="mpesa", reference="R2")
        self.subs["t1"].refresh_from_db()
        self.assertGreater(self.subs["t1"].current_period_end, old_end)
        self.assertEqual(self.subs["t1"].status, "active")

    def test_payment_lifts_a_restriction_immediately(self):
        """An operator who pays must not stay locked out until a sweep runs."""
        self.t1.status = "restricted"
        self.t1.save()
        TenantPayment.objects.create(
            tenant=self.t1, invoice=self.invoice,
            amount=self.invoice.amount, method="manual", reference="R3")
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.status, "active")

    def test_overdue_sweep_flags_but_does_not_cut_anyone_off(self):
        """
        Restriction is deliberate and manual — cutting an operator off has
        consequences for subscribers who have done nothing wrong.
        """
        TenantInvoice.objects.all_tenants().update(
            due_date=timezone.now() - timezone.timedelta(days=1))
        flagged = mark_overdue_tenants()

        self.assertEqual(flagged, 2)
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.status, "past_due")
        self.assertNotEqual(self.t1.status, "restricted")


class PlatformBillingAccessTests(PlatformBillingMixin, TestCase):
    """
    Who may see and settle what. The platform ledger must never be writable by
    the operator it bills.
    """

    def setUp(self):
        cache.clear()
        self.build_billing()
        generate_tenant_invoices()

    def test_operator_sees_only_their_own_bills(self):
        resp = self.auth(self.admin1).get("/api/platform/invoices/")
        self.assertEqual(resp.status_code, 200)
        operators = {r["tenant"] for r in resp.data["results"]}
        self.assertEqual(operators, {self.t1.id})

    def test_platform_staff_see_every_operators_bills(self):
        resp = self.auth(make_platform_owner()).get("/api/platform/invoices/")
        operators = {r["tenant"] for r in resp.data["results"]}
        self.assertEqual(operators, {self.t1.id, self.t2.id})

    def test_my_account_reports_what_is_owed(self):
        resp = self.auth(self.admin1).get("/api/platform/my-account/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["subscription"]["plan_name"], "Starter")
        self.assertEqual(Decimal(str(resp.data["amount_due"])), self.plan.price)

    def test_operator_cannot_mark_their_own_bill_paid(self):
        invoice = TenantInvoice.objects.all_tenants().get(tenant=self.t1)
        resp = self.auth(self.admin1).post("/api/platform/payments/", {
            "number": invoice.number, "amount": str(invoice.amount),
        }, format="json")
        self.assertEqual(resp.status_code, 403)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "unpaid")

    def test_platform_staff_may_record_a_payment(self):
        invoice = TenantInvoice.objects.all_tenants().get(tenant=self.t1)
        resp = self.auth(make_platform_owner()).post("/api/platform/payments/", {
            "number": invoice.number, "amount": str(invoice.amount),
            "method": "mpesa", "reference": "RCPT-P1",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "paid")

    def test_wrong_amount_is_refused(self):
        invoice = TenantInvoice.objects.all_tenants().get(tenant=self.t1)
        resp = self.auth(make_platform_owner()).post("/api/platform/payments/", {
            "number": invoice.number, "amount": "1.00",
        }, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_operator_may_read_the_plan_catalogue(self):
        resp = self.auth(self.admin1).get("/api/platform/plans/")
        self.assertEqual(resp.status_code, 200)

    def test_operator_may_not_change_the_plan_catalogue(self):
        resp = self.auth(self.admin1).post("/api/platform/plans/", {
            "name": "Free forever", "slug": "free", "price": "0.00",
        }, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_platform_settings_are_not_tenant_scoped(self):
        """
        The platform's own till lives in a separate table, so an operator
        administering their SystemSettings can never read or overwrite it.
        """
        PlatformSetting.objects.create(key="PLATFORM_MPESA_SHORTCODE", value="999999")
        self.assertEqual(get_platform_setting("PLATFORM_MPESA_SHORTCODE"), "999999")
        # Nothing of the sort leaks into an operator's own settings view.
        self.assertIsNone(get_setting("PLATFORM_MPESA_SHORTCODE", tenant=self.t1))


# ===========================================================
# 15. Phase 6 — suspension
# ===========================================================

class RestrictionScopeTests(PlatformBillingMixin, TestCase):
    """
    What restriction does, and — more importantly — what it must not do.

    An operator's subscribers paid *them* in good faith and are not party to a
    billing dispute with the platform. Restriction therefore stops the
    operator's dashboard and their ability to take on new business, and
    nothing else.
    """

    def setUp(self):
        cache.clear()
        self.build_billing()
        self.client = APIClient()

        with tenant_context(self.t1):
            Package.objects.create(
                tenant=self.t1, name="hs", download_speed=5, upload_speed=2,
                price=Decimal("50.00"), duration_value=2, duration_unit="hours",
                monthly_data_cap_gb=0, is_hotspot=True)
        for key, value in MPESA_KEYS.items():
            SystemSetting.objects.create(tenant=self.t1, key=key, value=value)

    def _restrict(self):
        set_tenant_status(self.t1, "restricted", reason="unpaid", automatic=True)
        self.t1.refresh_from_db()

    # ---- what it stops -----------------------------------------------------

    def test_restricted_operator_loses_their_dashboard(self):
        self._restrict()
        resp = self.auth(self.admin1).get("/api/customers/")
        self.assertEqual(resp.status_code, 403)

    def test_restricted_operator_cannot_add_customers(self):
        self._restrict()
        resp = self.auth(self.admin1).post("/api/customers/", {
            "full_name": "New", "phone": "254799111222", "connection_type": "pppoe",
        }, format="json")
        self.assertEqual(resp.status_code, 403)

    # ---- what it must NOT stop --------------------------------------------

    def test_restricted_operator_still_takes_walk_up_business(self):
        """
        Restriction is dashboard-only.

        This used to assert the opposite — a restricted operator was refused new
        walk-up customers, as leverage over an unpaid invoice. Reversed
        deliberately: the person turned away at a hotspot is not party to the
        dispute, and charging them for it is the wrong trade. What the platform
        withholds is its own product, the dashboard.
        """
        self._restrict()
        pkg = Package.objects.all_tenants().get(tenant=self.t1, is_hotspot=True)
        resp = self.client.post("/api/hotspot/purchase/", {
            "t": self.t1.public_token, "package_id": pkg.id, "phone": "0712345678",
        }, format="json")
        # Not 503-for-being-restricted. Whatever happens next is about the
        # purchase itself — payment configuration, plan caps — not standing.
        self.assertNotEqual(
            resp.data.get("detail"),
            "This provider is not accepting new customers right now.",
        )

    def test_restricted_operator_can_still_see_what_they_owe(self):
        """Locking someone out of the page where they would pay is self-defeating."""
        self._restrict()
        client = self.auth(self.admin1)
        self.assertEqual(client.get("/api/platform/my-account/").status_code, 200)
        self.assertEqual(client.get("/api/platform/invoices/").status_code, 200)

    def test_subscribers_of_a_restricted_operator_keep_their_access(self):
        """The heart of the policy: they paid the operator, not the platform."""
        with tenant_context(self.t1):
            customer = Customer.objects.create(
                tenant=self.t1, full_name="Existing", phone="254799333444",
                connection_type="hotspot", hotspot_username="AA:BB:CC:DD:EE:01")
            sub = Subscription.objects.create(
                tenant=self.t1, customer=customer, package=self.data["t1"]["package"],
                expiry_date=timezone.now() + timezone.timedelta(days=20))
            Invoice.objects.all_tenants().filter(subscription=sub).update(
                payment_status="paid")

        self._restrict()

        resp = self.client.get("/api/hotspot/status/", {
            "mac": "AA:BB:CC:DD:EE:01", "t": self.t1.public_token})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "active")

    def test_background_tasks_keep_running_for_a_restricted_operator(self):
        """Expiry, usage and failover must not stall — subscribers depend on them."""
        self._restrict()
        self.assertEqual(enforce_subscription_expiry(), 0)  # runs without error

    def test_platform_staff_can_still_act_on_a_restricted_operator(self):
        """Support has to be able to help them get un-restricted."""
        self._restrict()
        resp = self.auth(make_platform_owner()).get("/api/customers/")
        self.assertEqual(resp.status_code, 200)

    def test_other_operators_are_unaffected(self):
        self._restrict()
        self.assertEqual(self.auth(self.admin2).get("/api/customers/").status_code, 200)


class RestrictionEscalationTests(PlatformBillingMixin, TestCase):
    """Restriction is never the first an operator hears of a problem."""

    def setUp(self):
        cache.clear()
        self.build_billing()
        generate_tenant_invoices()

    def test_nobody_is_restricted_before_grace_expires(self):
        TenantInvoice.objects.all_tenants().update(
            due_date=timezone.now() - timezone.timedelta(days=Tenant.GRACE_DAYS - 1))
        self.assertEqual(restrict_expired_grace_tenants(), 0)
        self.t1.refresh_from_db()
        self.assertFalse(self.t1.is_restricted)

    def test_restriction_follows_a_fully_elapsed_grace_period(self):
        TenantInvoice.objects.all_tenants().update(
            due_date=timezone.now() - timezone.timedelta(days=Tenant.GRACE_DAYS + 1))
        self.assertEqual(restrict_expired_grace_tenants(), 2)
        self.t1.refresh_from_db()
        self.assertTrue(self.t1.is_restricted)

    def test_restriction_is_recorded_with_a_reason(self):
        TenantInvoice.objects.all_tenants().update(
            due_date=timezone.now() - timezone.timedelta(days=Tenant.GRACE_DAYS + 1))
        restrict_expired_grace_tenants()

        change = TenantStatusChange.objects.filter(tenant=self.t1).first()
        self.assertIsNotNone(change, "restriction left no audit trail")
        self.assertEqual(change.to_status, "restricted")
        self.assertTrue(change.automatic)
        self.assertIn("past due", change.reason)

    def test_reminders_go_out_before_and_after_the_due_date(self):
        self.t1.contact_phone = "254700000000"
        self.t1.save()
        # Only this operator has a contact number, so only they are reminded.
        TenantInvoice.objects.all_tenants().exclude(tenant=self.t1).delete()

        for offset in (-3, 3, 7, 14):
            with self.subTest(days_from_due=offset):
                TenantInvoice.objects.all_tenants().update(
                    due_date=timezone.now() - timezone.timedelta(days=offset))
                self.assertEqual(send_platform_billing_reminders(), 1)

    def test_no_reminder_on_a_day_that_is_not_a_milestone(self):
        self.t1.contact_phone = "254700000000"
        self.t1.save()
        TenantInvoice.objects.all_tenants().update(
            due_date=timezone.now() - timezone.timedelta(days=5))
        self.assertEqual(send_platform_billing_reminders(), 0)

    def test_paying_lifts_the_restriction(self):
        TenantInvoice.objects.all_tenants().update(
            due_date=timezone.now() - timezone.timedelta(days=Tenant.GRACE_DAYS + 1))
        restrict_expired_grace_tenants()

        invoice = TenantInvoice.objects.all_tenants().get(tenant=self.t1)
        TenantPayment.objects.create(
            tenant=self.t1, invoice=invoice, amount=invoice.amount,
            method="mpesa", reference="PAID-1")

        self.t1.refresh_from_db()
        self.assertEqual(self.t1.status, "active")
        self.assertFalse(self.t1.is_restricted)


class ManualStatusControlTests(PlatformBillingMixin, TestCase):

    def setUp(self):
        cache.clear()
        self.build_billing()

    def _url(self, tenant):
        return f"/api/platform/operators/{tenant.id}/status/"

    def test_operator_cannot_change_their_own_standing(self):
        resp = self.auth(self.admin1).post(
            self._url(self.t1), {"status": "active"}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_platform_staff_may_restrict_with_a_reason(self):
        resp = self.auth(make_platform_owner()).post(
            self._url(self.t1),
            {"status": "restricted", "reason": "Unpaid invoice PINV-1"},
            format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.t1.refresh_from_db()
        self.assertTrue(self.t1.is_restricted)

    def test_restricting_without_a_reason_is_refused(self):
        """An unexplained restriction is what makes a dispute unanswerable."""
        resp = self.auth(make_platform_owner()).post(
            self._url(self.t1), {"status": "restricted"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.t1.refresh_from_db()
        self.assertFalse(self.t1.is_restricted)

    def test_manual_change_records_who_did_it(self):
        owner = make_platform_owner()
        self.auth(owner).post(
            self._url(self.t1),
            {"status": "restricted", "reason": "non-payment"}, format="json")

        change = TenantStatusChange.objects.filter(tenant=self.t1).first()
        self.assertEqual(change.changed_by_id, owner.id)
        self.assertFalse(change.automatic)

    def test_history_is_readable_by_platform_staff(self):
        set_tenant_status(self.t1, "restricted", reason="test", automatic=True)
        resp = self.auth(make_platform_owner()).get(self._url(self.t1))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["is_restricted"])
        self.assertEqual(len(resp.data["history"]), 1)

    def test_reinstating_restores_access(self):
        set_tenant_status(self.t1, "restricted", reason="test", automatic=True)
        self.auth(make_platform_owner()).post(
            self._url(self.t1), {"status": "active", "reason": "settled"},
            format="json")
        self.t1.refresh_from_db()
        self.assertEqual(self.auth(self.admin1).get("/api/customers/").status_code, 200)


# ===========================================================
# 16. Findings from auditing phases 4-6
# ===========================================================

class StaleTokenClaimTests(TwoOperatorMixin, TestCase):
    """
    Scoping reads the token claim; permissions read the account. When someone's
    tenant or role changes those disagree until the old token expires, and the
    dangerous direction is real: a demoted platform account carries a null
    tenant claim, which means unscoped.
    """

    def setUp(self):
        cache.clear()
        self.build_operators()

    def test_demoted_platform_account_loses_platform_wide_visibility(self):
        user = User.objects.create_user(
            username="ex_platform", password="pw",
            role=User.PLATFORM_OWNER, tenant=None, is_staff=True)
        token = TenantTokenObtainPairSerializer.get_token(user).access_token

        # Demoted to a single operator; the old token still says "platform".
        user.role = User.TENANT_ADMIN
        user.tenant = self.t1
        user.save()

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        resp = client.get("/api/customers/")

        # Fail closed: one forced sign-in beats a window of stale platform-wide
        # visibility for an account whose access was just revoked.
        self.assertEqual(resp.status_code, 401)

    def test_operator_moved_between_businesses_must_sign_in_again(self):
        token = TenantTokenObtainPairSerializer.get_token(self.admin1).access_token
        self.admin1.tenant = self.t2
        self.admin1.save()

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(client.get("/api/customers/").status_code, 401)

    def test_an_unchanged_account_is_unaffected(self):
        self.assertEqual(self.auth(self.admin1).get("/api/customers/").status_code, 200)

    def test_refreshed_token_still_carries_the_operator(self):
        """Otherwise every refresh would silently drop scoping."""
        refresh = TenantTokenObtainPairSerializer.get_token(self.admin1)
        resp = APIClient().post(
            "/api/auth/refresh/", {"refresh": str(refresh)}, format="json")
        self.assertEqual(resp.status_code, 200)

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
        listing = client.get("/api/customers/")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(
            [r["full_name"] for r in listing.data["results"]], ["t1-customer"])


class PlanLimitTests(PlatformBillingMixin, TestCase):
    """
    Plan caps were stored since phase 5 but never consulted, so a limit meant
    nothing. They restrict growth only.
    """

    def setUp(self):
        cache.clear()
        self.build_billing()
        self.plan.max_customers = 1   # t1 already has one customer
        self.plan.max_routers = 1     # and one router
        self.plan.save()

    def test_adding_a_customer_beyond_the_cap_is_refused(self):
        resp = self.auth(self.admin1).post("/api/customers/", {
            "full_name": "Over cap", "phone": "254799555666",
            "connection_type": "pppoe",
        }, format="json")
        self.assertEqual(resp.status_code, 402)
        self.assertIn("Upgrade", resp.data["detail"])

    def test_adding_a_router_beyond_the_cap_is_refused(self):
        resp = self.auth(self.admin1).post("/api/admin/routers/", {
            "name": "Extra", "ip_address": "10.9.9.9",
            "username": "a", "password": "p",
        }, format="json")
        self.assertEqual(resp.status_code, 402)

    def test_room_under_the_cap_still_allows_growth(self):
        self.plan.max_customers = 10
        self.plan.save()
        resp = self.auth(self.admin1).post("/api/customers/", {
            "full_name": "Within cap", "phone": "254799555777",
            "connection_type": "pppoe",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)

    def test_zero_means_unlimited(self):
        self.plan.max_customers = 0
        self.plan.save()
        resp = self.auth(self.admin1).post("/api/customers/", {
            "full_name": "Unlimited", "phone": "254799555888",
            "connection_type": "pppoe",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)

    def test_being_over_the_cap_never_disconnects_anyone(self):
        """
        Downgrading a plan must not cut off subscribers who are already paying.
        Existing records stay readable and usable; only growth stops.
        """
        self.plan.max_customers = 0
        self.plan.save()
        with tenant_context(self.t1):
            for i in range(3):
                Customer.objects.create(
                    tenant=self.t1, full_name=f"Existing {i}",
                    phone=f"25479966600{i}", connection_type="pppoe")

        self.plan.max_customers = 1  # now well below the actual count
        self.plan.save()

        resp = self.auth(self.admin1).get("/api/customers/")
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(len(resp.data["results"]), 4)

    def test_an_operator_with_no_plan_is_not_capped(self):
        """Being unbilled should not mean being limited."""
        TenantSubscription.objects.all_tenants().filter(tenant=self.t1).delete()
        resp = self.auth(self.admin1).post("/api/customers/", {
            "full_name": "No plan", "phone": "254799555999",
            "connection_type": "pppoe",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)


class RestrictionNoticeTests(PlatformBillingMixin, TestCase):
    """Being locked out with no message turns a billing dispute into a crisis."""

    def setUp(self):
        cache.clear()
        self.build_billing()
        generate_tenant_invoices()
        self.t1.contact_phone = "254700000000"
        self.t1.save()

    @patch("billing.notifications.notify_customer")
    def test_operator_is_told_when_restricted(self, mock_notify):
        TenantInvoice.objects.all_tenants().update(
            due_date=timezone.now() - timezone.timedelta(days=Tenant.GRACE_DAYS + 1))
        restrict_expired_grace_tenants()

        self.assertTrue(mock_notify.called, "restriction sent no notice")
        message = mock_notify.call_args[0][1]
        self.assertIn("locked", message.lower())

    @patch("billing.notifications.notify_customer")
    def test_the_notice_says_customers_are_unaffected(self, mock_notify):
        """The single most important thing for them to know."""
        TenantInvoice.objects.all_tenants().update(
            due_date=timezone.now() - timezone.timedelta(days=Tenant.GRACE_DAYS + 1))
        restrict_expired_grace_tenants()

        message = mock_notify.call_args[0][1]
        self.assertIn("NOT affected", message)


# ===========================================================
# 17. Phase 7 — master dashboard and impersonation
# ===========================================================

class MasterDashboardTests(PlatformBillingMixin, TestCase):
    """Cross-operator views. Unscoped by design, so platform staff only."""

    def setUp(self):
        cache.clear()
        self.build_billing()
        generate_tenant_invoices()

    # ---- access ------------------------------------------------------------

    def test_operator_cannot_see_the_platform_overview(self):
        self.assertEqual(
            self.auth(self.admin1).get("/api/platform/overview/").status_code, 403)

    def test_operator_cannot_list_other_operators(self):
        self.assertEqual(
            self.auth(self.admin1).get("/api/platform/operators/").status_code, 403)

    # ---- overview ----------------------------------------------------------

    def test_overview_counts_every_operator(self):
        resp = self.auth(make_platform_owner()).get("/api/platform/overview/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["operators"]["total"], 2)

    def test_overview_reports_mrr_from_active_plans(self):
        resp = self.auth(make_platform_owner()).get("/api/platform/overview/")
        self.assertEqual(
            Decimal(str(resp.data["platform_revenue"]["mrr"])), self.plan.price * 2)

    def test_overview_reports_what_is_outstanding(self):
        resp = self.auth(make_platform_owner()).get("/api/platform/overview/")
        revenue = resp.data["platform_revenue"]
        self.assertEqual(revenue["outstanding_count"], 2)
        self.assertEqual(
            Decimal(str(revenue["outstanding_total"])), self.plan.price * 2)

    def test_overview_aggregates_the_whole_network(self):
        resp = self.auth(make_platform_owner()).get("/api/platform/overview/")
        network = resp.data["network"]
        self.assertEqual(network["subscribers"], 2)   # one per operator
        self.assertEqual(network["routers"], 2)

    # ---- operator list -----------------------------------------------------

    def test_operator_list_carries_per_operator_numbers(self):
        resp = self.auth(make_platform_owner()).get("/api/platform/operators/")
        self.assertEqual(resp.status_code, 200)
        rows = {r["name"]: r for r in resp.data}
        self.assertEqual(len(rows), 2)
        row = rows[self.t1.business_name or self.t1.name]
        self.assertEqual(row["subscribers"], 1)
        self.assertEqual(row["routers"], 1)
        self.assertEqual(row["plan"], "Starter")
        self.assertEqual(Decimal(str(row["amount_owed"])), self.plan.price)

    def test_operator_list_query_count_does_not_grow_with_operators(self):
        """
        A per-operator query per statistic is 200+ round trips at 50 operators.
        """
        client = self.auth(make_platform_owner())
        # +2 on Postgres for the per-request RLS scope set and clear.
        expected = 8 + (2 if connection.vendor == "postgresql" else 0)

        with self.assertNumQueries(expected):
            client.get("/api/platform/operators/")

        for i in range(5):
            Tenant.objects.create(name=f"Extra {i}", slug=f"extra-{i}")

        with self.assertNumQueries(expected):
            resp = client.get("/api/platform/operators/")
        self.assertEqual(len(resp.data), 7)

    def test_operator_list_can_be_filtered_by_status(self):
        set_tenant_status(self.t2, "restricted", reason="unpaid", automatic=True)
        resp = self.auth(make_platform_owner()).get(
            "/api/platform/operators/", {"status": "restricted"})
        self.assertEqual(len(resp.data), 1)
        self.assertTrue(resp.data[0]["is_restricted"])

    # ---- operator detail ---------------------------------------------------

    def test_detail_shows_billing_and_network(self):
        resp = self.auth(make_platform_owner()).get(
            f"/api/platform/operators/{self.t1.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["plan"]["plan_name"], "Starter")
        self.assertEqual(
            Decimal(str(resp.data["billing"]["amount_owed"])), self.plan.price)
        self.assertEqual(resp.data["network"]["subscribers"], 1)

    def test_detail_flags_an_operator_who_cannot_take_payments_yet(self):
        """The most common reason a new operator is stuck."""
        resp = self.auth(make_platform_owner()).get(
            f"/api/platform/operators/{self.t1.id}/")
        self.assertFalse(resp.data["payments_configured"])


class ImpersonationTests(PlatformBillingMixin, TestCase):
    """
    Support viewing as an operator.

    It grants no new access — it narrows an account that could already see
    everything down to one operator. That is what makes it safe to offer, and
    why every request is recorded.
    """

    def setUp(self):
        cache.clear()
        self.build_billing()
        self.owner = make_platform_owner()

    def _as_operator(self, tenant, reason="support ticket 123"):
        client = self.auth(self.owner)
        client.credentials(
            HTTP_AUTHORIZATION=client._credentials["HTTP_AUTHORIZATION"],
            HTTP_X_IMPERSONATE_TENANT=str(tenant.id),
            HTTP_X_IMPERSONATE_REASON=reason,
        )
        return client

    def test_platform_staff_see_only_that_operator_while_impersonating(self):
        resp = self._as_operator(self.t1).get("/api/customers/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            [r["full_name"] for r in resp.data["results"]], ["t1-customer"])

    def test_switching_operator_switches_what_is_visible(self):
        resp = self._as_operator(self.t2).get("/api/customers/")
        self.assertEqual(
            [r["full_name"] for r in resp.data["results"]], ["t2-customer"])

    def test_without_the_header_they_still_see_everything(self):
        resp = self.auth(self.owner).get("/api/customers/")
        names = {r["full_name"] for r in resp.data["results"]}
        self.assertEqual(names, {"t1-customer", "t2-customer"})

    def test_an_operator_cannot_impersonate_anyone(self):
        """The header must be inert for a non-platform account."""
        client = self.auth(self.admin1)
        client.credentials(
            HTTP_AUTHORIZATION=client._credentials["HTTP_AUTHORIZATION"],
            HTTP_X_IMPERSONATE_TENANT=str(self.t2.id),
        )
        resp = client.get("/api/customers/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            [r["full_name"] for r in resp.data["results"]], ["t1-customer"],
            "an operator reached another operator's data via the header",
        )

    def test_an_unauthenticated_request_cannot_impersonate(self):
        client = APIClient()
        client.credentials(HTTP_X_IMPERSONATE_TENANT=str(self.t1.id))
        self.assertEqual(client.get("/api/customers/").status_code, 401)

    # ---- audit -------------------------------------------------------------

    def test_every_impersonated_request_is_recorded(self):
        self._as_operator(self.t1).get("/api/customers/")

        log = ImpersonationLog.objects.filter(tenant=self.t1).first()
        self.assertIsNotNone(log, "impersonation left no audit trail")
        self.assertEqual(log.platform_user_id, self.owner.id)
        self.assertEqual(log.method, "GET")
        self.assertEqual(log.path, "/api/customers/")
        self.assertEqual(log.reason, "support ticket 123")

    def test_ordinary_platform_requests_are_not_logged(self):
        self.auth(self.owner).get("/api/customers/")
        self.assertEqual(ImpersonationLog.objects.count(), 0)

    def test_the_operator_detail_page_shows_support_access(self):
        self._as_operator(self.t1, reason="checking a payment").get("/api/customers/")

        resp = self.auth(self.owner).get(f"/api/platform/operators/{self.t1.id}/")
        history = resp.data["recent_support_access"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["by"], self.owner.username)
        self.assertEqual(history[0]["reason"], "checking a payment")


class OperatorOnboardingTests(PlatformBillingMixin, TestCase):
    """
    POST /api/platform/operators/ — creating a tenant and its first admin.

    The endpoint mints a working login, and nothing here deletes one, so the
    access rules matter as much as the happy path.
    """

    URL = "/api/platform/operators/"

    def setUp(self):
        cache.clear()
        self.build_billing()
        self.owner = make_platform_owner()

    def payload(self, **overrides):
        data = {
            "name": "Coastal Fibre Ltd",
            "admin_username": "coastadmin",
            "admin_password": "a-good-password",
        }
        data.update(overrides)
        return data

    # ---- access ------------------------------------------------------------

    def test_operator_admin_cannot_create_an_operator(self):
        resp = self.auth(self.admin1).post(self.URL, self.payload(), format="json")
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Tenant.objects.filter(slug="coastal-fibre-ltd").exists())

    def test_platform_staff_can_read_but_not_create(self):
        """
        The split that makes get_permissions() worth having: staff run support,
        the owner onboards businesses.
        """
        staff = User.objects.create_user(
            username="pstaff", password="x", role=User.PLATFORM_STAFF, tenant=None)
        self.assertEqual(self.auth(staff).get(self.URL).status_code, 200)
        self.assertEqual(
            self.auth(staff).post(self.URL, self.payload(), format="json").status_code,
            403,
        )

    def test_anonymous_is_rejected(self):
        self.assertIn(APIClient().post(self.URL, self.payload(), format="json").status_code,
                      (401, 403))

    # ---- creation ----------------------------------------------------------

    def test_owner_creates_tenant_and_its_admin_together(self):
        resp = self.auth(self.owner).post(self.URL, self.payload(), format="json")
        self.assertEqual(resp.status_code, 201, resp.data)

        tenant = Tenant.objects.get(slug="coastal-fibre-ltd")
        self.assertEqual(tenant.status, "trial")
        # business_name falls back to the operator name so subscriber-facing
        # messages are never blank.
        self.assertEqual(tenant.business_name, "Coastal Fibre Ltd")

        admin = User.objects.get(username="coastadmin")
        self.assertEqual(admin.role, User.TENANT_ADMIN)
        self.assertEqual(admin.tenant, tenant)
        self.assertTrue(admin.check_password("a-good-password"))

    def test_slug_is_derived_from_the_name(self):
        self.auth(self.owner).post(self.URL, self.payload(), format="json")
        self.assertTrue(Tenant.objects.filter(slug="coastal-fibre-ltd").exists())

    def test_a_repeated_name_gets_its_own_slug(self):
        self.auth(self.owner).post(self.URL, self.payload(), format="json")
        resp = self.auth(self.owner).post(
            self.URL, self.payload(admin_username="coastadmin2"), format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["slug"], "coastal-fibre-ltd-2")

    def test_plan_is_optional_and_starts_a_trialing_subscription(self):
        resp = self.auth(self.owner).post(
            self.URL, self.payload(plan="starter"), format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        tenant = Tenant.objects.get(slug="coastal-fibre-ltd")
        sub = TenantSubscription.objects.all_tenants().get(tenant=tenant)
        self.assertEqual(sub.plan, self.plan)
        self.assertEqual(sub.status, "trialing")

    def test_without_a_plan_no_subscription_is_created(self):
        self.auth(self.owner).post(self.URL, self.payload(), format="json")
        tenant = Tenant.objects.get(slug="coastal-fibre-ltd")
        self.assertFalse(
            TenantSubscription.objects.all_tenants().filter(tenant=tenant).exists())

    # ---- rejection ---------------------------------------------------------

    def test_duplicate_username_is_a_field_error_not_a_crash(self):
        self.auth(self.owner).post(self.URL, self.payload(), format="json")
        resp = self.auth(self.owner).post(self.URL, self.payload(), format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("admin_username", resp.data)

    def test_short_password_is_rejected(self):
        resp = self.auth(self.owner).post(
            self.URL, self.payload(admin_password="short"), format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("admin_password", resp.data)

    def test_taken_slug_is_rejected(self):
        resp = self.auth(self.owner).post(
            self.URL, self.payload(slug=self.t1.slug), format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("slug", resp.data)

    def test_unknown_plan_is_rejected(self):
        resp = self.auth(self.owner).post(
            self.URL, self.payload(plan="no-such-plan"), format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("plan", resp.data)

    def test_nothing_is_created_when_the_admin_cannot_be(self):
        """
        Tenant and user are one transaction. A tenant with no reachable admin
        is the half-finished state this endpoint exists to avoid.
        """
        User.objects.create_user(
            username="taken", password="x", role=User.TENANT_ADMIN, tenant=self.t1)
        before = Tenant.objects.count()
        resp = self.auth(self.owner).post(
            self.URL, self.payload(admin_username="taken"), format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Tenant.objects.count(), before)

    # ---- the new operator is properly isolated -----------------------------

    def test_the_new_admin_sees_only_their_own_empty_business(self):
        self.auth(self.owner).post(self.URL, self.payload(), format="json")
        admin = User.objects.get(username="coastadmin")
        resp = self.auth(admin).get("/api/customers/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 0)


class ImpersonationCorsTests(TestCase):
    """
    A browser will not send the impersonation headers unless the preflight
    names them.

    This is worth a test because every server-side check passes while it is
    broken: the preflight itself returns 200, the endpoint works from curl, and
    the API tests here drive Django directly and never perform a preflight at
    all. Only a real browser refuses, and it refuses by never sending the
    request — so the frontend reports a connection problem and the cause looks
    unrelated.
    """

    ORIGIN = "http://localhost:3000"

    def preflight(self, requested):
        return self.client.options(
            "/api/customers/",
            HTTP_ORIGIN=self.ORIGIN,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS=requested,
        )

    def test_preflight_allows_the_impersonation_tenant_header(self):
        allowed = self.preflight("authorization,x-impersonate-tenant")[
            "access-control-allow-headers"].lower()
        self.assertIn("x-impersonate-tenant", allowed)

    def test_preflight_allows_the_impersonation_reason_header(self):
        allowed = self.preflight("authorization,x-impersonate-reason")[
            "access-control-allow-headers"].lower()
        self.assertIn("x-impersonate-reason", allowed)

    def test_the_ordinary_headers_are_still_allowed(self):
        """The custom entries are added to the defaults, not substituted."""
        allowed = self.preflight("authorization,content-type")[
            "access-control-allow-headers"].lower()
        self.assertIn("authorization", allowed)
        self.assertIn("content-type", allowed)


# =====================================================
# 18. Phase 1 — accounts, passwords, roles
# =====================================================

class PasswordChangeTests(TwoOperatorMixin, TestCase):
    """Self-service password change, and what it must invalidate."""

    URL = "/api/auth/change-password/"

    def setUp(self):
        cache.clear()
        self.build_operators()

    def test_requires_the_current_password(self):
        resp = self.auth(self.admin1).post(
            self.URL, {"current_password": "wrong", "new_password": "N3wPassphrase!x"},
            format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("current_password", resp.data)

    def test_rejects_a_weak_new_password(self):
        resp = self.auth(self.admin1).post(
            self.URL, {"current_password": "pw", "new_password": "12345678"},
            format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("new_password", resp.data)

    def test_rejects_reusing_the_same_password(self):
        resp = self.auth(self.admin1).post(
            self.URL, {"current_password": "pw", "new_password": "pw"},
            format="json")
        self.assertEqual(resp.status_code, 400)

    def test_changes_the_password(self):
        resp = self.auth(self.admin1).post(
            self.URL, {"current_password": "pw", "new_password": "N3wPassphrase!x"},
            format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.admin1.refresh_from_db()
        self.assertTrue(self.admin1.check_password("N3wPassphrase!x"))

    def test_existing_tokens_stop_working(self):
        """
        The point of the token_version claim.

        Without it a password change would leave tokens minted against the old
        password working for up to a day — refresh tokens live that long and
        there is no blacklist app installed.
        """
        client = self.auth(self.admin1)                     # token issued now
        self.assertEqual(client.get("/api/auth/profile/").status_code, 200)

        self.admin1.set_password("N3wPassphrase!x")
        self.admin1.save(update_fields=["password"])
        self.admin1.invalidate_sessions()

        self.assertEqual(client.get("/api/auth/profile/").status_code, 401)

    def test_the_caller_gets_a_usable_token_back(self):
        """Succeeding must not bounce you to the login screen."""
        resp = self.auth(self.admin1).post(
            self.URL, {"current_password": "pw", "new_password": "N3wPassphrase!x"},
            format="json")
        fresh = APIClient()
        fresh.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
        self.assertEqual(fresh.get("/api/auth/profile/").status_code, 200)


class OperatorPasswordResetTests(PlatformBillingMixin, TestCase):
    """The owner-driven forgotten-password path."""

    def setUp(self):
        cache.clear()
        self.build_billing()
        self.owner = make_platform_owner()

    def url(self, tenant):
        return f"/api/platform/operators/{tenant.id}/reset-password/"

    def test_operator_cannot_reset_anyone(self):
        resp = self.auth(self.admin1).post(self.url(self.t1), {}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_platform_staff_cannot_reset(self):
        """Minting working credentials for someone else's business is owner-only."""
        staff = User.objects.create_user(
            username="pstaff2", password="x", role=User.PLATFORM_STAFF, tenant=None)
        resp = self.auth(staff).post(self.url(self.t1), {}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_owner_resets_and_the_new_password_works(self):
        resp = self.auth(self.owner).post(self.url(self.t1), {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        temp = resp.data["temporary_password"]
        self.assertEqual(resp.data["username"], self.admin1.username)

        self.admin1.refresh_from_db()
        self.assertTrue(self.admin1.check_password(temp))
        self.assertTrue(self.admin1.must_change_password)

    def test_reset_ends_existing_sessions(self):
        """
        A reset prompted by a suspected compromise is worthless if the suspect
        session keeps working.
        """
        victim = self.auth(self.admin1)
        self.assertEqual(victim.get("/api/auth/profile/").status_code, 200)

        self.auth(self.owner).post(self.url(self.t1), {}, format="json")

        self.assertEqual(victim.get("/api/auth/profile/").status_code, 401)

    def test_reset_is_audited(self):
        self.auth(self.owner).post(
            self.url(self.t1), {"reason": "phoned in, verified"}, format="json")
        log = AdminActionLog.objects.filter(action=AdminActionLog.RESET_PASSWORD).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor, self.owner)
        self.assertEqual(log.target_user, self.admin1)
        self.assertEqual(log.detail, "phoned in, verified")

    def test_unknown_operator_is_404(self):
        resp = self.auth(self.owner).post(
            "/api/platform/operators/999999/reset-password/", {}, format="json")
        self.assertEqual(resp.status_code, 404)

    def test_ambiguous_when_the_operator_has_two_admins(self):
        User.objects.create_user(
            username="second_admin", password="x",
            role=User.TENANT_ADMIN, tenant=self.t1)
        resp = self.auth(self.owner).post(self.url(self.t1), {}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("usernames", resp.data)

    def test_naming_the_account_resolves_the_ambiguity(self):
        other = User.objects.create_user(
            username="second_admin", password="x",
            role=User.TENANT_ADMIN, tenant=self.t1)
        resp = self.auth(self.owner).post(
            self.url(self.t1), {"username": other.username}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["username"], other.username)

    def test_cannot_reset_an_account_belonging_to_another_operator(self):
        resp = self.auth(self.owner).post(
            self.url(self.t1), {"username": self.admin2.username}, format="json")
        self.assertEqual(resp.status_code, 404)


class TenantUserManagementTests(TwoOperatorMixin, TestCase):
    """An operator admin managing their own staff — and only their own."""

    URL = "/api/users/"

    def setUp(self):
        cache.clear()
        self.build_operators()

    def test_lists_only_this_operators_users(self):
        resp = self.auth(self.admin1).get(self.URL)
        self.assertEqual(resp.status_code, 200)
        names = {u["username"] for u in resp.data["results"]}
        self.assertIn(self.admin1.username, names)
        self.assertNotIn(self.admin2.username, names)

    def test_creates_staff_inside_the_callers_operator(self):
        resp = self.auth(self.admin1).post(
            self.URL,
            {"username": "newstaff", "password": "S0meLongPassphrase", "role": "tenant_staff"},
            format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        created = User.objects.get(username="newstaff")
        self.assertEqual(created.tenant, self.t1)
        self.assertEqual(created.role, User.TENANT_STAFF)
        # Someone else chose this password, so it must be replaced on first use.
        self.assertTrue(created.must_change_password)

    def test_a_supplied_tenant_id_cannot_plant_a_user_elsewhere(self):
        """The write path forces the tenant; the queryset filter alone would not."""
        self.auth(self.admin1).post(
            self.URL,
            {"username": "planted", "password": "S0meLongPassphrase",
             "role": "tenant_staff", "tenant": self.t2.id},
            format="json")
        self.assertEqual(User.objects.get(username="planted").tenant, self.t1)

    def test_a_platform_role_cannot_be_created_here(self):
        """
        A platform role means a NULL tenant, which means unscoped. Creating one
        through a tenant-scoped endpoint would be a privilege escalation.
        """
        resp = self.auth(self.admin1).post(
            self.URL,
            {"username": "sneaky", "password": "S0meLongPassphrase",
             "role": "platform_owner"},
            format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(User.objects.filter(username="sneaky").exists())

    def test_cannot_reach_another_operators_user(self):
        resp = self.auth(self.admin1).patch(
            f"{self.URL}{self.admin2.id}/", {"is_active": False}, format="json")
        self.assertEqual(resp.status_code, 404)
        self.admin2.refresh_from_db()
        self.assertTrue(self.admin2.is_active)

    def test_tenant_staff_cannot_manage_users(self):
        staff = User.objects.create_user(
            username="plainstaff", password="x",
            role=User.TENANT_STAFF, tenant=self.t1)
        self.assertEqual(self.auth(staff).get(self.URL).status_code, 403)

    def test_disabling_ends_that_users_sessions(self):
        target = User.objects.create_user(
            username="tobedisabled", password="S0meLongPassphrase",
            role=User.TENANT_STAFF, tenant=self.t1)
        theirs = self.auth(target)
        self.assertEqual(theirs.get("/api/auth/profile/").status_code, 200)

        self.auth(self.admin1).delete(f"{self.URL}{target.id}/")

        target.refresh_from_db()
        self.assertFalse(target.is_active)
        self.assertEqual(theirs.get("/api/auth/profile/").status_code, 401)

    def test_delete_disables_rather_than_deletes(self):
        target = User.objects.create_user(
            username="keepme", password="x", role=User.TENANT_STAFF, tenant=self.t1)
        self.auth(self.admin1).delete(f"{self.URL}{target.id}/")
        self.assertTrue(User.objects.filter(pk=target.pk).exists())

    def test_cannot_disable_yourself(self):
        resp = self.auth(self.admin1).delete(f"{self.URL}{self.admin1.id}/")
        self.assertEqual(resp.status_code, 400)
        self.admin1.refresh_from_db()
        self.assertTrue(self.admin1.is_active)


class ProfileUpdateTests(TwoOperatorMixin, TestCase):
    URL = "/api/auth/profile/"

    def setUp(self):
        cache.clear()
        self.build_operators()

    def test_can_change_own_username(self):
        resp = self.auth(self.admin1).patch(
            self.URL, {"username": "renamed_admin"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.admin1.refresh_from_db()
        self.assertEqual(self.admin1.username, "renamed_admin")

    def test_username_change_is_audited(self):
        self.auth(self.admin1).patch(self.URL, {"username": "renamed_admin"}, format="json")
        log = AdminActionLog.objects.filter(action=AdminActionLog.CHANGE_USERNAME).first()
        self.assertIsNotNone(log)
        self.assertIn("renamed_admin", log.detail)

    def test_a_taken_username_is_rejected(self):
        resp = self.auth(self.admin1).patch(
            self.URL, {"username": self.admin2.username}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_role_and_tenant_are_not_self_service(self):
        self.auth(self.admin1).patch(
            self.URL, {"role": "platform_owner", "tenant": None}, format="json")
        self.admin1.refresh_from_db()
        self.assertEqual(self.admin1.role, User.TENANT_ADMIN)
        self.assertEqual(self.admin1.tenant, self.t1)

    def test_profile_reports_operator_status(self):
        resp = self.auth(self.admin1).get(self.URL)
        self.assertEqual(resp.data["tenant_status"], self.t1.status)


class OperatorDetailUpdateTests(PlatformBillingMixin, TestCase):
    def setUp(self):
        cache.clear()
        self.build_billing()
        self.owner = make_platform_owner()

    def url(self, tenant):
        return f"/api/platform/operators/{tenant.id}/"

    def test_owner_can_correct_details_after_onboarding(self):
        resp = self.auth(self.owner).patch(
            self.url(self.t1),
            {"business_name": "Corrected Name", "support_phone": "0799000111"},
            format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.business_name, "Corrected Name")

    def test_platform_staff_cannot_edit(self):
        staff = User.objects.create_user(
            username="pstaff3", password="x", role=User.PLATFORM_STAFF, tenant=None)
        resp = self.auth(staff).patch(
            self.url(self.t1), {"business_name": "Nope"}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_operator_cannot_edit_themselves_here(self):
        resp = self.auth(self.admin1).patch(
            self.url(self.t1), {"business_name": "Nope"}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_slug_and_status_are_not_editable(self):
        """Both are identity other things resolve against; status has its own audited endpoint."""
        original_slug, original_status = self.t1.slug, self.t1.status
        self.auth(self.owner).patch(
            self.url(self.t1),
            {"slug": "hijacked", "status": "restricted"}, format="json")
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.slug, original_slug)
        self.assertEqual(self.t1.status, original_status)

    def test_edits_are_audited(self):
        self.auth(self.owner).patch(
            self.url(self.t1), {"business_name": "Corrected Name"}, format="json")
        self.assertTrue(
            AdminActionLog.objects.filter(action=AdminActionLog.UPDATE_OPERATOR).exists())


# =====================================================
# 19. Phase 2 — operator lifecycle: the audit gaps and warnings
# =====================================================

class StatusAuditCompletenessTests(PlatformBillingMixin, TestCase):
    """
    Every transition leaves a row.

    Two paths used to write Tenant.status directly and so left no
    TenantStatusChange behind: the overdue sweep, which starts every
    escalation, and being reinstated by paying, which ends one. The history an
    operator gets shown in a dispute had a beginning and an end missing from it.
    """

    def setUp(self):
        cache.clear()
        self.build_billing()
        generate_tenant_invoices()

    def test_going_past_due_is_recorded(self):
        TenantInvoice.objects.all_tenants().update(
            due_date=timezone.now() - timezone.timedelta(days=1))
        mark_overdue_tenants()

        change = TenantStatusChange.objects.filter(
            tenant=self.t1, to_status="past_due").first()
        self.assertIsNotNone(change, "the overdue sweep left no audit row")
        self.assertTrue(change.automatic)
        self.assertEqual(change.from_status, "active")
        self.assertIn("past its due date", change.reason)

    def test_being_reinstated_by_paying_is_recorded(self):
        set_tenant_status(self.t1, "restricted", reason="unpaid", automatic=True)
        invoice = TenantInvoice.objects.all_tenants().filter(tenant=self.t1).first()

        with tenant_context(self.t1):
            TenantPayment.objects.create(
                tenant=self.t1, invoice=invoice, amount=invoice.amount,
                method="mpesa", reference="RCT999")

        self.t1.refresh_from_db()
        self.assertEqual(self.t1.status, "active")

        change = TenantStatusChange.objects.filter(
            tenant=self.t1, to_status="active").first()
        self.assertIsNotNone(change, "reinstatement by payment left no audit row")
        self.assertEqual(change.from_status, "restricted")
        self.assertIn("RCT999", change.reason)

    def test_the_full_escalation_reads_as_one_story(self):
        """active -> past_due -> restricted -> active, all four visible."""
        TenantInvoice.objects.all_tenants().update(
            due_date=timezone.now() - timezone.timedelta(days=Tenant.GRACE_DAYS + 1))
        mark_overdue_tenants()
        restrict_expired_grace_tenants()

        invoice = TenantInvoice.objects.all_tenants().filter(tenant=self.t1).first()
        with tenant_context(self.t1):
            TenantPayment.objects.create(
                tenant=self.t1, invoice=invoice, amount=invoice.amount,
                method="mpesa", reference="RCT1000")

        moves = list(
            TenantStatusChange.objects.filter(tenant=self.t1)
            .order_by("created_at").values_list("from_status", "to_status")
        )
        self.assertEqual(
            moves,
            [("active", "past_due"), ("past_due", "restricted"), ("restricted", "active")],
        )


class OperatorWarningTests(PlatformBillingMixin, TestCase):
    """A notice that changes nothing but is on the record."""

    def setUp(self):
        cache.clear()
        self.build_billing()
        self.owner = make_platform_owner()

    def url(self, tenant):
        return f"/api/platform/operators/{tenant.id}/warn/"

    def test_operator_cannot_warn_anyone(self):
        resp = self.auth(self.admin1).post(
            self.url(self.t1), {"message": "hi"}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_a_warning_needs_something_to_say(self):
        resp = self.auth(self.owner).post(self.url(self.t1), {"message": "  "}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_warning_does_not_change_standing(self):
        before = self.t1.status
        resp = self.auth(self.owner).post(
            self.url(self.t1), {"message": "Please settle invoice PINV-1"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.status, before)
        self.assertFalse(self.t1.is_restricted)

    def test_warning_lands_in_the_same_history(self):
        self.auth(self.owner).post(
            self.url(self.t1), {"message": "Please settle invoice PINV-1"}, format="json")
        entry = TenantStatusChange.objects.filter(tenant=self.t1).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.from_status, entry.to_status)
        self.assertIn("Warning:", entry.reason)
        self.assertEqual(entry.changed_by, self.owner)
        self.assertFalse(entry.automatic)

    def test_says_so_when_there_is_no_number_to_send_to(self):
        self.t1.contact_phone = ""
        self.t1.save(update_fields=["contact_phone"])
        resp = self.auth(self.owner).post(
            self.url(self.t1), {"message": "Settle up"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data["delivered"])
        self.assertIn("No contact phone", resp.data["reason_no_delivery"])

    def test_a_failed_send_still_records_the_warning(self):
        self.t1.contact_phone = "254700000000"
        self.t1.save(update_fields=["contact_phone"])
        with patch("billing.views.notify_customer", side_effect=RuntimeError("gateway down")):
            resp = self.auth(self.owner).post(
                self.url(self.t1), {"message": "Settle up"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data["delivered"])
        self.assertTrue(
            TenantStatusChange.objects.filter(tenant=self.t1, reason__contains="Warning:").exists())

    def test_unknown_operator_is_404(self):
        resp = self.auth(self.owner).post(
            "/api/platform/operators/999999/warn/", {"message": "x"}, format="json")
        self.assertEqual(resp.status_code, 404)


# =====================================================
# 20. Phase 3 — router event history and platform health
# =====================================================

class RouterEventTests(TwoOperatorMixin, TestCase):
    """Transitions are recorded; steady state is not."""

    def setUp(self):
        cache.clear()
        self.build_operators()
        self.router = self.data["t1"]["router"]

    def test_a_router_going_down_is_recorded(self):
        self.router.record_health(True)
        RouterEvent.objects.all_tenants().all().delete()

        self.router.record_health(
            False, error="TCP unreachable", cause=RouterEvent.CAUSE_UNREACHABLE)

        event = RouterEvent.objects.all_tenants().get()
        self.assertEqual(event.kind, RouterEvent.WENT_OFFLINE)
        self.assertEqual(event.cause, RouterEvent.CAUSE_UNREACHABLE)
        self.assertEqual(event.detail, "TCP unreachable")

    def test_repeated_probes_of_a_down_router_add_nothing(self):
        """
        The whole reason for recording transitions only. The sweep runs every
        two minutes, so logging each probe would add ~720 rows per router per
        day, every one repeating the row before it.
        """
        self.router.record_health(True)
        RouterEvent.objects.all_tenants().all().delete()

        for _ in range(10):
            self.router.record_health(False, error="still down")

        self.assertEqual(RouterEvent.objects.all_tenants().count(), 1)

    def test_coming_back_is_recorded_and_clears_the_error(self):
        self.router.record_health(False, error="TCP unreachable")
        self.router.record_health(True)

        self.router.refresh_from_db()
        self.assertTrue(self.router.is_online)
        self.assertEqual(self.router.last_error, "")
        self.assertTrue(
            RouterEvent.objects.all_tenants()
            .filter(kind=RouterEvent.CAME_ONLINE).exists())

    def test_record_health_reports_whether_it_changed(self):
        self.router.record_health(True)
        self.assertTrue(self.router.record_health(False, error="down"))
        self.assertFalse(self.router.record_health(False, error="down"))

    def test_an_auth_failure_is_distinguishable_from_unreachable(self):
        """
        A router that answers and rejects the credentials is a configuration
        problem; one that cannot be reached is a network problem. Collapsing
        them loses the distinction that decides who fixes it.
        """
        self.router.record_health(True)
        self.router.record_health(
            False, error="invalid user name or password",
            cause=RouterEvent.CAUSE_AUTH_FAILED)
        event = RouterEvent.objects.all_tenants().filter(
            kind=RouterEvent.WENT_OFFLINE).first()
        self.assertEqual(event.cause, RouterEvent.CAUSE_AUTH_FAILED)

    def test_events_are_scoped_to_their_operator(self):
        other = self.data["t2"]["router"]
        # is_online defaults to False, so both must come up first — recording
        # "offline" on an already-offline router is correctly not a transition
        # and writes nothing.
        self.router.record_health(True)
        other.record_health(True)
        self.router.record_health(False, error="down")
        other.record_health(False, error="down")

        with tenant_context(self.t1):
            names = {e.router_id for e in RouterEvent.objects.all()}
        self.assertEqual(names, {self.router.id})

    def test_history_survives_a_second_failure(self):
        """
        last_error holds one string, so each failure destroyed the previous one.
        That is the gap this table exists to fill.
        """
        self.router.record_health(True)
        self.router.record_health(False, error="first failure")
        self.router.record_health(True)
        self.router.record_health(False, error="second failure")

        details = list(
            RouterEvent.objects.all_tenants()
            .filter(kind=RouterEvent.WENT_OFFLINE)
            .order_by("created_at").values_list("detail", flat=True)
        )
        self.assertEqual(details, ["first failure", "second failure"])


class RouterUptimeTests(TwoOperatorMixin, TestCase):
    def setUp(self):
        cache.clear()
        self.build_operators()
        self.router = self.data["t1"]["router"]

    def _event(self, kind, minutes_ago):
        event = RouterEvent.objects.create(
            tenant=self.t1, router=self.router, kind=kind, detail="")
        # auto_now_add ignores an assigned value, so it is set afterwards.
        RouterEvent.objects.filter(pk=event.pk).update(
            created_at=timezone.now() - timezone.timedelta(minutes=minutes_ago))
        return event

    def test_a_router_with_no_history_reports_its_current_state(self):
        self.router.is_online = True
        self.router.save(update_fields=["is_online"])
        result = router_uptime(self.router, timezone.now() - timezone.timedelta(days=1))
        self.assertEqual(result["uptime_percent"], 100.0)
        self.assertEqual(result["outages"], 0)

    def test_a_closed_outage_is_counted(self):
        self._event(RouterEvent.WENT_OFFLINE, 60)
        self._event(RouterEvent.CAME_ONLINE, 30)

        result = router_uptime(self.router, timezone.now() - timezone.timedelta(hours=2))
        self.assertEqual(result["outages"], 1)
        # 30 minutes down out of 120.
        self.assertAlmostEqual(result["downtime_seconds"], 1800, delta=60)
        self.assertAlmostEqual(result["uptime_percent"], 75.0, delta=1)

    def test_an_ongoing_outage_counts_up_to_now(self):
        self._event(RouterEvent.WENT_OFFLINE, 30)
        self.router.is_online = False
        self.router.save(update_fields=["is_online"])

        result = router_uptime(self.router, timezone.now() - timezone.timedelta(hours=1))
        self.assertEqual(result["outages"], 1)
        self.assertAlmostEqual(result["downtime_seconds"], 1800, delta=60)

    def test_state_before_the_window_is_carried_in(self):
        """
        A router that went down before the window and is still down was down for
        all of it — not up until the first event inside the window says otherwise.
        """
        self._event(RouterEvent.WENT_OFFLINE, 300)     # 5 hours ago
        self.router.is_online = False
        self.router.save(update_fields=["is_online"])

        result = router_uptime(self.router, timezone.now() - timezone.timedelta(hours=1))
        self.assertAlmostEqual(result["uptime_percent"], 0.0, delta=2)


class RouterEventsEndpointTests(TwoOperatorMixin, TestCase):
    URL = "/api/admin/routers/events/"

    def setUp(self):
        cache.clear()
        self.build_operators()
        self.router = self.data["t1"]["router"]

    def test_returns_events_and_availability(self):
        self.router.record_health(True)
        self.router.record_health(False, error="TCP unreachable")

        resp = self.auth(self.admin1).get(self.URL)
        self.assertEqual(resp.status_code, 200)
        router = resp.data["routers"][0]
        self.assertIn("availability", router)
        self.assertTrue(any(e["detail"] == "TCP unreachable" for e in router["events"]))

    def test_does_not_show_another_operators_routers(self):
        other = self.data["t2"]["router"]
        other.record_health(False, error="down")

        resp = self.auth(self.admin1).get(self.URL)
        names = {r["name"] for r in resp.data["routers"]}
        self.assertNotIn(other.name, names)

    def test_days_is_clamped(self):
        resp = self.auth(self.admin1).get(self.URL, {"days": 9999})
        self.assertEqual(resp.data["days"], 90)


class PlatformHealthTests(PlatformBillingMixin, TestCase):
    URL = "/api/platform/health/"

    def setUp(self):
        cache.clear()
        self.build_billing()
        self.owner = make_platform_owner()

    def test_operator_cannot_see_platform_health(self):
        self.assertEqual(self.auth(self.admin1).get(self.URL).status_code, 403)

    def test_an_offline_router_names_its_operator(self):
        """"A router is down" is not actionable on this side without whose."""
        router = self.data["t1"]["router"]
        router.record_health(False, error="TCP unreachable")

        resp = self.auth(self.owner).get(self.URL)
        self.assertEqual(resp.status_code, 200)
        entry = next(r for r in resp.data["routers_offline"] if r["router"] == router.name)
        self.assertEqual(entry["operator_id"], self.t1.id)
        self.assertEqual(entry["last_error"], "TCP unreachable")

    def test_operators_who_cannot_take_money_are_listed(self):
        resp = self.auth(self.owner).get(self.URL)
        ids = {o["operator_id"] for o in resp.data["payments_unconfigured"]}
        self.assertIn(self.t1.id, ids)

    def test_operators_owing_are_listed(self):
        set_tenant_status(self.t1, "past_due", reason="test", automatic=True)
        resp = self.auth(self.owner).get(self.URL)
        ids = {o["operator_id"] for o in resp.data["operators_owing"]}
        self.assertIn(self.t1.id, ids)

    def test_all_clear_is_false_when_anything_needs_attention(self):
        resp = self.auth(self.owner).get(self.URL)
        self.assertFalse(resp.data["all_clear"])


class RouterEventPruningTests(TwoOperatorMixin, TestCase):
    def setUp(self):
        cache.clear()
        self.build_operators()
        self.router = self.data["t1"]["router"]

    def test_old_events_are_removed_and_recent_ones_kept(self):
        old = RouterEvent.objects.create(
            tenant=self.t1, router=self.router, kind=RouterEvent.WENT_OFFLINE)
        RouterEvent.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=200))
        RouterEvent.objects.create(
            tenant=self.t1, router=self.router, kind=RouterEvent.CAME_ONLINE)

        removed = prune_router_events_task(days=90)

        self.assertEqual(removed, 1)
        self.assertEqual(RouterEvent.objects.all_tenants().count(), 1)


# =====================================================
# 21. Phase 4 — platform analytics
# =====================================================

class PlatformAnalyticsTests(PlatformBillingMixin, TestCase):
    URL = "/api/platform/analytics/"

    def setUp(self):
        cache.clear()
        self.build_billing()
        self.owner = make_platform_owner()

    # ---- access ------------------------------------------------------------

    def test_operator_cannot_read_platform_analytics(self):
        self.assertEqual(self.auth(self.admin1).get(self.URL).status_code, 403)

    # ---- shape -------------------------------------------------------------

    def test_every_day_in_the_window_is_present(self):
        """
        Gap-filled deliberately. A missing day renders as a drop to zero rather
        than as a day nothing happened, which is a different claim.
        """
        resp = self.auth(self.owner).get(self.URL, {"days": 14})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["series"]), 14)

        days = [p["day"] for p in resp.data["series"]]
        self.assertEqual(days, sorted(days))
        self.assertEqual(len(set(days)), 14)

    def test_days_is_clamped_to_a_year(self):
        resp = self.auth(self.owner).get(self.URL, {"days": 100000})
        self.assertEqual(resp.data["days"], 365)

    def test_a_junk_day_count_does_not_error(self):
        resp = self.auth(self.owner).get(self.URL, {"days": ""})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["days"], 30)

    # ---- content -----------------------------------------------------------

    def test_platform_revenue_lands_on_the_day_it_was_paid(self):
        invoice = TenantInvoice.objects.all_tenants().filter(tenant=self.t1).first()
        if invoice is None:
            generate_tenant_invoices()
            invoice = TenantInvoice.objects.all_tenants().filter(tenant=self.t1).first()

        with tenant_context(self.t1):
            TenantPayment.objects.create(
                tenant=self.t1, invoice=invoice, amount=Decimal("2000.00"),
                method="mpesa", reference="AN1")

        resp = self.auth(self.owner).get(self.URL, {"days": 7})
        today = timezone.localtime(timezone.now()).date().isoformat()
        point = next(p for p in resp.data["series"] if p["day"] == today)
        self.assertEqual(point["platform_revenue"], 2000.0)
        self.assertEqual(resp.data["totals"]["platform_revenue"], 2000.0)

    def test_the_operator_line_carries_in_operators_created_earlier(self):
        """
        Cumulative, not counted from the window's first day — otherwise the
        line starts at zero and implies the platform began whenever the range
        happens to start.
        """
        old = Tenant.objects.create(name="Long Standing", slug="long-standing")
        Tenant.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=400))

        resp = self.auth(self.owner).get(self.URL, {"days": 7})
        series = resp.data["series"]

        # Day one already knows about the operator that predates the window.
        self.assertGreaterEqual(series[0]["operators"], 1)
        # And the line never goes backwards.
        counts = [p["operators"] for p in series]
        self.assertEqual(counts, sorted(counts))
        # By the end it accounts for everyone.
        self.assertEqual(counts[-1], Tenant.objects.count())

    def test_narrowing_to_one_operator_excludes_the_others(self):
        invoice = TenantInvoice.objects.all_tenants().filter(tenant=self.t2).first()
        if invoice is None:
            generate_tenant_invoices()
            invoice = TenantInvoice.objects.all_tenants().filter(tenant=self.t2).first()

        with tenant_context(self.t2):
            TenantPayment.objects.create(
                tenant=self.t2, invoice=invoice, amount=Decimal("500.00"),
                method="mpesa", reference="AN2")

        resp = self.auth(self.owner).get(self.URL, {"days": 7, "tenant": self.t1.id})
        self.assertEqual(resp.data["totals"]["platform_revenue"], 0.0)
        self.assertEqual(resp.data["operator"], self.t1.business_name or self.t1.name)

    def test_unknown_operator_is_404(self):
        resp = self.auth(self.owner).get(self.URL, {"tenant": 999999})
        self.assertEqual(resp.status_code, 404)

    def test_subscribers_added_counts_new_customers(self):
        with tenant_context(self.t1):
            Customer.objects.create(
                full_name="Analytics Person", phone="254799000111",
                connection_type="pppoe", tenant=self.t1)

        resp = self.auth(self.owner).get(self.URL, {"days": 7})
        self.assertGreaterEqual(resp.data["totals"]["subscribers_added"], 1)

    # ---- cost --------------------------------------------------------------

    def test_query_count_does_not_grow_with_the_window(self):
        """
        These are cross-tenant aggregates over tables that only grow. Bucketing
        in SQL rather than looping in Python is what keeps this flat, and a
        regression to per-day or per-operator queries would not otherwise be
        visible until it was slow in production.
        """
        self.auth(self.owner).get(self.URL, {"days": 7})   # warm any lazy setup

        with CaptureQueriesContext(connection) as short:
            self.auth(self.owner).get(self.URL, {"days": 7})
        with CaptureQueriesContext(connection) as long:
            self.auth(self.owner).get(self.URL, {"days": 365})

        self.assertEqual(len(short.captured_queries), len(long.captured_queries))

    def test_query_count_does_not_grow_with_the_number_of_operators(self):
        with CaptureQueriesContext(connection) as before:
            self.auth(self.owner).get(self.URL, {"days": 30})

        for i in range(5):
            Tenant.objects.create(name=f"Extra {i}", slug=f"extra-{i}")

        with CaptureQueriesContext(connection) as after:
            self.auth(self.owner).get(self.URL, {"days": 30})

        self.assertEqual(len(before.captured_queries), len(after.captured_queries))


# =====================================================
# 22. Phase 5 — stations
# =====================================================

class StationScopingTests(TwoOperatorMixin, TestCase):
    """
    Router selection must never leave the subscriber's own site.

    This is the same shape as the cross-tenant provisioning bug the expansion
    plan calls out as having physical consequences: a router in another town
    cannot carry this subscriber, so moving them there does not fail over — it
    takes them offline while reporting success.
    """

    def setUp(self):
        cache.clear()
        self.build_operators()

        with tenant_context(self.t1):
            self.kilifi = Station.objects.create(tenant=self.t1, name="Kilifi Town")
            self.mtwapa = Station.objects.create(tenant=self.t1, name="Mtwapa")

            self.k1 = RouterDevice.objects.create(
                tenant=self.t1, name="kilifi-1", ip_address="10.1.0.1",
                username="a", password="p", priority=1, station=self.kilifi)
            self.k2 = RouterDevice.objects.create(
                tenant=self.t1, name="kilifi-2", ip_address="10.1.0.2",
                username="a", password="p", priority=2, station=self.kilifi)
            self.m1 = RouterDevice.objects.create(
                tenant=self.t1, name="mtwapa-1", ip_address="10.2.0.1",
                username="a", password="p", priority=1, station=self.mtwapa)

            self.customer = Customer.objects.create(
                tenant=self.t1, full_name="Kilifi Person", phone="254700900001",
                connection_type="pppoe", router=self.k1)

    def test_only_same_station_routers_are_considered(self):
        routers = list(_tenant_routers(self.t1.id, self.kilifi.id))
        self.assertEqual({r.name for r in routers}, {"kilifi-1", "kilifi-2"})

    def test_a_subscribers_station_comes_from_their_router(self):
        self.assertEqual(_station_of(self.customer), self.kilifi.id)

    def test_no_router_means_no_narrowing(self):
        stray = Customer.objects.create(
            tenant=self.t1, full_name="Unassigned", phone="254700900002",
            connection_type="pppoe")
        self.assertIsNone(_station_of(stray))

    @patch("billing.router_service.safe_connect_router")
    def test_failover_stays_at_the_subscribers_station(self, connect):
        """The whole point: Kilifi must fail over to Kilifi, never to Mtwapa."""
        connect.side_effect = lambda r: object() if r.name == "kilifi-2" else None

        router, api = pick_failover_router(
            exclude_router_id=self.k1.id, customer=self.customer)

        self.assertIsNotNone(router)
        self.assertEqual(router.name, "kilifi-2")

    @patch("billing.router_service.safe_connect_router")
    def test_failover_refuses_rather_than_cross_a_station(self, connect):
        """
        Every Kilifi router is down and Mtwapa is up. The correct answer is
        None. Returning the Mtwapa router would look like a successful
        failover and leave the subscriber with no connection.
        """
        connect.side_effect = lambda r: object() if r.station_id == self.mtwapa.id else None

        router, api = pick_failover_router(
            exclude_router_id=self.k1.id, customer=self.customer)

        self.assertIsNone(router, "failover crossed into another station")

    @patch("billing.router_service.safe_connect_router")
    def test_a_working_router_is_chosen_from_the_right_station(self, connect):
        connect.side_effect = lambda r: object() if r.station_id == self.kilifi.id else None
        router, api = pick_working_router(customer=self.customer)
        self.assertEqual(router.station_id, self.kilifi.id)

    @patch("billing.router_service.safe_connect_router")
    def test_an_operator_without_stations_is_unaffected(self, connect):
        """
        The single-site case, which is most operators. Nothing about their
        behaviour changes because they never made a station.
        """
        plain = self.data["t2"]["customer"]
        connect.side_effect = lambda r: object()
        router, api = pick_working_router(customer=plain)
        self.assertIsNotNone(router)
        self.assertEqual(router.tenant_id, self.t2.id)

    @patch("billing.router_service.safe_connect_router")
    def test_station_never_widens_past_the_operator(self, connect):
        """Station narrows within a tenant; it must not become a way out of one."""
        connect.side_effect = lambda r: object()
        routers = list(_tenant_routers(self.t1.id, self.kilifi.id))
        self.assertTrue(all(r.tenant_id == self.t1.id for r in routers))

    @patch("billing.router_service.safe_connect_router")
    def test_a_new_subscriber_can_be_steered_to_a_station(self, connect):
        connect.side_effect = lambda r: object()
        with patch("billing.router_service.count_pppoe_sessions", return_value=0):
            router, api = pick_best_router_for_new_customer(
                tenant_id=self.t1.id, station_id=self.mtwapa.id)
        self.assertIsNotNone(router)
        self.assertEqual(router.station_id, self.mtwapa.id)

    @patch("billing.router_service.safe_connect_router")
    def test_an_existing_subscribers_station_beats_the_argument(self, connect):
        """
        Re-homing must not move somebody towns because a caller passed the
        wrong station.
        """
        connect.side_effect = lambda r: object()
        with patch("billing.router_service.count_pppoe_sessions", return_value=0):
            router, api = pick_best_router_for_new_customer(
                customer=self.customer, station_id=self.mtwapa.id)
        self.assertEqual(router.station_id, self.kilifi.id)


class StationApiTests(TwoOperatorMixin, TestCase):
    URL = "/api/stations/"

    def setUp(self):
        cache.clear()
        self.build_operators()
        with tenant_context(self.t1):
            self.kilifi = Station.objects.create(tenant=self.t1, name="Kilifi Town")
        with tenant_context(self.t2):
            self.theirs = Station.objects.create(tenant=self.t2, name="Their Site")

    def test_lists_only_this_operators_stations(self):
        resp = self.auth(self.admin1).get(self.URL)
        self.assertEqual(resp.status_code, 200)
        names = {s["name"] for s in resp.data["results"]}
        self.assertIn("Kilifi Town", names)
        self.assertNotIn("Their Site", names)

    def test_creating_a_station_attaches_it_to_the_caller(self):
        resp = self.auth(self.admin1).post(
            self.URL, {"name": "Mtwapa", "code": "MTW"}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        created = Station.objects.all_tenants().get(name="Mtwapa")
        self.assertEqual(created.tenant, self.t1)

    def test_a_supplied_tenant_is_ignored(self):
        self.auth(self.admin1).post(
            self.URL, {"name": "Planted", "tenant": self.t2.id}, format="json")
        self.assertEqual(
            Station.objects.all_tenants().get(name="Planted").tenant, self.t1)

    def test_cannot_reach_another_operators_station(self):
        resp = self.auth(self.admin1).patch(
            f"{self.URL}{self.theirs.id}/", {"name": "Hijacked"}, format="json")
        self.assertEqual(resp.status_code, 404)

    def test_duplicate_name_within_an_operator_is_rejected(self):
        resp = self.auth(self.admin1).post(
            self.URL, {"name": "Kilifi Town"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_two_operators_may_each_have_a_kilifi(self):
        resp = self.auth(self.admin2).post(
            self.URL, {"name": "Kilifi Town"}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)

    def test_a_station_with_routers_cannot_be_deleted(self):
        with tenant_context(self.t1):
            RouterDevice.objects.create(
                tenant=self.t1, name="at-kilifi", ip_address="10.9.0.1",
                username="a", password="p", station=self.kilifi)

        resp = self.auth(self.admin1).delete(f"{self.URL}{self.kilifi.id}/")
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(Station.objects.all_tenants().filter(pk=self.kilifi.pk).exists())

    def test_an_empty_station_can_be_deleted(self):
        resp = self.auth(self.admin1).delete(f"{self.URL}{self.kilifi.id}/")
        self.assertEqual(resp.status_code, 204)

    def test_counts_are_reported_per_station(self):
        with tenant_context(self.t1):
            router = RouterDevice.objects.create(
                tenant=self.t1, name="counted", ip_address="10.9.0.2",
                username="a", password="p", station=self.kilifi, is_online=False)
            Customer.objects.create(
                tenant=self.t1, full_name="Counted", phone="254700900009",
                connection_type="pppoe", router=router)

        resp = self.auth(self.admin1).get(self.URL)
        row = next(s for s in resp.data["results"] if s["id"] == self.kilifi.id)
        self.assertEqual(row["routers"], 1)
        self.assertEqual(row["routers_offline"], 1)
        self.assertEqual(row["subscribers"], 1)

    def test_deleting_a_station_never_deletes_its_routers(self):
        """SET_NULL, not CASCADE — a site is a label, not an owner of hardware."""
        with tenant_context(self.t1):
            router = RouterDevice.objects.create(
                tenant=self.t1, name="survivor", ip_address="10.9.0.3",
                username="a", password="p", station=self.kilifi)
        self.kilifi.delete()
        router.refresh_from_db()
        self.assertIsNone(router.station_id)


# =====================================================
# 23. Platform-side M-Pesa setup for an operator
# =====================================================

class OperatorMpesaSetupTests(PlatformBillingMixin, TestCase):
    """
    The owner finishing an operator's payment setup for them.

    Onboarding is not done when the account exists — it is done when money can
    reach them, and that step is gated on Safaricom. An operator waiting on
    Daraja looks perfectly healthy from their own dashboard: nothing errors,
    there is simply no revenue.
    """

    def setUp(self):
        cache.clear()
        self.build_billing()
        self.owner = make_platform_owner()

    def url(self, tenant):
        return f"/api/platform/operators/{tenant.id}/mpesa/"

    def mpesa_test_url(self, tenant):
        return f"/api/platform/operators/{tenant.id}/mpesa/test/"

    # ---- access ------------------------------------------------------------

    def test_operator_cannot_read_another_operators_setup(self):
        resp = self.auth(self.admin1).get(self.url(self.t2))
        self.assertEqual(resp.status_code, 403)

    def test_platform_staff_may_read_but_not_write(self):
        """Reading is safe because every secret comes back masked. Writing decides
        whose bank account a subscriber's money lands in."""
        staff = User.objects.create_user(
            username="pstaff_mpesa", password="x",
            role=User.PLATFORM_STAFF, tenant=None)
        self.assertEqual(self.auth(staff).get(self.url(self.t1)).status_code, 200)
        self.assertEqual(
            self.auth(staff).put(
                self.url(self.t1), {"MPESA_SHORTCODE": "4321"}, format="json"
            ).status_code,
            403,
        )

    def test_unknown_operator_is_404(self):
        resp = self.auth(self.owner).get("/api/platform/operators/999999/mpesa/")
        self.assertEqual(resp.status_code, 404)

    # ---- reading -----------------------------------------------------------

    def test_reports_what_is_still_missing(self):
        resp = self.auth(self.owner).get(self.url(self.t1))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data["configured"])
        self.assertTrue(resp.data["missing"])

    def test_surfaces_the_callback_url_to_register_with_safaricom(self):
        resp = self.auth(self.owner).get(self.url(self.t1))
        self.assertIn("callback_url", resp.data)

    def test_secrets_are_never_returned_in_readable_form(self):
        self.auth(self.owner).put(
            self.url(self.t1),
            {"MPESA_CONSUMER_SECRET": "super-secret", "MPESA_PASSKEY": "pass-key"},
            format="json")

        resp = self.auth(self.owner).get(self.url(self.t1))
        self.assertEqual(resp.data["MPESA_CONSUMER_SECRET"], "********")
        self.assertEqual(resp.data["MPESA_PASSKEY"], "********")
        self.assertNotIn("super-secret", str(resp.data))

    # ---- writing -----------------------------------------------------------

    def test_owner_can_configure_an_operator(self):
        resp = self.auth(self.owner).put(
            self.url(self.t1),
            {
                "MPESA_ENV": "sandbox",
                "MPESA_CONSUMER_KEY": "ck",
                "MPESA_CONSUMER_SECRET": "cs",
                "MPESA_SHORTCODE": "600000",
                "MPESA_PASSKEY": "pk",
            },
            format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["missing"], [])
        self.assertEqual(
            get_setting("MPESA_SHORTCODE", tenant=self.t1), "600000")

    def test_writes_land_on_the_named_operator_only(self):
        self.auth(self.owner).put(
            self.url(self.t1), {"MPESA_SHORTCODE": "111111"}, format="json")
        self.assertEqual(get_setting("MPESA_SHORTCODE", tenant=self.t1), "111111")
        self.assertIn(get_setting("MPESA_SHORTCODE", default="", tenant=self.t2), ("", None))

    def test_a_masked_value_leaves_the_secret_alone(self):
        self.auth(self.owner).put(
            self.url(self.t1), {"MPESA_CONSUMER_SECRET": "original"}, format="json")
        self.auth(self.owner).put(
            self.url(self.t1),
            {"MPESA_CONSUMER_SECRET": "********", "MPESA_SHORTCODE": "222222"},
            format="json")
        self.assertEqual(get_setting("MPESA_CONSUMER_SECRET", tenant=self.t1), "original")
        self.assertEqual(get_setting("MPESA_SHORTCODE", tenant=self.t1), "222222")

    def test_messaging_credentials_are_not_settable_here(self):
        """This endpoint is about payments; the rest is the operator's own page."""
        self.auth(self.owner).put(
            self.url(self.t1), {"AT_API_KEY": "should-not-land"}, format="json")
        self.assertIn(get_setting("AT_API_KEY", default="", tenant=self.t1), ("", None))

    def test_configuring_is_audited_without_recording_the_values(self):
        self.auth(self.owner).put(
            self.url(self.t1),
            {"MPESA_CONSUMER_SECRET": "do-not-log-me", "MPESA_SHORTCODE": "333333"},
            format="json")

        log = AdminActionLog.objects.filter(
            action=AdminActionLog.CONFIGURE_PAYMENTS).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.target_tenant, self.t1)
        self.assertIn("MPESA_SHORTCODE", log.detail)
        self.assertNotIn("do-not-log-me", log.detail)

    # ---- testing the credentials ------------------------------------------

    def test_testing_an_unconfigured_operator_says_what_is_missing(self):
        resp = self.auth(self.owner).post(self.mpesa_test_url(self.t1), {}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.data["success"])
        self.assertTrue(resp.data["missing"])

    @patch("billing.views.get_mpesa_access_token", return_value="a-token")
    def test_a_working_configuration_reports_success(self, _token):
        self.auth(self.owner).put(
            self.url(self.t1),
            {"MPESA_CONSUMER_KEY": "ck", "MPESA_CONSUMER_SECRET": "cs",
             "MPESA_SHORTCODE": "600000", "MPESA_PASSKEY": "pk"},
            format="json")
        resp = self.auth(self.owner).post(self.mpesa_test_url(self.t1), {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(resp.data["success"])

    @patch("billing.views.get_mpesa_access_token",
           side_effect=Exception("400 Client Error: Bad Request"))
    def test_a_rejected_configuration_passes_safaricoms_reason_back(self, _token):
        self.auth(self.owner).put(
            self.url(self.t1),
            {"MPESA_CONSUMER_KEY": "wrong", "MPESA_CONSUMER_SECRET": "wrong",
             "MPESA_SHORTCODE": "600000", "MPESA_PASSKEY": "wrong"},
            format="json")
        resp = self.auth(self.owner).post(self.mpesa_test_url(self.t1), {}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.data["success"])
        self.assertIn("Bad Request", resp.data["error"])

    def test_the_token_is_never_returned(self):
        """The operator's access token is not something the caller needs."""
        with patch("billing.views.get_mpesa_access_token", return_value="secret-token"):
            self.auth(self.owner).put(
                self.url(self.t1),
                {"MPESA_CONSUMER_KEY": "ck", "MPESA_CONSUMER_SECRET": "cs",
                 "MPESA_SHORTCODE": "600000", "MPESA_PASSKEY": "pk"},
                format="json")
            resp = self.auth(self.owner).post(self.mpesa_test_url(self.t1), {}, format="json")
        self.assertNotIn("secret-token", str(resp.data))


class OperatorOnboardingWithMpesaTests(PlatformBillingMixin, TestCase):
    URL = "/api/platform/operators/"

    def setUp(self):
        cache.clear()
        self.build_billing()
        self.owner = make_platform_owner()

    def test_an_operator_can_be_created_ready_to_take_money(self):
        resp = self.auth(self.owner).post(self.URL, {
            "name": "Ready Networks",
            "admin_username": "readyadmin",
            "admin_password": "a-good-password",
            "mpesa_env": "sandbox",
            "mpesa_consumer_key": "ck",
            "mpesa_consumer_secret": "cs",
            "mpesa_shortcode": "600000",
            "mpesa_passkey": "pk",
        }, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["payments_missing"], [])
        tenant = Tenant.objects.get(slug="ready-networks")
        self.assertEqual(get_setting("MPESA_SHORTCODE", tenant=tenant), "600000")

    def test_credentials_stay_optional(self):
        """Waiting on Safaricom is the normal case, not an error."""
        resp = self.auth(self.owner).post(self.URL, {
            "name": "Waiting Networks",
            "admin_username": "waitingadmin",
            "admin_password": "a-good-password",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertTrue(resp.data["payments_missing"])

    def test_the_secret_is_never_echoed_back(self):
        resp = self.auth(self.owner).post(self.URL, {
            "name": "Quiet Networks",
            "admin_username": "quietadmin",
            "admin_password": "a-good-password",
            "mpesa_consumer_secret": "never-echo-this",
            "mpesa_passkey": "nor-this",
        }, format="json")
        self.assertNotIn("never-echo-this", str(resp.data))
        self.assertNotIn("nor-this", str(resp.data))


# =====================================================
# 24. Making the audit log readable, plans changeable,
#     and the station rollups that phase 5 skipped
# =====================================================

class AuditLogReadTests(PlatformBillingMixin, TestCase):
    """
    The log was written from the start and readable nowhere.

    Same defect this codebase already had once, with the operator status
    history: recorded faithfully, surfaced never.
    """

    URL = "/api/platform/audit/"

    def setUp(self):
        cache.clear()
        self.build_billing()
        self.owner = make_platform_owner()
        # Something to read: a reset against operator one.
        self.auth(self.owner).post(
            f"/api/platform/operators/{self.t1.id}/reset-password/",
            {"reason": "audit fixture"}, format="json")
        # That reset bumped their token_version, and this in-memory copy still
        # holds the old one — a token minted from it would be rejected, which
        # is the invalidation doing its job.
        self.admin1.refresh_from_db()

    def test_platform_staff_see_every_operator(self):
        resp = self.auth(self.owner).get(self.URL)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["actions"])

    def test_an_operator_sees_only_actions_against_their_own_business(self):
        """
        Including ones a platform account took on them — that is the part they
        have a right to see.
        """
        self.auth(self.owner).post(
            f"/api/platform/operators/{self.t2.id}/reset-password/",
            {"reason": "other operator"}, format="json")

        resp = self.auth(self.admin1).get(self.URL)
        self.assertEqual(resp.status_code, 200)
        operators = {a["operator_id"] for a in resp.data["actions"]}
        self.assertEqual(operators, {self.t1.id})

    def test_the_reset_on_them_is_visible_to_them(self):
        resp = self.auth(self.admin1).get(self.URL)
        actions = {a["action"] for a in resp.data["actions"]}
        self.assertIn(AdminActionLog.RESET_PASSWORD, actions)

    def test_rows_say_who_did_it_and_why(self):
        resp = self.auth(self.owner).get(self.URL)
        row = next(a for a in resp.data["actions"]
                   if a["action"] == AdminActionLog.RESET_PASSWORD)
        self.assertEqual(row["by"], self.owner.username)
        self.assertTrue(row["by_platform"])
        self.assertEqual(row["detail"], "audit fixture")

    def test_can_be_filtered_to_one_operator(self):
        self.auth(self.owner).post(
            f"/api/platform/operators/{self.t2.id}/reset-password/",
            {"reason": "other"}, format="json")
        resp = self.auth(self.owner).get(self.URL, {"tenant": self.t2.id})
        operators = {a["operator_id"] for a in resp.data["actions"]}
        self.assertEqual(operators, {self.t2.id})

    def test_limit_is_clamped(self):
        resp = self.auth(self.owner).get(self.URL, {"limit": 99999})
        self.assertLessEqual(len(resp.data["actions"]), 500)

    def test_a_subscriber_cannot_read_it(self):
        customer = User.objects.create_user(
            username="just_a_customer", password="x",
            role=User.CUSTOMER, tenant=self.t1)
        self.assertEqual(self.auth(customer).get(self.URL).status_code, 403)


class OperatorPlanChangeTests(PlatformBillingMixin, TestCase):
    """A plan could only be chosen at onboarding and never changed after."""

    def setUp(self):
        cache.clear()
        self.build_billing()
        self.owner = make_platform_owner()
        self.bigger = PlatformPlan.objects.create(
            name="Growth", slug="growth", price=Decimal("5000.00"),
            billing_period_days=30, max_customers=1000, max_routers=20)

    def url(self, tenant):
        return f"/api/platform/operators/{tenant.id}/plan/"

    def test_operator_cannot_change_their_own_plan(self):
        resp = self.auth(self.admin1).post(
            self.url(self.t1), {"plan": "growth"}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_platform_staff_cannot_change_a_plan(self):
        """It decides what they are billed."""
        staff = User.objects.create_user(
            username="pstaff_plan", password="x",
            role=User.PLATFORM_STAFF, tenant=None)
        resp = self.auth(staff).post(
            self.url(self.t1), {"plan": "growth"}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_owner_moves_an_operator_onto_another_plan(self):
        resp = self.auth(self.owner).post(
            self.url(self.t1), {"plan": "growth"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)

        sub = TenantSubscription.objects.all_tenants().get(tenant=self.t1)
        self.assertEqual(sub.plan, self.bigger)
        self.assertEqual(resp.data["previous"], "Starter")

    def test_the_current_period_is_not_re_dated(self):
        """
        Re-dating would either bill them twice for the same days or hand them a
        free period, depending which way they moved.
        """
        sub = TenantSubscription.objects.all_tenants().get(tenant=self.t1)
        before_start, before_end = sub.current_period_start, sub.current_period_end

        self.auth(self.owner).post(self.url(self.t1), {"plan": "growth"}, format="json")

        sub.refresh_from_db()
        self.assertEqual(sub.current_period_start, before_start)
        self.assertEqual(sub.current_period_end, before_end)

    def test_an_operator_with_no_plan_gets_one(self):
        fresh = Tenant.objects.create(name="Planless", slug="planless")
        resp = self.auth(self.owner).post(self.url(fresh), {"plan": "growth"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(
            TenantSubscription.objects.all_tenants().filter(tenant=fresh).exists())

    def test_an_unknown_plan_is_rejected(self):
        resp = self.auth(self.owner).post(
            self.url(self.t1), {"plan": "no-such-plan"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_an_inactive_plan_cannot_be_assigned(self):
        self.bigger.is_active = False
        self.bigger.save(update_fields=["is_active"])
        resp = self.auth(self.owner).post(
            self.url(self.t1), {"plan": "growth"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_the_change_is_audited(self):
        self.auth(self.owner).post(self.url(self.t1), {"plan": "growth"}, format="json")
        log = AdminActionLog.objects.filter(action=AdminActionLog.CHANGE_PLAN).first()
        self.assertIsNotNone(log)
        self.assertIn("Growth", log.detail)
        self.assertEqual(log.target_tenant, self.t1)


class StationRollupTests(TwoOperatorMixin, TestCase):
    """
    The part of phase 5 that was planned and then not done: a router-by-router
    list answers "is this box up", while an operator with two towns is asking
    "is Kilifi up".
    """

    def setUp(self):
        cache.clear()
        self.build_operators()
        with tenant_context(self.t1):
            self.kilifi = Station.objects.create(tenant=self.t1, name="Kilifi Town")
            self.mtwapa = Station.objects.create(tenant=self.t1, name="Mtwapa")
            self.k1 = RouterDevice.objects.create(
                tenant=self.t1, name="k1", ip_address="10.1.0.1",
                username="a", password="p", station=self.kilifi, is_online=True)
            self.m1 = RouterDevice.objects.create(
                tenant=self.t1, name="m1", ip_address="10.2.0.1",
                username="a", password="p", station=self.mtwapa, is_online=True)

    def test_health_is_reported_per_station(self):
        resp = self.auth(self.admin1).get("/api/admin/routers/events/")
        self.assertEqual(resp.status_code, 200)
        names = {s["name"] for s in resp.data["stations"]}
        self.assertIn("Kilifi Town", names)
        self.assertIn("Mtwapa", names)

    def test_an_offline_router_shows_against_its_own_station(self):
        self.m1.record_health(False, error="down")

        resp = self.auth(self.admin1).get("/api/admin/routers/events/")
        by_name = {s["name"]: s for s in resp.data["stations"]}
        self.assertEqual(by_name["Mtwapa"]["routers_offline"], 1)
        self.assertEqual(by_name["Kilifi Town"]["routers_offline"], 0)

    def test_routers_with_no_station_are_still_reported(self):
        """The single-site case must not vanish from its own health page."""
        resp = self.auth(self.admin1).get("/api/admin/routers/events/")
        unnamed = [s for s in resp.data["stations"] if s["name"] is None]
        self.assertTrue(unnamed, "routers without a station disappeared")

    def test_analytics_breaks_subscribers_down_by_station(self):
        with tenant_context(self.t1):
            Customer.objects.create(
                tenant=self.t1, full_name="At Kilifi", phone="254700800001",
                connection_type="pppoe", router=self.k1)
            Customer.objects.create(
                tenant=self.t1, full_name="At Mtwapa", phone="254700800002",
                connection_type="pppoe", router=self.m1)

        owner = make_platform_owner()
        resp = self.auth(owner).get(
            "/api/platform/analytics/", {"days": 7, "tenant": self.t1.id})

        by_name = {s["name"]: s["subscribers"] for s in resp.data["stations"]}
        self.assertEqual(by_name.get("Kilifi Town"), 1)
        self.assertEqual(by_name.get("Mtwapa"), 1)

    def test_the_platform_wide_view_has_no_station_list(self):
        """Every site of every business answers nothing."""
        owner = make_platform_owner()
        resp = self.auth(owner).get("/api/platform/analytics/", {"days": 7})
        self.assertEqual(resp.data["stations"], [])


# =====================================================
# 25. Two receipts, one invoice
# =====================================================

class DoublePaymentTests(TwoOperatorMixin, TestCase):
    """
    The receipt-level idempotency stops the same receipt being applied twice.
    It does nothing about two DIFFERENT receipts against one invoice — a
    customer who pays twice, or an STK re-initiated after a timeout that then
    also succeeds.

    The manual payment path has guarded this from the start. The callback path
    did not, and it is the one Safaricom retries.
    """

    URL = "/api/mpesa/stk-callback/"

    def setUp(self):
        cache.clear()
        self.build_operators()
        self.invoice = self.data["t1"]["invoice"]
        self.customer = self.data["t1"]["customer"]
        # DRF's client, so format="json" is honoured — the plain Django test
        # client posts the dict as a form and the view sees a string.
        self.api = APIClient()

    def _callback(self, receipt):
        return {
            "Body": {"stkCallback": {
                "ResultCode": 0,
                "ResultDesc": "Success",
                "CallbackMetadata": {"Item": [
                    {"Name": "MpesaReceiptNumber", "Value": receipt},
                    {"Name": "Amount", "Value": float(self.invoice.total_amount)},
                    {"Name": "PhoneNumber", "Value": 254700000001},
                    {"Name": "AccountReference", "Value": self.invoice.invoice_number},
                ]},
            }}
        }

    @patch("billing.views.is_trusted_mpesa_ip", return_value=True)
    def test_the_same_receipt_twice_is_ignored(self, _ip):
        """Already true before this change; kept so it cannot regress."""
        self.api.post(self.URL, self._callback("RCT-A"), format="json")
        resp = self.api.post(self.URL, self._callback("RCT-A"), format="json")
        self.assertIn("Duplicate", resp.data["detail"])
        self.assertEqual(
            Payment.objects.all_tenants().filter(subscription=self.invoice.subscription).count(),
            1,
        )

    @patch("billing.views.is_trusted_mpesa_ip", return_value=True)
    def test_two_different_receipts_pay_the_invoice_once(self, _ip):
        first = self.api.post(self.URL, self._callback("RCT-A"), format="json")
        self.assertEqual(first.status_code, 200, first.data)

        second = self.api.post(self.URL, self._callback("RCT-B"), format="json")
        self.assertEqual(second.status_code, 200)
        self.assertIn("already paid", second.data["detail"].lower())

        self.assertEqual(
            Payment.objects.all_tenants().filter(subscription=self.invoice.subscription).count(),
            1,
            "a second receipt created a second payment against one invoice",
        )

    @patch("billing.views.is_trusted_mpesa_ip", return_value=True)
    def test_the_second_receipt_is_still_recorded(self, _ip):
        """
        Money did arrive. Dropping the record would leave nothing to reconcile
        against when the customer asks where their second payment went.
        """
        self.api.post(self.URL, self._callback("RCT-A"), format="json")
        self.api.post(self.URL, self._callback("RCT-B"), format="json")

        tx = MpesaTransaction.objects.all_tenants().get(mpesa_receipt="RCT-B")
        self.assertTrue(tx.processed)
        self.assertEqual(tx.invoice_id, self.invoice.id)
        self.assertIn("already paid", tx.error_message.lower())

    @patch("billing.views.is_trusted_mpesa_ip", return_value=True)
    def test_a_hotspot_customer_gets_one_voucher_not_two(self, _ip):
        """
        The concrete cost of the gap: Payment.save() mints a voucher for a
        hotspot customer, so one purchase would have handed out two.
        """
        with tenant_context(self.t1):
            self.customer.connection_type = "hotspot"
            self.customer.save(update_fields=["connection_type"])

        self.api.post(self.URL, self._callback("RCT-A"), format="json")
        self.api.post(self.URL, self._callback("RCT-B"), format="json")

        vouchers = Voucher.objects.all_tenants().filter(
            subscription=self.invoice.subscription).count()
        self.assertEqual(vouchers, 1)


# =====================================================
# 26. Making an operator's staff account worth having
# =====================================================

class TenantStaffCapabilityTests(TwoOperatorMixin, TestCase):
    """
    Before this, 29 of 30 operator endpoints required tenant_admin, so a staff
    account could sign in and reach almost nothing — the team feature produced
    logins that could not do the job.

    The rule: reading the business is the day job; changing its configuration,
    its money settings or its hardware is not.
    """

    def setUp(self):
        cache.clear()
        self.build_operators()
        self.staff = User.objects.create_user(
            username="day_staff", password="x",
            role=User.TENANT_STAFF, tenant=self.t1)

    # ---- what staff must be able to do -------------------------------------

    def test_staff_can_see_the_customer_list(self):
        self.assertEqual(self.auth(self.staff).get("/api/customers/").status_code, 200)

    def test_staff_can_see_packages_and_invoices(self):
        client = self.auth(self.staff)
        self.assertEqual(client.get("/api/packages/").status_code, 200)
        self.assertEqual(client.get("/api/invoices/").status_code, 200)

    def test_staff_can_see_the_dashboard(self):
        client = self.auth(self.staff)
        self.assertEqual(client.get("/api/reports/revenue/").status_code, 200)
        self.assertEqual(client.get("/api/dashboard/invoices/unpaid/").status_code, 200)
        self.assertEqual(client.get("/api/dashboard/mpesa/failed/").status_code, 200)

    def test_staff_can_see_the_network(self):
        client = self.auth(self.staff)
        self.assertEqual(client.get("/api/admin/routers/").status_code, 200)
        self.assertEqual(client.get("/api/admin/routers/health/").status_code, 200)
        self.assertEqual(client.get("/api/admin/routers/failovers/").status_code, 200)
        self.assertEqual(client.get("/api/admin/routers/events/").status_code, 200)

    def test_staff_can_look_up_access(self):
        """The single most common thing anyone on a support desk does."""
        resp = self.auth(self.staff).get("/api/admin/access-lookup/", {"q": "0700"})
        self.assertNotEqual(resp.status_code, 403)

    def test_staff_can_record_a_manual_payment(self):
        """Someone paid in cash at the counter — that is the counter's job."""
        invoice = self.data["t1"]["invoice"]
        resp = self.auth(self.staff).post("/api/payments/manual/", {
            "invoice_number": invoice.invoice_number,
            "amount": str(invoice.total_amount),
            "method": "cash",
        }, format="json")
        self.assertNotEqual(resp.status_code, 403)

    # ---- what stays with the admin -----------------------------------------

    def test_staff_cannot_add_a_customer(self):
        resp = self.auth(self.staff).post("/api/customers/", {
            "full_name": "Nope", "phone": "254700111999", "connection_type": "pppoe",
        }, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_staff_cannot_change_a_package_price(self):
        package = self.data["t1"]["package"]
        resp = self.auth(self.staff).patch(
            f"/api/packages/{package.id}/", {"price": "1.00"}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_staff_cannot_touch_settings_or_credentials(self):
        client = self.auth(self.staff)
        self.assertEqual(client.get("/api/system/settings/").status_code, 403)
        self.assertEqual(client.get("/api/system/test/mpesa/").status_code, 403)

    def test_staff_cannot_manage_the_team(self):
        """Otherwise staff could promote themselves."""
        self.assertEqual(self.auth(self.staff).get("/api/users/").status_code, 403)

    def test_staff_cannot_add_or_remove_routers(self):
        resp = self.auth(self.staff).post("/api/admin/routers/", {
            "name": "rogue", "ip_address": "10.0.0.9", "username": "a", "password": "p",
        }, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_staff_cannot_broadcast_to_every_customer(self):
        resp = self.auth(self.staff).post(
            "/api/admin/broadcast/", {"message": "hi"}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_staff_cannot_cut_off_a_customer(self):
        resp = self.auth(self.staff).post(
            "/api/admin/access-deactivate/",
            {"subscription_id": 1, "reason": "no"}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_staff_cannot_manage_stations(self):
        self.assertEqual(self.auth(self.staff).get("/api/stations/").status_code, 403)

    # ---- and none of it crosses operators ----------------------------------

    def test_staff_see_only_their_own_operators_customers(self):
        resp = self.auth(self.staff).get("/api/customers/")
        names = {c["full_name"] for c in resp.data["results"]}
        self.assertNotIn(self.data["t2"]["customer"].full_name, names)

    def test_an_admin_keeps_everything_staff_lost(self):
        client = self.auth(self.admin1)
        self.assertEqual(client.get("/api/system/settings/").status_code, 200)
        self.assertEqual(client.get("/api/users/").status_code, 200)
        self.assertEqual(client.get("/api/stations/").status_code, 200)


# =====================================================
# 27. Operator analytics
# =====================================================

class OperatorAnalyticsTests(TwoOperatorMixin, TestCase):
    URL = "/api/reports/analytics/"

    def setUp(self):
        cache.clear()
        self.build_operators()
        self.package = self.data["t1"]["package"]
        self.customer = self.data["t1"]["customer"]
        self.sub = self.data["t1"]["sub"]

    def _pay(self, amount, days_ago=0, hour=None):
        with tenant_context(self.t1):
            p = Payment.objects.create(
                tenant=self.t1, customer=self.customer, subscription=self.sub,
                amount=Decimal(str(amount)), method="mpesa",
                reference=f"AN{amount}{days_ago}{hour or 0}")
        when = timezone.now() - timezone.timedelta(days=days_ago)
        if hour is not None:
            when = timezone.localtime(when).replace(hour=hour, minute=0)
        Payment.objects.all_tenants().filter(pk=p.pk).update(paid_at=when)
        return p

    # ---- access ------------------------------------------------------------

    def test_staff_may_read_it(self):
        """Reading the business is the day job."""
        staff = User.objects.create_user(
            username="an_staff", password="x", role=User.TENANT_STAFF, tenant=self.t1)
        self.assertEqual(self.auth(staff).get(self.URL).status_code, 200)

    def test_another_operators_figures_never_appear(self):
        with tenant_context(self.t2):
            Payment.objects.create(
                tenant=self.t2, customer=self.data["t2"]["customer"],
                subscription=self.data["t2"]["sub"],
                amount=Decimal("99999.00"), method="mpesa", reference="OTHER")

        resp = self.auth(self.admin1).get(self.URL, {"days": 30})
        self.assertLess(resp.data["totals"]["revenue"], 99999)

    # ---- the range ---------------------------------------------------------

    def test_every_day_in_the_window_is_present(self):
        resp = self.auth(self.admin1).get(self.URL, {"days": 14})
        self.assertEqual(len(resp.data["series"]), 14)
        days = [p["day"] for p in resp.data["series"]]
        self.assertEqual(days, sorted(days))

    def test_an_explicit_range_is_honoured(self):
        resp = self.auth(self.admin1).get(
            self.URL, {"from": "2026-07-01", "to": "2026-07-07"})
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["range"]["from"], "2026-07-01")

    def test_a_backwards_range_is_refused(self):
        resp = self.auth(self.admin1).get(
            self.URL, {"from": "2026-07-31", "to": "2026-07-01"})
        self.assertEqual(resp.status_code, 400)

    def test_an_absurd_range_is_refused(self):
        resp = self.auth(self.admin1).get(
            self.URL, {"from": "2000-01-01", "to": "2026-07-01"})
        self.assertEqual(resp.status_code, 400)

    def test_a_junk_date_is_refused_rather_than_crashing(self):
        resp = self.auth(self.admin1).get(
            self.URL, {"from": "not-a-date", "to": "also-not"})
        self.assertEqual(resp.status_code, 400)

    # ---- the numbers -------------------------------------------------------

    def test_revenue_lands_on_the_day_it_was_paid(self):
        self._pay(500, days_ago=2)
        resp = self.auth(self.admin1).get(self.URL, {"days": 7})
        day = (timezone.localtime(timezone.now()).date()
               - timezone.timedelta(days=2)).isoformat()
        point = next(p for p in resp.data["series"] if p["day"] == day)
        self.assertEqual(point["revenue"], 500.0)

    def test_a_delta_is_none_when_there_is_nothing_to_compare_with(self):
        """
        None, not zero and not 100%. "No change" and "no basis for comparison"
        are different statements, and the second must not look like the first.
        """
        resp = self.auth(self.admin1).get(self.URL)
        self.assertIsNone(resp.data["pulse"]["today"]["delta"])

    def test_a_delta_is_computed_against_the_prior_window(self):
        self._pay(100, days_ago=1)   # yesterday
        self._pay(200, days_ago=0)   # today
        resp = self.auth(self.admin1).get(self.URL)
        self.assertEqual(resp.data["pulse"]["today"]["delta"], 100.0)

    def test_arpu_is_revenue_over_paying_customers(self):
        self._pay(1000, days_ago=1)
        resp = self.auth(self.admin1).get(self.URL, {"days": 7})
        totals = resp.data["totals"]
        expected = round(totals["revenue"] / totals["active_customers"], 2)
        self.assertEqual(totals["arpu"], expected)

    def test_packages_come_back_with_the_volume_behind_them(self):
        """Revenue without purchase count cannot distinguish one big sale from many."""
        self._pay(300, days_ago=1)
        resp = self.auth(self.admin1).get(self.URL, {"days": 7})
        row = resp.data["by_package"][0]
        self.assertIn("purchases", row)
        self.assertIn("customers", row)

    def test_every_hour_of_the_day_is_present(self):
        self._pay(50, days_ago=1, hour=20)
        resp = self.auth(self.admin1).get(self.URL, {"days": 7})
        hours = [h["hour"] for h in resp.data["peak_hours"]]
        self.assertEqual(hours, list(range(24)))

    def test_expiring_soon_reports_what_is_at_risk(self):
        resp = self.auth(self.admin1).get(self.URL)
        expiring = resp.data["expiring"]
        for bucket in ("today", "next_7_days", "expired_last_7_days"):
            self.assertIn("count", expiring[bucket])
            self.assertIn("value", expiring[bucket])

    def test_flow_reports_joins_against_lapses(self):
        resp = self.auth(self.admin1).get(self.URL, {"days": 30})
        flow = resp.data["flow"]
        self.assertIn("joined", flow)
        self.assertIn("lapsed", flow)
        self.assertIn("net_value", flow)

    # ---- stations ----------------------------------------------------------

    def test_a_single_site_operator_gets_no_station_breakdown(self):
        """One row saying what the totals already said is noise."""
        resp = self.auth(self.admin1).get(self.URL)
        self.assertEqual(resp.data["by_station"], [])

    def test_two_sites_are_broken_down(self):
        with tenant_context(self.t1):
            Station.objects.create(tenant=self.t1, name="Kilifi")
            Station.objects.create(tenant=self.t1, name="Mtwapa")
        resp = self.auth(self.admin1).get(self.URL)
        names = {s["name"] for s in resp.data["by_station"]}
        self.assertEqual(names, {"Kilifi", "Mtwapa"})

    def test_an_unknown_station_is_404(self):
        resp = self.auth(self.admin1).get(self.URL, {"station": 999999})
        self.assertEqual(resp.status_code, 404)

    def test_another_operators_station_does_not_resolve(self):
        with tenant_context(self.t2):
            theirs = Station.objects.create(tenant=self.t2, name="Not Yours")
        resp = self.auth(self.admin1).get(self.URL, {"station": theirs.id})
        self.assertEqual(resp.status_code, 404)

    # ---- cost --------------------------------------------------------------

    def test_query_count_does_not_grow_with_the_window(self):
        """
        Every panel is a SQL aggregate. A regression to looping the days or the
        packages would not show up until it was slow somewhere real.
        """
        self.auth(self.admin1).get(self.URL, {"days": 7})   # warm

        with CaptureQueriesContext(connection) as short:
            self.auth(self.admin1).get(self.URL, {"days": 7})
        with CaptureQueriesContext(connection) as long:
            self.auth(self.admin1).get(self.URL, {"days": 90})

        self.assertEqual(len(short.captured_queries), len(long.captured_queries))


# =====================================================
# 28. Paying and actually getting on the network
# =====================================================

class ProvisioningRetryTests(TwoOperatorMixin, TestCase):
    """
    The worst shape a failure can take here: the customer pays, the invoice is
    marked paid, the subscription goes active, an SMS says the account is
    ready — and no router answered, so they have nothing. It used to log a
    warning and stop.
    """

    def setUp(self):
        cache.clear()
        self.build_operators()
        self.customer = self.data["t1"]["customer"]

    def test_enable_reports_failure_rather_than_returning_nothing(self):
        """
        The distinction the caller needs. This returned None either way, so
        "provisioned" and "no router was reachable" were indistinguishable.
        """
        with patch("billing.router_service.pick_working_router", return_value=(None, None)):
            self.assertIs(enable_customer_access(self.customer), False)

    def test_enable_reports_success(self):
        with patch("billing.router_service.pick_working_router",
                   return_value=(self.data["t1"]["router"], object())), \
             patch("billing.router_service.create_pppoe_secret"), \
             patch("billing.router_service.enable_pppoe"):
            self.assertIs(enable_customer_access(self.customer), True)

    def test_a_reachable_router_provisions_first_time(self):
        with patch("billing.router_service.enable_customer_access", return_value=True) as ok:
            result = ensure_customer_access_task(self.customer.id)
        self.assertTrue(result)
        self.assertEqual(ok.call_count, 1)

    def test_it_retries_rather_than_giving_up(self):
        """A router down for ninety seconds must not cost someone their access."""
        with patch("billing.router_service.enable_customer_access", return_value=False), \
             patch.object(ensure_customer_access_task, "retry",
                          side_effect=Retry("retrying")) as retry:
            with self.assertRaises(Retry):
                ensure_customer_access_task(self.customer.id)
        self.assertTrue(retry.called)
        # Backs off rather than hammering a box that is rebooting.
        self.assertGreaterEqual(retry.call_args.kwargs["countdown"], 60)

    def test_when_it_finally_gives_up_a_person_is_told(self):
        """
        Out of attempts, the customer has paid and has nothing. That stops being
        a network event and becomes something someone must handle.
        """
        task = ensure_customer_access_task
        with patch("billing.router_service.enable_customer_access", return_value=False), \
             patch("billing.tasks.alert_tasks.notify_admin_task.delay") as alert:
            # Celery's task proxy has no patchable `request`; push a real one
            # describing the final attempt instead.
            task.push_request(retries=task.max_retries)
            try:
                result = task(self.customer.id)
            finally:
                task.pop_request()

        self.assertFalse(result)
        self.assertTrue(alert.called, "the operator was not told")
        self.assertIn("paid", alert.call_args.args[0].lower())

    def test_the_failure_is_recorded_against_the_customer(self):
        task = ensure_customer_access_task
        with patch("billing.router_service.enable_customer_access", return_value=False), \
             patch("billing.tasks.alert_tasks.notify_admin_task.delay"):
            task.push_request(retries=task.max_retries)
            try:
                task(self.customer.id)
            finally:
                task.pop_request()

        log = AccessAuditLog.objects.all_tenants().filter(
            customer=self.customer, action="provisioning_failed").first()
        self.assertIsNotNone(log, "nothing on the customer's own record")
        self.assertIn("no router was reachable", log.reason)

    def test_a_deleted_customer_does_not_crash_the_worker(self):
        missing = 9999999
        self.assertIs(ensure_customer_access_task(missing), False)


class SilentRehomingTests(TwoOperatorMixin, TestCase):
    """
    A customer moved to different hardware must appear in Failover Logs — the
    page an operator opens to ask exactly that question.
    """

    def setUp(self):
        cache.clear()
        self.build_operators()
        self.customer = self.data["t1"]["customer"]
        with tenant_context(self.t1):
            self.spare = RouterDevice.objects.create(
                tenant=self.t1, name="t1-spare", ip_address="10.0.9.9",
                username="a", password="p", priority=2)

    def test_an_automatic_move_is_recorded(self):
        before = self.customer.router
        with patch("billing.router_service.pick_working_router",
                   return_value=(self.spare, object())), \
             patch("billing.router_service.create_pppoe_secret"), \
             patch("billing.router_service.enable_pppoe"):
            enable_customer_access(self.customer)

        log = RouterFailoverLog.objects.all_tenants().filter(
            customer=self.customer).order_by("-id").first()
        self.assertIsNotNone(log, "the move left no trace in Failover Logs")
        self.assertEqual(log.to_router, self.spare)
        self.assertEqual(log.from_router, before)
        self.assertEqual(log.reason, "auto_recovery")

    def test_staying_put_records_nothing(self):
        """Only moves are events. A customer on their own router is not news."""
        RouterFailoverLog.objects.all_tenants().all().delete()
        with patch("billing.router_service.pick_working_router",
                   return_value=(self.customer.router, object())), \
             patch("billing.router_service.create_pppoe_secret"), \
             patch("billing.router_service.enable_pppoe"):
            enable_customer_access(self.customer)

        self.assertEqual(RouterFailoverLog.objects.all_tenants().count(), 0)


# =====================================================
# 29. BlessedTexts
# =====================================================

class BlessedTextsSmsTests(TwoOperatorMixin, TestCase):
    """
    The provider answers HTTP 200 for a refusal and puts the reason in the body.

    That is the whole reason this was rewritten: the previous implementation
    called raise_for_status() and returned True, so a message rejected for an
    empty account or a bad number was recorded as sent. An expiry warning
    nobody received, logged as delivered, is worse than a visible failure.
    """

    def setUp(self):
        cache.clear()
        self.build_operators()
        for key, value in (
            ("BLESSEDTEXTS_API_KEY", "test-key"),
            ("BLESSEDTEXTS_SENDER_ID", "23107"),
        ):
            SystemSetting.objects.create(tenant=self.t1, key=key, value=value)
        clear_settings_cache(tenant=self.t1)

    def _response(self, payload, status_code=200):
        mock = MagicMock()
        mock.status_code = status_code
        mock.json.return_value = payload
        mock.raise_for_status.return_value = None
        return mock

    # ---- single send -------------------------------------------------------

    def test_a_success_code_is_a_success(self):
        body = [{"status_code": "1000", "status_desc": "Success",
                 "phone": "254722000000", "message_cost": "0.5"}]
        with patch("billing.notifications.requests.post", return_value=self._response(body)):
            self.assertTrue(send_sms("254722000000", "hello", tenant=self.t1))

    def test_a_refusal_inside_a_200_is_a_failure(self):
        """The exact shape the old code reported as sent."""
        body = [{"status_code": "1009", "status_desc": "Low bulk credits",
                 "phone": "254722000000"}]
        with patch("billing.notifications.requests.post", return_value=self._response(body)):
            self.assertFalse(send_sms("254722000000", "hello", tenant=self.t1))

    def test_an_invalid_number_is_a_failure(self):
        body = [{"status_code": "1008", "status_desc": "Invalid Phone number"}]
        with patch("billing.notifications.requests.post", return_value=self._response(body)):
            self.assertFalse(send_sms("nonsense", "hello", tenant=self.t1))

    def test_a_bare_object_response_is_handled(self):
        """A malformed request answers with an object rather than a list."""
        body = {"status_code": "1005", "status_desc": "Missing Message"}
        with patch("billing.notifications.requests.post", return_value=self._response(body)):
            self.assertFalse(send_sms("254722000000", "", tenant=self.t1))

    def test_missing_credentials_never_reach_the_network(self):
        with patch("billing.notifications.requests.post") as post:
            self.assertFalse(send_sms("254722000000", "hello", tenant=self.t2))
        post.assert_not_called()

    def test_each_operator_sends_on_their_own_account(self):
        SystemSetting.objects.create(
            tenant=self.t2, key="BLESSEDTEXTS_API_KEY", value="their-key")
        SystemSetting.objects.create(
            tenant=self.t2, key="BLESSEDTEXTS_SENDER_ID", value="99999")
        clear_settings_cache(tenant=self.t2)

        body = [{"status_code": "1000"}]
        with patch("billing.notifications.requests.post",
                   return_value=self._response(body)) as post:
            send_sms("254722000000", "hello", tenant=self.t2)

        sent = post.call_args.kwargs["json"]
        self.assertEqual(sent["api_key"], "their-key")
        self.assertEqual(sent["sender_id"], "99999")

    # ---- the failures that need a person -----------------------------------

    def test_running_out_of_credit_reaches_the_operator(self):
        """
        A bad number concerns one customer. An empty account concerns every
        message until somebody acts, so it must not sit in a log.
        """
        body = [{"status_code": "1009", "status_desc": "Low bulk credits"}]
        with patch("billing.notifications.requests.post", return_value=self._response(body)), \
             patch("billing.tasks.alert_tasks.notify_admin_task.delay") as alert:
            send_sms("254722000000", "hello", tenant=self.t1)
        self.assertTrue(alert.called)
        self.assertIn("not going out", alert.call_args.args[0])

    def test_a_bad_number_does_not_alarm_the_operator(self):
        body = [{"status_code": "1008", "status_desc": "Invalid Phone number"}]
        with patch("billing.notifications.requests.post", return_value=self._response(body)), \
             patch("billing.tasks.alert_tasks.notify_admin_task.delay") as alert:
            send_sms("nonsense", "hello", tenant=self.t1)
        self.assertFalse(alert.called)

    # ---- bulk --------------------------------------------------------------

    def test_bulk_sends_one_request_for_many_recipients(self):
        pairs = [(f"25472200000{i}", f"hello {i}") for i in range(5)]
        body = [{"status_code": "1000", "phone": p} for p, _ in pairs]
        with patch("billing.notifications.requests.post",
                   return_value=self._response(body)) as post:
            result = send_bulk_sms(pairs, tenant=self.t1)

        self.assertEqual(post.call_count, 1, "one request, not one per recipient")
        self.assertEqual(result["sent"], 5)
        self.assertEqual(result["failed"], 0)

    def test_bulk_counts_the_refusals_separately(self):
        pairs = [("254722000001", "a"), ("254722000002", "b")]
        body = [
            {"status_code": "1000", "phone": "254722000001"},
            {"status_code": "1008", "phone": "254722000002",
             "status_desc": "Invalid Phone number"},
        ]
        with patch("billing.notifications.requests.post", return_value=self._response(body)):
            result = send_bulk_sms(pairs, tenant=self.t1)

        self.assertEqual(result["sent"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertIn("254722000002", result["errors"][0])

    def test_recipients_the_provider_ignored_count_as_failed(self):
        """
        Fewer rows than recipients means someone was not sent to. Counting the
        difference as sent would be the same lie in a new place.
        """
        pairs = [("254722000001", "a"), ("254722000002", "b"), ("254722000003", "c")]
        body = [{"status_code": "1000", "phone": "254722000001"}]
        with patch("billing.notifications.requests.post", return_value=self._response(body)):
            result = send_bulk_sms(pairs, tenant=self.t1)

        self.assertEqual(result["sent"], 1)
        self.assertEqual(result["failed"], 2)

    def test_a_network_failure_fails_everyone_rather_than_nobody(self):
        pairs = [("254722000001", "a"), ("254722000002", "b")]
        with patch("billing.notifications.requests.post", side_effect=Exception("timeout")):
            result = send_bulk_sms(pairs, tenant=self.t1)
        self.assertEqual(result["sent"], 0)
        self.assertEqual(result["failed"], 2)

    # ---- balance -----------------------------------------------------------

    def test_balance_is_read(self):
        body = {"status_code": "1000", "balance": "200"}
        with patch("billing.notifications.requests.post", return_value=self._response(body)):
            result = sms_balance(tenant=self.t1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["balance"], 200)

    def test_balance_reports_an_invalid_key_rather_than_zero(self):
        """Zero credit and a rejected key are different problems."""
        body = {"status_code": "1002"}
        with patch("billing.notifications.requests.post", return_value=self._response(body)):
            result = sms_balance(tenant=self.t1)
        self.assertFalse(result["ok"])
        self.assertIn("Invalid API key", result["error"])

    def test_balance_without_a_key_does_not_call_out(self):
        with patch("billing.notifications.requests.post") as post:
            result = sms_balance(tenant=self.t2)
        post.assert_not_called()
        self.assertFalse(result["ok"])


# =====================================================
# 30. Erasing a closed operator
# =====================================================

class OperatorDeletionTests(PlatformBillingMixin, TestCase):
    """
    Irreversible, and it destroys billing history. Three gates, because there
    is no undo.
    """

    def setUp(self):
        cache.clear()
        self.build_billing()
        self.owner = make_platform_owner()

    def url(self, tenant):
        return f"/api/platform/operators/{tenant.id}/"

    def _close(self, tenant):
        set_tenant_status(tenant, "cancelled", reason="test", automatic=True)
        tenant.refresh_from_db()

    def _delete(self, tenant, confirm=None, as_user=None):
        return self.auth(as_user or self.owner).delete(
            self.url(tenant),
            {"confirm": confirm if confirm is not None
             else (tenant.business_name or tenant.name)},
            format="json",
        )

    # ---- the gates ---------------------------------------------------------

    def test_a_live_operator_cannot_be_deleted(self):
        """Never the first action taken against a working business."""
        resp = self._delete(self.t1)
        self.assertEqual(resp.status_code, 409)
        self.assertTrue(Tenant.objects.filter(pk=self.t1.pk).exists())

    def test_a_past_due_operator_cannot_be_deleted(self):
        set_tenant_status(self.t1, "past_due", reason="test", automatic=True)
        self.t1.refresh_from_db()
        resp = self._delete(self.t1)
        self.assertEqual(resp.status_code, 409)

    def test_platform_staff_cannot_delete(self):
        self._close(self.t1)
        staff = User.objects.create_user(
            username="pstaff_del", password="x",
            role=User.PLATFORM_STAFF, tenant=None)
        resp = self._delete(self.t1, as_user=staff)
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Tenant.objects.filter(pk=self.t1.pk).exists())

    def test_an_operator_cannot_delete_anyone(self):
        self._close(self.t1)
        resp = self._delete(self.t1, as_user=self.admin1)
        self.assertEqual(resp.status_code, 403)

    def test_the_name_must_be_typed_back(self):
        """The difference between deciding and mis-clicking."""
        self._close(self.t1)
        resp = self._delete(self.t1, confirm="something else")
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(Tenant.objects.filter(pk=self.t1.pk).exists())

    def test_a_blank_confirmation_is_refused(self):
        self._close(self.t1)
        resp = self._delete(self.t1, confirm="")
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(Tenant.objects.filter(pk=self.t1.pk).exists())

    # ---- the deletion ------------------------------------------------------

    def test_a_closed_operator_and_everything_of_theirs_goes(self):
        self._close(self.t1)
        tenant_id = self.t1.id

        resp = self._delete(self.t1)
        self.assertEqual(resp.status_code, 200, resp.data)

        self.assertFalse(Tenant.objects.filter(pk=tenant_id).exists())
        self.assertFalse(User.objects.filter(tenant_id=tenant_id).exists())
        for model in (Customer, Package, RouterDevice, Subscription, Invoice, Payment):
            self.assertFalse(
                model.objects.all_tenants().filter(tenant_id=tenant_id).exists(),
                f"{model.__name__} rows survived",
            )

    def test_it_reports_what_it_destroyed(self):
        self._close(self.t1)
        resp = self._delete(self.t1)
        self.assertIn("Customer", resp.data["removed"])
        self.assertGreaterEqual(resp.data["removed"]["Customer"], 1)

    def test_the_other_operator_is_untouched(self):
        """The property worth protecting most."""
        self._close(self.t1)
        before = Customer.objects.all_tenants().filter(tenant=self.t2).count()
        self.assertGreater(before, 0)

        self._delete(self.t1)

        self.assertTrue(Tenant.objects.filter(pk=self.t2.pk).exists())
        self.assertEqual(
            Customer.objects.all_tenants().filter(tenant=self.t2).count(), before)
        self.assertTrue(User.objects.filter(tenant=self.t2).exists())

    def test_the_audit_row_outlives_the_operator(self):
        """
        Afterwards there is nothing left to count, so the record has to be
        written first and has to survive the thing it describes.
        """
        self._close(self.t1)
        name = self.t1.business_name or self.t1.name

        self._delete(self.t1)

        log = AdminActionLog.objects.filter(
            action=AdminActionLog.DELETE_OPERATOR).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor, self.owner)
        # The tenant reference is SET_NULL, so the name must be kept as text.
        self.assertIsNone(log.target_tenant_id)
        self.assertEqual(log.target_label, name)
        self.assertIn("Customer=", log.detail)

    def test_an_operator_with_no_records_can_still_be_deleted(self):
        empty = Tenant.objects.create(name="Never Started", slug="never-started")
        set_tenant_status(empty, "cancelled", reason="test", automatic=True)
        empty.refresh_from_db()

        resp = self._delete(empty)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(Tenant.objects.filter(pk=empty.pk).exists())

    def test_an_unknown_operator_is_404(self):
        resp = self.auth(self.owner).delete(
            "/api/platform/operators/999999/", {"confirm": "x"}, format="json")
        self.assertEqual(resp.status_code, 404)


# =====================================================
# 31. Whose voucher is it
# =====================================================

class VoucherTenantScopeTests(TwoOperatorMixin, TestCase):
    """
    A voucher belongs to one operator and must only work on that operator's
    portal.

    validate_voucher() searched every operator on the platform. The endpoint
    that calls it is public, so no middleware sets a tenant context and the
    managers run unscoped — and the RLS policy allows everything when
    app.current_tenant_id is unset, by design, because that is what lets
    platform staff and Celery run cross-tenant. Both layers were therefore
    open at once.

    Verified against the running stack before it was fixed: presenting
    BlueWave's voucher on Skylink's captive portal answered "Access granted"
    and bound a device MAC to a BlueWave subscriber.
    """

    def setUp(self):
        cache.clear()
        self.build_operators()
        self.vouchers = {}
        for tag in ("t1", "t2"):
            d = self.data[tag]
            with tenant_context(d["tenant"]):
                d["customer"].connection_type = "hotspot"
                d["customer"].pppoe_username = ""
                d["customer"].save()
                sub = d["sub"]
                sub.expiry_date = timezone.now() + timezone.timedelta(days=5)
                sub.status = "active"
                sub.save()
                self.vouchers[tag] = Voucher.objects.create(
                    tenant=d["tenant"],
                    code=f"WIFI-{tag.upper()}0001",
                    subscription=sub,
                    expires_at=sub.expiry_date,
                )
                Payment.objects.create(
                    tenant=d["tenant"], customer=d["customer"], subscription=sub,
                    amount=Decimal("50.00"), method="mpesa",
                    reference=f"RCPT{tag.upper()}001",
                )

    def validate(self, tenant, code, mac):
        return APIClient().post(
            f"/api/hotspot/validate/?t={tenant.public_token}",
            {"code": code, "mac_address": mac},
            format="json",
        )

    # ---- the hole ----------------------------------------------------------

    def test_a_voucher_does_not_work_on_another_operators_portal(self):
        resp = self.validate(self.t1, self.vouchers["t2"].code, "AA:BB:CC:00:00:01")
        self.assertEqual(resp.status_code, 400, resp.data)

        # And nothing was bound to the other operator's subscriber.
        other = Customer.objects.all_tenants().get(pk=self.data["t2"]["customer"].pk)
        self.assertEqual(other.hotspot_username, "")

    def test_an_mpesa_receipt_does_not_work_on_another_operators_portal(self):
        resp = self.validate(self.t1, "RCPTT2001", "AA:BB:CC:00:00:02")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_the_service_refuses_to_cross_operators_when_given_one(self):
        """Directly, so the guarantee does not depend on the view."""
        self.assertIsNone(
            validate_voucher(self.vouchers["t2"].code, tenant=self.t1)
        )
        self.assertIsNotNone(
            validate_voucher(self.vouchers["t2"].code, tenant=self.t2)
        )

    # ---- still works for the operator it belongs to ------------------------

    def test_a_voucher_works_on_its_own_portal(self):
        resp = self.validate(self.t1, self.vouchers["t1"].code, "AA:BB:CC:00:00:03")
        self.assertEqual(resp.status_code, 200, resp.data)
        mine = Customer.objects.all_tenants().get(pk=self.data["t1"]["customer"].pk)
        self.assertEqual(mine.hotspot_username, "AA:BB:CC:00:00:03")

    def test_an_mpesa_receipt_works_on_its_own_portal(self):
        resp = self.validate(self.t1, "RCPTT1001", "AA:BB:CC:00:00:04")
        self.assertEqual(resp.status_code, 200, resp.data)

    # ---- refusing rather than guessing -------------------------------------

    def test_no_operator_token_is_refused_while_several_exist(self):
        """
        The same rule _hotspot_customer_for() already applied to MAC lookups.
        Searching every operator is what made the leak possible, so an
        unidentifiable portal is an error, not a wildcard.
        """
        resp = APIClient().post(
            "/api/hotspot/validate/",
            {"code": self.vouchers["t1"].code, "mac_address": "AA:BB:CC:00:00:05"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("provider", resp.data["detail"])

    def test_an_unknown_operator_token_is_refused(self):
        resp = APIClient().post(
            "/api/hotspot/validate/?t=not-a-real-token",
            {"code": self.vouchers["t1"].code, "mac_address": "AA:BB:CC:00:00:06"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)


# =====================================================
# 32. Pasting the M-Pesa message
# =====================================================

class MpesaMessageAsVoucherTests(TwoOperatorMixin, TestCase):
    """
    Nobody reads a receipt code off an SMS and retypes it. They long-press the
    message and paste the whole thing, so the whole thing has to work.

    A pasted message is a way of typing a code, never evidence that a payment
    happened: the code still has to match a Payment this operator already
    recorded from a callback we matched ourselves.
    """

    MESSAGE = (
        "TGX11AA001 Confirmed. Ksh50.00 sent to SKYLINK WIFI for account "
        "254711000101 on 1/8/26 at 10:15 AM. New M-PESA balance is Ksh1,234.00. "
        "Transaction cost, Ksh0.00."
    )

    def setUp(self):
        cache.clear()
        self.build_operators()
        d = self.data["t1"]
        with tenant_context(self.t1):
            d["customer"].connection_type = "hotspot"
            d["customer"].pppoe_username = ""
            d["customer"].save()
            self.sub = d["sub"]
            self.sub.expiry_date = timezone.now() + timezone.timedelta(days=3)
            self.sub.status = "active"
            self.sub.save()
            inv = self.sub.invoice
            inv.payment_status = "paid"
            inv.save(update_fields=["payment_status"])
            Payment.objects.create(
                tenant=self.t1, customer=d["customer"], subscription=self.sub,
                amount=Decimal("50.00"), method="mpesa", reference="TGX11AA001",
            )
            self.voucher = Voucher.objects.create(
                tenant=self.t1, code="WIFI-ABC123", subscription=self.sub,
                expires_at=self.sub.expiry_date,
            )

    def validate(self, code, mac, tenant=None):
        return APIClient().post(
            f"/api/hotspot/validate/?t={(tenant or self.t1).public_token}",
            {"code": code, "mac_address": mac},
            format="json",
        )

    # ---- parsing -----------------------------------------------------------

    def test_the_receipt_is_taken_from_a_whole_message(self):
        self.assertEqual(extract_codes(self.MESSAGE)[0], "TGX11AA001")

    def test_a_bare_code_is_left_alone(self):
        """Typing the code by hand must behave exactly as it always did."""
        self.assertEqual(extract_codes("WIFI-ABC123"), ["WIFI-ABC123"])
        self.assertEqual(extract_codes("  TGX11AA001 "), ["TGX11AA001"])

    def test_a_voucher_pasted_inside_a_message_is_found(self):
        text = "Welcome to Skylink! Your Voucher Code: WIFI-ABC123 valid until tomorrow."
        self.assertIn("WIFI-ABC123", extract_codes(text))

    def test_the_word_confirmed_is_not_treated_as_a_code(self):
        self.assertNotIn("CONFIRMED", extract_codes(self.MESSAGE))

    def test_a_paste_cannot_smuggle_a_batch_of_guesses(self):
        """
        The rate limit counts requests, so the number of codes one request may
        test is the thing that actually bounds guessing.
        """
        stuffed = " ".join(f"AAAA{n:06d}" for n in range(50))
        self.assertLessEqual(len(extract_codes(stuffed)), 3)

    def test_absurdly_long_input_is_truncated(self):
        self.assertLessEqual(len(extract_codes("X" * 5000)), 3)

    # ---- end to end --------------------------------------------------------

    def test_pasting_the_message_connects_the_device(self):
        resp = self.validate(self.MESSAGE, "AA:BB:CC:11:00:01")
        self.assertEqual(resp.status_code, 200, resp.data)
        c = Customer.objects.all_tenants().get(pk=self.data["t1"]["customer"].pk)
        self.assertEqual(c.hotspot_username, "AA:BB:CC:11:00:01")

    def test_a_message_from_another_operator_does_not_work(self):
        resp = self.validate(self.MESSAGE, "AA:BB:CC:11:00:02", tenant=self.t2)
        self.assertEqual(resp.status_code, 400)

    def test_an_invented_message_grants_nothing(self):
        """A forged SMS is just a string. The receipt has to be one of ours."""
        forged = self.MESSAGE.replace("TGX11AA001", "ZZ99ZZ9999")
        resp = self.validate(forged, "AA:BB:CC:11:00:03")
        self.assertEqual(resp.status_code, 400)

    def test_a_message_for_an_unpaid_invoice_grants_nothing(self):
        with tenant_context(self.t1):
            inv = self.sub.invoice
            inv.payment_status = "unpaid"
            inv.save(update_fields=["payment_status"])
            Voucher.objects.all_tenants().filter(pk=self.voucher.pk).delete()
        resp = self.validate(self.MESSAGE, "AA:BB:CC:11:00:04")
        self.assertEqual(resp.status_code, 400)



# =====================================================
# 33. Finding a payment, and finding a hotspot subscriber
# =====================================================

class MpesaLedgerTests(TwoOperatorMixin, TestCase):
    """
    Reading the M-Pesa ledger, which until now could only be read when it had
    gone wrong.
    """

    URL = "/api/mpesa/transactions/"

    def setUp(self):
        cache.clear()
        self.build_operators()
        for receipt, ok in (("RCPTOK0001", True), ("RCPTNO0001", False)):
            d = self.data["t1"]
            with tenant_context(d["tenant"]):
                MpesaTransaction.objects.create(
                    tenant=d["tenant"],
                    mpesa_receipt=receipt,
                    amount=Decimal("500.00"),
                    phone_number="254711000101",
                    account_reference=d["invoice"].invoice_number,
                    invoice=d["invoice"],
                    raw_payload={},
                    status="success" if ok else "failed",
                    processed=ok,
                    error_message="" if ok else "Amount mismatch",
                )
        with tenant_context(self.t2):
            MpesaTransaction.objects.create(
                tenant=self.t2, mpesa_receipt="OTHER00001", amount=Decimal("10.00"),
                phone_number="254722000201", raw_payload={}, status="success",
                processed=True,
            )

    def test_an_operator_sees_every_transaction_of_theirs(self):
        resp = self.auth(self.admin1).get(self.URL)
        self.assertEqual(resp.status_code, 200)
        receipts = {r["mpesa_receipt"] for r in resp.data["results"]}
        self.assertEqual(receipts, {"RCPTOK0001", "RCPTNO0001"})

    def test_an_operator_never_sees_another_operators(self):
        """The property that matters most in a payments view."""
        resp = self.auth(self.admin1).get(self.URL)
        receipts = {r["mpesa_receipt"] for r in resp.data["results"]}
        self.assertNotIn("OTHER00001", receipts)

    def test_a_receipt_can_be_looked_up(self):
        """What an operator is holding when a customer reads one out."""
        resp = self.auth(self.admin1).get(self.URL, {"search": "RCPTOK0001"})
        self.assertEqual(len(resp.data["results"]), 1)
        self.assertEqual(resp.data["results"][0]["mpesa_receipt"], "RCPTOK0001")

    def test_searching_by_phone_works(self):
        resp = self.auth(self.admin1).get(self.URL, {"search": "254711000101"})
        self.assertEqual(len(resp.data["results"]), 2)

    def test_failures_can_be_isolated(self):
        resp = self.auth(self.admin1).get(self.URL, {"status": "failed"})
        self.assertEqual(len(resp.data["results"]), 1)
        self.assertEqual(resp.data["results"][0]["error_message"], "Amount mismatch")

    def test_a_row_says_who_it_was(self):
        resp = self.auth(self.admin1).get(self.URL, {"search": "RCPTOK0001"})
        row = resp.data["results"][0]
        self.assertEqual(row["customer"], self.data["t1"]["customer"].full_name)
        self.assertEqual(row["connection_type"], "pppoe")
        self.assertEqual(row["invoice_number"], self.data["t1"]["invoice"].invoice_number)

    def test_a_subscriber_cannot_read_the_ledger(self):
        customer_user = User.objects.create_user(
            username="sub_reader", password="pw", role=User.CUSTOMER, tenant=self.t1)
        resp = self.auth(customer_user).get(self.URL)
        self.assertEqual(resp.status_code, 403)


class HotspotCustomerSearchTests(TwoOperatorMixin, TestCase):
    """
    A hotspot subscriber has no PPPoE username, so the customer search matched
    them on name and phone alone — and the code on their receipt, the one thing
    both they and the operator are actually holding, found nothing.
    """

    URL = "/api/customers/"

    def setUp(self):
        cache.clear()
        self.build_operators()
        d = self.data["t1"]
        with tenant_context(self.t1):
            d["customer"].connection_type = "hotspot"
            d["customer"].pppoe_username = ""
            d["customer"].hotspot_username = "AA:BB:CC:DD:EE:FF"
            d["customer"].save()
            sub = d["sub"]
            sub.expiry_date = timezone.now() + timezone.timedelta(days=2)
            sub.save()
            self.voucher = Voucher.objects.create(
                tenant=self.t1, code="WIFI-FIND01", subscription=sub,
                expires_at=sub.expiry_date,
            )
            Payment.objects.create(
                tenant=self.t1, customer=d["customer"], subscription=sub,
                amount=Decimal("50.00"), method="mpesa", reference="RCPTFIND01",
            )

    def search(self, term):
        resp = self.auth(self.admin1).get(self.URL, {"search": term})
        self.assertEqual(resp.status_code, 200, resp.data)
        return resp.data["results"]

    def test_a_voucher_code_finds_its_subscriber(self):
        rows = self.search("WIFI-FIND01")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], self.data["t1"]["customer"].id)

    def test_an_mpesa_receipt_finds_its_subscriber(self):
        rows = self.search("RCPTFIND01")
        self.assertEqual(len(rows), 1)

    def test_a_device_mac_finds_its_subscriber(self):
        rows = self.search("AA:BB:CC:DD:EE:FF")
        self.assertEqual(len(rows), 1)

    def test_the_row_carries_the_voucher(self):
        """
        The newest active one. Paying mints a voucher, so a subscriber who has
        renewed holds several — showing the oldest would send them to reception
        with a code that no longer works.
        """
        with tenant_context(self.t1):
            newest = (
                Voucher.objects.filter(subscription=self.data["t1"]["sub"], is_active=True)
                .order_by("-created_at").first()
            )
        rows = self.search("WIFI-FIND01")
        self.assertEqual(rows[0]["voucher_code"], newest.code)
        self.assertEqual(rows[0]["hotspot_username"], "AA:BB:CC:DD:EE:FF")

    def test_the_search_still_does_not_reach_across_operators(self):
        """It joins more tables now; it must not join its way out of a tenant."""
        rows = self.search(self.data["t2"]["customer"].full_name)
        self.assertEqual(rows, [])

    def test_one_customer_appears_once_despite_the_joins(self):
        """
        Searching across subscriptions and payments multiplies rows. A customer
        with three payments must not be listed three times.
        """
        with tenant_context(self.t1):
            for n in range(3):
                Payment.objects.create(
                    tenant=self.t1, customer=self.data["t1"]["customer"],
                    subscription=self.data["t1"]["sub"],
                    amount=Decimal("50.00"), method="mpesa",
                    reference=f"RCPTDUP{n:03d}",
                )
        rows = self.search(self.data["t1"]["customer"].full_name)
        self.assertEqual(len(rows), 1)

    def test_the_voucher_column_does_not_cost_a_query_per_row(self):
        """
        The serializer walks each customer's subscriptions and their vouchers.
        Without prefetching that is two queries per row, which is invisible on
        a seeded test and painful on a real page of 25.
        """
        with tenant_context(self.t1):
            for n in range(6):
                c = Customer.objects.create(
                    tenant=self.t1, full_name=f"HS {n}", phone=f"2547330000{n:02d}",
                    connection_type="hotspot")
                s = Subscription.objects.create(
                    tenant=self.t1, customer=c, package=self.data["t1"]["package"],
                    expiry_date=timezone.now() + timezone.timedelta(days=1))
                Voucher.objects.create(
                    tenant=self.t1, code=f"WIFI-BULK{n:02d}", subscription=s,
                    expires_at=s.expiry_date)

        client = self.auth(self.admin1)
        client.get(self.URL)  # warm any per-connection setup
        with CaptureQueriesContext(connection) as ctx:
            resp = client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        self.assertLess(
            len(ctx.captured_queries), 15,
            f"{len(ctx.captured_queries)} queries for one page — prefetch lost",
        )


# =====================================================
# 34. The captive portal is a different origin
# =====================================================

class HotspotPortalCorsTests(TestCase):
    """
    login.html runs on the MikroTik and calls the API cross-origin. Its origin
    is whatever address that router answers on, which differs per operator and
    per site — so it can never be listed in CORS_ALLOWED_ORIGINS.

    Without the private-network rule the preflight returns 200 with no
    Access-Control-Allow-Origin, the browser refuses, and voucher login is dead
    on every router while the server logs look healthy. The same shape as the
    impersonation headers, and invisible to the live suite for the same reason:
    axios's Node adapter performs no preflight.
    """

    def preflight(self, origin, path="/api/hotspot/validate/", method="POST"):
        return APIClient().options(
            path,
            HTTP_ORIGIN=origin,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD=method,
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type",
        )

    def assertAllowed(self, origin, **kw):
        resp = self.preflight(origin, **kw)
        self.assertEqual(
            resp.headers.get("access-control-allow-origin"), origin,
            f"{origin} was refused — the portal there cannot call the API",
        )

    def assertRefused(self, origin):
        resp = self.preflight(origin)
        self.assertIsNone(
            resp.headers.get("access-control-allow-origin"),
            f"{origin} was allowed and should not be",
        )

    @override_settings(DEBUG=False)
    def test_the_addresses_a_mikrotik_actually_answers_on(self):
        for origin in (
            "http://10.5.50.1",
            "http://192.168.88.1",
            "http://172.16.0.1",
            "http://172.31.255.254",
            "http://login.hotspot",
            "https://192.168.1.1",
            "http://10.0.0.1:8080",
        ):
            with self.subTest(origin=origin):
                self.assertAllowed(origin)

    @override_settings(DEBUG=False)
    def test_the_connected_page_can_read_device_status(self):
        """alogin.html fetches the voucher from the same private origin."""
        self.assertAllowed(
            "http://10.5.50.1", path="/api/hotspot/status/", method="GET")

    @override_settings(DEBUG=False)
    def test_the_public_internet_is_still_not_allowed(self):
        """The rule is bounded to private addresses, not opened to everyone."""
        for origin in (
            "http://8.8.8.8",
            "https://evil.example.com",
            "http://172.15.0.1",     # just below the RFC1918 block
            "http://172.32.0.1",     # just above it
            "http://10.5.50.1.evil.com",
            "http://192.168.1.1.attacker.net",
        ):
            with self.subTest(origin=origin):
                self.assertRefused(origin)


# =====================================================
# 35. What the captive portal is handed
# =====================================================

class HotspotPortalPayloadTests(TwoOperatorMixin, TestCase):
    """
    The portal renders from one request, and that request must carry nothing
    image-shaped. A visitor there has no internet except the walled garden, so
    the page is markup only — a banner slot briefly lived here and was removed
    for exactly that reason.
    """

    def setUp(self):
        cache.clear()
        self.build_operators()
        with tenant_context(self.t1):
            Package.objects.create(
                tenant=self.t1, name="t1-hotspot", download_speed=5, upload_speed=5,
                price=Decimal("50.00"), duration_value=24, duration_unit="hours",
                monthly_data_cap_gb=0, is_hotspot=True)

    def portal(self, tenant):
        return APIClient().get(f"/api/hotspot/packages/?t={tenant.public_token}")

    def test_the_payload_carries_no_image_urls(self):
        """
        The reason the banner was removed. Anything here that resolves to an
        image is a request a customer with no internet has to make before the
        page is usable, so there must not be one.
        """
        body = json.dumps(self.portal(self.t1).data).lower()
        for shape in ("banner", ".jpg", ".png", ".webp", ".gif", "image"):
            self.assertNotIn(shape, body, f"{shape!r} is back in the portal payload")

    def test_the_support_number_is_carried(self):
        """The portal's footer offers it; it had nowhere to read it from."""
        self.t1.support_phone = "254700111222"
        self.t1.save(update_fields=["support_phone"])
        self.assertEqual(self.portal(self.t1).data["support_phone"], "254700111222")

    def test_an_unknown_portal_token_is_refused(self):
        resp = APIClient().get("/api/hotspot/packages/?t=not-a-token")
        self.assertEqual(resp.status_code, 404)

    def test_only_hotspot_packages_are_offered(self):
        """The PPPoE catalogue is not for sale to a walk-up customer."""
        names = {p["name"] for p in self.portal(self.t1).data["results"]}
        self.assertIn("t1-hotspot", names)
        self.assertNotIn("t1-package", names)

class MikrotikPortalPageTests(TestCase):
    """
    mikrotik-hotspot/ is uploaded to a router by hand and never imported by
    anything, so nothing else in this suite touches it. A broken tag or a
    mistyped MikroTik variable there does not fail a build — it renders as a
    blank page in front of a customer standing at a counter.

    These are the checks that survive that gap.
    """

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
            "meta", "param", "source", "track", "wbr"}

    # Everything MikroTik substitutes on a hotspot page. Anything else inside
    # $(...) is a typo, and a typo ships as literal text.
    KNOWN_VARS = {
        "mac", "ip", "username", "password", "error", "if", "endif",
        "link-login", "link-login-only", "link-logout", "link-status",
        "link-orig", "link-redirect", "session-time-left", "uptime",
        "uptime-secs", "idle-timeout", "refresh-timeout", "trial",
        "chap-id", "chap-challenge", "hostname", "identity",
        "bytes-in", "bytes-out", "bytes-in-nice", "bytes-out-nice",
        "packets-in", "packets-out",
        "limit-bytes-in", "limit-bytes-out",
        "remain-bytes-in", "remain-bytes-out",
    }

    @property
    def folder(self):
        """
        Beside the backend in a checkout; bind-mounted at the root in the
        container, because the folder sits outside the Docker build context
        and cannot be COPYed in. Both are checked so the tests run in either,
        and the assertion below means neither can silently find nothing.
        """
        from django.conf import settings
        root = Path(settings.BASE_DIR).parent
        for candidate in (root / "mikrotik-hotspot", Path("/mikrotik-hotspot")):
            if candidate.is_dir():
                return candidate
        return root / "mikrotik-hotspot"

    def pages(self):
        found = sorted(self.folder.glob("*.html"))
        self.assertTrue(found, f"no portal pages under {self.folder}")
        return found

    def test_the_folder_is_where_it_is_expected(self):
        names = {p.name for p in self.pages()}
        self.assertIn("login.html", names)
        self.assertIn("alogin.html", names)

    def test_every_tag_is_closed(self):
        from html.parser import HTMLParser

        void = self.VOID

        class Balance(HTMLParser):
            def __init__(self):
                super().__init__()
                self.stack, self.problems = [], []

            def handle_starttag(self, tag, attrs):
                if tag not in void:
                    self.stack.append((tag, self.getpos()[0]))

            def handle_endtag(self, tag):
                if tag in void:
                    return
                if not self.stack:
                    self.problems.append(f"line {self.getpos()[0]}: stray </{tag}>")
                    return
                opened, line = self.stack.pop()
                if opened != tag:
                    self.problems.append(
                        f"line {self.getpos()[0]}: </{tag}> closes <{opened}> from line {line}")

        for page in self.pages():
            with self.subTest(page=page.name):
                parser = Balance()
                parser.feed(page.read_text(encoding="utf-8"))
                unclosed = [f"<{t}> line {n}" for t, n in parser.stack]
                self.assertEqual(parser.problems + unclosed, [])

    def test_only_real_mikrotik_variables_are_used(self):
        for page in self.pages():
            with self.subTest(page=page.name):
                used = set(re.findall(r"\$\(([a-z0-9-]+)\)", page.read_text(encoding="utf-8")))
                self.assertEqual(
                    used - self.KNOWN_VARS, set(),
                    "these would render as literal text on the page",
                )

    def test_local_assets_exist(self):
        """
        The router serves only what is uploaded to it. A path that resolves in
        the repository and not on the device is a broken image on every phone.
        """
        for page in self.pages():
            for src in re.findall(r'src="(?!https?://|data:)([^"]+)"',
                                  page.read_text(encoding="utf-8")):
                with self.subTest(page=page.name, src=src):
                    self.assertTrue((self.folder / src).exists(), f"{src} is not in the folder")

    def test_the_logo_stays_small_enough_to_serve(self):
        """
        A captive portal is the one place a visitor has no working internet,
        and MikroTik flash is small. The source logo is 2.2 MB; the copy here
        must stay a fraction of that or it silently stops being uploadable.
        """
        logo = self.folder / "smartbill.png"
        self.assertTrue(logo.exists())
        self.assertLess(logo.stat().st_size, 120_000, "the portal logo has grown")

    def test_settings_live_in_one_file(self):
        """
        Each page used to carry its own copy of the backend address and the
        operator token: four places to keep in step by hand, and nothing to
        notice when they drifted. They read config.js now, so a page that
        talks to the API must include it and must not redeclare either value.
        """
        config = self.folder / "config.js"
        self.assertTrue(config.exists(), "the one file to edit is missing")

        text = config.read_text(encoding="utf-8")
        for name in ("API_BASE", "TENANT_TOKEN"):
            self.assertRegex(text, rf"var {name}\s*=")

        for page in self.pages():
            body = page.read_text(encoding="utf-8")
            if "API_BASE" not in body and "loadProviderName" not in body:
                continue
            with self.subTest(page=page.name):
                self.assertIn('src="config.js"', body, "does not read the shared config")
                self.assertNotRegex(
                    body, r"var API_BASE\s*=",
                    "declares its own copy of a value that lives in config.js")

    def test_every_page_shows_whose_wifi_it_is(self):
        """
        These sit on a router and cannot know whose they are without asking.
        They carried one operator's name hardcoded, and after that was removed
        they carried nothing — which left a subscriber looking at the billing
        platform's branding instead of the provider they actually pay.
        """
        for name in ("login.html", "alogin.html", "status.html", "logout.html"):
            page = self.folder / name
            with self.subTest(page=name):
                body = page.read_text(encoding="utf-8")
                self.assertRegex(
                    body, r'id="provider"',
                    "has nowhere to put the operator's name")
                self.assertTrue(
                    "loadProviderName" in body or "data.provider" in body
                    or "d.provider" in body,
                    "never asks who the operator is")

    def test_no_page_hardcodes_an_operator(self):
        """The name belongs to whoever deployed it, not to whoever wrote it."""
        for page in self.pages():
            with self.subTest(page=page.name):
                self.assertNotIn("Skylink", page.read_text(encoding="utf-8"))

    def test_every_page_is_built_for_a_phone(self):
        for page in self.pages():
            with self.subTest(page=page.name):
                self.assertIn(
                    '<meta name="viewport"', page.read_text(encoding="utf-8"),
                    "without this it renders desktop-wide on a phone",
                )

    def test_the_code_box_can_take_a_whole_mpesa_message(self):
        """
        It was capped at 30 characters — shorter than any M-Pesa message, so
        the one thing a customer has on their phone could not be pasted in.
        """
        text = (self.folder / "login.html").read_text(encoding="utf-8")
        box = re.search(r'<(input|textarea)[^>]*id="code"[^>]*>', text)
        self.assertIsNotNone(box, "the code box is gone")
        self.assertNotIn("maxlength", box.group(0))

    def test_the_router_page_sells_without_leaving_the_router(self):
        """
        Buying used to bounce to the web app: another origin, another page
        load, another thing to fail for someone standing at a counter. The
        whole purchase is on this page now, so it must talk to all four
        endpoints that takes.
        """
        text = (self.folder / "login.html").read_text(encoding="utf-8")
        for endpoint in ("/hotspot/packages/", "/hotspot/purchase/",
                         "/hotspot/payment-status/", "/hotspot/validate/"):
            with self.subTest(endpoint=endpoint):
                self.assertIn(endpoint, text)

    def test_the_router_page_no_longer_hands_off_to_the_web_app(self):
        """The redirect it replaced, so it cannot quietly come back."""
        text = (self.folder / "login.html").read_text(encoding="utf-8")
        self.assertNotIn("PAYMENT_URL", text)

    def test_a_package_name_is_never_interpolated_into_markup(self):
        """
        Package names are operator input and this page has no framework
        escaping them, so they are assigned as text.
        """
        text = (self.folder / "login.html").read_text(encoding="utf-8")
        self.assertIn(".textContent = pkg.name", text)
        self.assertNotRegex(text, r"innerHTML[^;]*pkg\.name")

    def test_the_login_page_can_answer_chap(self):
        """
        RouterOS ships with login-by = cookie,http-chap, and under CHAP a
        plaintext password — empty or not — is rejected. The page posted one
        anyway, so on a router left at its defaults the customer paid and then
        got "invalid username or password". md5.js had been sitting in this
        folder for exactly this and nothing loaded it.
        """
        text = (self.folder / "login.html").read_text(encoding="utf-8")
        self.assertIn("md5.js", text, "the hasher is not loaded")
        self.assertIn("chap-id", text, "the challenge is never read")
        self.assertRegex(text, r"MD5\(\s*CHAP_ID")

    def test_the_login_page_backs_off_when_throttled(self):
        """
        Polling shares a rate limit with every other device behind the same
        NAT. Hammering through a 429 keeps the bucket empty for everyone on
        the hotspot, including the person whose payment is landing.
        """
        text = (self.folder / "login.html").read_text(encoding="utf-8")
        self.assertIn("429", text)

    def test_no_page_carries_a_second_backend_url(self):
        """
        logout.html had its own PAYMENT_URL to keep in step with login.html by
        hand, and nothing validated it. Two sources of truth for one address.
        """
        for page in self.pages():
            with self.subTest(page=page.name):
                self.assertNotIn("PAYMENT_URL", page.read_text(encoding="utf-8"))

    def test_the_connected_page_shows_the_voucher(self):
        """
        The reason it was rewritten. Without messaging credentials — optional,
        per operator — this is the only place the code is ever displayed.
        """
        text = (self.folder / "alogin.html").read_text(encoding="utf-8")
        self.assertIn("voucher_code", text)
        self.assertIn("/hotspot/status/", text)


# =====================================================
# 37. Who may read a voucher back
# =====================================================

class HotspotPollTokenTests(TwoOperatorMixin, TestCase):
    """
    /hotspot/payment-status/ returns the voucher once an invoice is paid, and
    it was addressed by invoice number alone.

    Those look like INV-20260801191649-1338 — a second-resolution timestamp
    and four hex characters — so a five-minute window is roughly twenty
    million combinations. Not guessable by hand, but nothing about it is
    secret either, and the only thing between a stranger and somebody else's
    voucher was the rate limit. A rate limit is a cost, not a boundary.
    """

    def setUp(self):
        cache.clear()
        self.build_operators()
        d = self.data["t1"]
        with tenant_context(self.t1):
            d["customer"].connection_type = "hotspot"
            d["customer"].pppoe_username = ""
            d["customer"].save()
            self.sub = d["sub"]
            self.sub.expiry_date = timezone.now() + timezone.timedelta(days=1)
            self.sub.save()
            self.invoice = self.sub.invoice
            self.invoice.payment_status = "paid"
            self.invoice.save(update_fields=["payment_status"])
            self.voucher = Voucher.objects.create(
                tenant=self.t1, code="WIFI-POLL01", subscription=self.sub,
                expires_at=self.sub.expiry_date,
            )
        self.ref = self.invoice.invoice_number

    def poll(self, token=None, tenant=None):
        params = {"t": (tenant or self.t1).public_token, "ref": self.ref}
        if token is not None:
            params["token"] = token
        return APIClient().get("/api/hotspot/payment-status/", params)

    # ---- the hole -----------------------------------------------------------

    def test_the_invoice_number_alone_no_longer_yields_the_voucher(self):
        body = self.poll().data
        self.assertEqual(body["status"], "paid")
        self.assertNotIn("voucher_code", body, "guessing a reference is enough again")

    def test_a_wrong_token_yields_nothing(self):
        body = self.poll(token="0" * 32).data
        self.assertEqual(body["status"], "paid")
        self.assertNotIn("voucher_code", body)

    def test_a_token_for_a_different_invoice_does_not_transfer(self):
        from billing.security import poll_token_for

        body = self.poll(token=poll_token_for("INV-SOMETHING-ELSE")).data
        self.assertNotIn("voucher_code", body)

    # ---- the purchaser ------------------------------------------------------

    def test_the_token_purchase_hands_back_releases_it(self):
        from billing.security import poll_token_for

        body = self.poll(token=poll_token_for(self.ref)).data
        self.assertEqual(body["status"], "paid")
        self.assertEqual(body["voucher_code"], "WIFI-POLL01")

    def test_status_is_still_answered_without_a_token(self):
        """
        A portal that reloaded and lost the token still needs to know whether
        the money arrived. That answer grants nothing.
        """
        with tenant_context(self.t1):
            self.invoice.payment_status = "unpaid"
            self.invoice.save(update_fields=["payment_status"])
        self.assertEqual(self.poll().data["status"], "unpaid")

    def test_a_purchase_returns_a_token(self):
        with tenant_context(self.t1):
            SystemSetting.objects.create(
                tenant=self.t1, key="MPESA_CONSUMER_KEY", value="k")
            SystemSetting.objects.create(
                tenant=self.t1, key="MPESA_CONSUMER_SECRET", value="s")
            SystemSetting.objects.create(
                tenant=self.t1, key="MPESA_SHORTCODE", value="174379")
            SystemSetting.objects.create(
                tenant=self.t1, key="MPESA_PASSKEY", value="p")
            pkg = Package.objects.create(
                tenant=self.t1, name="poll-pkg", download_speed=5, upload_speed=5,
                price=Decimal("50.00"), duration_value=1, duration_unit="hours",
                monthly_data_cap_gb=0, is_hotspot=True)
        clear_settings_cache(tenant=self.t1)

        with patch("billing.views.initiate_stk_push_task.delay"):
            resp = APIClient().post(
                f"/api/hotspot/purchase/?t={self.t1.public_token}",
                {"package_id": pkg.id, "phone": "0712345678"},
                format="json",
            )
        self.assertEqual(resp.status_code, 202, resp.data)
        self.assertTrue(resp.data["poll_token"])
        self.assertEqual(len(resp.data["poll_token"]), 32)

    def test_the_token_is_not_the_reference_in_disguise(self):
        from billing.security import poll_token_for

        self.assertNotIn(self.ref, poll_token_for(self.ref))

    def test_another_operator_cannot_read_it_even_with_the_token(self):
        """Scoping still comes first — the token is not a way around it."""
        from billing.security import poll_token_for

        body = self.poll(token=poll_token_for(self.ref), tenant=self.t2).data
        self.assertEqual(body["status"], "not_found")


class HotspotPollThrottleTests(TwoOperatorMixin, TestCase):
    """
    Every customer of a hotspot sits behind one NAT, so the API sees a single
    address for the whole site. An IP-keyed limit on the endpoint a portal
    polls means one person waiting on an M-Pesa prompt spends the site's
    entire allowance and everyone else starts getting 429s while their own
    payments are landing.
    """

    def setUp(self):
        cache.clear()
        self.build_operators()

    def test_two_devices_do_not_share_one_bucket(self):
        from billing.throttles import HotspotPollThrottle

        throttle = HotspotPollThrottle()
        factory = APIClient()

        a = factory.get("/api/hotspot/status/", {"mac": "AA:AA:AA:AA:AA:AA"}).wsgi_request
        b = factory.get("/api/hotspot/status/", {"mac": "BB:BB:BB:BB:BB:BB"}).wsgi_request
        self.assertNotEqual(
            throttle.get_cache_key(a, None), throttle.get_cache_key(b, None),
            "one device's polling would exhaust another's allowance",
        )

    def test_a_caller_identifying_nothing_falls_back_to_its_address(self):
        """Which is exactly where someone guessing references belongs."""
        from billing.throttles import HotspotPollThrottle

        throttle = HotspotPollThrottle()
        request = APIClient().get("/api/hotspot/status/").wsgi_request
        key = throttle.get_cache_key(request, None)
        self.assertIn("hotspot_poll", key)

    def test_the_mac_is_not_stored_in_the_cache_key(self):
        """A cache key is not a place to keep a device identifier."""
        from billing.throttles import HotspotPollThrottle

        throttle = HotspotPollThrottle()
        request = APIClient().get(
            "/api/hotspot/status/", {"mac": "AA:BB:CC:DD:EE:FF"}).wsgi_request
        self.assertNotIn("AA:BB:CC:DD:EE:FF", throttle.get_cache_key(request, None))

    def test_polling_has_more_headroom_than_guessing(self):
        """
        The two scopes exist to be different. If they ever converge, either
        polling is throttled to uselessness or guessing is not throttled at
        all.
        """
        from django.conf import settings

        rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        poll = int(rates["hotspot_poll"].split("/")[0])
        guess = int(rates["hotspot_public"].split("/")[0])
        self.assertGreater(poll, guess)


# =====================================================
# 38. The subscriber's own portal
# =====================================================

class CustomerPortalTests(TwoOperatorMixin, TestCase):
    """
    The two pages a PPPoE subscriber actually uses.

    Renewing was broken end to end. The page listed packages from an operator
    endpoint that answers 403 for a subscriber — with nothing catching it, so
    the list was silently empty and the button permanently disabled — and had
    it worked, paying navigated to a route that was never registered, landing
    the customer on the 404 page with their money gone.
    """

    def setUp(self):
        cache.clear()
        self.build_operators()

        for tag in ("t1", "t2"):
            d = self.data[tag]
            with tenant_context(d["tenant"]):
                d["customer"].pppoe_username = f"{tag}-user"
                d["customer"].pppoe_password = "secret"
                d["customer"].save()
                d["user"] = User.objects.create_user(
                    username=f"{tag}_sub", password="pw",
                    role=User.CUSTOMER, tenant=d["tenant"])
                d["customer"].user = d["user"]
                d["customer"].save(update_fields=["user"])
                d["hotspot_pkg"] = Package.objects.create(
                    tenant=d["tenant"], name=f"{tag}-hotspot",
                    download_speed=5, upload_speed=5, price=Decimal("50.00"),
                    duration_value=1, duration_unit="hours",
                    monthly_data_cap_gb=0, is_hotspot=True)

        self.sub_user = self.data["t1"]["user"]

    # ---- the catalogue -----------------------------------------------------

    def test_a_subscriber_can_list_what_they_may_renew_onto(self):
        resp = self.auth(self.sub_user).get("/api/pppoe/packages/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(resp.data["results"], "the renew page has nothing to show")

    def test_the_operator_catalogue_stays_shut(self):
        """
        The reason this endpoint exists rather than widening that one: the
        admin serializer is fields = "__all__".
        """
        self.assertEqual(
            self.auth(self.sub_user).get("/api/packages/").status_code, 403)

    def test_only_this_operators_packages_are_offered(self):
        names = {p["name"] for p in
                 self.auth(self.sub_user).get("/api/pppoe/packages/").data["results"]}
        self.assertIn("t1-package", names)
        self.assertNotIn("t2-package", names)

    def test_hotspot_packages_are_not_offered_to_a_pppoe_line(self):
        names = {p["name"] for p in
                 self.auth(self.sub_user).get("/api/pppoe/packages/").data["results"]}
        self.assertNotIn("t1-hotspot", names)

    def test_the_catalogue_exposes_no_internal_columns(self):
        """A subscriber sees a price list, not the model."""
        row = self.auth(self.sub_user).get("/api/pppoe/packages/").data["results"][0]
        for leaked in ("tenant", "monthly_data_cap_gb_raw", "created_at"):
            self.assertNotIn(leaked, row)

    # ---- renewing ----------------------------------------------------------

    def _configure_mpesa(self, tenant):
        with tenant_context(tenant):
            for key in ("MPESA_CONSUMER_KEY", "MPESA_CONSUMER_SECRET",
                        "MPESA_SHORTCODE", "MPESA_PASSKEY"):
                SystemSetting.objects.update_or_create(
                    tenant=tenant, key=key, defaults={"value": "x"})
        clear_settings_cache(tenant=tenant)

    def test_renewing_queues_the_prompt_rather_than_waiting_on_safaricom(self):
        """
        This called Daraja inside the request: a worker held for however long
        Safaricom took, on a page a customer is watching, and a slow answer
        became a timeout with the subscription already created.
        """
        self._configure_mpesa(self.t1)
        with patch("billing.views.initiate_stk_push_task.delay") as queued, \
             patch("billing.views.initiate_stk_push") as direct:
            resp = self.auth(self.sub_user).post(
                "/api/pppoe/renew/",
                {"package_id": self.data["t1"]["package"].id, "phone": "0712345678"},
                format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(queued.called)
        self.assertFalse(direct.called, "still calling Safaricom in the request")

    def test_renewing_returns_the_invoice_the_next_page_needs(self):
        self._configure_mpesa(self.t1)
        with patch("billing.views.initiate_stk_push_task.delay"):
            resp = self.auth(self.sub_user).post(
                "/api/pppoe/renew/",
                {"package_id": self.data["t1"]["package"].id, "phone": "0712345678"},
                format="json")
        self.assertTrue(resp.data["invoice_number"])

    def test_an_operator_with_no_mpesa_says_so_immediately(self):
        resp = self.auth(self.sub_user).post(
            "/api/pppoe/renew/",
            {"package_id": self.data["t1"]["package"].id, "phone": "0712345678"},
            format="json")
        self.assertEqual(resp.status_code, 503)
        self.assertIn("cannot accept payments", resp.data["detail"])

    def test_no_ghost_subscription_when_payments_are_unconfigured(self):
        before = Subscription.objects.all_tenants().filter(
            customer=self.data["t1"]["customer"]).count()
        self.auth(self.sub_user).post(
            "/api/pppoe/renew/",
            {"package_id": self.data["t1"]["package"].id, "phone": "0712345678"},
            format="json")
        self.assertEqual(
            Subscription.objects.all_tenants().filter(
                customer=self.data["t1"]["customer"]).count(),
            before, "a failed renewal left a subscription behind")

    def test_a_pppoe_line_cannot_be_renewed_onto_hotspot_pricing(self):
        self._configure_mpesa(self.t1)
        resp = self.auth(self.sub_user).post(
            "/api/pppoe/renew/",
            {"package_id": self.data["t1"]["hotspot_pkg"].id, "phone": "0712345678"},
            format="json")
        self.assertEqual(resp.status_code, 404)

    def test_another_operators_package_cannot_be_renewed_onto(self):
        self._configure_mpesa(self.t1)
        resp = self.auth(self.sub_user).post(
            "/api/pppoe/renew/",
            {"package_id": self.data["t2"]["package"].id, "phone": "0712345678"},
            format="json")
        self.assertEqual(resp.status_code, 404)

    # ---- waiting for the money ---------------------------------------------

    def test_a_subscriber_can_watch_their_own_renewal(self):
        invoice = self.data["t1"]["invoice"]
        resp = self.auth(self.sub_user).get(
            "/api/pppoe/renewal-status/", {"ref": invoice.invoice_number})
        self.assertEqual(resp.status_code, 200)
        self.assertIn(resp.data["status"], ("unpaid", "pending", "paid"))

    def test_a_subscriber_cannot_watch_somebody_elses(self):
        """
        Scoped to the caller's own invoices, which is why this one needs no
        token where the hotspot equivalent does.
        """
        other = self.data["t2"]["invoice"]
        resp = self.auth(self.sub_user).get(
            "/api/pppoe/renewal-status/", {"ref": other.invoice_number})
        self.assertEqual(resp.data["status"], "not_found")

    def test_a_paid_renewal_reports_the_new_expiry(self):
        invoice = self.data["t1"]["invoice"]
        with tenant_context(self.t1):
            invoice.payment_status = "paid"
            invoice.save(update_fields=["payment_status"])
        resp = self.auth(self.sub_user).get(
            "/api/pppoe/renewal-status/", {"ref": invoice.invoice_number})
        self.assertEqual(resp.data["status"], "paid")
        self.assertTrue(resp.data["expires_at"])

    # ---- the portal itself -------------------------------------------------

    def test_a_subscriber_reads_only_their_own_account(self):
        resp = self.auth(self.sub_user).get("/api/pppoe/portal/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["customer"]["full_name"], "t1-customer")

    def test_operator_staff_cannot_use_the_subscriber_portal_as_a_backdoor(self):
        """It reads customer_profile, and staff have none."""
        resp = self.auth(self.admin1).get("/api/pppoe/portal/")
        self.assertEqual(resp.status_code, 404)

    def test_a_subscriber_cannot_reach_the_operator_console(self):
        client = self.auth(self.sub_user)
        for path in ("/api/customers/", "/api/mpesa/transactions/",
                     "/api/system/settings/", "/api/admin/pppoe/sessions/"):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 403)


# =====================================================
# 39. The operator console, as both roles that use it
# =====================================================

class AccessLookupTests(TwoOperatorMixin, TestCase):
    """
    The page someone opens with a customer standing in front of them, reading
    out whatever they have.

    It searched voucher code, M-Pesa receipt and phone — not the PPPoE
    username, which is the only identifier most subscribers have, and not the
    device MAC. The page named after looking up access could not look up the
    commonest kind of it.
    """

    URL = "/api/admin/access-lookup/"

    def setUp(self):
        cache.clear()
        self.build_operators()
        d = self.data["t1"]
        with tenant_context(self.t1):
            d["customer"].pppoe_username = "SKY-9001"
            d["customer"].save()
            self.sub = d["sub"]
            self.sub.expiry_date = timezone.now() + timezone.timedelta(days=5)
            self.sub.status = "active"
            self.sub.save()

            self.hotspot = Customer.objects.create(
                tenant=self.t1, full_name="HS One", phone="254733000001",
                connection_type="hotspot", hotspot_username="AA:BB:CC:11:22:33")
            hs_sub = Subscription.objects.create(
                tenant=self.t1, customer=self.hotspot, package=d["package"],
                expiry_date=timezone.now() + timezone.timedelta(days=1))
            Voucher.objects.create(
                tenant=self.t1, code="WIFI-LOOK01", subscription=hs_sub,
                expires_at=hs_sub.expiry_date)

    def look(self, q, user=None):
        return self.auth(user or self.admin1).get(self.URL, {"q": q})

    def test_a_pppoe_username_finds_its_subscriber(self):
        resp = self.look("SKY-9001")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["customer"]["name"], "t1-customer")
        self.assertEqual(resp.data["type"], "pppoe_username")

    def test_a_username_is_not_case_sensitive(self):
        """Read off a screen and typed by hand, at a counter."""
        self.assertEqual(self.look("sky-9001").status_code, 200)

    def test_a_device_mac_finds_its_subscriber(self):
        resp = self.look("AA:BB:CC:11:22:33")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["customer"]["name"], "HS One")
        self.assertEqual(resp.data["type"], "device")

    def test_the_older_ways_still_work(self):
        for query, kind in (("WIFI-LOOK01", "voucher"),
                            ("254733000001", "phone")):
            with self.subTest(query=query):
                resp = self.look(query)
                self.assertEqual(resp.status_code, 200, resp.data)
                self.assertEqual(resp.data["type"], kind)

    def test_a_username_does_not_reach_across_operators(self):
        """It joins on more fields now; it must not join out of the tenant."""
        with tenant_context(self.t2):
            self.data["t2"]["customer"].pppoe_username = "SKY-9002"
            self.data["t2"]["customer"].save()
        self.assertEqual(self.look("SKY-9002").status_code, 404)

    def test_an_unknown_query_is_still_a_clean_miss(self):
        self.assertEqual(self.look("nothing-like-this").status_code, 404)

    def test_operator_staff_may_look_someone_up(self):
        """The support desk is the whole point of the staff role."""
        staff = User.objects.create_user(
            username="look_staff", password="pw", role=User.TENANT_STAFF,
            tenant=self.t1, is_staff=True)
        self.assertEqual(self.look("SKY-9001", user=staff).status_code, 200)


class OperatorRoleSurfaceTests(TwoOperatorMixin, TestCase):
    """
    What each role that can sign into the operator console may actually read.

    The customer portal shipped a page whose own users were forbidden from the
    endpoint it fetched, and the silence made it look like there was simply no
    data. These pin the same question for the console: a page a role can open
    is a page whose reads that role can make.
    """

    def setUp(self):
        cache.clear()
        self.build_operators()
        self.staff = User.objects.create_user(
            username="surface_staff", password="pw", role=User.TENANT_STAFF,
            tenant=self.t1, is_staff=True)

    # Every read behind a page operator staff can open.
    STAFF_READABLE = [
        "/api/reports/revenue/",
        "/api/admin/usage/daily/",
        "/api/reports/analytics/",
        "/api/customers/",
        "/api/packages/",
        "/api/dashboard/invoices/unpaid/",
        "/api/mpesa/transactions/",
        "/api/dashboard/mpesa/failed/",
        "/api/admin/pppoe/sessions/",
        "/api/admin/routers/",
        "/api/admin/routers/health/",
        "/api/admin/routers/events/",
        "/api/admin/routers/failovers/",
        "/api/auth/profile/",
    ]

    # Behind a page only an admin can open.
    ADMIN_ONLY = [
        "/api/system/settings/",
        "/api/users/",
        "/api/stations/",
        "/api/platform/my-account/",
        "/api/platform/invoices/",
    ]

    def test_staff_can_read_every_page_they_can_open(self):
        client = self.auth(self.staff)
        for path in self.STAFF_READABLE:
            with self.subTest(path=path):
                self.assertLess(
                    client.get(path).status_code, 400,
                    "the router admits staff to this page but the API refuses them",
                )

    def test_staff_cannot_read_what_only_an_admin_may_open(self):
        client = self.auth(self.staff)
        for path in self.ADMIN_ONLY:
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 403)

    def test_an_admin_can_read_all_of_it(self):
        client = self.auth(self.admin1)
        for path in self.STAFF_READABLE + self.ADMIN_ONLY:
            with self.subTest(path=path):
                self.assertLess(client.get(path).status_code, 400)

    def test_staff_may_look_but_not_change(self):
        """The split the staff role exists for."""
        client = self.auth(self.staff)
        self.assertEqual(client.get("/api/customers/").status_code, 200)
        self.assertEqual(
            client.post("/api/customers/",
                        {"full_name": "Nope", "phone": "254700999999",
                         "connection_type": "pppoe"}, format="json").status_code,
            403)
        self.assertEqual(
            client.delete(f"/api/customers/{self.data['t1']['customer'].id}/").status_code,
            403)

    def test_nothing_here_reaches_the_other_operator(self):
        client = self.auth(self.admin1)
        rows = client.get("/api/customers/").data["results"]
        self.assertTrue(rows)
        self.assertNotIn("t2-customer", {r["full_name"] for r in rows})


# =====================================================
# 40. Support contacts, and selling at the counter
# =====================================================

class SupportContactTests(TwoOperatorMixin, TestCase):
    """
    A customer at a hotspot has no internet and no other way to ask for help,
    so the portal has to carry the operator's numbers. Two of them, because one
    is a person and people are sometimes unreachable.
    """

    def setUp(self):
        cache.clear()
        self.build_operators()

    def portal(self, tenant):
        return APIClient().get(f"/api/hotspot/packages/?t={tenant.public_token}")

    def test_both_numbers_reach_the_portal(self):
        self.t1.support_phone = "0722111222"
        self.t1.support_phone_2 = "0733444555"
        self.t1.save(update_fields=["support_phone", "support_phone_2"])
        self.assertEqual(
            self.portal(self.t1).data["support_phones"],
            ["0722111222", "0733444555"],
        )

    def test_one_number_is_fine(self):
        self.t1.support_phone = "0722111222"
        self.t1.save(update_fields=["support_phone"])
        self.assertEqual(self.portal(self.t1).data["support_phones"], ["0722111222"])

    def test_none_is_an_empty_list_not_a_blank_entry(self):
        """The portal hides the block; a button dialling nothing is worse."""
        self.assertEqual(self.portal(self.t1).data["support_phones"], [])

    def test_a_second_number_alone_still_shows(self):
        self.t1.support_phone_2 = "0733444555"
        self.t1.save(update_fields=["support_phone_2"])
        self.assertEqual(self.portal(self.t1).data["support_phones"], ["0733444555"])

    def test_numbers_belong_to_one_operator(self):
        self.t1.support_phone = "0722111222"
        self.t1.save(update_fields=["support_phone"])
        self.assertEqual(self.portal(self.t2).data["support_phones"], [])

    def test_an_operator_can_set_their_own(self):
        """
        These lived on the tenant and only the platform owner could write
        them, so an operator could not publish their own support number.
        """
        resp = self.auth(self.admin1).put(
            "/api/system/settings/",
            {"SUPPORT_PHONE": "0700111222", "SUPPORT_PHONE_2": "0700333444"},
            format="json")
        self.assertIn(resp.status_code, (200, 202), resp.data)
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.support_phone, "0700111222")
        self.assertEqual(self.t1.support_phone_2, "0700333444")

    def test_they_come_back_on_the_settings_page(self):
        self.t1.support_phone = "0700111222"
        self.t1.save(update_fields=["support_phone"])
        data = self.auth(self.admin1).get("/api/system/settings/").data
        self.assertEqual(data["SUPPORT_PHONE"], "0700111222")
        self.assertIn("SUPPORT_PHONE_2", data)

    def test_operator_staff_cannot_change_them(self):
        staff = User.objects.create_user(
            username="sup_staff", password="pw", role=User.TENANT_STAFF,
            tenant=self.t1, is_staff=True)
        resp = self.auth(staff).put(
            "/api/system/settings/", {"SUPPORT_PHONE": "0700000000"}, format="json")
        self.assertEqual(resp.status_code, 403)


class CounterSaleTests(TwoOperatorMixin, TestCase):
    """
    Creating a hotspot customer produced a row with a MAC, no subscription and
    no voucher — marked active, with no access, and nothing on screen to hand
    over. An operator taking cash from someone in front of them could not
    finish the job.
    """

    def setUp(self):
        cache.clear()
        self.build_operators()
        with tenant_context(self.t1):
            self.pkg = Package.objects.create(
                tenant=self.t1, name="t1-counter", download_speed=5, upload_speed=5,
                price=Decimal("50.00"), duration_value=3, duration_unit="hours",
                monthly_data_cap_gb=0, is_hotspot=True)

    def create(self, **extra):
        body = {
            "full_name": "Walk In",
            "phone": "254799000100",
            "connection_type": "hotspot",
        }
        body.update(extra)
        return self.auth(self.admin1).post("/api/customers/", body, format="json")

    def test_a_package_and_cash_produces_a_usable_code(self):
        resp = self.create(package=self.pkg.id, paid_with="cash")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertTrue(resp.data["voucher_code"], "nothing to hand the customer")
        self.assertTrue(resp.data["subscription_id"])

    def test_the_code_is_real_and_redeems(self):
        code = self.create(package=self.pkg.id, paid_with="cash").data["voucher_code"]
        resp = APIClient().post(
            f"/api/hotspot/validate/?t={self.t1.public_token}",
            {"code": code, "mac_address": "CC:DD:EE:FF:00:01"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)

    def test_the_subscription_runs_for_the_package_duration(self):
        resp = self.create(package=self.pkg.id, paid_with="cash")
        with tenant_context(self.t1):
            sub = Subscription.objects.get(id=resp.data["subscription_id"])
        hours = (sub.expiry_date - timezone.now()).total_seconds() / 3600
        self.assertGreater(hours, 2.5)
        self.assertLess(hours, 3.5)

    def test_without_a_package_nothing_changes(self):
        """The old behaviour, for an operator recording somebody to bill later."""
        resp = self.create()
        self.assertEqual(resp.status_code, 201)
        self.assertIsNone(resp.data.get("voucher_code"))
        with tenant_context(self.t1):
            self.assertFalse(
                Subscription.objects.filter(customer_id=resp.data["id"]).exists())

    def test_a_package_without_payment_leaves_an_invoice_and_no_code(self):
        """Sold on credit: they owe for it, and are not online yet."""
        resp = self.create(package=self.pkg.id)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIsNone(resp.data["voucher_code"])
        with tenant_context(self.t1):
            sub = Subscription.objects.get(id=resp.data["subscription_id"])
            self.assertEqual(sub.invoice.payment_status, "unpaid")

    def test_another_operators_package_cannot_be_sold(self):
        resp = self.create(package=self.data["t2"]["package"].id, paid_with="cash")
        self.assertEqual(resp.status_code, 201)
        self.assertIn("provisioning_error", resp.data)
        self.assertIsNone(resp.data.get("voucher_code"))

    def test_the_payment_is_recorded_against_this_operator(self):
        resp = self.create(package=self.pkg.id, paid_with="cash")
        with tenant_context(self.t1):
            payment = Payment.objects.get(subscription_id=resp.data["subscription_id"])
        self.assertEqual(payment.tenant_id, self.t1.id)
        self.assertEqual(payment.method, "cash")

    def test_operator_staff_cannot_sell(self):
        """Creating a customer is a change, and staff may only read."""
        staff = User.objects.create_user(
            username="sale_staff", password="pw", role=User.TENANT_STAFF,
            tenant=self.t1, is_staff=True)
        resp = self.auth(staff).post(
            "/api/customers/",
            {"full_name": "Nope", "phone": "254799000101",
             "connection_type": "hotspot", "package": self.pkg.id,
             "paid_with": "cash"},
            format="json")
        self.assertEqual(resp.status_code, 403)


# =====================================================
# 41. Giving access away
# =====================================================

class CompAccessTests(TwoOperatorMixin, TestCase):
    """
    Somebody paid and did not get online, or was let down twice, and is
    standing there wanting what they already paid for.

    Before this the only ways to help were to record a payment that never
    happened — putting money in the books nobody received — or to do nothing.
    """

    def setUp(self):
        cache.clear()
        self.build_operators()
        d = self.data["t1"]
        with tenant_context(self.t1):
            self.hotspot = Customer.objects.create(
                tenant=self.t1, full_name="Let Down", phone="254799000200",
                connection_type="hotspot")
            self.hs_pkg = Package.objects.create(
                tenant=self.t1, name="t1-comp-hs", download_speed=5, upload_speed=5,
                price=Decimal("50.00"), duration_value=3, duration_unit="hours",
                monthly_data_cap_gb=0, is_hotspot=True)
        self.pppoe = d["customer"]

    def comp(self, customer, package=None, reason="Paid Tuesday, never got online",
             user=None):
        body = {"reason": reason}
        if package is not None:
            body["package_id"] = package.id
        return self.auth(user or self.admin1).post(
            f"/api/admin/customers/{customer.id}/comp/", body, format="json")

    # ---- it works ----------------------------------------------------------

    def test_a_hotspot_customer_gets_a_usable_code(self):
        resp = self.comp(self.hotspot, self.hs_pkg)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertTrue(resp.data["voucher_code"])

    def test_the_free_code_actually_redeems(self):
        code = self.comp(self.hotspot, self.hs_pkg).data["voucher_code"]
        resp = APIClient().post(
            f"/api/hotspot/validate/?t={self.t1.public_token}",
            {"code": code, "mac_address": "EE:FF:00:11:22:33"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)

    def test_a_pppoe_line_is_restored_without_a_code(self):
        """Payment.save() already does the right thing per connection type."""
        resp = self.comp(self.pppoe, self.data["t1"]["package"])
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIsNone(resp.data["voucher_code"])
        self.assertEqual(resp.data["connection_type"], "pppoe")

    # ---- and costs nothing -------------------------------------------------

    def test_it_adds_nothing_to_revenue(self):
        """The whole point: they get the thing, the books do not gain money."""
        from django.db.models import Sum

        self.comp(self.hotspot, self.hs_pkg)
        with tenant_context(self.t1):
            comped = Payment.objects.filter(method="comp")
            self.assertEqual(comped.count(), 1)
            self.assertEqual(comped.aggregate(t=Sum("amount"))["t"], Decimal("0.00"))

    def test_it_is_still_countable(self):
        """
        Recorded as a payment of zero rather than as no payment, so free
        internet appears in the figures instead of vanishing.
        """
        self.comp(self.hotspot, self.hs_pkg)
        with tenant_context(self.t1):
            payment = Payment.objects.get(method="comp")
        self.assertEqual(payment.amount, Decimal("0.00"))
        self.assertIn("never got online", payment.reference)

    def test_the_giveaway_is_on_the_record(self):
        self.comp(self.hotspot, self.hs_pkg, reason="Second outage this week")
        log = AdminActionLog.objects.filter(action=AdminActionLog.COMP_VOUCHER).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor, self.admin1)
        self.assertEqual(log.target_label, "Let Down")
        self.assertIn("Second outage this week", log.detail)

    # ---- the guards --------------------------------------------------------

    def test_a_reason_is_required(self):
        """In three months the question will be why."""
        resp = self.comp(self.hotspot, self.hs_pkg, reason="   ")
        self.assertEqual(resp.status_code, 400)
        with tenant_context(self.t1):
            self.assertFalse(Payment.objects.filter(method="comp").exists())

    def test_a_package_is_required(self):
        resp = self.comp(self.hotspot, None)
        self.assertEqual(resp.status_code, 400)

    def test_operator_staff_cannot_give_away_the_product(self):
        """
        Staff may read the console all day. Giving away what the business
        sells is a decision about money.
        """
        staff = User.objects.create_user(
            username="comp_staff", password="pw", role=User.TENANT_STAFF,
            tenant=self.t1, is_staff=True)
        resp = self.comp(self.hotspot, self.hs_pkg, user=staff)
        self.assertEqual(resp.status_code, 403)

    def test_another_operators_customer_cannot_be_comped(self):
        resp = self.comp(self.data["t2"]["customer"], self.hs_pkg)
        self.assertEqual(resp.status_code, 404)

    def test_another_operators_package_cannot_be_given(self):
        resp = self.comp(self.hotspot, self.data["t2"]["package"])
        self.assertEqual(resp.status_code, 400)

    def test_a_subscriber_cannot_comp_themselves(self):
        sub_user = User.objects.create_user(
            username="comp_cust", password="pw", role=User.CUSTOMER, tenant=self.t1)
        resp = self.comp(self.hotspot, self.hs_pkg, user=sub_user)
        self.assertEqual(resp.status_code, 403)


# =====================================================
# 42. Issuing a voucher at the counter
# =====================================================

class IssueVoucherTests(TwoOperatorMixin, TestCase):
    """
    The captive portal's flow, run by the operator instead of the customer:
    pick a package, take their number, say how it was paid, hand over the
    code. No M-Pesa prompt and no callback to wait for, because the money has
    already changed hands — or is being waived.
    """

    URL = "/api/admin/vouchers/issue/"

    def setUp(self):
        cache.clear()
        self.build_operators()
        with tenant_context(self.t1):
            self.pkg = Package.objects.create(
                tenant=self.t1, name="t1-counter", download_speed=5, upload_speed=5,
                price=Decimal("50.00"), duration_value=3, duration_unit="hours",
                monthly_data_cap_gb=0, is_hotspot=True)

    def issue(self, user=None, **body):
        payload = {"package_id": self.pkg.id, "phone": "0712000111",
                   "paid_with": "cash"}
        payload.update(body)
        return self.auth(user or self.admin1).post(self.URL, payload, format="json")

    # ---- selling -----------------------------------------------------------

    def test_a_cash_sale_produces_a_code(self):
        resp = self.issue()
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertTrue(resp.data["voucher_code"])
        self.assertFalse(resp.data["free"])

    def test_the_code_redeems(self):
        code = self.issue().data["voucher_code"]
        resp = APIClient().post(
            f"/api/hotspot/validate/?t={self.t1.public_token}",
            {"code": code, "mac_address": "11:22:33:44:55:66"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)

    def test_a_cash_sale_is_worth_the_package_price(self):
        resp = self.issue()
        with tenant_context(self.t1):
            payment = Payment.objects.get(subscription__customer_id=resp.data["customer_id"])
        self.assertEqual(payment.amount, self.pkg.price)
        self.assertEqual(payment.method, "cash")

    def test_a_returning_number_is_reused(self):
        """A regular topping up should not collect a new record each time."""
        first = self.issue()
        second = self.issue()
        self.assertTrue(first.data["new_customer"])
        self.assertFalse(second.data["new_customer"])
        self.assertEqual(first.data["customer_id"], second.data["customer_id"])

    def test_the_number_is_normalised(self):
        resp = self.issue(phone="0712000111")
        with tenant_context(self.t1):
            customer = Customer.objects.get(id=resp.data["customer_id"])
        self.assertEqual(customer.phone, "254712000111")

    # ---- giving away -------------------------------------------------------

    def test_free_costs_nothing_and_still_works(self):
        resp = self.issue(paid_with="comp", reason="Router down all morning")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertTrue(resp.data["voucher_code"])
        self.assertTrue(resp.data["free"])
        with tenant_context(self.t1):
            payment = Payment.objects.get(subscription__customer_id=resp.data["customer_id"])
        self.assertEqual(payment.amount, Decimal("0.00"))
        self.assertEqual(payment.method, "comp")

    def test_free_needs_a_reason(self):
        resp = self.issue(paid_with="comp")
        self.assertEqual(resp.status_code, 400)
        with tenant_context(self.t1):
            self.assertFalse(Customer.objects.filter(phone="254712000111").exists())

    def test_a_giveaway_is_on_the_record(self):
        self.issue(paid_with="comp", reason="Second outage this week")
        log = AdminActionLog.objects.filter(action=AdminActionLog.COMP_VOUCHER).first()
        self.assertIsNotNone(log)
        self.assertIn("Second outage this week", log.detail)

    def test_a_paid_sale_writes_no_giveaway_record(self):
        self.issue(paid_with="cash")
        self.assertFalse(
            AdminActionLog.objects.filter(action=AdminActionLog.COMP_VOUCHER).exists())

    # ---- the guards --------------------------------------------------------

    def test_a_bad_number_is_refused(self):
        self.assertEqual(self.issue(phone="123").status_code, 400)

    def test_a_package_is_required(self):
        self.assertEqual(self.issue(package_id=999999).status_code, 400)

    def test_a_pppoe_package_cannot_be_sold_as_a_voucher(self):
        resp = self.issue(package_id=self.data["t1"]["package"].id)
        self.assertEqual(resp.status_code, 400)

    def test_another_operators_package_cannot_be_sold(self):
        resp = self.issue(package_id=self.data["t2"]["package"].id)
        self.assertEqual(resp.status_code, 400)

    def test_how_it_was_paid_must_be_said(self):
        resp = self.issue(paid_with="")
        self.assertEqual(resp.status_code, 400)

    def test_operator_staff_cannot_issue(self):
        staff = User.objects.create_user(
            username="issue_staff", password="pw", role=User.TENANT_STAFF,
            tenant=self.t1, is_staff=True)
        self.assertEqual(self.issue(user=staff).status_code, 403)

    def test_the_subscriber_belongs_to_the_issuing_operator(self):
        resp = self.issue()
        with tenant_context(self.t1):
            customer = Customer.objects.get(id=resp.data["customer_id"])
        self.assertEqual(customer.tenant_id, self.t1.id)


# =====================================================
# 43. The limits that were never reaching the router
# =====================================================

class RouterAttributeNameTests(TestCase):
    """
    RouterOS attributes are hyphenated and librouteros sends keyword arguments
    through verbatim. A Python keyword cannot contain a hyphen, so rate_limit=
    put the literal word "rate_limit" on the wire and RouterOS did not know it.

    Nothing failed loudly. Speed tiers were unlimited, a PPPoE account could be
    logged in from anywhere as often as you liked, a hotspot voucher had no
    device limit, and no session ever timed out at the router. Every one of
    those is a paid boundary that simply was not there.

    These read the exact keys the calls send, because that is the only place
    the mistake is visible.
    """

    class FakePath:
        """Records what was sent, and iterates as empty so nothing is reused."""

        def __init__(self):
            self.added = []

        def __iter__(self):
            return iter([])

        def add(self, **kwargs):
            self.added.append(kwargs)
            return "*1"

        def remove(self, **kwargs):
            pass

    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                name="r", ip_address="10.0.0.1", username="a", password="p",
                tenant=self.tenant)
            self.package = Package.objects.create(
                tenant=self.tenant, name="p", download_speed=10, upload_speed=5,
                price=Decimal("100.00"), duration_value=1, duration_unit="days",
                monthly_data_cap_gb=0, is_hotspot=True, max_devices=3)

    def _profile_call(self, ensure):
        path = self.FakePath()
        api = MagicMock()
        api.path.return_value = path
        with patch("billing.router_profiles.connect_router", return_value=api):
            ensure(self.router, self.package)
        self.assertTrue(path.added, "nothing was sent to the router")
        return path.added[0]

    # ---- profiles ----------------------------------------------------------

    def test_the_hotspot_speed_limit_uses_the_name_routeros_knows(self):
        from billing.router_profiles import ensure_hotspot_profile

        sent = self._profile_call(ensure_hotspot_profile)
        self.assertIn("rate-limit", sent)
        self.assertNotIn("rate_limit", sent, "the router would ignore this")

    def test_the_device_limit_reaches_the_router(self):
        from billing.router_profiles import ensure_hotspot_profile

        sent = self._profile_call(ensure_hotspot_profile)
        self.assertIn("shared-users", sent)
        self.assertEqual(sent["shared-users"], "3", "the package allows three")

    def test_the_pppoe_speed_limit_uses_the_name_routeros_knows(self):
        from billing.router_profiles import ensure_pppoe_profile

        sent = self._profile_call(ensure_pppoe_profile)
        self.assertIn("rate-limit", sent)
        self.assertNotIn("rate_limit", sent)

    def test_one_session_per_pppoe_account_reaches_the_router(self):
        """Without it a household shares one login with the whole street."""
        from billing.router_profiles import ensure_pppoe_profile

        sent = self._profile_call(ensure_pppoe_profile)
        self.assertEqual(sent.get("only-one"), "yes")
        self.assertNotIn("only_one", sent)

    def test_the_profile_name_carries_the_device_count(self):
        """
        A package edited from one device to three would otherwise keep the
        profile it already had, and the new limit would never arrive.
        """
        from billing.router_profiles import ensure_hotspot_profile

        sent = self._profile_call(ensure_hotspot_profile)
        self.assertIn("_D3", sent["name"])

    # ---- the user itself ---------------------------------------------------

    def test_the_session_time_limit_reaches_the_router(self):
        from billing.router_service import enable_hotspot

        path = self.FakePath()
        api = MagicMock()
        api.path.return_value = path
        with patch("billing.router_service.ensure_hotspot_profile", return_value="P"):
            enable_hotspot(api, self.router, "AA:BB:CC:DD:EE:FF", self.package,
                           timezone.now() + timezone.timedelta(hours=2))
        sent = path.added[0]
        self.assertIn("limit-uptime", sent)
        self.assertNotIn("limit_uptime", sent, "no session would ever expire")

    def test_taking_a_device_off_ends_its_live_session(self):
        """
        Removing the user alone leaves an established session running until it
        times out of its own accord, so an expired customer stayed online while
        the system recorded them as cut off.
        """
        from billing.router_service import disable_hotspot

        removed = []

        class Active:
            def __iter__(self):
                return iter([{".id": "*9", "user": "AA:BB:CC:DD:EE:FF"}])

            def remove(self, **kwargs):
                removed.append(("active", kwargs))

        class Users:
            def __iter__(self):
                return iter([{".id": "*1", "name": "AA:BB:CC:DD:EE:FF"}])

            def remove(self, **kwargs):
                removed.append(("user", kwargs))

        api = MagicMock()
        api.path.side_effect = lambda *p: Active() if "active" in p else Users()
        disable_hotspot(api, "AA:BB:CC:DD:EE:FF")

        self.assertIn("active", [kind for kind, _ in removed],
                      "the live session was left running")
        self.assertIn("user", [kind for kind, _ in removed])


class VoucherDeviceLimitTests(TwoOperatorMixin, TestCase):
    """
    One code, bought for one phone, was being passed around a room. And a
    household paying for three devices had no way to say so — access was bound
    to a single MAC on the customer row, so the limit was always exactly one
    and was enforced by overwriting rather than by counting.
    """

    def setUp(self):
        cache.clear()
        self.build_operators()
        d = self.data["t1"]
        with tenant_context(self.t1):
            d["customer"].connection_type = "hotspot"
            d["customer"].pppoe_username = ""
            d["customer"].save()
            self.sub = d["sub"]
            self.sub.expiry_date = timezone.now() + timezone.timedelta(days=2)
            self.sub.status = "active"
            self.sub.save()
            self.voucher = Voucher.objects.create(
                tenant=self.t1, code="WIFI-DEV001", subscription=self.sub,
                expires_at=self.sub.expiry_date)
            # A real voucher only exists because a payment minted it. This one
            # is hand-built, so the invoice has to be settled to match — the
            # portal reports "pending" until it is.
            invoice = self.sub.invoice
            invoice.payment_status = "paid"
            invoice.save(update_fields=["payment_status"])

    def allow(self, n):
        with tenant_context(self.t1):
            pkg = self.sub.package
            pkg.max_devices = n
            pkg.save(update_fields=["max_devices"])

    def redeem(self, mac):
        return APIClient().post(
            f"/api/hotspot/validate/?t={self.t1.public_token}",
            {"code": "WIFI-DEV001", "mac_address": mac}, format="json")

    # ---- one device --------------------------------------------------------

    def test_one_device_means_one_phone(self):
        self.allow(1)
        self.assertEqual(self.redeem("AA:00:00:00:00:01").status_code, 200)
        second = self.redeem("BB:00:00:00:00:02")
        self.assertEqual(second.status_code, 409, second.data)
        self.assertIn("another phone", second.data["detail"])

    def test_the_same_phone_may_come_back(self):
        """Reconnecting is not a second device."""
        self.allow(1)
        self.assertEqual(self.redeem("AA:00:00:00:00:01").status_code, 200)
        self.assertEqual(self.redeem("AA:00:00:00:00:01").status_code, 200)

    # ---- more than one -----------------------------------------------------

    def test_three_devices_means_three_and_no_more(self):
        self.allow(3)
        for i in range(3):
            with self.subTest(device=i):
                self.assertEqual(
                    self.redeem(f"AA:00:00:00:00:0{i}").status_code, 200)
        fourth = self.redeem("FF:00:00:00:00:99")
        self.assertEqual(fourth.status_code, 409, fourth.data)
        self.assertEqual(fourth.data["devices_allowed"], 3)
        self.assertEqual(fourth.data["devices_used"], 3)

    def test_every_allowed_device_can_reconnect(self):
        self.allow(2)
        self.redeem("AA:00:00:00:00:01")
        self.redeem("AA:00:00:00:00:02")
        for mac in ("AA:00:00:00:00:01", "AA:00:00:00:00:02"):
            with self.subTest(mac=mac):
                self.assertEqual(self.redeem(mac).status_code, 200)

    def test_a_second_device_is_recognised_by_the_portal(self):
        """
        Resolving a subscriber used to read the one MAC on the customer row, so
        a second phone on a multi-device package was told it was not registered.
        """
        self.allow(2)
        self.redeem("AA:00:00:00:00:01")
        self.redeem("AA:00:00:00:00:02")
        resp = APIClient().get(
            "/api/hotspot/status/",
            {"t": self.t1.public_token, "mac": "AA:00:00:00:00:02"})
        self.assertEqual(resp.data["status"], "active", resp.data)

    # ---- across operators --------------------------------------------------

    def test_a_device_belongs_to_one_subscriber(self):
        """
        Otherwise one phone gets two allowances out of one payment by being
        registered to two accounts.
        """
        self.allow(1)
        self.redeem("AA:00:00:00:00:01")
        with tenant_context(self.t1):
            other = Customer.objects.create(
                tenant=self.t1, full_name="Other", phone="254733000999",
                connection_type="hotspot")
            # Contained, or the failed insert poisons the surrounding
            # transaction and every later query in this test raises instead.
            with self.assertRaises(IntegrityError), transaction.atomic():
                CustomerDevice.objects.create(
                    tenant=self.t1, customer=other,
                    mac_address="AA:00:00:00:00:01")

    def test_devices_are_counted_per_operator(self):
        """The same MAC may legitimately be a subscriber of two operators."""
        with tenant_context(self.t2):
            c2 = self.data["t2"]["customer"]
            CustomerDevice.objects.create(
                tenant=self.t2, customer=c2, mac_address="AA:00:00:00:00:01")
        self.allow(1)
        self.assertEqual(self.redeem("AA:00:00:00:00:01").status_code, 200)


# =====================================================
# 44. What they have used, and what they are allowed
# =====================================================

class DataUsageReportingTests(TwoOperatorMixin, TestCase):
    """
    An operator asking why a tower is saturated needs the number whether or not
    there is a ceiling to compare it against, so an unlimited plan reports
    consumption too.
    """

    def setUp(self):
        cache.clear()
        self.build_operators()
        d = self.data["t1"]
        self.customer = d["customer"]
        with tenant_context(self.t1):
            self.sub = d["sub"]
            self.sub.start_date = timezone.now() - timezone.timedelta(days=2)
            self.sub.expiry_date = timezone.now() + timezone.timedelta(days=5)
            self.sub.status = "active"
            self.sub.save()

    def record(self, down, up, when=None):
        with tenant_context(self.t1):
            PPPoEUsageRecord.objects.create(
                tenant=self.t1, customer=self.customer,
                period_start=when or timezone.now(),
                period_end=when or timezone.now(),
                download_bytes=down, upload_bytes=up)

    def usage(self):
        resp = self.auth(self.admin1).get(f"/api/customers/{self.customer.id}/")
        self.assertEqual(resp.status_code, 200, resp.data)
        return resp.data["data_usage"]

    def set_cap(self, gb):
        with tenant_context(self.t1):
            pkg = self.sub.package
            pkg.monthly_data_cap_gb = gb
            pkg.save(update_fields=["monthly_data_cap_gb"])

    # ---- consumption -------------------------------------------------------

    def test_nothing_used_reads_as_zero_not_as_missing(self):
        u = self.usage()
        self.assertEqual(u["used_bytes"], 0)
        self.assertEqual(u["download_bytes"], 0)

    def test_both_directions_are_counted(self):
        self.record(3 * 1024 ** 3, 1 * 1024 ** 3)
        u = self.usage()
        self.assertEqual(u["download_bytes"], 3 * 1024 ** 3)
        self.assertEqual(u["upload_bytes"], 1 * 1024 ** 3)
        self.assertEqual(u["used_bytes"], 4 * 1024 ** 3)

    def test_usage_before_this_subscription_is_not_counted(self):
        """
        The subscription is the thing that was sold, so it is what the
        allowance is measured against — not the calendar month, and not
        whatever they used on a package that has already ended.
        """
        self.record(9 * 1024 ** 3, 0,
                    when=timezone.now() - timezone.timedelta(days=30))
        self.record(1 * 1024 ** 3, 0)
        self.assertEqual(self.usage()["used_bytes"], 1 * 1024 ** 3)

    # ---- against the cap ---------------------------------------------------

    def test_a_cap_of_zero_is_unlimited(self):
        self.set_cap(0)
        u = self.usage()
        self.assertTrue(u["unlimited"])
        self.assertIsNone(u["percent_used"])

    def test_unlimited_still_reports_what_was_used(self):
        """The number is the point, not the comparison."""
        self.set_cap(0)
        self.record(2 * 1024 ** 3, 0)
        u = self.usage()
        self.assertTrue(u["unlimited"])
        self.assertEqual(u["used_bytes"], 2 * 1024 ** 3)

    def test_a_cap_reports_how_much_of_it_is_gone(self):
        self.set_cap(10)
        self.record(2 * 1024 ** 3, 0.5 * 1024 ** 3)
        u = self.usage()
        self.assertFalse(u["unlimited"])
        self.assertEqual(u["cap_gb"], 10)
        self.assertEqual(u["percent_used"], 25.0)

    def test_going_over_is_visible_rather_than_clamped_to_full(self):
        """100% and 300% are different conversations."""
        self.set_cap(1)
        self.record(3 * 1024 ** 3, 0)
        self.assertEqual(self.usage()["percent_used"], 300.0)

    def test_a_customers_own_cap_beats_the_packages(self):
        self.set_cap(10)
        with tenant_context(self.t1):
            self.customer.custom_data_cap_gb = 2
            self.customer.save(update_fields=["custom_data_cap_gb"])
        self.assertEqual(self.usage()["cap_gb"], 2)

    # ---- devices -----------------------------------------------------------

    def test_the_devices_panel_says_how_many_are_allowed(self):
        with tenant_context(self.t1):
            pkg = self.sub.package
            pkg.max_devices = 3
            pkg.save(update_fields=["max_devices"])
            CustomerDevice.objects.create(
                tenant=self.t1, customer=self.customer,
                mac_address="AA:00:00:00:00:01")

        resp = self.auth(self.admin1).get(f"/api/customers/{self.customer.id}/")
        devices = resp.data["devices"]
        self.assertEqual(devices["allowed"], 3)
        self.assertEqual(len(devices["in_use"]), 1)
        self.assertEqual(devices["in_use"][0]["mac_address"], "AA:00:00:00:00:01")

    def test_another_operator_cannot_read_any_of_it(self):
        self.assertEqual(
            self.auth(self.admin2).get(f"/api/customers/{self.customer.id}/").status_code,
            404)


# =====================================================
# 45. Blocking a device, and retiring a code
# =====================================================

class DeviceBlockingTests(TwoOperatorMixin, TestCase):
    """
    Blocking and removing answer different questions.

    A lost phone should be removed, so the replacement can take its place. A
    stolen one should be blocked — refused even with a valid code — and
    blocking it must not cost the customer a place they paid for.
    """

    def setUp(self):
        cache.clear()
        self.build_operators()
        d = self.data["t1"]
        with tenant_context(self.t1):
            d["customer"].connection_type = "hotspot"
            d["customer"].pppoe_username = ""
            d["customer"].save()
            self.customer = d["customer"]
            self.sub = d["sub"]
            self.sub.expiry_date = timezone.now() + timezone.timedelta(days=2)
            self.sub.status = "active"
            self.sub.save()
            self.sub.package.max_devices = 2
            self.sub.package.save(update_fields=["max_devices"])
            self.voucher = Voucher.objects.create(
                tenant=self.t1, code="WIFI-BLK001", subscription=self.sub,
                expires_at=self.sub.expiry_date)
            inv = self.sub.invoice
            inv.payment_status = "paid"
            inv.save(update_fields=["payment_status"])
            self.device = CustomerDevice.objects.create(
                tenant=self.t1, customer=self.customer,
                mac_address="AA:00:00:00:00:01")

    def redeem(self, mac):
        return APIClient().post(
            f"/api/hotspot/validate/?t={self.t1.public_token}",
            {"code": "WIFI-BLK001", "mac_address": mac}, format="json")

    def act(self, action, reason=None, user=None, device=None):
        body = {"action": action}
        if reason is not None:
            body["reason"] = reason
        return self.auth(user or self.admin1).post(
            f"/api/admin/devices/{(device or self.device).id}/", body, format="json")

    # ---- blocking ----------------------------------------------------------

    @patch("billing.views._kick_device", return_value=0)
    def test_a_blocked_device_is_refused_even_with_a_good_code(self, _):
        self.assertEqual(self.act("block", "Handset reported stolen").status_code, 200)
        resp = self.redeem("AA:00:00:00:00:01")
        self.assertEqual(resp.status_code, 403, resp.data)
        self.assertTrue(resp.data["blocked"])

    @patch("billing.views._kick_device", return_value=0)
    def test_blocking_does_not_cost_the_customer_a_place(self, _):
        """
        They paid for two devices. Blocking a stolen one must leave two usable,
        not one.
        """
        self.redeem("BB:00:00:00:00:02")
        self.act("block", "Stolen")
        self.assertEqual(self.redeem("CC:00:00:00:00:03").status_code, 200)

    @patch("billing.views._kick_device", return_value=0)
    def test_a_blocked_device_resolves_to_nobody(self, _):
        """
        Otherwise it keeps reading status and calling reconnect on the account
        it was blocked from.
        """
        self.act("block", "Stolen")
        resp = APIClient().get("/api/hotspot/status/", {
            "t": self.t1.public_token, "mac": "AA:00:00:00:00:01"})
        self.assertEqual(resp.data["status"], "not_found")

    @patch("billing.views._kick_device", return_value=0)
    def test_blocking_needs_a_reason(self, _):
        resp = self.act("block", "   ")
        self.assertEqual(resp.status_code, 400)
        self.device.refresh_from_db()
        self.assertFalse(self.device.blocked)

    @patch("billing.views._kick_device", return_value=0)
    def test_unblocking_lets_it_back(self, _):
        self.act("block", "Mistake")
        self.assertEqual(self.act("unblock").status_code, 200)
        self.assertEqual(self.redeem("AA:00:00:00:00:01").status_code, 200)

    @patch("billing.views._kick_device", return_value=0)
    def test_blocking_is_written_down_with_the_reason(self, _):
        self.act("block", "Sharing the connection with a shop")
        log = AccessAuditLog.objects.all_tenants().filter(
            action="device_blocked").first()
        self.assertIsNotNone(log)
        self.assertIn("Sharing the connection", log.reason)
        self.assertIn("AA:00:00:00:00:01", log.reason)

    # ---- removing ----------------------------------------------------------

    @patch("billing.views._kick_device", return_value=0)
    def test_removing_frees_the_place(self, _):
        self.redeem("BB:00:00:00:00:02")     # both places taken
        self.assertEqual(self.redeem("CC:00:00:00:00:03").status_code, 409)

        resp = self.auth(self.admin1).delete(f"/api/admin/devices/{self.device.id}/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(self.redeem("CC:00:00:00:00:03").status_code, 200)

    @patch("billing.views._kick_device", return_value=0)
    def test_a_removed_device_may_come_back(self, _):
        """Removed is not blocked — it can claim a new place."""
        self.auth(self.admin1).delete(f"/api/admin/devices/{self.device.id}/")
        self.assertEqual(self.redeem("AA:00:00:00:00:01").status_code, 200)

    # ---- who may --------------------------------------------------------

    @patch("billing.views._kick_device", return_value=0)
    def test_operator_staff_cannot_block(self, _):
        staff = User.objects.create_user(
            username="blk_staff", password="pw", role=User.TENANT_STAFF,
            tenant=self.t1, is_staff=True)
        self.assertEqual(self.act("block", "no", user=staff).status_code, 403)

    @patch("billing.views._kick_device", return_value=0)
    def test_another_operator_cannot_touch_this_device(self, _):
        self.assertEqual(self.act("block", "no", user=self.admin2).status_code, 404)


class VoucherDeactivationTests(TwoOperatorMixin, TestCase):
    """
    Retiring one code, without expiring the subscription it belongs to.

    The existing revoke expires everything — right when somebody has stopped
    paying, wrong when a single code has leaked and the customer is owed a
    replacement.
    """

    def setUp(self):
        cache.clear()
        self.build_operators()
        d = self.data["t1"]
        with tenant_context(self.t1):
            d["customer"].connection_type = "hotspot"
            d["customer"].pppoe_username = ""
            d["customer"].save()
            self.sub = d["sub"]
            self.sub.expiry_date = timezone.now() + timezone.timedelta(days=2)
            self.sub.status = "active"
            self.sub.save()
            self.leaked = Voucher.objects.create(
                tenant=self.t1, code="WIFI-LEAK01", subscription=self.sub,
                expires_at=self.sub.expiry_date)
            self.spare = Voucher.objects.create(
                tenant=self.t1, code="WIFI-SPARE1", subscription=self.sub,
                expires_at=self.sub.expiry_date)

    def retire(self, code, reason="Shared publicly", user=None):
        return self.auth(user or self.admin1).post(
            f"/api/admin/vouchers/{code}/deactivate/", {"reason": reason},
            format="json")

    def test_a_retired_code_stops_working(self):
        self.assertEqual(self.retire("WIFI-LEAK01").status_code, 200)
        resp = APIClient().post(
            f"/api/hotspot/validate/?t={self.t1.public_token}",
            {"code": "WIFI-LEAK01", "mac_address": "AA:11:11:11:11:11"},
            format="json")
        self.assertEqual(resp.status_code, 400)

    def test_the_subscription_and_the_other_codes_are_untouched(self):
        """The difference from revoking, which expires the lot."""
        self.retire("WIFI-LEAK01")
        with tenant_context(self.t1):
            self.sub.refresh_from_db()
            self.assertEqual(self.sub.status, "active")
            self.assertTrue(Voucher.objects.get(code="WIFI-SPARE1").is_active)

    def test_a_reason_is_required(self):
        resp = self.auth(self.admin1).post(
            "/api/admin/vouchers/WIFI-LEAK01/deactivate/", {}, format="json")
        self.assertEqual(resp.status_code, 400)
        with tenant_context(self.t1):
            self.assertTrue(Voucher.objects.get(code="WIFI-LEAK01").is_active)

    def test_retiring_is_written_down(self):
        self.retire("WIFI-LEAK01", reason="Posted in a WhatsApp group")
        log = AccessAuditLog.objects.all_tenants().filter(
            action="voucher_deactivated").first()
        self.assertIsNotNone(log)
        self.assertIn("Posted in a WhatsApp group", log.reason)

    def test_retiring_twice_says_so_rather_than_failing(self):
        self.retire("WIFI-LEAK01")
        resp = self.retire("WIFI-LEAK01")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("already retired", resp.data["detail"])

    def test_operator_staff_cannot_retire_a_code(self):
        staff = User.objects.create_user(
            username="vch_staff", password="pw", role=User.TENANT_STAFF,
            tenant=self.t1, is_staff=True)
        self.assertEqual(self.retire("WIFI-LEAK01", user=staff).status_code, 403)

    def test_another_operators_code_cannot_be_retired(self):
        self.assertEqual(self.retire("WIFI-LEAK01", user=self.admin2).status_code, 404)


# =====================================================
# 46. A sold package is part of the billing record
# =====================================================

class PackageDeletionTests(TwoOperatorMixin, TestCase):
    """
    Subscription.package was CASCADE, so deleting a package took every
    subscription on it and, through those, the invoices, the payments and the
    vouchers. Measured against the development data before it was changed: one
    package, one customer, five rows destroyed — including the record that
    money had changed hands.

    The confirm dialog said "existing subscriptions are not affected".
    """

    def setUp(self):
        cache.clear()
        self.build_operators()
        with tenant_context(self.t1):
            self.unused = Package.objects.create(
                tenant=self.t1, name="never-sold", download_speed=5, upload_speed=5,
                price=Decimal("100.00"), duration_value=1, duration_unit="days",
                monthly_data_cap_gb=0, is_hotspot=True)
        self.in_use = self.data["t1"]["package"]

    def delete(self, package, user=None):
        return self.auth(user or self.admin1).delete(f"/api/packages/{package.id}/")

    # ---- the refusal -------------------------------------------------------

    def test_a_package_customers_are_on_cannot_be_deleted(self):
        resp = self.delete(self.in_use)
        self.assertEqual(resp.status_code, 409, resp.data)
        self.assertGreaterEqual(resp.data["active_subscriptions"], 1)
        self.assertTrue(Package.objects.filter(pk=self.in_use.pk).exists())

    def test_the_refusal_says_what_to_do_instead(self):
        resp = self.delete(self.in_use)
        self.assertTrue(resp.data["can_archive"])
        self.assertIn("archive", resp.data["detail"].lower())

    def test_a_package_with_only_past_subscriptions_is_also_refused(self):
        """
        Suspending everyone does not make it safe: those subscriptions and
        their invoices still name the package, and deleting it would delete
        the record of what those customers paid for.
        """
        with tenant_context(self.t1):
            sub = self.data["t1"]["sub"]
            sub.status = "expired"
            sub.save(update_fields=["status"])

        resp = self.delete(self.in_use)
        self.assertEqual(resp.status_code, 409, resp.data)
        self.assertGreaterEqual(resp.data["past_subscriptions"], 1)

    def test_the_database_refuses_too(self):
        """
        Not only the view. A cascade this destructive should not depend on one
        code path remembering to check.
        """
        from django.db.models import ProtectedError

        with tenant_context(self.t1), transaction.atomic():
            with self.assertRaises(ProtectedError):
                self.in_use.delete()

    def test_nothing_is_destroyed_by_the_attempt(self):
        with tenant_context(self.t1):
            before = (
                Subscription.objects.filter(package=self.in_use).count(),
                Payment.objects.count(),
                Invoice.objects.count(),
            )
        self.delete(self.in_use)
        with tenant_context(self.t1):
            after = (
                Subscription.objects.filter(package=self.in_use).count(),
                Payment.objects.count(),
                Invoice.objects.count(),
            )
        self.assertEqual(before, after)

    # ---- what may still be deleted -----------------------------------------

    def test_a_package_nobody_ever_bought_can_be_deleted(self):
        resp = self.delete(self.unused)
        self.assertEqual(resp.status_code, 204, getattr(resp, "data", None))
        self.assertFalse(Package.objects.filter(pk=self.unused.pk).exists())

    def test_operator_staff_cannot_delete_a_package(self):
        staff = User.objects.create_user(
            username="pkg_staff", password="pw", role=User.TENANT_STAFF,
            tenant=self.t1, is_staff=True)
        self.assertEqual(self.delete(self.unused, user=staff).status_code, 403)

    # ---- archiving, which is what was wanted -------------------------------

    def test_archiving_retires_it_without_touching_anybody(self):
        resp = self.auth(self.admin1).post(f"/api/packages/{self.in_use.id}/archive/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(resp.data["is_archived"])

        with tenant_context(self.t1):
            self.assertTrue(
                Subscription.objects.filter(package=self.in_use).exists(),
                "archiving must not disturb the people on it")

    def test_an_archived_package_is_not_offered_on_the_portal(self):
        with tenant_context(self.t1):
            hotspot = Package.objects.create(
                tenant=self.t1, name="retiring", download_speed=5, upload_speed=5,
                price=Decimal("50.00"), duration_value=1, duration_unit="hours",
                monthly_data_cap_gb=0, is_hotspot=True)

        portal = f"/api/hotspot/packages/?t={self.t1.public_token}"
        self.assertIn("retiring",
                      {p["name"] for p in APIClient().get(portal).data["results"]})

        self.auth(self.admin1).post(f"/api/packages/{hotspot.id}/archive/")
        self.assertNotIn("retiring",
                         {p["name"] for p in APIClient().get(portal).data["results"]})

    def test_an_archived_package_cannot_be_sold(self):
        with tenant_context(self.t1):
            hotspot = Package.objects.create(
                tenant=self.t1, name="retired", download_speed=5, upload_speed=5,
                price=Decimal("50.00"), duration_value=1, duration_unit="hours",
                monthly_data_cap_gb=0, is_hotspot=True, is_archived=True)

        resp = self.auth(self.admin1).post(
            "/api/admin/vouchers/issue/",
            {"package_id": hotspot.id, "phone": "0712000999", "paid_with": "cash"},
            format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_archiving_is_reversible(self):
        self.auth(self.admin1).post(f"/api/packages/{self.in_use.id}/archive/")
        resp = self.auth(self.admin1).post(f"/api/packages/{self.in_use.id}/archive/")
        self.assertFalse(resp.data["is_archived"])

    def test_another_operator_cannot_archive_this_package(self):
        resp = self.auth(self.admin2).post(f"/api/packages/{self.in_use.id}/archive/")
        self.assertEqual(resp.status_code, 404)


# =====================================================
# 47. The connections that did not happen
# =====================================================

class ConnectionAttemptTests(TwoOperatorMixin, TestCase):
    """
    Only successes were recorded. An operator heard about the customer who
    complained and nothing about the twenty who mistyped a code and gave up.
    """

    URL = "/api/admin/connection-attempts/"

    def setUp(self):
        cache.clear()
        self.build_operators()
        d = self.data["t1"]
        with tenant_context(self.t1):
            d["customer"].connection_type = "hotspot"
            d["customer"].pppoe_username = ""
            d["customer"].save()
            sub = d["sub"]
            sub.expiry_date = timezone.now() + timezone.timedelta(days=2)
            sub.status = "active"
            sub.save()
            sub.package.max_devices = 1
            sub.package.save(update_fields=["max_devices"])
            Voucher.objects.create(
                tenant=self.t1, code="WIFI-REAL01", subscription=sub,
                expires_at=sub.expiry_date)

    def try_code(self, code, mac):
        return APIClient().post(
            f"/api/hotspot/validate/?t={self.t1.public_token}",
            {"code": code, "mac_address": mac}, format="json")

    def attempts(self, user=None, **params):
        return self.auth(user or self.admin1).get(self.URL, params)

    # ---- recording ---------------------------------------------------------

    def test_a_mistyped_code_is_recorded(self):
        self.try_code("WIFI-REAL0X", "AA:00:00:00:00:01")
        rows = self.attempts().data["results"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code_tried"], "WIFI-REAL0X")
        self.assertEqual(rows[0]["outcome"], "invalid")

    def test_a_code_used_on_too_many_devices_is_recorded(self):
        """The one that looks like a customer being difficult and is not."""
        self.try_code("WIFI-REAL01", "AA:00:00:00:00:01")
        self.try_code("WIFI-REAL01", "BB:00:00:00:00:02")
        outcomes = {a["outcome"] for a in self.attempts().data["results"]}
        self.assertIn("device_limit", outcomes)

    def test_a_blocked_device_trying_again_is_recorded(self):
        self.try_code("WIFI-REAL01", "AA:00:00:00:00:01")
        with tenant_context(self.t1):
            device = CustomerDevice.objects.get(mac_address="AA:00:00:00:00:01")
            device.blocked = True
            device.blocked_reason = "Stolen"
            device.save(update_fields=["blocked", "blocked_reason"])

        self.try_code("WIFI-REAL01", "AA:00:00:00:00:01")
        outcomes = {a["outcome"] for a in self.attempts().data["results"]}
        self.assertIn("blocked", outcomes)

    def test_a_successful_connection_records_nothing(self):
        """This is a list of failures. A success in it is noise."""
        self.try_code("WIFI-REAL01", "AA:00:00:00:00:01")
        self.assertEqual(self.attempts().data["count"], 0)

    def test_recording_never_breaks_the_answer(self):
        """
        A portal that cannot write a diagnostic must still answer the customer.
        """
        with patch("billing.views.ConnectionAttempt.objects.create",
                   side_effect=Exception("disk full")):
            resp = self.try_code("WIFI-NOPE01", "AA:00:00:00:00:09")
        self.assertEqual(resp.status_code, 400)

    # ---- reading -----------------------------------------------------------

    def test_they_can_be_filtered_by_reason(self):
        self.try_code("WIFI-WRONG1", "AA:00:00:00:00:01")
        self.try_code("WIFI-WRONG2", "AA:00:00:00:00:02")
        self.assertEqual(self.attempts(outcome="invalid").data["count"], 2)
        self.assertEqual(self.attempts(outcome="blocked").data["count"], 0)

    def test_one_operator_never_sees_anothers(self):
        self.try_code("WIFI-WRONG1", "AA:00:00:00:00:01")
        self.assertEqual(self.attempts(user=self.admin2).data["count"], 0)

    def test_operator_staff_may_read_them(self):
        """Somebody cannot get online is precisely the support desk's job."""
        staff = User.objects.create_user(
            username="att_staff", password="pw", role=User.TENANT_STAFF,
            tenant=self.t1, is_staff=True)
        self.assertEqual(self.attempts(user=staff).status_code, 200)

    # ---- retention ---------------------------------------------------------

    def test_old_attempts_are_pruned(self):
        """A diagnostic, not a ledger. Nothing else would remove a row."""
        from billing.tasks.router_health import prune_connection_attempts_task

        with tenant_context(self.t1):
            old = ConnectionAttempt.objects.create(
                tenant=self.t1, code_tried="WIFI-OLD001",
                mac_address="AA:00:00:00:00:05", outcome="invalid")
            ConnectionAttempt.objects.filter(pk=old.pk).update(
                created_at=timezone.now() - timezone.timedelta(days=30))
            recent = ConnectionAttempt.objects.create(
                tenant=self.t1, code_tried="WIFI-NEW001",
                mac_address="AA:00:00:00:00:06", outcome="invalid")

        prune_connection_attempts_task()

        with tenant_context(self.t1):
            self.assertFalse(ConnectionAttempt.objects.filter(pk=old.pk).exists())
            self.assertTrue(ConnectionAttempt.objects.filter(pk=recent.pk).exists())


class SubscriberFacingTests(TwoOperatorMixin, TestCase):
    """
    Two things the operator could see and the person paying could not: the
    terms they are agreeing to, and how much of their bundle is left.
    """

    def setUp(self):
        cache.clear()
        self.build_operators()
        d = self.data["t1"]
        with tenant_context(self.t1):
            d["customer"].connection_type = "hotspot"
            d["customer"].pppoe_username = ""
            d["customer"].hotspot_username = "AA:BB:CC:00:00:01"
            d["customer"].save()
            self.customer = d["customer"]
            self.sub = d["sub"]
            self.sub.start_date = timezone.now() - timezone.timedelta(days=1)
            self.sub.expiry_date = timezone.now() + timezone.timedelta(days=2)
            self.sub.status = "active"
            self.sub.save()
            inv = self.sub.invoice
            inv.payment_status = "paid"
            inv.save(update_fields=["payment_status"])

    # ---- terms -------------------------------------------------------------

    def test_no_terms_set_means_none_offered(self):
        """A link to nothing is worse than no link."""
        resp = APIClient().get(f"/api/hotspot/provider/?t={self.t1.public_token}")
        self.assertIsNone(resp.data["terms_url"])

    def test_terms_reach_the_portal(self):
        with tenant_context(self.t1):
            SystemSetting.objects.create(
                tenant=self.t1, key="HOTSPOT_TERMS_URL",
                value="https://example.com/terms")
        clear_settings_cache(tenant=self.t1)

        resp = APIClient().get(f"/api/hotspot/provider/?t={self.t1.public_token}")
        self.assertEqual(resp.data["terms_url"], "https://example.com/terms")

    def test_an_operator_can_set_their_own_terms(self):
        resp = self.auth(self.admin1).put(
            "/api/system/settings/",
            {"HOTSPOT_TERMS_URL": "https://skylink.example.com/terms"},
            format="json")
        self.assertIn(resp.status_code, (200, 202), resp.data)
        clear_settings_cache(tenant=self.t1)
        self.assertEqual(
            APIClient().get(
                f"/api/hotspot/provider/?t={self.t1.public_token}").data["terms_url"],
            "https://skylink.example.com/terms")

    def test_terms_belong_to_one_operator(self):
        with tenant_context(self.t1):
            SystemSetting.objects.create(
                tenant=self.t1, key="HOTSPOT_TERMS_URL", value="https://a.example/terms")
        clear_settings_cache(tenant=self.t1)
        self.assertIsNone(
            APIClient().get(
                f"/api/hotspot/provider/?t={self.t2.public_token}").data["terms_url"])

    # ---- their own usage ---------------------------------------------------

    def status(self):
        return APIClient().get("/api/hotspot/status/", {
            "t": self.t1.public_token, "mac": "AA:BB:CC:00:00:01"})

    def test_a_subscriber_can_see_what_they_have_used(self):
        with tenant_context(self.t1):
            HotspotUsageRecord.objects.create(
                tenant=self.t1, customer=self.customer,
                period_start=timezone.now(), period_end=timezone.now(),
                download_bytes=2 * 1024 ** 3, upload_bytes=0)

        usage = self.status().data["usage"]
        self.assertEqual(usage["used_bytes"], 2 * 1024 ** 3)

    def test_an_unlimited_plan_still_reports_consumption(self):
        """"How much have I used" is fair with or without a ceiling."""
        with tenant_context(self.t1):
            self.sub.package.monthly_data_cap_gb = 0
            self.sub.package.save(update_fields=["monthly_data_cap_gb"])
            HotspotUsageRecord.objects.create(
                tenant=self.t1, customer=self.customer,
                period_start=timezone.now(), period_end=timezone.now(),
                download_bytes=1024 ** 3, upload_bytes=0)

        usage = self.status().data["usage"]
        self.assertTrue(usage["unlimited"])
        self.assertEqual(usage["used_bytes"], 1024 ** 3)
        self.assertIsNone(usage["percent_used"])

    def test_a_capped_plan_reports_how_much_is_gone(self):
        with tenant_context(self.t1):
            self.sub.package.monthly_data_cap_gb = 4
            self.sub.package.save(update_fields=["monthly_data_cap_gb"])
            HotspotUsageRecord.objects.create(
                tenant=self.t1, customer=self.customer,
                period_start=timezone.now(), period_end=timezone.now(),
                download_bytes=1024 ** 3, upload_bytes=0)

        usage = self.status().data["usage"]
        self.assertEqual(usage["cap_gb"], 4)
        self.assertEqual(usage["percent_used"], 25.0)

    def test_another_device_cannot_read_this_subscribers_usage(self):
        resp = APIClient().get("/api/hotspot/status/", {
            "t": self.t1.public_token, "mac": "FF:FF:FF:FF:FF:FF"})
        self.assertEqual(resp.data["status"], "not_found")
        self.assertNotIn("usage", resp.data)
