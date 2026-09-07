"""
The dashboard's headline counts must mean what an operator reads them as.

A subscription is created the moment somebody starts a purchase -- born
status="active" with an unpaid invoice -- so an abandoned checkout leaves one
behind and it stays "active" until it ages out. Counting those made "Active"
mean "people who opened the buy page", not "people I am carrying".

On 2026-09-07 that was 80 of 355 active on production, and 2327 of 10974 on the
tile beside it, which is labelled "not paying".

Nobody is served on an unpaid subscription -- provisioning happens in
Payment.save() -- so the number was not merely imprecise, it counted people who
had never been connected.
"""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from billing.models import Customer, Package, Subscription, Tenant
from billing.reports import customer_stats
from billing.tenancy import tenant_context


class DashboardCountTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.package = Package.objects.create(
                tenant=self.tenant, name="1 GB unlimited", download_speed=2,
                upload_speed=2, price=Decimal("50.00"), duration_value=1,
                duration_unit="days", is_hotspot=True, data_cap_mb=1024)

    _n = [0]

    def _subscription(self, *, status, payment_status):
        self._n[0] += 1
        n = self._n[0]
        with tenant_context(self.tenant):
            customer = Customer.objects.create(
                tenant=self.tenant, full_name=f"Hotspot {n:04d}",
                phone=f"07000{n:05d}", connection_type="hotspot")
            sub = Subscription.objects.create(
                tenant=self.tenant, customer=customer, package=self.package,
                status=status,
                start_date=timezone.now(),
                expiry_date=timezone.now() + timezone.timedelta(days=1))

            # Subscription.save() already made the invoice, unpaid -- which is
            # the very behaviour under test: a purchase that is started creates
            # a live-looking subscription before any money arrives. Creating a
            # second one violates the one-to-one, so the existing row is what
            # gets settled here, exactly as Payment.save() settles it.
            invoice = sub.invoice
            invoice.payment_status = payment_status
            invoice.save(update_fields=["payment_status"])
            return sub

    def test_an_abandoned_checkout_is_not_an_active_subscriber(self):
        """
        The headline number. Somebody who opened the buy page and walked away
        has a live subscription row and has never been connected.
        """
        self._subscription(status="active", payment_status="paid")
        self._subscription(status="active", payment_status="unpaid")

        with tenant_context(self.tenant):
            stats = customer_stats()
        self.assertEqual(stats["active_subscriptions"], 1)

    def test_a_pending_payment_does_not_count_either(self):
        """
        An STK push that was sent and never confirmed. Live-looking, unpaid,
        and nobody is on the network for it.
        """
        self._subscription(status="active", payment_status="pending")

        with tenant_context(self.tenant):
            stats = customer_stats()
        self.assertEqual(stats["active_subscriptions"], 0)

    def test_expired_counts_only_what_was_paid_for(self):
        """
        The tile beside it says "not paying". An abandoned checkout that aged
        out never was paying, and counting it overstates churn.
        """
        self._subscription(status="expired", payment_status="paid")
        self._subscription(status="expired", payment_status="unpaid")

        with tenant_context(self.tenant):
            stats = customer_stats()
        self.assertEqual(stats["expired_subscriptions"], 1)

    def test_the_invoice_tiles_are_deliberately_not_filtered(self):
        """
        Those count invoices, not subscribers, and an unpaid invoice is the
        entire point of the tile. Filtering them would empty it.
        """
        self._subscription(status="active", payment_status="unpaid")
        self._subscription(status="active", payment_status="pending")

        with tenant_context(self.tenant):
            stats = customer_stats()
        self.assertEqual(stats["unpaid_invoices"], 1)
        self.assertEqual(stats["pending_invoices"], 1)

    def test_a_paid_subscriber_is_still_counted(self):
        """The change must not empty the dashboard."""
        self._subscription(status="active", payment_status="paid")
        self._subscription(status="active", payment_status="paid")

        with tenant_context(self.tenant):
            stats = customer_stats()
        self.assertEqual(stats["active_subscriptions"], 2)
