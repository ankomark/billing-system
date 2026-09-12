"""
The window a customer buys starts when their money lands.

`start_date` defaults to the moment the Subscription row is written, and that
row is written when somebody picks a package -- before the STK push has even
been sent. The callback arrives seconds later, and every one of those seconds
came off a window the customer paid for in full.

Measured across 200 M-Pesa purchases on 2026-09-12: median 17s, worst 43s, a
3-hour package delivering 2.994 hours, and 62 minutes of time paid for and not
received across the sample. Small individually, and wrong in the direction that
favours the operator.

The exception is an expiry somebody moved by hand. That is a decision, and
recomputing it here would quietly undo it -- the comps granted on 2026-08-26
and 27 carry deliberately stretched windows (a 6-hour bundle set to run a
month), and settling one of those later must not shrink it back to six hours.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from billing.models import (
    Customer, Invoice, Package, Payment, RouterDevice, Subscription, Tenant,
    Voucher,
)
from billing.tenancy import tenant_context


class TheClockStartsWhenTheyPay(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.package = Package.objects.create(
                tenant=self.tenant, name="3hrs - 3GB", download_speed=5,
                upload_speed=2, price=Decimal("10.00"), duration_value=3,
                duration_unit="hours", data_cap_mb=3000, is_hotspot=True,
                max_devices=2)
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="clock-r", ip_address="10.9.9.1",
                username="u", password="p", is_active=True, is_online=True)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Paid Late",
                phone="254700960001", connection_type="hotspot",
                router=self.router, status="active",
                hotspot_username="AA:BB:CC:60:00:01")

    def _subscription_tapped(self, *, ago):
        """A checkout started `ago` before now, its expiry as the tap implied."""
        with tenant_context(self.tenant):
            sub = Subscription.objects.create(
                customer=self.customer, package=self.package, status="active")
            tapped = timezone.now() - ago
            Subscription.objects.filter(pk=sub.pk).update(
                start_date=tapped,
                expiry_date=self.package.calculate_expiry(tapped))
            sub.refresh_from_db()
        return sub

    def _pay(self, sub):
        with tenant_context(self.tenant):
            # Patched where Payment.save imports them from: the import is
            # local to the method, so billing.models never holds the names.
            # The customer already has a router, so router selection is never
            # reached -- only the provisioning call needs silencing.
            with patch("billing.models.notify_customer"), \
                 patch("billing.router_service.enable_customer_access"), \
                 patch("billing.router_service.safe_connect_router",
                       return_value=object()):
                Payment.objects.create(
                    tenant=self.tenant, customer=self.customer,
                    subscription=sub, amount=self.package.price,
                    method="mpesa", reference="TESTCLOCK1")
        sub.refresh_from_db()
        return sub

    # ------------------------------------------------------------- the fix

    def test_the_window_is_measured_from_the_payment(self):
        """
        Tapped Buy twenty minutes ago, paying now: the three hours run from
        now, not from the tap.
        """
        sub = self._subscription_tapped(ago=timedelta(minutes=20))

        sub = self._pay(sub)

        window = (sub.expiry_date - sub.start_date).total_seconds()
        self.assertAlmostEqual(window, 3 * 3600, delta=5)
        self.assertAlmostEqual(
            (sub.expiry_date - timezone.now()).total_seconds(), 3 * 3600,
            delta=10,
            msg="the customer was short-changed by the time the prompt took")

    def test_the_start_date_moves_to_the_payment(self):
        sub = self._subscription_tapped(ago=timedelta(minutes=20))

        sub = self._pay(sub)

        self.assertAlmostEqual(
            (timezone.now() - sub.start_date).total_seconds(), 0, delta=10)

    def test_a_prompt_answered_instantly_is_barely_changed(self):
        """The ordinary case must not be disturbed by the correction."""
        sub = self._subscription_tapped(ago=timedelta(seconds=2))
        before = sub.expiry_date

        sub = self._pay(sub)

        self.assertAlmostEqual(
            (sub.expiry_date - before).total_seconds(), 2, delta=5)

    def test_the_voucher_expires_with_the_corrected_window(self):
        """
        Minted inside the same transaction, from subscription.expiry_date. If
        the recomputation happened after, the voucher would expire on the old
        clock and stop working before the package did.
        """
        sub = self._subscription_tapped(ago=timedelta(minutes=30))

        sub = self._pay(sub)

        voucher = Voucher.objects.all_tenants().filter(subscription=sub).first()
        self.assertIsNotNone(voucher)
        self.assertAlmostEqual(
            (voucher.expires_at - sub.expiry_date).total_seconds(), 0, delta=5)

    def test_the_subscription_still_ends_up_active_and_paid(self):
        """The behaviour that was already there, kept."""
        sub = self._subscription_tapped(ago=timedelta(minutes=5))

        sub = self._pay(sub)

        self.assertEqual(sub.status, "active")
        self.assertEqual(
            Invoice.objects.all_tenants().get(subscription=sub).payment_status,
            "paid")

    # --------------------------------------------------- the hand-moved case

    def test_an_expiry_moved_by_hand_is_left_alone(self):
        """
        The comp case. A 6-hour bundle deliberately set to run a month must not
        be shrunk back to six hours when somebody records the missing payment.
        """
        sub = self._subscription_tapped(ago=timedelta(days=2))
        stretched = timezone.now() + timedelta(days=25)
        with tenant_context(self.tenant):
            Subscription.objects.filter(pk=sub.pk).update(
                expiry_date=stretched)
            sub.refresh_from_db()

        sub = self._pay(sub)

        self.assertAlmostEqual(
            (sub.expiry_date - stretched).total_seconds(), 0, delta=5,
            msg="settling a comp destroyed the window somebody granted")

    def test_a_hand_moved_expiry_keeps_its_original_start_date(self):
        """
        Moving the start would change what the stretched window means, and the
        whole point of leaving it alone is to change nothing.
        """
        sub = self._subscription_tapped(ago=timedelta(days=2))
        tapped = sub.start_date
        with tenant_context(self.tenant):
            Subscription.objects.filter(pk=sub.pk).update(
                expiry_date=timezone.now() + timedelta(days=25))
            sub.refresh_from_db()

        sub = self._pay(sub)

        self.assertAlmostEqual(
            (sub.start_date - tapped).total_seconds(), 0, delta=5)

    def test_a_hand_moved_expiry_is_still_marked_active(self):
        sub = self._subscription_tapped(ago=timedelta(days=2))
        with tenant_context(self.tenant):
            Subscription.objects.filter(
                pk=sub.pk).update(expiry_date=timezone.now() + timedelta(days=25),
                                  status="expired")
            sub.refresh_from_db()

        sub = self._pay(sub)

        self.assertEqual(sub.status, "active")
