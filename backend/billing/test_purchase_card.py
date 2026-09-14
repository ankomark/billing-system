"""
One card per purchase, instead of a package list and a code list side by side.

An operator holding a voucher code had to match it against a separate list of
subscriptions to answer the only questions anyone asks at a counter: what is
this code for, has it run out, and how much of it is left. The voucher belongs
to the subscription — it is the thing that was sold — so it is carried on the
row.

The expensive part is the usage figure. Asking for it per row is one aggregate
each, which is the regression CustomerDetailSerializerTests was written for and
has now caught twice. It is computed once, for the subscription in force, and
handed down.
"""

from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient
from django.utils import timezone

from billing.models import (
    Customer, Invoice, MpesaTransaction, Package, RouterDevice, Subscription,
    Tenant, Voucher,
)
from billing.tenancy import tenant_context


class ThePurchaseCard(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="card-r", ip_address="10.9.0.130",
                username="u", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="24hrs - 10GB", download_speed=4,
                upload_speed=2, price=Decimal("40.00"), duration_value=24,
                duration_unit="hours", data_cap_mb=10000, is_hotspot=True)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Card Holder",
                phone="254700190001", connection_type="hotspot",
                router=self.router, status="active")
            self.sub = Subscription.objects.create(
                customer=self.customer, package=self.package, status="active")
            Invoice.objects.filter(subscription=self.sub).update(
                payment_status="paid")
            self.sub.refresh_from_db()
            self.voucher = Voucher.objects.create(
                tenant=self.tenant, code="WIFI-CARD01",
                subscription=self.sub, expires_at=self.sub.expiry_date,
                is_active=True)

        from django.contrib.auth import get_user_model

        U = get_user_model()
        admin = U.objects.create_user(
            username="card-admin", password="x", role="tenant_admin",
            tenant=self.tenant)
        self.client = APIClient()
        self.client.force_authenticate(user=admin)

    def row(self, sub_id=None):
        resp = self.client.get(f"/api/customers/{self.customer.id}/")
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        rows = resp.data["subscriptions"]
        target = sub_id or self.sub.id
        return next(r for r in rows if r["id"] == target)

    # ──────────────────────────────────────────────── the voucher is on the row

    def test_the_code_is_on_the_subscription(self):
        """The whole point: no cross-referencing two lists."""
        row = self.row()

        self.assertEqual([v["code"] for v in row["vouchers"]], ["WIFI-CARD01"])

    def test_a_retired_code_still_shows_with_its_state(self):
        """
        An operator asking "why is this code not working" needs to see it and
        see that it is off, not to find it missing.
        """
        with tenant_context(self.tenant):
            Voucher.objects.filter(pk=self.voucher.pk).update(is_active=False)

        self.assertFalse(self.row()["vouchers"][0]["is_active"])

    def test_a_subscription_with_no_code_says_so_plainly(self):
        with tenant_context(self.tenant):
            bare = Subscription.objects.create(
                customer=self.customer, package=self.package, status="active")

        self.assertEqual(self.row(bare.id)["vouchers"], [])

    # ─────────────────────────────────────────────────────────── what was sold

    def test_the_package_and_dates_are_there(self):
        row = self.row()

        self.assertEqual(row["package_name"], "24hrs - 10GB")
        self.assertIsNotNone(row["start_date"])
        self.assertIsNotNone(row["expiry_date"])

    def test_the_cap_is_the_one_that_was_sold(self):
        """
        Not the package's cap today. Raising a package must not rewrite what
        somebody already bought — see Subscription.data_cap_mb.
        """
        with tenant_context(self.tenant):
            Package.objects.filter(pk=self.package.pk).update(data_cap_mb=99000)

        self.assertEqual(self.row()["data_cap_mb"], 10000)

    def test_an_unlimited_package_reads_as_zero(self):
        with tenant_context(self.tenant):
            Subscription.objects.filter(pk=self.sub.pk).update(data_cap_mb=0)

        self.assertEqual(self.row()["data_cap_mb"], 0)

    # ──────────────────────────────────────────────────────────── who paid

    def test_the_number_that_was_charged_is_shown(self):
        """
        Not always the subscriber's own. Somebody whose friend had M-Pesa
        balance is bought for by that friend, and a disputed payment is settled
        by the number that was actually charged.
        """
        with tenant_context(self.tenant):
            MpesaTransaction.objects.create(
                tenant=self.tenant, invoice=self.sub.invoice,
                mpesa_receipt="TEST00001", amount=Decimal("40.00"),
                phone_number="254711222333", checkout_request_id="ws_CO_1",
                raw_payload={})

        self.assertEqual(self.row()["paid_from"], "254711222333")

    def test_a_comped_bundle_has_no_payer(self):
        """
        None, not the subscriber's own number. Inventing one would make a gift
        look like a sale, which is the thing an operator is trying to tell
        apart.
        """
        self.assertIsNone(self.row()["paid_from"])

    # ─────────────────────────────────────────────────────── is it running now

    def test_a_live_subscription_says_so(self):
        self.assertTrue(self.row()["is_live"])

    def test_one_past_its_expiry_is_not_live_whatever_the_row_says(self):
        """
        A row reads "active" until a sweep reaches it. An operator looking at
        03:00 must not be told a bundle that ended at midnight is running.
        """
        with tenant_context(self.tenant):
            Subscription.objects.filter(pk=self.sub.pk).update(
                expiry_date=timezone.now() - timezone.timedelta(hours=1))

        row = self.row()
        self.assertEqual(row["status"], "active")
        self.assertFalse(row["is_live"])

    # ──────────────────────────────────────────────────────────────── usage

    def test_the_running_bundle_carries_its_usage(self):
        row = self.row()

        self.assertIsNotNone(row["data_used_bytes"])

    def test_a_finished_bundle_shows_nothing_rather_than_zero(self):
        """
        Zero is a real answer — bought and never used. A row that cannot say
        must not be mistaken for one.
        """
        with tenant_context(self.tenant):
            old = Subscription.objects.create(
                customer=self.customer, package=self.package, status="expired")

        self.assertIsNone(self.row(old.id)["data_used_bytes"])

    def test_the_usage_agrees_with_the_panel_above_it(self):
        """
        One computation, handed down. Two would be two chances to disagree
        about the same subscriber on the same screen.
        """
        resp = self.client.get(f"/api/customers/{self.customer.id}/")
        row = next(r for r in resp.data["subscriptions"] if r["id"] == self.sub.id)

        self.assertEqual(row["data_used_bytes"],
                         resp.data["data_usage"]["used_bytes"])
