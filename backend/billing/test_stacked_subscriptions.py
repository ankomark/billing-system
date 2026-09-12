"""
Noticing a customer who accumulates live subscriptions.

Every row in a stack is active, paid and inside its own expiry, so each one
answers yes to every check the system makes. Nothing was wrong with any of
customer 33's nine subscriptions on 2026-09-12 -- every one was a comp issued
by the operator's own admin, logged in AdminActionLog with a reason. What was
wrong was the total: KSh 2,271 of packages overlapping continuously from 26
August to 9 October, and ten device rows spread across them so that no single
package's two-device limit was ever breached while five ran at once.

It surfaced only because a one-week package turned out to expire in 21 days.
Without that accident nothing would ever have reported it.

The hard part is not finding stacks, it is not crying wolf. Topping up creates
overlap on purpose -- buying two hours with twenty minutes left is exactly this
shape, and enforce_subscription_expiry's coverage check exists to support it.
So two is normal and three is the default threshold.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from billing.models import (
    Customer, CustomerDevice, Invoice, Package, Payment, Subscription, Tenant,
)
from billing.services.stacked_subscriptions import find_stacked
from billing.tenancy import tenant_context


class FindingStacks(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.week = Package.objects.create(
                tenant=self.tenant, name="1 week - 10GB", download_speed=5,
                upload_speed=2, price=Decimal("30.00"), duration_value=1,
                duration_unit="weeks", data_cap_mb=10000, is_hotspot=True,
                max_devices=2)
            self.month = Package.objects.create(
                tenant=self.tenant, name="1 month - 30GB", download_speed=5,
                upload_speed=2, price=Decimal("598.00"), duration_value=1,
                duration_unit="months", data_cap_mb=30000, is_hotspot=True,
                max_devices=2)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Collector",
                phone="254700970001", connection_type="hotspot",
                status="active", hotspot_username="AA:BB:CC:70:00:01")

    def _sub(self, package=None, *, paid=True, comp=False,
             expires_in=timedelta(days=5), customer=None):
        cust = customer or self.customer
        with tenant_context(self.tenant):
            sub = Subscription.objects.create(
                customer=cust, package=package or self.week, status="active")
            Subscription.objects.filter(pk=sub.pk).update(
                expiry_date=timezone.now() + expires_in)
            Invoice.objects.filter(subscription=sub).update(
                payment_status="paid" if paid else "pending")
            if comp:
                # Straight to the table: Payment.save provisions routers and
                # settles invoices, and this is about the record, not the act.
                Payment.objects.create(
                    tenant=self.tenant, customer=cust, subscription=sub,
                    amount=Decimal("0.00"), method="comp", reference="Refund")
                Invoice.objects.filter(subscription=sub).update(
                    payment_status="paid")
            sub.refresh_from_db()
        return sub

    # ------------------------------------------------------- not crying wolf

    def test_one_subscription_is_not_a_stack(self):
        self._sub()
        self.assertEqual(find_stacked(), [])

    def test_a_top_up_is_not_a_stack(self):
        """
        Two live subscriptions is somebody buying more time before the old
        package ends. It is deliberately supported, and flagging it would bury
        the real thing in noise.
        """
        self._sub(expires_in=timedelta(minutes=20))
        self._sub(expires_in=timedelta(hours=2))

        self.assertEqual(find_stacked(), [])

    def test_three_is_reported(self):
        for _ in range(3):
            self._sub()

        stacks = find_stacked()

        self.assertEqual(len(stacks), 1)
        self.assertEqual(stacks[0].customer.pk, self.customer.pk)
        self.assertEqual(stacks[0].live_count, 3)

    def test_the_threshold_can_be_lowered(self):
        self._sub()
        self._sub()

        self.assertEqual(len(find_stacked(min_live=2)), 1)

    def test_expired_rows_do_not_count(self):
        """A customer with a long history is not a customer with a stack."""
        self._sub()
        self._sub(expires_in=timedelta(hours=-1))
        self._sub(expires_in=timedelta(days=-9))

        self.assertEqual(find_stacked(), [])

    def test_unpaid_rows_do_not_count(self):
        """
        Abandoned checkouts pile up on their own and are somebody else's job --
        close_abandoned_checkouts closes them, and reporting them here would
        duplicate that with a scarier label.
        """
        self._sub()
        self._sub(paid=False)
        self._sub(paid=False)

        self.assertEqual(find_stacked(), [])

    def test_suspended_rows_do_not_count(self):
        self._sub()
        self._sub()
        s = self._sub()
        with tenant_context(self.tenant):
            Subscription.objects.filter(pk=s.pk).update(status="suspended")

        self.assertEqual(find_stacked(), [])

    # ------------------------------------------------------- what it reports

    def test_comped_rows_are_identified_and_valued_at_list_price(self):
        """
        A comp settles the invoice at 0.00, so the invoice total says nothing
        about what was given away. The package price is what it would have
        cost.
        """
        self._sub(comp=True)
        self._sub(self.month, comp=True)
        self._sub()

        stack = find_stacked()[0]

        self.assertEqual(len(stack.comps), 2)
        self.assertEqual(stack.comp_value, Decimal("628.00"))

    def test_the_device_allowance_is_the_sum_not_the_largest(self):
        """
        The part that costs bandwidth. Each subscription carries its own
        allowance and its own device rows, so stacking adds them together --
        ten devices on packages advertised as two-device, with no single limit
        breached.
        """
        for _ in range(3):
            self._sub()
        with tenant_context(self.tenant):
            for i in range(6):
                CustomerDevice.objects.create(
                    tenant=self.tenant, customer=self.customer,
                    mac_address=f"AA:BB:CC:70:01:{i:02X}")

        stack = find_stacked()[0]

        self.assertEqual(stack.device_allowance, 6)
        self.assertEqual(stack.devices, 6)

    def test_the_covered_span_is_reported(self):
        self._sub(expires_in=timedelta(days=2))
        self._sub(expires_in=timedelta(days=30))
        self._sub(expires_in=timedelta(days=9))

        stack = find_stacked()[0]

        self.assertAlmostEqual(
            (stack.covered_until - timezone.now()).days, 29, delta=1)

    def test_the_worst_stack_comes_first(self):
        with tenant_context(self.tenant):
            other = Customer.objects.create(
                tenant=self.tenant, full_name="Smaller",
                phone="254700970002", connection_type="hotspot",
                status="active", hotspot_username="AA:BB:CC:70:00:02")
        for _ in range(3):
            self._sub(customer=other)
        for _ in range(5):
            self._sub()

        stacks = find_stacked()

        self.assertEqual(stacks[0].customer.pk, self.customer.pk)
        self.assertEqual(stacks[0].live_count, 5)
        self.assertEqual(stacks[1].live_count, 3)

    def test_it_changes_nothing(self):
        """
        Reports only. Every row in a stack is valid, and whether it should
        exist is a judgement about a customer rather than a rule a sweep can
        apply.
        """
        subs = [self._sub() for _ in range(3)]

        find_stacked()

        for s in subs:
            s.refresh_from_db()
            self.assertEqual(s.status, "active")
