"""
The code the connected page hands over has to be this phone's code.

One number can hold several packages at once — the model says so, and
production shows it: a test account holding a two-week subscription comped
on 22 August, with four separate three-hour purchases stacked on top of it.

The page reports the customer's longest-running access, and that is right:
enable_customer_access grants on exactly that subscription, so the time left
on screen is the time the router will actually give. The voucher was being
chosen the same way, and that is not right. It is labelled "write this down,
it reconnects this phone", and it was showing a code bought days earlier for
a different package.

Observed 2026-08-25: 10/= paid for three hours produced voucher C38HOH, and
the screen showed Q67AD4 — a two-week code from a comp.
"""

from decimal import Decimal
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from billing.security import device_token_for
from billing.models import (
    Customer, CustomerDevice, Package, RouterDevice, Subscription, Tenant,
    Voucher,
)
from billing.tenancy import tenant_context

PHONE = "8C:8D:28:01:F0:77"


class ConnectedPageVoucherTests(TestCase):
    """One subscriber, two live subscriptions, one phone."""

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        now = timezone.now()
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r", ip_address="10.0.0.1",
                username="u", password="p")
            long_pkg = Package.objects.create(
                tenant=self.tenant, name="2 weeks unlimited", download_speed=5,
                upload_speed=2, price=Decimal("200.00"), duration_value=2,
                duration_unit="weeks", data_cap_mb=0, is_hotspot=True,
                max_devices=1)
            short_pkg = Package.objects.create(
                tenant=self.tenant, name="3hrs unlimited", download_speed=5,
                upload_speed=2, price=Decimal("10.00"), duration_value=3,
                duration_unit="hours", data_cap_mb=0, is_hotspot=True,
                max_devices=1)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Mark", phone="254701071435",
                connection_type="hotspot", router=self.router,
                hotspot_username=PHONE)

            # Comped days ago, still running. This is the one the router
            # grants on, so the page is right to report its time.
            self.long_sub = Subscription.objects.create(
                tenant=self.tenant, customer=self.customer, package=long_pkg,
                status="active", expiry_date=now + timedelta(days=10))
            self._pay(self.long_sub)
            self.long_voucher = Voucher.objects.create(
                tenant=self.tenant, code="Q67AD4", subscription=self.long_sub,
                expires_at=self.long_sub.expiry_date)

            # Bought minutes ago, on this phone, for 10 bob.
            self.short_sub = Subscription.objects.create(
                tenant=self.tenant, customer=self.customer, package=short_pkg,
                status="active", expiry_date=now + timedelta(hours=3))
            self._pay(self.short_sub)
            self.short_voucher = Voucher.objects.create(
                tenant=self.tenant, code="C38HOH", subscription=self.short_sub,
                expires_at=self.short_sub.expiry_date, bound_mac=PHONE,
                first_used_at=now)
            CustomerDevice.objects.create(
                tenant=self.tenant, customer=self.customer,
                subscription=self.short_sub, mac_address=PHONE)

    def _pay(self, sub):
        inv = sub.invoice
        inv.payment_status = "paid"
        inv.save(update_fields=["payment_status"])

    def status(self, mac=PHONE, with_token=True):
        params = {"t": self.tenant.public_token, "mac": mac}
        if with_token:
            params["dt"] = device_token_for(mac)
        return APIClient().get("/api/hotspot/status/", params)

    def test_it_shows_the_code_this_phone_redeemed(self):
        resp = self.status()
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(
            resp.data.get("voucher_code"), "C38HOH",
            "the page handed over a code bought for a different package")

    def test_the_time_left_still_reports_what_the_router_grants(self):
        """
        Not changed, deliberately. enable_customer_access provisions on the
        customer's longest active subscription, so reporting the shorter one
        would tell a subscriber they are about to go offline when the router
        will keep them on for another ten days.
        """
        resp = self.status()
        self.assertEqual(resp.data["package"], "2 weeks unlimited")
        self.assertEqual(resp.data["expires_at"], self.long_sub.expiry_date)

    def test_a_device_that_redeemed_nothing_still_gets_a_code(self):
        """
        A phone let back on without redeeming, or bound before bound_mac was
        written, has no code of its own. The old answer is the best one left,
        and it must not become no answer at all.
        """
        with tenant_context(self.tenant):
            Voucher.objects.filter(pk=self.short_voucher.pk).update(
                bound_mac="")

        resp = self.status()
        self.assertEqual(resp.data.get("voucher_code"), "Q67AD4")

    def test_a_retired_code_is_not_offered(self):
        """Handing over a code that will be refused is worse than the wrong one."""
        with tenant_context(self.tenant):
            Voucher.objects.filter(pk=self.short_voucher.pk).update(
                is_active=False)

        resp = self.status()
        self.assertEqual(resp.data.get("voucher_code"), "Q67AD4")

    def test_an_expired_subscriptions_code_is_not_offered(self):
        with tenant_context(self.tenant):
            Subscription.objects.filter(pk=self.short_sub.pk).update(
                expiry_date=timezone.now() - timedelta(minutes=1))

        resp = self.status()
        self.assertEqual(resp.data.get("voucher_code"), "Q67AD4")

    def test_no_device_token_still_means_no_code(self):
        """
        The gate that decides whether a code is released at all is unchanged:
        this endpoint takes the MAC from the query string and cannot check it.
        """
        resp = self.status(with_token=False)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertNotIn("voucher_code", resp.data)
