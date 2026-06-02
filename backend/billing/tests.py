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
from unittest.mock import patch
from cryptography.fernet import Fernet

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from rest_framework.test import APIClient

from billing.models import (
    User, Customer, Package, Subscription,
    Invoice, Payment, Voucher, MpesaTransaction, RouterDevice,
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
