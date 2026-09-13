"""
The allowance a customer gets is the one they were sold.

The cap was read off the package every time it was needed, so editing a package
rewrote history. Raising 24hrs from 5GB to 10GB on 2026-09-13 handed the extra
5GB to all 27 people already on it, and lowering one would have cut off
somebody mid-package who had paid for the larger bundle.

Customer 1813 is what that looks like from the outside: 5.22GB spent against a
5GB allowance, still online, because by the time anything asked, the package
said 10GB and they were under it.

Rows written before the field existed carry null and still follow the package,
which is exactly what they did before -- the change is inert for them until an
operator decides otherwise.
"""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from billing.models import (
    Customer, Invoice, Package, RouterDevice, Subscription, Tenant,
)
from billing.services.usage import MB, cap_bytes_for
from billing.services.voucher_service import (
    REFUSED_CAPPED, REFUSED_EXPIRED, describe_refusal,
)
from billing.tenancy import tenant_context


class TheCapIsFixedAtPurchase(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="cap-r", ip_address="10.9.0.60",
                username="u", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="24hrs - 5GB", download_speed=5,
                upload_speed=2, price=Decimal("40.00"), duration_value=24,
                duration_unit="hours", data_cap_mb=5000, is_hotspot=True)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Capped", phone="254700150001",
                connection_type="hotspot", router=self.router, status="active")

    def _sub(self):
        with tenant_context(self.tenant):
            sub = Subscription.objects.create(
                customer=self.customer, package=self.package, status="active")
            Invoice.objects.filter(subscription=sub).update(
                payment_status="paid")
            sub.refresh_from_db()
        return sub

    def _raise_package_to(self, mb):
        with tenant_context(self.tenant):
            Package.objects.filter(pk=self.package.pk).update(data_cap_mb=mb)
            self.package.refresh_from_db()

    # --------------------------------------------------------- at purchase

    def test_the_cap_is_copied_when_the_subscription_is_made(self):
        sub = self._sub()

        self.assertEqual(sub.data_cap_mb, 5000)

    def test_raising_the_package_does_not_raise_what_was_sold(self):
        """
        The production case. 27 live subscribers had their allowance doubled by
        an edit to a price list.
        """
        sub = self._sub()
        self._raise_package_to(10000)
        sub.refresh_from_db()

        self.assertEqual(cap_bytes_for(self.customer, sub), 5000 * MB)

    def test_lowering_the_package_does_not_cut_anybody_short(self):
        """
        The same fault in the direction that takes something away: somebody who
        paid for the larger bundle keeps it.
        """
        sub = self._sub()
        self._raise_package_to(1000)
        sub.refresh_from_db()

        self.assertEqual(cap_bytes_for(self.customer, sub), 5000 * MB)

    def test_a_purchase_after_the_change_gets_the_new_cap(self):
        """The other half of what was asked: new customers buy the new bundle."""
        self._raise_package_to(10000)
        sub = self._sub()

        self.assertEqual(sub.data_cap_mb, 10000)
        self.assertEqual(cap_bytes_for(self.customer, sub), 10000 * MB)

    def test_a_later_save_does_not_re_read_the_package(self):
        """
        Taken once, at creation. Re-reading on every save would put the package
        back in charge the moment anything else touched the row.
        """
        sub = self._sub()
        self._raise_package_to(10000)

        with tenant_context(self.tenant):
            sub.status = "suspended"
            sub.save(update_fields=["status"])
        sub.refresh_from_db()

        self.assertEqual(sub.data_cap_mb, 5000)

    # ------------------------------------------------------ the old rows

    def test_a_row_written_before_the_field_still_follows_the_package(self):
        """
        Null means inherit. Every subscription that existed before this behaves
        exactly as it did yesterday, so the deploy changes nothing by itself.
        """
        sub = self._sub()
        with tenant_context(self.tenant):
            Subscription.objects.filter(pk=sub.pk).update(data_cap_mb=None)
            sub.refresh_from_db()

        self._raise_package_to(10000)
        sub.refresh_from_db()

        self.assertEqual(cap_bytes_for(self.customer, sub), 10000 * MB)

    def test_an_operator_override_still_wins(self):
        """
        custom_data_cap_mb on the customer is a deliberate decision about one
        subscriber and outranks both.
        """
        sub = self._sub()
        with tenant_context(self.tenant):
            Customer.objects.filter(pk=self.customer.pk).update(
                custom_data_cap_mb=200)
            self.customer.refresh_from_db()

        self.assertEqual(cap_bytes_for(self.customer, sub), 200 * MB)

    def test_an_unlimited_override_is_honoured(self):
        """Zero means unlimited, not "no override" -- see cap_bytes_for."""
        sub = self._sub()
        with tenant_context(self.tenant):
            Customer.objects.filter(pk=self.customer.pk).update(
                custom_data_cap_mb=0)
            self.customer.refresh_from_db()

        self.assertEqual(cap_bytes_for(self.customer, sub), 0)


