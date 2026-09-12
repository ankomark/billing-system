"""
The rows an abandoned checkout leaves behind, and what closing them must not do.

Tapping a package writes an `active` subscription and an unpaid invoice before
any money moves. Ignore the M-Pesa prompt and that row sits inside its window
forever -- and `active` with a future expiry is what this codebase means by
"entitled to service". 90 were live across 63 subscribers on 2026-09-12, the
oldest from 26 August, and two call sites had already misread them.

The dangerous half of this cleanup is not the closing. It is closing a row that
somebody actually paid for: an invoice left `pending` while the money sat in
the account is a reconciliation gap, and expiring it takes away service that
was bought. Most of what follows is about refusing to do that.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from billing.models import (
    Customer, Invoice, MpesaTransaction, Package, Payment, Subscription, Tenant,
)
from billing.services.abandoned_checkouts import close_abandoned_checkouts
from billing.tenancy import tenant_context


class ClosingAbandonedCheckouts(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.package = Package.objects.create(
                tenant=self.tenant, name="3 weeks - 20GB", download_speed=5,
                upload_speed=2, price=Decimal("250.00"), duration_value=3,
                duration_unit="weeks", data_cap_mb=20000, is_hotspot=True)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Walked Away",
                phone="254700910001", connection_type="hotspot",
                hotspot_username="AA:BB:CC:10:00:01", status="active")

    def _sub(self, *, paid=False, age=timedelta(days=3), customer=None):
        with tenant_context(self.tenant):
            sub = Subscription.objects.create(
                customer=customer or self.customer, package=self.package,
                status="active",
                start_date=timezone.now() - age)
            Invoice.objects.filter(subscription=sub).update(
                payment_status="paid" if paid else "pending")
            sub.refresh_from_db()
        return sub

    def _status(self, sub):
        return Subscription.objects.all_tenants().get(pk=sub.pk).status

    # ---------------------------------------------------------------- closing

    def test_an_ignored_prompt_is_closed(self):
        sub = self._sub()

        result = close_abandoned_checkouts(apply=True)

        self.assertEqual(result.closed, 1)
        self.assertEqual(self._status(sub), "expired")

    def test_a_paid_subscription_is_never_touched(self):
        """The expensive mistake. This is a customer using the network."""
        sub = self._sub(paid=True)

        result = close_abandoned_checkouts(apply=True)

        self.assertEqual(result.closed, 0)
        self.assertEqual(self._status(sub), "active")

    def test_a_prompt_sent_minutes_ago_is_left_in_flight(self):
        """
        Not abandoned, just unanswered. Callbacks have landed late before, and
        closing this one cuts off somebody mid-purchase.
        """
        sub = self._sub(age=timedelta(minutes=3))

        result = close_abandoned_checkouts(apply=True)

        self.assertEqual(result.closed, 0)
        self.assertEqual(result.in_flight, 1)
        self.assertEqual(self._status(sub), "active")

    def test_reporting_writes_nothing(self):
        """The default. An operator must be able to look before acting."""
        sub = self._sub()

        result = close_abandoned_checkouts(apply=False)

        self.assertEqual(len(result.closed_rows), 1)
        self.assertEqual(result.closed, 0)
        self.assertEqual(self._status(sub), "active")

    def test_running_twice_closes_nothing_the_second_time(self):
        """Idempotent, because it is scheduled."""
        self._sub()

        close_abandoned_checkouts(apply=True)
        again = close_abandoned_checkouts(apply=True)

        self.assertEqual(again.closed, 0)

    def test_an_already_expired_row_is_not_reprocessed(self):
        sub = self._sub()
        with tenant_context(self.tenant):
            Subscription.objects.filter(pk=sub.pk).update(status="expired")

        result = close_abandoned_checkouts(apply=True)

        self.assertEqual(result.closed, 0)

    # ------------------------------------------------- money that did arrive

    def test_a_row_with_a_successful_mpesa_transaction_is_refused(self):
        """
        The reconciliation gap. The customer was debited; only the invoice
        failed to catch up. Expiring this takes away service that was paid for.
        """
        sub = self._sub()
        with tenant_context(self.tenant):
            MpesaTransaction.objects.create(
                tenant=self.tenant, invoice=sub.invoice, raw_payload={}, status="success",
                mpesa_receipt="TEST0000AA", amount=Decimal("250.00"))

        result = close_abandoned_checkouts(apply=True)

        self.assertEqual(result.closed, 0)
        self.assertEqual(self._status(sub), "active")
        self.assertTrue(result.needs_a_human)
        self.assertEqual(result.banked[0][0].pk, sub.pk)

    def test_a_receipt_alone_is_enough_to_refuse(self):
        """
        Safaricom issues a receipt only once the customer has been debited, so
        the receipt outranks whatever our own status column says.
        """
        sub = self._sub()
        with tenant_context(self.tenant):
            MpesaTransaction.objects.create(
                tenant=self.tenant, invoice=sub.invoice, raw_payload={}, status="failed",
                mpesa_receipt="TEST0000BB", amount=Decimal("250.00"))

        result = close_abandoned_checkouts(apply=True)

        self.assertEqual(result.closed, 0)
        self.assertTrue(result.needs_a_human)

    def test_a_payment_row_by_any_other_route_is_refused(self):
        """
        Cash taken at the shop, captured by hand. Still money.

        Recording a Payment normally settles the invoice itself -- Payment.save
        marks it paid and provisions the customer -- so this state should not
        arise, and the invoice filter alone would already protect the row. It
        is forced back to pending here because "should not arise" is exactly
        what was said about the 90 rows this sweep exists to clean up. If the
        settle half ever fails while the Payment lands, the money is real and
        the sweep must still refuse.
        """
        sub = self._sub()
        with tenant_context(self.tenant):
            Payment.objects.create(
                tenant=self.tenant, customer=self.customer, subscription=sub,
                amount=Decimal("250.00"), method="cash")
            Invoice.objects.filter(subscription=sub).update(
                payment_status="pending")

        result = close_abandoned_checkouts(apply=True)

        self.assertEqual(result.closed, 0)
        self.assertTrue(result.needs_a_human)
        self.assertEqual(self._status(sub), "active")

    def test_a_failed_transaction_with_no_receipt_does_not_protect_the_row(self):
        """
        A prompt that was declined is exactly what this cleans up. Treating any
        transaction row as money would make the sweep a no-op.
        """
        sub = self._sub()
        with tenant_context(self.tenant):
            MpesaTransaction.objects.create(
                tenant=self.tenant, invoice=sub.invoice, raw_payload={}, status="failed",
                amount=Decimal("250.00"))

        result = close_abandoned_checkouts(apply=True)

        self.assertEqual(result.closed, 1)
        self.assertFalse(result.needs_a_human)

    # ------------------------------------------------- grants, not purchases

    def test_a_comp_whose_payment_never_landed_is_not_closed(self):
        """
        Free internet granted by hand, where the comp Payment was never
        recorded. The invoice reads 0.00 and pending, which is exactly what an
        abandoned checkout looks like from the invoice alone -- but nobody
        abandons an M-Pesa prompt for nothing, and closing this takes back a
        decision an operator made on purpose.

        Two of these existed in production on 2026-09-12: customer 143 and
        customer 773, granted in the same batch as six comps that did settle,
        minutes apart, with the same package and the same stretched expiry.
        """
        sub = self._sub()
        with tenant_context(self.tenant):
            Invoice.objects.filter(subscription=sub).update(total_amount=0)

        result = close_abandoned_checkouts(apply=True)

        self.assertEqual(result.closed, 0)
        self.assertEqual(self._status(sub), "active")

    def test_an_unsettled_grant_is_reported_so_it_can_be_fixed(self):
        """
        Excluded from closing, but not invisible. Nothing else looks for these,
        and while the invoice reads unpaid the giveaway is not counted and the
        customer reads as online without paying.
        """
        sub = self._sub()
        with tenant_context(self.tenant):
            Invoice.objects.filter(subscription=sub).update(total_amount=0)

        result = close_abandoned_checkouts(apply=True)

        self.assertEqual([s.pk for s in result.unsettled_grants], [sub.pk])

    def test_a_settled_comp_is_not_reported_as_unsettled(self):
        """A comp that went through Payment properly is simply a paid row."""
        sub = self._sub(paid=True)
        with tenant_context(self.tenant):
            Invoice.objects.filter(subscription=sub).update(total_amount=0)

        result = close_abandoned_checkouts(apply=True)

        self.assertEqual(result.unsettled_grants, [])
        self.assertEqual(result.closed, 0)

    def test_a_zero_amount_row_is_not_counted_as_in_flight_either(self):
        """
        A grant is not a purchase at any age, so a recent one must not inflate
        the in-flight count and imply somebody is mid-payment.
        """
        sub = self._sub(age=timedelta(minutes=3))
        with tenant_context(self.tenant):
            Invoice.objects.filter(subscription=sub).update(total_amount=0)

        result = close_abandoned_checkouts(apply=True)

        self.assertEqual(result.in_flight, 0)

    # ------------------------------------------------------- blast radius

    def test_one_customer_keeps_the_package_they_paid_for(self):
        """
        The shape found in production: a real purchase with abandoned attempts
        beside it. Closing the litter must not disturb the live subscription.
        """
        paid = self._sub(paid=True)
        litter = [self._sub(), self._sub()]

        close_abandoned_checkouts(apply=True)

        self.assertEqual(self._status(paid), "active")
        for row in litter:
            self.assertEqual(self._status(row), "expired")

    def test_another_tenants_rows_are_swept_too(self):
        """
        all_tenants on purpose. This runs from beat, which has no request and
        therefore no tenant, and every operator accrues these.
        """
        other = Tenant.objects.create(name="Other", slug="other-abandoned")
        with tenant_context(other):
            package = Package.objects.create(
                tenant=other, name="1 week", download_speed=5, upload_speed=2,
                price=Decimal("30.00"), duration_value=1, duration_unit="weeks",
                data_cap_mb=10000, is_hotspot=True)
            customer = Customer.objects.create(
                tenant=other, full_name="Elsewhere", phone="254700910002",
                connection_type="hotspot", status="active",
                hotspot_username="AA:BB:CC:10:00:02")
            sub = Subscription.objects.create(
                customer=customer, package=package, status="active",
                start_date=timezone.now() - timedelta(days=2))
            Invoice.objects.filter(subscription=sub).update(
                payment_status="pending")

        result = close_abandoned_checkouts(apply=True)

        self.assertEqual(result.closed, 1)
        self.assertEqual(self._status(sub), "expired")

    def test_no_invoice_is_marked_paid_by_the_sweep(self):
        """
        It closes subscriptions; it does not settle debts. An invoice quietly
        flipped here would be a false record of payment.
        """
        sub = self._sub()

        close_abandoned_checkouts(apply=True)

        self.assertEqual(
            Invoice.objects.all_tenants().get(subscription=sub).payment_status,
            "pending")
