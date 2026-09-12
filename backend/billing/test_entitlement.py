"""
Nobody reaches the network without a paid, live, unsuspended subscription.

Three questions decide it, and the history of this codebase is call sites that
asked one or two of them. `status="active"` is the seductive one: it is what
the query says, it reads like an entitlement, and it is set by
Subscription.save the instant somebody taps a package -- before any money
moves. An ignored M-Pesa prompt leaves a row that answers yes.

These tests pin the rule itself, the gate in front of the hardware, and the
voucher validator that was getting it wrong.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from billing.models import (
    Customer, Invoice, Package, RouterDevice, Subscription, Tenant, Voucher,
)
from billing.router_service import NotEntitled, provision_customer_on_router
from billing.services.entitlement import (
    entitlement_reason, granting_subscription, is_entitled,
)
from billing.services.voucher_service import validate_voucher
from billing.tenancy import tenant_context


class _Base(TestCase):

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
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="ent-r1", ip_address="10.9.5.1",
                username="u", password="p")
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Entitled",
                phone="254700930001", connection_type="hotspot",
                hotspot_username="AA:BB:CC:30:00:01", status="active",
                router=self.router)

    def _sub(self, package=None, *, paid=True, status="active",
             expires_in=timedelta(days=3)):
        with tenant_context(self.tenant):
            sub = Subscription.objects.create(
                customer=self.customer, package=package or self.long,
                status="active")
            Subscription.objects.filter(pk=sub.pk).update(
                expiry_date=timezone.now() + expires_in, status=status)
            Invoice.objects.filter(subscription=sub).update(
                payment_status="paid" if paid else "pending")
            sub.refresh_from_db()
        return sub


class TheRuleItself(_Base):

    def test_a_paid_live_active_subscription_is_entitled(self):
        self.assertTrue(is_entitled(self._sub()))

    def test_an_unpaid_subscription_is_not(self):
        """The abandoned M-Pesa prompt. Active, in date, never paid for."""
        sub = self._sub(paid=False)
        self.assertFalse(is_entitled(sub))
        self.assertIn("not paid", entitlement_reason(sub))

    def test_a_suspended_subscription_is_not(self):
        """
        What enforce_usage_caps writes when a data allowance runs out. It
        leaves expiry_date alone, so anything checking only the date lets a
        capped subscriber straight back on and the cap means nothing.
        """
        sub = self._sub(status="suspended")
        self.assertFalse(is_entitled(sub))
        self.assertIn("data allowance", entitlement_reason(sub))

    def test_a_row_marked_expired_with_a_future_date_is_not(self):
        """
        The abandoned-checkout sweep marks rows expired without touching
        expiry_date, so status and date disagree on purpose. The status wins.
        """
        self.assertFalse(is_entitled(self._sub(status="expired")))

    def test_a_past_expiry_is_not(self):
        self.assertFalse(is_entitled(self._sub(expires_in=timedelta(hours=-1))))

    def test_none_is_not(self):
        self.assertFalse(is_entitled(None))
        self.assertEqual(entitlement_reason(None), "no subscription")


class ChoosingWhatToServe(_Base):

    def test_the_longest_paid_subscription_wins(self):
        self._sub(self.short, expires_in=timedelta(hours=2))
        longer = self._sub(self.long, expires_in=timedelta(days=18))

        self.assertEqual(granting_subscription(self.customer).pk, longer.pk)

    def test_an_unpaid_longer_one_does_not_win(self):
        """
        The bug this whole module exists to prevent. Ordering by expiry before
        filtering by payment picks the biggest thing nobody paid for.
        """
        paid = self._sub(self.short, expires_in=timedelta(hours=2))
        self._sub(self.long, paid=False, expires_in=timedelta(days=18))

        self.assertEqual(granting_subscription(self.customer).pk, paid.pk)

    def test_nothing_paid_means_nothing_to_serve(self):
        self._sub(self.long, paid=False)
        self.assertIsNone(granting_subscription(self.customer))

    def test_a_suspended_subscription_is_not_chosen(self):
        self._sub(self.long, status="suspended")
        self.assertIsNone(granting_subscription(self.customer))


class TheGateInFrontOfTheHardware(_Base):
    """
    provision_customer_on_router refuses whatever the caller believed.

    Three call sites have carried the `status="active"` bug, each found months
    apart after the damage. This is the backstop that makes the next one fail
    loudly instead of quietly handing out service.
    """

    def _provision(self, sub):
        with patch("billing.router_service._grant_hotspot") as grant:
            provision_customer_on_router(
                api=object(), router=self.router,
                customer=self.customer, subscription=sub)
        return grant

    def test_a_paid_subscription_provisions(self):
        grant = self._provision(self._sub())
        self.assertTrue(grant.called)

    def test_an_unpaid_subscription_is_refused(self):
        with self.assertRaises(NotEntitled) as caught:
            self._provision(self._sub(paid=False))
        self.assertIn("not paid", str(caught.exception))

    def test_a_suspended_subscription_is_refused(self):
        with self.assertRaises(NotEntitled):
            self._provision(self._sub(status="suspended"))

    def test_the_refusal_names_the_customer_and_the_reason(self):
        """This ends up in a log somebody has to act on."""
        with self.assertRaises(NotEntitled) as caught:
            self._provision(self._sub(paid=False))
        message = str(caught.exception)
        self.assertIn(str(self.customer.pk), message)
        self.assertIn("refusing to provision", message)

    def test_it_raises_rather_than_returning_false(self):
        """
        A refusal that can be ignored is how disable_customer_access came to
        report success while leaving subscribers online. Returning False here
        would provision nothing while the caller told the customer they were on.
        """
        sub = self._sub(paid=False)
        with patch("billing.router_service._grant_hotspot") as grant:
            with self.assertRaises(NotEntitled):
                provision_customer_on_router(
                    api=object(), router=self.router,
                    customer=self.customer, subscription=sub)
        self.assertFalse(grant.called)


class TheVoucherValidator(_Base):
    """
    The gap this scan found. _subscription_is_valid_for_access blocked
    {"revoked", "cancelled"} -- statuses this system has never had -- so the
    deny-list matched nothing and only the expiry date was ever checked.
    """

    def _voucher(self, sub, code="WIFI-TESTAA"):
        with tenant_context(self.tenant):
            return Voucher.objects.create(
                tenant=self.tenant, code=code, subscription=sub,
                expires_at=sub.expiry_date, is_active=True)

    def test_a_voucher_on_a_paid_subscription_validates(self):
        sub = self._sub()
        self._voucher(sub)

        self.assertEqual(
            validate_voucher("WIFI-TESTAA", tenant=self.tenant).pk, sub.pk)

    def test_a_voucher_on_a_capped_subscription_is_refused(self):
        """
        The one that mattered. A customer who spends their 20GB is suspended
        and disconnected; re-entering the same voucher told them "Access
        granted" and bound their device again.
        """
        sub = self._sub(status="suspended")
        self._voucher(sub)

        self.assertIsNone(validate_voucher("WIFI-TESTAA", tenant=self.tenant))

    def test_a_voucher_on_an_unpaid_subscription_is_refused(self):
        """
        Vouchers are minted only by Payment.save, so this should be
        unreachable. Checked anyway: the receipt branch of _resolve_code has
        always checked payment and this branch never did, and "unreachable" is
        not worth relying on when the check is free.
        """
        sub = self._sub(paid=False)
        self._voucher(sub)

        self.assertIsNone(validate_voucher("WIFI-TESTAA", tenant=self.tenant))

    def test_a_voucher_on_an_expired_subscription_is_refused(self):
        sub = self._sub(expires_in=timedelta(hours=-1))
        self._voucher(sub)

        self.assertIsNone(validate_voucher("WIFI-TESTAA", tenant=self.tenant))
