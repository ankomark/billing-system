"""
Customer.status must agree with whether the customer holds anything.

The flag is written by several paths and cleared by others, and they do not
cover each other. On 2026-09-13 it was wrong in both directions at once: 19
customers flagged `active` holding nothing live, and 3 flagged `expired` while
holding a paid subscription they had just bought.

Neither direction is cosmetic. enforce_usage_caps scans
`customer__status="active"`, so a paying customer carrying a stale `expired`
is skipped by cap enforcement entirely and can spend an unlimited allowance on
a capped package. A stale `active` inflates every dashboard counting the flag
and puts the customer in the set auto-failover migrates when a router drops --
work done for somebody with nothing to serve.

The M-Pesa callback never set this; only the counter sale did. That is the
source half. The reconciler is the other half, because the paths that END a
subscription without clearing the flag are many and each is correct in itself:
the abandoned-checkout sweep, the window trim, an operator expiring a row by
hand, and a subscription already expired when the sweep ran and so never
appearing in its query.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from billing.models import (
    Customer, Invoice, Package, Payment, RouterDevice, Subscription, Tenant,
)
from billing.services.customer_status import sync_customer_status
from billing.tenancy import tenant_context


class _Base(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.package = Package.objects.create(
                tenant=self.tenant, name="3hrs - 3GB", download_speed=5,
                upload_speed=2, price=Decimal("10.00"), duration_value=3,
                duration_unit="hours", data_cap_mb=3000, is_hotspot=True)
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="status-r", ip_address="10.9.6.1",
                username="u", password="p", is_active=True, is_online=True)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Drifter",
                phone="254700990001", connection_type="hotspot",
                router=self.router, status="active",
                hotspot_username="AA:BB:CC:90:00:01")

    def _sub(self, *, paid=True, expires_in=timedelta(hours=2)):
        with tenant_context(self.tenant):
            sub = Subscription.objects.create(
                customer=self.customer, package=self.package, status="active")
            Subscription.objects.filter(pk=sub.pk).update(
                expiry_date=timezone.now() + expires_in)
            Invoice.objects.filter(subscription=sub).update(
                payment_status="paid" if paid else "pending")
            sub.refresh_from_db()
        return sub

    def _status(self):
        return Customer.objects.all_tenants().get(pk=self.customer.pk).status

    def _set(self, value):
        with tenant_context(self.tenant):
            Customer.objects.all_tenants().filter(
                pk=self.customer.pk).update(status=value)


class TheReconciler(_Base):

    def test_a_paying_customer_flagged_expired_is_activated(self):
        """
        The expensive direction. enforce_usage_caps scans
        customer__status="active", so this customer's data cap was not being
        enforced at all.
        """
        self._sub()
        self._set("expired")

        activate, expire = sync_customer_status(apply=True)

        self.assertEqual([c.pk for c in activate], [self.customer.pk])
        self.assertEqual(self._status(), "active")

    def test_a_customer_holding_nothing_is_expired(self):
        self._sub(expires_in=timedelta(hours=-1))

        activate, expire = sync_customer_status(apply=True)

        self.assertEqual([c.pk for c in expire], [self.customer.pk])
        self.assertEqual(self._status(), "expired")

    def test_a_customer_who_never_paid_is_expired(self):
        """Eleven of the nineteen found in production had never paid at all."""
        self._sub(paid=False)

        sync_customer_status(apply=True)

        self.assertEqual(self._status(), "expired")

    def test_a_correct_flag_is_left_alone(self):
        self._sub()

        activate, expire = sync_customer_status(apply=True)

        self.assertEqual(activate, [])
        self.assertEqual(expire, [])
        self.assertEqual(self._status(), "active")

    def test_reporting_writes_nothing(self):
        self._sub()
        self._set("expired")

        activate, _ = sync_customer_status(apply=False)

        self.assertEqual(len(activate), 1)
        self.assertEqual(self._status(), "expired")

    def test_a_hand_set_status_is_never_touched(self):
        """
        An operator who suspended somebody made a decision. This only moves
        between active and expired; guessing at anything else would undo it.
        """
        self._sub()
        self._set("suspended")

        activate, expire = sync_customer_status(apply=True)

        self.assertEqual(activate, [])
        self.assertEqual(expire, [])
        self.assertEqual(self._status(), "suspended")

    def test_a_suspended_subscription_does_not_count_as_holding(self):
        """
        Spending a data allowance suspends the subscription and leaves its
        expiry alone, so only the status distinguishes it.
        """
        sub = self._sub()
        with tenant_context(self.tenant):
            Subscription.objects.filter(pk=sub.pk).update(status="suspended")

        sync_customer_status(apply=True)

        self.assertEqual(self._status(), "expired")

    def test_it_is_idempotent(self):
        self._sub()
        self._set("expired")

        sync_customer_status(apply=True)
        activate, expire = sync_customer_status(apply=True)

        self.assertEqual((activate, expire), ([], []))


class ThePaymentItself(_Base):
    """
    The source half. The counter sale has always set this; the M-Pesa callback
    never did, so a subscriber swept to `expired` who bought again kept the
    flag until something else noticed.
    """

    def test_a_payment_activates_the_customer(self):
        sub = self._sub(paid=False)
        self._set("expired")
        # Re-read, because _set writes through the queryset and leaves this
        # instance stale. The M-Pesa callback loads the customer fresh off the
        # invoice, so a test holding a stale copy would be asking Payment.save
        # a question production never asks it.
        self.customer.refresh_from_db()

        with tenant_context(self.tenant):
            with patch("billing.models.notify_customer"), \
                 patch("billing.router_service.enable_customer_access"), \
                 patch("billing.router_service.safe_connect_router",
                       return_value=object()):
                Payment.objects.create(
                    tenant=self.tenant, customer=self.customer,
                    subscription=sub, amount=self.package.price,
                    method="mpesa", reference="TESTSTATUS1")

        self.assertEqual(self._status(), "active")

    def test_an_already_active_customer_is_not_rewritten(self):
        """The common case, and it must not cost a write on every payment."""
        sub = self._sub(paid=False)

        with tenant_context(self.tenant):
            with patch("billing.models.notify_customer"), \
                 patch("billing.router_service.enable_customer_access"), \
                 patch("billing.router_service.safe_connect_router",
                       return_value=object()), \
                 patch.object(Customer, "save") as saved:
                Payment.objects.create(
                    tenant=self.tenant, customer=self.customer,
                    subscription=sub, amount=self.package.price,
                    method="mpesa", reference="TESTSTATUS2")

        self.assertFalse(
            saved.called,
            "an already-active customer was written on a payment anyway")
