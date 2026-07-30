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
from decimal import Decimal
from unittest import skipUnless
from unittest.mock import patch
from cryptography.fernet import Fernet

from django.db import connection

from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import ProtectedError
from django.db.utils import IntegrityError
from django.test import TestCase, override_settings
from django.utils import timezone

from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from billing.config import clear_settings_cache, get_setting
from billing.router_service import (
    pick_best_router_for_new_customer,
    pick_working_router,
)
from billing.tenancy import TenantManager, tenant_context

from billing.models import (
    User, Customer, Package, Subscription,
    Invoice, Payment, Voucher, MpesaTransaction, RouterDevice,
    AccessAuditLog, Tenant, RouterFailoverLog, ExpiryReminderLog,
    SystemSetting, PPPoEUsageSnapshot, PPPoEUsageState, PPPoEUsageRecord,
    HotspotUsageState, HotspotUsageRecord, UsageRecord,
)
from billing.fields import ENCRYPTED_PREFIX

# A fixed key used only in encryption-specific tests.
# Other test classes work without any key (plaintext passthrough).
TEST_ENCRYPTION_KEY = Fernet.generate_key().decode()


# ===========================================================
# Shared factory helpers
# ===========================================================

def make_admin(username="admin_user"):
    return User.objects.create_user(username=username, password="adminpass", role="admin")


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
    user = User.objects.create_user(
        username=f"pppoe_{username_suffix}", password="pass", role="customer",
    )
    return Customer.objects.create(
        user=user, full_name="PPPoE Test Customer",
        phone=phone, connection_type="pppoe", router=router,
    )


def make_hotspot_customer(router, phone="254700111222", username_suffix="01"):
    user = User.objects.create_user(
        username=f"hs_{username_suffix}", password="pass", role="customer",
    )
    return Customer.objects.create(
        user=user, full_name="Hotspot Test Customer",
        phone=phone, connection_type="hotspot", router=router,
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

    @patch("billing.router_service.enable_customer_access")
    @patch("billing.models.notify_customer")
    def test_router_access_enabled_exactly_once(self, _, mock_enable):
        with self.captureOnCommitCallbacks(execute=True):
            Payment.objects.create(
                customer=self.customer, subscription=self.sub,
                amount=self.package.price, method="cash",
            )
        mock_enable.assert_called_once()

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
        self.customer.hotspot_username = "AA:BB:CC:DD:EE:FF"
        self.customer.save(update_fields=["hotspot_username"])
        resp = self.client.post(self.URL, {
            "code": "WIFI-TEST01",
            "mac_address": "11:22:33:44:55:66",
        })
        self.assertEqual(resp.status_code, 400)

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
        user = User.objects.create_user(username="enc_cust", password="pass", role="customer")
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
        user = User.objects.create_user(username="bad_hs", password="x", role="customer")
        customer = Customer(
            user=user, full_name="Bad", phone="254788801001",
            connection_type="hotspot", pppoe_username="SKY-BAD-001",
        )
        with self.assertRaises(ValidationError):
            customer.full_clean()

    def test_pppoe_customer_with_hotspot_username_invalid(self):
        from django.core.exceptions import ValidationError
        user = User.objects.create_user(username="bad_pp", password="x", role="customer")
        customer = Customer(
            user=user, full_name="Bad", phone="254788801002",
            connection_type="pppoe", hotspot_username="AA:BB:CC:DD:EE:FF",
        )
        with self.assertRaises(ValidationError):
            customer.full_clean()

    def test_valid_pppoe_customer_passes_validation(self):
        user = User.objects.create_user(username="good_pp", password="x", role="customer")
        customer = Customer(
            user=user, full_name="Good", phone="254788801003",
            connection_type="pppoe",
        )
        customer.full_clean()  # must not raise

    def test_valid_hotspot_customer_passes_validation(self):
        user = User.objects.create_user(username="good_hs", password="x", role="customer")
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
        User.objects.create_user(username="throttle_user", password="correct!", role="admin")

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
            {"username": "throttle_user", "password": "correct!"},
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
        # 3 = customer (+select_related) + subscriptions prefetch + vouchers prefetch
        with self.assertNumQueries(3):
            self._detail()

        for i in range(4):
            sub = Subscription.objects.create(
                customer=self.customer, package=self.package,
            )
            Voucher.objects.create(
                code=f"WIFI-EXTRA{i}", subscription=sub,
                expires_at=timezone.now() + timezone.timedelta(days=5),
            )

        # 3 = customer (+select_related) + subscriptions prefetch + vouchers prefetch
        with self.assertNumQueries(3):
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
            username="admin_one", password="pw", role="admin",
            tenant=self.t1, is_staff=True)
        self.admin2 = User.objects.create_user(
            username="admin_two", password="pw", role="admin",
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
        """Real JWT, so TenantMiddleware resolves the operator as in production."""
        client = APIClient()
        token = str(RefreshToken.for_user(user).access_token)
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
        with tenant_context(self.t1):
            self.assertEqual(Customer.objects.count(), 1)
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

    def test_policy_covers_every_scoped_table(self):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT tablename FROM pg_policies WHERE policyname = 'tenant_isolation'"
            )
            covered = {row[0] for row in cur.fetchall()}
        expected = {m._meta.db_table for m in SCOPED_MODELS}
        self.assertEqual(expected - covered, set(), "tables missing an RLS policy")
