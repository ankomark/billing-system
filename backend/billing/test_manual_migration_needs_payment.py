"""
The manual half of the admin migration endpoint needs the same paid guard.

AdminMigrateCustomerView has two branches. Without a router_id it delegates to
migrate_customer_router, which has required a settled invoice since 2026-09-09.
With one, it provisions directly -- and that branch was left on `status=active`
alone, so the same endpoint applied two different rules depending on whether
the operator named a router. Naming one is the ordinary case: it is what the
"move to skylink3" button sends.

It is also the more dangerous branch, because provisioning writes a fresh
limit-uptime measured from now. Picking the unpaid row does not merely carry an
unpaid allowance across, it renews it.

Found by auditing the 316 sessions live on 2026-09-12. All 227 subscribers
behind them held a valid paid subscription, but 34 were also carrying pending
rows from abandoned M-Pesa prompts, and for three of those the pending row
outlasted the paid one -- customer 45 held a pending three-week 20GB alongside
the one-week 4GB they had actually bought. Nothing had gone wrong yet only
because no operator had pressed the button.

The fake API is inert on purpose, as in test_migration_needs_payment: the
question is which subscription gets chosen, and a fake that accepts anything
cannot tell you what a router would have refused.
"""

from decimal import Decimal
from unittest.mock import patch

from django.urls import reverse
from rest_framework.test import APITestCase

from billing.models import (
    Customer, Invoice, Package, RouterDevice, Subscription, Tenant, User,
)
from billing.tenancy import tenant_context


class ManualMigrationRebuildsThePaidPackage(APITestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.admin = User.objects.create_user(
                username="op-manual", password="x", role="tenant_admin",
                tenant=self.tenant)
            self.here = RouterDevice.objects.create(
                tenant=self.tenant, name="here-m", ip_address="10.9.1.1",
                username="a", password="p", priority=1)
            self.there = RouterDevice.objects.create(
                tenant=self.tenant, name="there-m", ip_address="10.9.1.2",
                username="a", password="p", priority=2)

            self.short_paid = Package.objects.create(
                tenant=self.tenant, name="3hrs", download_speed=5,
                upload_speed=2, price=Decimal("10.00"), duration_value=3,
                duration_unit="hours", data_cap_mb=3000, is_hotspot=True)
            self.long_unpaid = Package.objects.create(
                tenant=self.tenant, name="3 weeks", download_speed=5,
                upload_speed=2, price=Decimal("250.00"), duration_value=3,
                duration_unit="weeks", data_cap_mb=20000, is_hotspot=True)

            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Abandoned Prompt",
                phone="254700900101", connection_type="hotspot",
                hotspot_username="AA:BB:CC:00:01:01", router=self.here,
                status="active")

        self.client.force_authenticate(user=self.admin)
        self.url = reverse("admin-migrate-customer")

    def _subscribe(self, package, paid):
        with tenant_context(self.tenant):
            sub = Subscription.objects.create(
                customer=self.customer, package=package, status="active")
            Invoice.objects.filter(subscription=sub).update(
                payment_status="paid" if paid else "pending")
        return sub

    def _migrate(self):
        """Name a router, and capture the subscription it provisions against."""
        seen = {}

        def remember(api, router, customer, subscription):
            seen["subscription"] = subscription
            return True

        with patch("billing.views.safe_connect_router", return_value=object()), \
             patch("billing.views.provision_customer_on_router",
                   side_effect=remember):
            response = self.client.post(self.url, {
                "customer_id": self.customer.id,
                "router_id": self.there.id,
            }, format="json")
        return response, seen

    def test_a_paid_short_package_beats_an_unpaid_long_one(self):
        """
        The failure in one assertion. Ordering by expiry alone picks the three
        weeks precisely because nobody paid for them.
        """
        paid = self._subscribe(self.short_paid, paid=True)
        self._subscribe(self.long_unpaid, paid=False)

        response, seen = self._migrate()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            seen["subscription"].id, paid.id,
            "the manual branch provisioned the package the customer walked "
            "away from paying for, because it outlasts the one they bought")

    def test_an_unpaid_subscription_alone_is_refused(self):
        """
        Nothing to move. Provisioning here is free service, and the refusal
        must say why rather than claiming there is no subscription at all --
        an operator looking at an active row needs to know the money is what
        is missing.
        """
        self._subscribe(self.long_unpaid, paid=False)

        response, seen = self._migrate()

        self.assertEqual(response.status_code, 400)
        self.assertIn("paid", response.data["detail"].lower())
        self.assertNotIn("subscription", seen)

    def test_any_invoice_status_other_than_paid_is_refused(self):
        """
        Written as "is paid" rather than "is not pending", so a status added
        later cannot quietly become grounds for provisioning.
        """
        sub = self._subscribe(self.long_unpaid, paid=False)
        with tenant_context(self.tenant):
            Invoice.objects.filter(subscription=sub).update(
                payment_status="failed")

        response, _ = self._migrate()

        self.assertEqual(response.status_code, 400)

    def test_a_paying_customer_still_moves(self):
        """
        The guard must not cost a paying subscriber their move, which is the
        expensive mistake and the whole reason the endpoint exists.
        """
        self._subscribe(self.short_paid, paid=True)

        response, seen = self._migrate()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn("there-m", response.data["detail"])
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.router_id, self.there.id)

    def test_the_longest_paid_subscription_is_still_chosen(self):
        """
        The guard narrows the field; it does not replace the sort.
        """
        self._subscribe(self.short_paid, paid=True)
        longer = self._subscribe(self.long_unpaid, paid=True)

        response, seen = self._migrate()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(seen["subscription"].id, longer.id)
