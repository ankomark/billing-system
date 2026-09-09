"""
A migration must rebuild the subscriber on the package they paid for.

Every subscription is born `active` with an unpaid invoice — Subscription.save
creates it that way — so "active" says nothing about whether money arrived.
enable_customer_access has filtered on the paid invoice since 2026-08-25, when
25 unpaid subscriptions were found still inside their window with 9 subscribers
provisioned on one. migrate_customer_router was never given the same guard, and
it sorts by `-expiry_date`, which inverts the intent: the longer the package
somebody walked away from paying for, the more certainly it wins.

Found on 2026-09-09, re-homing subscribers after an OLT cable moved from one
router to another. Of 90 moved, 14 were rebuilt against a subscription nobody
had paid for — 11 of them had no paid subscription at all — and each got a
fresh limit-uptime measured from the moment of the move, so the unpaid
allowance was renewed rather than merely carried over.

The fake API here is deliberately inert. The point is only which subscription
gets chosen, and a fake that accepts anything cannot tell you what the router
would have refused — the lesson already recorded against limit-uptime in
create_pppoe_secret.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from billing.models import (
    Customer, Invoice, Package, RouterDevice, Subscription, Tenant,
)
from billing.router_service import migrate_customer_router
from billing.tenancy import tenant_context


class MigrationRebuildsThePaidPackage(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Paid", slug="paid-test")
        with tenant_context(self.tenant):
            self.here = RouterDevice.objects.create(
                tenant=self.tenant, name="here", ip_address="10.9.0.1",
                username="a", password="p", priority=1)
            self.there = RouterDevice.objects.create(
                tenant=self.tenant, name="there", ip_address="10.9.0.2",
                username="a", password="p", priority=2)

            self.short_paid = Package.objects.create(
                name="3hrs", download_speed=5, upload_speed=2,
                price=Decimal("10.00"), duration_value=3, duration_unit="hours",
                data_cap_mb=0, is_hotspot=False)
            self.long_unpaid = Package.objects.create(
                name="3 weeks", download_speed=5, upload_speed=2,
                price=Decimal("250.00"), duration_value=3, duration_unit="weeks",
                data_cap_mb=0, is_hotspot=False)

            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Abandoned Prompt",
                phone="254700900001", connection_type="pppoe",
                router=self.here, status="active")

    def _subscribe(self, package, paid):
        """One active subscription, with its invoice settled or not."""
        with tenant_context(self.tenant):
            sub = Subscription.objects.create(
                customer=self.customer, package=package, status="active")
            Invoice.objects.filter(subscription=sub).update(
                payment_status="paid" if paid else "pending")
        return sub

    def _migrate(self):
        """Run a deliberate migration, capturing the package it provisions."""
        seen = {}

        def remember(api, router, customer, package, expiry_date=None):
            seen["package"] = package
            seen["expiry"] = expiry_date
            return True

        with patch("billing.router_service.pick_best_router_for_new_customer",
                   return_value=(self.there, object())), \
             patch("billing.router_service.create_pppoe_secret",
                   side_effect=remember), \
             patch("billing.router_service.enable_pppoe", return_value=True), \
             patch("billing.router_service.safe_connect_router",
                   return_value=None):
            ok, msg = migrate_customer_router(
                self.customer, reason="admin_manual")
        return ok, msg, seen

    def test_a_paid_short_package_beats_an_unpaid_long_one(self):
        """
        The whole failure in one assertion. Ordering by expiry alone picks the
        three-week package precisely because nobody paid for it and it runs
        longer than the three hours they did pay for.
        """
        paid = self._subscribe(self.short_paid, paid=True)
        self._subscribe(self.long_unpaid, paid=False)

        ok, msg, seen = self._migrate()

        self.assertTrue(ok, msg)
        self.assertEqual(
            seen["package"].id, self.short_paid.id,
            "migration rebuilt the subscriber on the package they abandoned "
            "paying for, because it outlasts the one they bought")
        self.assertEqual(seen["expiry"], paid.expiry_date)

    def test_an_unpaid_subscription_alone_is_not_migrated(self):
        """
        Nothing to move. Provisioning against this is free service, and the
        subscriber has no paid window for the move to preserve.
        """
        self._subscribe(self.long_unpaid, paid=False)

        ok, msg, seen = self._migrate()

        self.assertFalse(ok)
        self.assertEqual(msg, "No active subscription")
        self.assertNotIn("package", seen)

    def test_an_expired_invoice_status_other_than_paid_is_refused(self):
        """
        `pending` is not the only way an invoice fails to be paid, and the
        guard is written as "is paid" rather than "is not pending" so a new
        status cannot quietly become grounds for provisioning.
        """
        with tenant_context(self.tenant):
            sub = self._subscribe(self.long_unpaid, paid=False)
            Invoice.objects.filter(subscription=sub).update(
                payment_status="failed")

        ok, msg, _ = self._migrate()

        self.assertFalse(ok)
        self.assertEqual(msg, "No active subscription")

    def test_the_paid_subscription_still_migrates_normally(self):
        """
        The guard must not cost a paying subscriber their move — which is the
        expensive mistake, and the one this whole path exists to perform.
        """
        self._subscribe(self.short_paid, paid=True)

        ok, msg, seen = self._migrate()

        self.assertTrue(ok, msg)
        self.assertIn("there", msg)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.router_id, self.there.id)

    def test_the_longest_paid_subscription_is_still_the_one_chosen(self):
        """
        Ordering by expiry is right among subscriptions that were paid for —
        the guard narrows the field, it does not replace the sort.
        """
        self._subscribe(self.short_paid, paid=True)
        longer = self._subscribe(self.long_unpaid, paid=True)

        ok, msg, seen = self._migrate()

        self.assertTrue(ok, msg)
        self.assertEqual(seen["package"].id, self.long_unpaid.id)
        self.assertEqual(seen["expiry"], longer.expiry_date)