class WhatTheCustomerIsTold(TestCase):
    """
    Somebody who has spent their data gets a different sentence from somebody
    whose time ran out, and both differ from a code that never existed.

    "Invalid" sends a customer whose code is perfectly good back to retype it.
    "Expired" sends them to argue that they still have hours left, which they
    do. Neither says the one thing that would get them back online.
    """

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="msg-r", ip_address="10.9.0.61",
                username="u", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="24hrs - 5GB", download_speed=5,
                upload_speed=2, price=Decimal("40.00"), duration_value=24,
                duration_unit="hours", data_cap_mb=5000, is_hotspot=True)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Spent It",
                phone="254700150002", connection_type="hotspot",
                router=self.router, status="active")
            self.sub = Subscription.objects.create(
                customer=self.customer, package=self.package, status="active")
            Invoice.objects.filter(subscription=self.sub).update(
                payment_status="paid")
            from billing.models import Voucher
            self.voucher = Voucher.objects.create(
                tenant=self.tenant, code="WIFI-CAPPED1",
                subscription=self.sub, expires_at=self.sub.expiry_date,
                is_active=True)

    def _suspend(self):
        with tenant_context(self.tenant):
            Subscription.objects.filter(pk=self.sub.pk).update(
                status="suspended", capped_at=timezone.now())

    def test_a_spent_allowance_says_so(self):
        self._suspend()

        self.assertEqual(
            describe_refusal("WIFI-CAPPED1", tenant=self.tenant),
            REFUSED_CAPPED)

    def test_time_running_out_still_says_that_instead(self):
        """The two must not collapse into one message."""
        with tenant_context(self.tenant):
            Subscription.objects.filter(pk=self.sub.pk).update(
                expiry_date=timezone.now() - timezone.timedelta(hours=1))

        self.assertEqual(
            describe_refusal("WIFI-CAPPED1", tenant=self.tenant),
            REFUSED_EXPIRED)

    def test_a_capped_subscription_that_has_also_expired_reads_as_capped(self):
        """
        The allowance went first. Telling somebody their time ran out when it
        was the data invites the wrong argument at the counter.
        """
        self._suspend()
        with tenant_context(self.tenant):
            Subscription.objects.filter(pk=self.sub.pk).update(
                expiry_date=timezone.now() - timezone.timedelta(hours=1))

        self.assertEqual(
            describe_refusal("WIFI-CAPPED1", tenant=self.tenant),
            REFUSED_CAPPED)

    def test_an_mpesa_receipt_gets_the_same_answer(self):
        """
        People paste the M-Pesa message as often as they type the code, and the
        two paths must not disagree about why somebody is refused.
        """
        from billing.models import Payment
        with tenant_context(self.tenant):
            with patch("billing.models.notify_customer"), \
                 patch("billing.router_service.enable_customer_access"), \
                 patch("billing.router_service.safe_connect_router",
                       return_value=object()):
                Payment.objects.create(
                    tenant=self.tenant, customer=self.customer,
                    subscription=self.sub, amount=Decimal("40.00"),
                    method="mpesa", reference="TESTCAP001")
        self._suspend()

        self.assertEqual(
            describe_refusal("TESTCAP001", tenant=self.tenant),
            REFUSED_CAPPED)
