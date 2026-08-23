"""
A code bought on a phone works on the television it was bought for.

The purchase flow redeems the code on whatever device the portal is open on,
the moment M-Pesa confirms — `login.html`, on `status === 'paid'`. Nobody types
anything there. On a one-device package that phone then held the only place,
and every other device the customer owned was refused "in use on another device
right now".

Every gate did what it was written to do. `_voucher_first_used_here` matches the
buyer's own address, and the television is not it; `_evict_idle_device` frees
places nobody is using, and the phone had a live session because we had just
connected it. The device standing between a paying customer and their
television was their own phone, put there by us, seconds earlier.

So a binding now records whether anybody asked for it. One the purchase flow
made yields to a device whose owner typed the code. One somebody typed does
not — a code passed around a room is refused exactly as before, and the total
never exceeds what the package allows.
"""

from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from billing.models import (
    ConnectionAttempt, Customer, CustomerDevice, Package, RouterDevice,
    Subscription, Tenant, Voucher,
)
from billing.tenancy import tenant_context

PHONE = "5C:C5:D4:6B:37:E9"      # the handset that paid
TV = "B4:04:29:4B:4E:34"         # what the code was bought for
LAPTOP = "6C:02:E0:08:B5:23"


class BoughtHereUsedThereTests(TestCase):

    VALIDATE = "/api/hotspot/validate/"

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r", ip_address="10.9.0.2",
                username="a", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="1 device", download_speed=5,
                upload_speed=2, price=Decimal("50.00"), duration_value=1,
                duration_unit="hours", monthly_data_cap_gb=0, is_hotspot=True,
                max_devices=1)

    # ---- helpers ---------------------------------------------------------

    def _customer(self, *, code="WIFIME", max_devices=None):
        with tenant_context(self.tenant):
            package = self.package
            if max_devices is not None:
                package = Package.objects.create(
                    tenant=self.tenant, name=f"{max_devices} devices",
                    download_speed=5, upload_speed=2, price=Decimal("50.00"),
                    duration_value=1, duration_unit="hours",
                    monthly_data_cap_gb=0, is_hotspot=True,
                    max_devices=max_devices)
            customer = Customer.objects.create(
                tenant=self.tenant, full_name="C", phone="254700000401",
                connection_type="hotspot", router=self.router)
            sub = Subscription.objects.create(
                tenant=self.tenant, customer=customer, package=package,
                status="active",
                expiry_date=timezone.now() + timedelta(hours=1))
            Voucher.objects.create(
                tenant=self.tenant, code=code, subscription=sub,
                expires_at=timezone.now() + timedelta(hours=1))
        return customer

    def _redeem(self, code, mac, *, online=frozenset(), auto=False):
        """
        `online` is what the routers report as in use; None means not one of
        them answered. `auto` is the purchase flow presenting the code for a
        device, rather than somebody typing it there.
        """
        body = {"code": code, "mac_address": mac}
        if auto:
            body["auto"] = True
        reported = None if online is None else set(online)
        with patch("billing.views.enable_customer_access"), \
             patch("billing.router_service.active_hotspot_macs",
                   return_value=reported), \
             patch("billing.router_service.safe_connect_router",
                   return_value=None):
            return self.client.post(
                f"{self.VALIDATE}?t={self.tenant.public_token}",
                body, format="json")

    def _macs(self, customer):
        with tenant_context(self.tenant):
            return set(
                CustomerDevice.objects.all_tenants()
                .filter(customer=customer)
                .values_list("mac_address", flat=True))

    def _outcomes(self):
        return list(
            ConnectionAttempt.objects.all_tenants()
            .values_list("outcome", flat=True))

    # ---- the complaint ---------------------------------------------------

    def test_a_television_can_use_a_code_bought_on_a_phone(self):
        """
        The phone paid and was connected automatically. The customer walks to
        the television and types the code. The phone is still online — it has
        been for all of thirty seconds — and used to make this a 409.
        """
        customer = self._customer()
        self.assertEqual(
            self._redeem("WIFIME", PHONE, auto=True).status_code, 200)

        response = self._redeem("WIFIME", TV, online={PHONE})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self._macs(customer), {TV})
        self.assertEqual(self._outcomes(), [], "nothing should have been refused")

    def test_the_place_is_taken_back_even_when_no_router_answers(self):
        """
        How the binding was made is our own record, not the router's, so an
        unreachable router does not prevent giving back a place we took. This
        is the fiber1 case: the router has been unreachable for days.
        """
        customer = self._customer()
        self.assertEqual(
            self._redeem("WIFIME", PHONE, auto=True).status_code, 200)

        response = self._redeem("WIFIME", TV, online=None)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self._macs(customer), {TV})

    def test_a_binding_that_belongs_to_no_package_is_moved_not_duplicated(self):
        """
        269 of the 316 bindings in production carry no subscription — devices
        whose package has since expired, and which the migration leaves alone
        because a device on no live package counts against no allowance.

        When such a phone buys again and presents the new code, its existing
        row has to be re-pointed at the new subscription. Writing a second row
        instead collides with the unique (tenant, mac_address), and the one
        device guaranteed to hit it is the customer's own.
        """
        customer = self._customer()
        with tenant_context(self.tenant):
            CustomerDevice.objects.create(
                tenant=self.tenant, customer=customer, subscription=None,
                mac_address=PHONE)

        response = self._redeem("WIFIME", PHONE)

        self.assertEqual(response.status_code, 200, response.data)
        with tenant_context(self.tenant):
            rows = CustomerDevice.objects.all_tenants().filter(
                customer=customer, mac_address=PHONE)
            self.assertEqual(rows.count(), 1, "a second row was written")
            self.assertIsNotNone(
                rows.first().subscription_id,
                "the binding was left pointing at no package")

    # ---- and what must still be refused ----------------------------------

    def test_a_phone_somebody_is_using_keeps_its_place(self):
        """
        Typed on the phone, so the phone is there on purpose. A second device
        does not get to take it — this is the room sharing one code.
        """
        customer = self._customer()
        self.assertEqual(self._redeem("WIFIME", PHONE).status_code, 200)

        response = self._redeem("WIFIME", TV, online={PHONE})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self._macs(customer), {PHONE})
        self.assertEqual(self._outcomes(), ["device_limit"])

    def test_entering_the_code_on_the_phone_protects_it(self):
        """
        The phone was connected automatically, but its owner has since typed
        the code on it themselves. It is now there on purpose and stops being
        displaceable, so a code entered elsewhere cannot cut them off.
        """
        customer = self._customer()
        self.assertEqual(
            self._redeem("WIFIME", PHONE, auto=True).status_code, 200)
        self.assertEqual(
            self._redeem("WIFIME", PHONE, online={PHONE}).status_code, 200)

        response = self._redeem("WIFIME", TV, online={PHONE})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self._macs(customer), {PHONE})

    def test_the_purchase_flow_does_not_displace_anybody(self):
        """
        Only a device somebody typed the code on may take a place back. A
        second automatic connection is not that, or one purchase would knock
        another device off.
        """
        customer = self._customer()
        self.assertEqual(
            self._redeem("WIFIME", PHONE, auto=True).status_code, 200)

        response = self._redeem("WIFIME", TV, online={PHONE}, auto=True)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self._macs(customer), {PHONE})

    def test_the_device_limit_still_holds(self):
        """
        Two deliberate devices on a two-device package, both online. The third
        is refused: displacement never lets the total exceed the allowance.
        """
        customer = self._customer(code="TWODEV", max_devices=2)
        self.assertEqual(self._redeem("TWODEV", PHONE).status_code, 200)
        self.assertEqual(
            self._redeem("TWODEV", TV, online={PHONE}).status_code, 200)

        response = self._redeem("TWODEV", LAPTOP, online={PHONE, TV})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self._macs(customer), {PHONE, TV})

    def test_a_second_device_fits_under_a_two_device_package(self):
        """
        The allowance is counted, not assumed to be one. A television joining
        a phone on a two-device package is simply within what was paid for,
        and needs no displacement at all.
        """
        customer = self._customer(code="TWODEV", max_devices=2)
        self.assertEqual(
            self._redeem("TWODEV", PHONE, auto=True).status_code, 200)

        response = self._redeem("TWODEV", TV, online={PHONE})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self._macs(customer), {PHONE, TV})
