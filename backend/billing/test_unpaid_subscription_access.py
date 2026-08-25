"""
Access follows the money, not the row.

Every subscription is created `active` with an unpaid invoice: the status
field defaults to active, and Subscription.save() makes the invoice as
unpaid. Payment.save() then sets the status it already had. So "active" has
never meant "paid", and two places treated it as though it did.

Measured in production on 2026-08-25, before this was fixed:

  * 25 subscriptions active, unpaid, and still inside their window
  * 9 subscribers provisioned on one of them
  * one had paid 10/- for three hours while holding an unpaid 100/- weekly
  * one had paid 40/- for a day while holding an unpaid 75/- two-day
  * the rest had paid nothing at all — they abandoned the M-Pesa prompt and
    came back through /hotspot/reconnect/, which asked only whether a
    subscription was active

A customer who abandons a purchase must end up exactly where they started.
"""

from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from billing.models import (
    Customer, Package, RouterDevice, Subscription, Tenant,
)
from billing.tenancy import tenant_context

PHONE_MAC = "AA:BB:CC:00:11:22"


class UnpaidSubscriptionAccessTests(TestCase):
    """One subscriber who paid for a little and abandoned a lot."""

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        now = timezone.now()
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r", ip_address="10.0.0.1",
                username="u", password="p")
            self.small = Package.objects.create(
                tenant=self.tenant, name="3hrs unlimited", download_speed=5,
                upload_speed=2, price=Decimal("10.00"), duration_value=3,
                duration_unit="hours", monthly_data_cap_gb=0, is_hotspot=True,
                max_devices=1)
            self.big = Package.objects.create(
                tenant=self.tenant, name="5 GB unlimited highspeed",
                download_speed=20, upload_speed=10, price=Decimal("100.00"),
                duration_value=1, duration_unit="weeks",
                monthly_data_cap_gb=0, is_hotspot=True, max_devices=1)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Wanjiru", phone="254706568711",
                connection_type="hotspot", router=self.router,
                hotspot_username=PHONE_MAC)

            # Paid for three hours.
            self.paid = Subscription.objects.create(
                tenant=self.tenant, customer=self.customer,
                package=self.small, status="active",
                expiry_date=now + timedelta(hours=3))
            inv = self.paid.invoice
            inv.payment_status = "paid"
            inv.save(update_fields=["payment_status"])

            # Started buying a week, never finished. Created active and
            # unpaid, exactly as the purchase endpoint leaves it.
            self.abandoned = Subscription.objects.create(
                tenant=self.tenant, customer=self.customer,
                package=self.big, status="active",
                expiry_date=now + timedelta(days=7))

    def _grant(self):
        """Run a real grant, capturing what expiry reached the router."""
        with patch("billing.router_service.safe_connect_router",
                   return_value=MagicMock()), \
             patch("billing.router_service.ensure_hotspot_profile",
                   return_value="prof"), \
             patch("billing.router_service.enable_hotspot") as enable, \
             tenant_context(self.tenant):
            from billing.router_service import enable_customer_access
            granted = enable_customer_access(self.customer)
        return granted, enable

    def test_the_abandoned_purchase_is_unpaid_and_active(self):
        """The state this is all about, asserted rather than assumed."""
        self.assertEqual(self.abandoned.status, "active")
        self.assertNotEqual(self.abandoned.invoice.payment_status, "paid")

    def test_access_is_built_on_what_was_paid_for(self):
        """
        The abandoned week expires later than the paid three hours, so the
        longest-running-subscription rule picked it and provisioned a
        subscriber onto a package nobody had paid for.
        """
        granted, enable = self._grant()
        self.assertTrue(granted)

        expiries = [c.args[4] for c in enable.call_args_list]
        self.assertTrue(expiries, "nothing was provisioned")
        for expiry in expiries:
            self.assertEqual(
                expiry, self.paid.expiry_date,
                "the router was given the abandoned purchase's expiry")

    def test_paying_nothing_at_all_grants_nothing(self):
        with tenant_context(self.tenant):
            inv = self.paid.invoice
            inv.payment_status = "unpaid"
            inv.save(update_fields=["payment_status"])

        granted, enable = self._grant()
        self.assertFalse(granted, "access was granted with nothing paid")
        enable.assert_not_called()

    def test_reconnect_refuses_a_subscriber_who_never_paid(self):
        """
        Abandon the prompt, come back here. This asked only whether a
        subscription was active, and every subscription is born active.
        """
        with tenant_context(self.tenant):
            inv = self.paid.invoice
            inv.payment_status = "unpaid"
            inv.save(update_fields=["payment_status"])

        with patch("billing.views.enable_customer_task.delay") as task:
            resp = APIClient().post("/api/hotspot/reconnect/", {
                "t": self.tenant.public_token, "mac": PHONE_MAC},
                format="json")

        self.assertEqual(resp.status_code, 403, resp.data)
        self.assertEqual(resp.data["reason"], "no_subscription")
        task.assert_not_called()

    def test_reconnect_still_works_for_somebody_who_paid(self):
        """The refusal must be about payment, not about reconnecting."""
        with patch("billing.views.enable_customer_task.delay") as task:
            resp = APIClient().post("/api/hotspot/reconnect/", {
                "t": self.tenant.public_token, "mac": PHONE_MAC},
                format="json")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "allowed")
        self.assertEqual(
            resp.data["expires_at"], self.paid.expiry_date,
            "reconnect reported the abandoned purchase's time")
        task.assert_called_once()

    def test_a_comped_subscription_still_grants(self):
        """
        Giving access away is a deliberate operator action and marks the
        invoice paid. It must keep working — this is about money never
        arriving, not about money being waived.
        """
        with tenant_context(self.tenant):
            inv = self.paid.invoice
            inv.payment_status = "unpaid"
            inv.save(update_fields=["payment_status"])
            comped = self.abandoned.invoice
            comped.payment_status = "paid"
            comped.save(update_fields=["payment_status"])

        granted, enable = self._grant()
        self.assertTrue(granted)
        self.assertEqual(
            enable.call_args_list[0].args[4], self.abandoned.expiry_date)
