"""
An unpaid subscription must not keep somebody online when their real one ends.

enforce_subscription_expiry marks a subscription expired and then asks whether
anything else is still keeping that customer connected, so a top-up is not
self-defeating -- buying two hours with twenty minutes left should not have the
old package's expiry cut off the new one. That check was right about top-ups
and wrong about payment: it asked only for `active` with a future expiry.

Every subscription is born `active` with an unpaid invoice, so an ignored
M-Pesa prompt answers yes. When the customer's real package ran out, this
concluded they were still covered, skipped the disable, and left
customer.status alone -- and the longer the package they had walked away from
paying for, the longer they stayed on. A 250/- three-week prompt abandoned in
August was still suppressing expiry in September.

This is the one place in the class that hands out service rather than
withholding it. The grant paths were fixed in August and September; nothing
had looked at the path that decides whether to take service away.

Found on 2026-09-12 auditing the 316 sessions then live: 90 unpaid-but-active
rows across 63 subscribers, 35 of them outlasting every paid row their owner
had, 29 of those owners having paid nothing at all.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from billing.models import (
    Customer, Invoice, Package, Subscription, Tenant,
)
from billing.tasks.subscription_tasks import enforce_subscription_expiry
from billing.tenancy import tenant_context


class ExpiryLooksForPaidCover(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.short = Package.objects.create(
                tenant=self.tenant, name="3hrs - 3GB", download_speed=5,
                upload_speed=2, price=Decimal("10.00"), duration_value=3,
                duration_unit="hours", data_cap_mb=3000, is_hotspot=True)
            self.long = Package.objects.create(
                tenant=self.tenant, name="3 weeks - 20GB", download_speed=5,
                upload_speed=2, price=Decimal("250.00"), duration_value=3,
                duration_unit="weeks", data_cap_mb=20000, is_hotspot=True)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Ran Out",
                phone="254700920001", connection_type="hotspot",
                hotspot_username="AA:BB:CC:20:00:01", status="active")

    def _sub(self, package, *, paid, expires_in):
        with tenant_context(self.tenant):
            sub = Subscription.objects.create(
                customer=self.customer, package=package, status="active")
            Subscription.objects.filter(pk=sub.pk).update(
                expiry_date=timezone.now() + expires_in)
            Invoice.objects.filter(subscription=sub).update(
                payment_status="paid" if paid else "pending")
            sub.refresh_from_db()
        return sub

    def _run(self):
        """Run the sweep, capturing who got queued for disconnection."""
        with patch("billing.tasks.subscription_tasks.disable_customer_task"
                   ) as disable:
            enforce_subscription_expiry.run()
        return [c.args[0] for c in disable.delay.call_args_list]

    def test_an_abandoned_prompt_does_not_keep_them_connected(self):
        """
        The whole bug. The paid three hours are over; the only thing left is a
        three-week package nobody paid for, and it suppressed the disconnect.
        """
        self._sub(self.short, paid=True, expires_in=timedelta(minutes=-1))
        self._sub(self.long, paid=False, expires_in=timedelta(days=18))

        disabled = self._run()

        self.assertIn(self.customer.pk, disabled,
                      "an unpaid subscription kept the customer online after "
                      "the one they paid for ran out")

    def test_the_customer_is_marked_expired_too(self):
        """
        Skipping the disable also skipped this, so the dashboard showed an
        active customer with nothing paid behind them.
        """
        self._sub(self.short, paid=True, expires_in=timedelta(minutes=-1))
        self._sub(self.long, paid=False, expires_in=timedelta(days=18))

        self._run()

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.status, "expired")

    def test_a_paid_top_up_still_keeps_them_connected(self):
        """
        The behaviour the check exists for, and the expensive one to break:
        buying more time before the old package ends must not disconnect them.
        """
        self._sub(self.short, paid=True, expires_in=timedelta(minutes=-1))
        self._sub(self.long, paid=True, expires_in=timedelta(days=18))

        disabled = self._run()

        self.assertNotIn(self.customer.pk, disabled)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.status, "active")

    def test_the_expired_row_is_still_marked_expired_either_way(self):
        """Whether cover remains is a separate question from this row ending."""
        ended = self._sub(self.short, paid=True,
                          expires_in=timedelta(minutes=-1))
        self._sub(self.long, paid=True, expires_in=timedelta(days=18))

        self._run()

        ended.refresh_from_db()
        self.assertEqual(ended.status, "expired")

    def test_nothing_left_at_all_still_disconnects(self):
        """The ordinary case, unchanged."""
        self._sub(self.short, paid=True, expires_in=timedelta(minutes=-1))

        disabled = self._run()

        self.assertIn(self.customer.pk, disabled)
