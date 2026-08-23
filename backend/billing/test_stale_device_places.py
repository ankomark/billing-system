"""
A customer who disconnected is told to disconnect.

The complaint, in the operator's words: a client gets disconnected, types the
same voucher again, and is told the code is in use with another device — when
he is the one who bought it. Three things in the redemption path produced that
answer, and none of them is about a code being shared.

* A hotspot session is not ended by the phone leaving. RouterOS keeps it until
  its own idle-timeout fires, and operators routinely turn that off. Every
  session counted as "in use", so a device that walked out hours ago went on
  holding the only place a one-device package allows — and held it until the
  package expired, because nothing but a redemption ever cleared it.

* Only `customer.router` was ever asked. It is nullable, and a tenth of the
  hotspot subscribers on the live system have it null. For them the answer was
  always "I could not find out", which is the answer that makes a place
  unfreeable, so their first address change locked them out for the rest of
  their package.

* The phone the code was bought on had no standing. Once its binding went —
  evicted while it was off, or rotated by Android's per-network address — it
  queued for a place behind whatever else the account had since used, and lost.

Separately: every refusal that was not about a device said "Invalid or expired
voucher", so a customer whose hour had simply run out was told their code was
invalid and retyped it until they gave up. Expiry is now its own answer.
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
from billing.router_service import ros_duration_seconds
from billing.tenancy import tenant_context

PHONE = "3E:5E:04:A6:ED:BD"
LAPTOP = "6C:02:E0:08:B5:23"


class RouterDurationTests(TestCase):
    """
    The idle time comes off the router as a word, and the whole of the fix
    below rests on reading it correctly. A wrong parse is silent: too small
    frees a place somebody is using, too large keeps refusing its owner.
    """

    def test_the_shapes_routeros_actually_emits(self):
        self.assertEqual(ros_duration_seconds("45s"), 45)
        self.assertEqual(ros_duration_seconds("5m30s"), 330)
        self.assertEqual(ros_duration_seconds("1h2m3s"), 3723)
        self.assertEqual(ros_duration_seconds("2d1h"), 176400)
        self.assertEqual(ros_duration_seconds("1w"), 604800)
        self.assertEqual(ros_duration_seconds("00:05:30"), 330)
        self.assertEqual(ros_duration_seconds("90"), 90)

    def test_anything_unreadable_is_no_answer_rather_than_zero(self):
        """
        Zero would read as "idle for no time at all", which is the safe answer
        inverted — an unparseable field would start freeing places.
        """
        for value in (None, "", "  ", "later", "1x2y"):
            self.assertIsNone(ros_duration_seconds(value), value)


class ActiveSessionIdleTests(TestCase):
    """`active_hotspot_macs` decides what counts as a device in use."""

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r", ip_address="10.9.0.7",
                username="a", password="p")

    def _api(self, sessions):
        class _Api:
            def path(self, *_):
                return list(sessions)
        return _Api()

    def _macs(self, sessions, **kwargs):
        from billing import router_service

        with patch.object(router_service, "safe_connect_router",
                          return_value=self._api(sessions)):
            return router_service.active_hotspot_macs(self.router, **kwargs)

    def test_a_session_left_behind_stops_holding_a_place(self):
        macs = self._macs(
            [{"mac-address": PHONE, "idle-time": "3h20m"}],
            max_idle_seconds=600,
        )
        self.assertEqual(macs, set())

    def test_a_session_in_use_still_holds_its_place(self):
        macs = self._macs(
            [{"mac-address": PHONE, "idle-time": "12s"}],
            max_idle_seconds=600,
        )
        self.assertEqual(macs, {PHONE})

    def test_an_unreadable_idle_time_keeps_the_session(self):
        """Silence must not free a place — that is the guarantee inverted."""
        macs = self._macs(
            [{"mac-address": PHONE}], max_idle_seconds=600)
        self.assertEqual(macs, {PHONE})

    def test_without_a_threshold_nothing_is_filtered(self):
        macs = self._macs([{"mac-address": PHONE, "idle-time": "9h"}])
        self.assertEqual(macs, {PHONE})

    def test_an_unreachable_router_is_still_not_an_empty_answer(self):
        from billing import router_service

        with patch.object(router_service, "safe_connect_router",
                          return_value=None):
            self.assertIsNone(
                router_service.active_hotspot_macs(self.router))


class StaleDevicePlaceTests(TestCase):

    VALIDATE = "/api/hotspot/validate/"

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r", ip_address="10.9.0.1",
                username="a", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="1 device", download_speed=5,
                upload_speed=2, price=Decimal("50.00"), duration_value=1,
                duration_unit="hours", monthly_data_cap_gb=0, is_hotspot=True,
                max_devices=1)

    # ---- helpers ---------------------------------------------------------

    def _customer(self, *, code="WIFIME", hours=1, router=True, bound_mac=""):
        with tenant_context(self.tenant):
            customer = Customer.objects.create(
                tenant=self.tenant, full_name="C", phone="254700000301",
                connection_type="hotspot",
                router=self.router if router else None)
            sub = Subscription.objects.create(
                tenant=self.tenant, customer=customer, package=self.package,
                status="active",
                expiry_date=timezone.now() + timedelta(hours=hours))
            voucher = Voucher.objects.create(
                tenant=self.tenant, code=code, subscription=sub,
                bound_mac=bound_mac,
                expires_at=timezone.now() + timedelta(hours=max(hours, 1)))
        return customer, sub, voucher

    def _bind(self, customer, mac, subscription=None):
        """
        A device place, as the redemption endpoint makes one — which is to say
        against the subscription that paid for it. A binding belonging to no
        package counts against no allowance, so leaving it off here would test
        a row shape production no longer creates.
        """
        with tenant_context(self.tenant):
            if subscription is None:
                subscription = (
                    Subscription.objects.filter(customer=customer)
                    .order_by("-id").first())
            CustomerDevice.objects.create(
                tenant=self.tenant, customer=customer,
                subscription=subscription, mac_address=mac)
            customer.hotspot_username = mac
            customer.save(update_fields=["hotspot_username"])

    def _redeem(self, code, mac, *, online=frozenset()):
        """`online` is what the routers report as in use right now."""
        with patch("billing.views.enable_customer_access"), \
             patch("billing.router_service.active_hotspot_macs",
                   return_value=set(online)), \
             patch("billing.router_service.safe_connect_router",
                   return_value=None):
            return self.client.post(
                f"{self.VALIDATE}?t={self.tenant.public_token}",
                {"code": code, "mac_address": mac}, format="json")

    def _devices(self, customer):
        return CustomerDevice.objects.all_tenants().filter(customer=customer)

    def _outcomes(self):
        return list(
            ConnectionAttempt.objects.all_tenants()
            .values_list("outcome", flat=True))

    # ---- the subscriber nobody could ask a router about -------------------

    def test_a_customer_with_no_router_can_still_free_a_stale_binding(self):
        """
        `customer.router` is nullable and often null. Reading it alone meant
        no router could be asked, no place could be freed, and the owner of the
        code was refused until it expired.
        """
        customer, _, _ = self._customer(router=False)
        self._bind(customer, LAPTOP)

        response = self._redeem("WIFIME", PHONE, online=set())

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            {d.mac_address for d in self._devices(customer)}, {PHONE})

    def test_no_router_anywhere_still_leaves_the_limit_standing(self):
        """
        The fallback widens who is asked. It must not turn "I could not find
        out" into "nobody is online", or an operator with an unreachable estate
        would be handing out unlimited devices.
        """
        customer, _, _ = self._customer(router=False)
        self._bind(customer, LAPTOP)

        with patch("billing.views.enable_customer_access"), \
             patch("billing.router_service.active_hotspot_macs",
                   return_value=None), \
             patch("billing.router_service.safe_connect_router",
                   return_value=None):
            response = self.client.post(
                f"{self.VALIDATE}?t={self.tenant.public_token}",
                {"code": "WIFIME", "mac_address": PHONE}, format="json")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self._outcomes(), ["device_limit"])

    def test_one_router_answering_is_an_answer(self):
        """
        A second router that cannot be reached must not hold a customer's place
        on hardware they were never on.
        """
        with tenant_context(self.tenant):
            RouterDevice.objects.create(
                tenant=self.tenant, name="r2", ip_address="10.9.0.2",
                username="a", password="p")
        customer, _, _ = self._customer(router=False)
        self._bind(customer, LAPTOP)

        answers = iter([None, set()])
        with patch("billing.views.enable_customer_access"), \
             patch("billing.router_service.active_hotspot_macs",
                   side_effect=lambda *a, **k: next(answers)), \
             patch("billing.router_service.safe_connect_router",
                   return_value=None):
            response = self.client.post(
                f"{self.VALIDATE}?t={self.tenant.public_token}",
                {"code": "WIFIME", "mac_address": PHONE}, format="json")

        self.assertEqual(response.status_code, 200, response.data)

    # ---- the phone that bought the code -----------------------------------

    def test_the_phone_the_code_was_bought_on_gets_its_place_back(self):
        """
        The complaint itself. The buyer's handset comes back to a place taken
        by the account's other device, and is told the code it paid for is in
        use elsewhere — with that other device online, so nothing is evictable.
        """
        customer, _, _ = self._customer(bound_mac=PHONE)
        self._bind(customer, LAPTOP)

        response = self._redeem("WIFIME", PHONE, online={LAPTOP})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            {d.mac_address for d in self._devices(customer)}, {PHONE})

    def test_reclaiming_never_takes_more_than_the_package_allows(self):
        """
        Reclaiming replaces a place, it does not add one. A code good for one
        device stays good for one device.
        """
        customer, _, _ = self._customer(bound_mac=PHONE)
        self._bind(customer, LAPTOP)

        self._redeem("WIFIME", PHONE, online={LAPTOP})

        self.assertEqual(self._devices(customer).count(), 1)

    def test_a_device_that_never_bought_the_code_still_waits_its_turn(self):
        """
        The guard on the rule above. Without it the device limit is gone: any
        phone could take a place from a device that is using the connection.
        """
        customer, _, _ = self._customer(bound_mac=PHONE)
        self._bind(customer, LAPTOP)

        other = "AA:BB:CC:DD:EE:FF"
        response = self._redeem("WIFIME", other, online={LAPTOP})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            {d.mac_address for d in self._devices(customer)}, {LAPTOP})

    def test_an_unbound_voucher_grants_nothing_extra(self):
        """
        `bound_mac` is empty until a code is first redeemed. An empty column
        must not match an empty address, or every first redemption would be a
        reclaim.
        """
        customer, _, _ = self._customer(bound_mac="")
        self._bind(customer, LAPTOP)

        response = self._redeem("WIFIME", PHONE, online={LAPTOP})

        self.assertEqual(response.status_code, 409)

    # ---- expiry is not invalidity ------------------------------------------

    def test_a_code_that_ran_out_is_not_called_invalid(self):
        """
        Told "invalid", a customer whose hour ended retypes the code. Told it
        ran out, they buy another package — which is the thing that works.
        """
        customer, sub, voucher = self._customer()
        with tenant_context(self.tenant):
            Voucher.objects.all_tenants().filter(pk=voucher.pk).update(
                expires_at=timezone.now() - timedelta(minutes=5))
            Subscription.objects.all_tenants().filter(pk=sub.pk).update(
                expiry_date=timezone.now() - timedelta(minutes=5))

        response = self._redeem("WIFIME", PHONE)

        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.data.get("expired"))
        self.assertIn("run out of time", response.data["detail"])
        self.assertEqual(self._outcomes(), ["expired"])

    def test_a_code_that_never_existed_is_still_invalid(self):
        self._customer()

        response = self._redeem("NOSUCH", PHONE)

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("expired", response.data)
        self.assertEqual(self._outcomes(), ["invalid"])

    def test_a_spent_receipt_pasted_from_the_sms_reads_as_expired(self):
        """
        The live case: a ten-shilling package bought at 12:48 and pasted back
        at 15:48, three hours later. The receipt is real, the subscription is
        over, and the customer was told the code was invalid.
        """
        from billing.models import Payment

        customer, sub, _ = self._customer()
        with tenant_context(self.tenant):
            Payment.objects.create(
                tenant=self.tenant, customer=customer, subscription=sub,
                amount=Decimal("10.00"), method="mpesa",
                reference="UHEFV3876T")
            Subscription.objects.all_tenants().filter(pk=sub.pk).update(
                expiry_date=timezone.now() - timedelta(minutes=1))

        response = self._redeem("UHEFV3876T Confirmed", PHONE)

        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.data.get("expired"))
        self.assertEqual(self._outcomes(), ["expired"])
