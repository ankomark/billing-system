"""
The phone a bundle was bought on keeps it while it is being used.

Voucher R0Q8ZZ (customer 2326, 2026-11-03 to 11-07): five other people's
handsets redeemed the buyer's M-Pesa confirmation SMS and, because a place
was free whenever its device had been quiet for ten minutes, pushed the
buyer's phone off 24 times in four days. These hold the fix: strangers cannot
displace the buyer's phone while it is in use, the buyer can still set up the
device they bought for and recover from a lost phone, and they are told when
somebody else gets on.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from billing.models import (
    Customer, CustomerDevice, HotspotUsageState, Package, RouterDevice,
    Subscription, Tenant, Voucher,
)
from billing.tenancy import tenant_context

BUYER = "2E:EF:2C:81:BE:C5"
STRANGER = "0A:18:83:6D:EA:2B"


class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.get(slug="skylink")
        now = timezone.now()
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r", ip_address="10.0.0.1",
                username="a", password="p")
            package = Package.objects.create(
                tenant=self.tenant, name="3 weeks - 80GB", download_speed=5,
                upload_speed=2, price=Decimal("300.00"), duration_value=3,
                duration_unit="weeks", data_cap_mb=80000, is_hotspot=True,
                max_devices=1)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Buyer", phone="254713335781",
                connection_type="hotspot", router=self.router,
                hotspot_username=BUYER)
            self.subscription = Subscription.objects.create(
                tenant=self.tenant, customer=self.customer, package=package,
                status="active", start_date=now - timedelta(days=9),
                expiry_date=now + timedelta(days=12))
            Voucher.objects.create(
                tenant=self.tenant, code="R0Q8ZZ",
                subscription=self.subscription, bound_mac=BUYER,
                first_used_at=self.subscription.start_date,
                expires_at=self.subscription.expiry_date)
            self.buyer_device = CustomerDevice.objects.create(
                tenant=self.tenant, customer=self.customer,
                subscription=self.subscription, mac_address=BUYER)
            self.state = HotspotUsageState.objects.create(
                tenant=self.tenant, customer=self.customer, router=self.router,
                mac_address=BUYER, last_seen_at=now - timedelta(minutes=40))

    def evict(self, *, reclaiming=False, deliberate=True):
        """The buyer's phone has no live session -- it is in a pocket."""
        from billing.views import _evict_idle_device
        with patch("billing.router_service.active_hotspot_macs",
                   return_value=set()), \
             patch("billing.router_service.safe_connect_router",
                   return_value=None), \
             patch("billing.views._kick_device"), \
             tenant_context(self.tenant):
            devices = list(CustomerDevice.objects.filter(customer=self.customer))
            return _evict_idle_device(
                self.customer, devices, reclaiming=reclaiming,
                deliberate=deliberate, subscription=self.subscription)

class BuyerDeviceProtectedTests(_Base):

    def test_a_stranger_cannot_push_the_buyer_off(self):
        """The R0Q8ZZ case: quiet for 40 minutes is not gone."""
        self.assertIsNone(self.evict())
        with tenant_context(self.tenant):
            self.assertTrue(CustomerDevice.objects.filter(
                pk=self.buyer_device.pk).exists())

    def test_not_even_when_the_buyers_phone_was_bound_automatically(self):
        with tenant_context(self.tenant):
            CustomerDevice.objects.filter(pk=self.buyer_device.pk).update(
                auto_bound=True)
        self.assertIsNone(self.evict())

    def test_the_device_it_was_bought_for_can_take_over_at_first(self):
        """Bought on the phone, for the television in the next room."""
        with tenant_context(self.tenant):
            Subscription.objects.filter(pk=self.subscription.pk).update(
                start_date=timezone.now() - timedelta(minutes=20))
            self.subscription.refresh_from_db()
        self.assertIsNotNone(self.evict())

    def test_a_lost_phone_does_not_strand_the_bundle(self):
        with tenant_context(self.tenant):
            HotspotUsageState.objects.filter(pk=self.state.pk).update(
                last_seen_at=timezone.now() - timedelta(days=2))
        self.assertIsNotNone(self.evict())

    def test_a_phone_never_seen_online_is_not_protected(self):
        with tenant_context(self.tenant):
            HotspotUsageState.objects.filter(pk=self.state.pk).delete()
        self.assertIsNotNone(self.evict())

    def test_other_devices_are_still_released_as_before(self):
        """Protection is for the buyer's phone only; a stranger holding the
        second place goes when idle, exactly as it did."""
        with tenant_context(self.tenant):
            other = CustomerDevice.objects.create(
                tenant=self.tenant, customer=self.customer,
                subscription=self.subscription, mac_address=STRANGER)
        evicted = self.evict()
        # Deleted, so Django has cleared its pk; the address says which it was.
        self.assertEqual(evicted.mac_address, STRANGER)
        with tenant_context(self.tenant):
            self.assertFalse(CustomerDevice.objects.filter(pk=other.pk).exists())
            self.assertTrue(CustomerDevice.objects.filter(
                pk=self.buyer_device.pk).exists())


class NewDeviceTextTests(_Base):

    def tell(self, mac):
        from billing.views import _tell_buyer_new_device
        with patch("billing.tasks.notification_tasks.notify_customer_task.delay"
                   ) as sent, tenant_context(self.tenant),                 self.captureOnCommitCallbacks(execute=True):
            _tell_buyer_new_device(self.customer, self.subscription, mac)
        return sent

    def test_the_buyer_is_told_when_another_device_joins(self):
        sent = self.tell(STRANGER)
        sent.assert_called_once()
        self.assertEqual(sent.call_args[0][0], "254713335781")
        self.assertIn("M-Pesa message", sent.call_args[0][1])

    def test_not_for_their_own_phone(self):
        self.tell(BUYER).assert_not_called()

    def test_not_while_they_are_setting_up(self):
        with tenant_context(self.tenant):
            Subscription.objects.filter(pk=self.subscription.pk).update(
                start_date=timezone.now() - timedelta(minutes=20))
            self.subscription.refresh_from_db()
        self.tell(STRANGER).assert_not_called()

    def test_once_in_six_hours(self):
        self.tell(STRANGER).assert_called_once()
        self.tell("5E:6E:EE:E2:C0:CD").assert_not_called()



class RedeemThroughThePortal(_Base):
    """
    The R0Q8ZZ case end to end, through the endpoint the captive portal calls:
    a stranger's phone presents the buyer's M-Pesa confirmation while the
    buyer's phone is in a pocket.
    """

    VALIDATE = "/api/hotspot/validate/"
    RECEIPT = "UJP3M0L1Z1 Confirmed. Ksh300.00 paid to SKYLINK"

    def setUp(self):
        super().setUp()
        from billing.models import Invoice, Payment
        from rest_framework.test import APIClient

        self.client = APIClient()
        with tenant_context(self.tenant):
            Invoice.objects.filter(subscription=self.subscription).update(
                payment_status="paid")
            # The receipt redeems through the payment it settled, which is
            # what made the confirmation SMS work as a code at all.
            Payment.objects.create(
                tenant=self.tenant, customer=self.customer,
                subscription=self.subscription, amount=Decimal("300.00"),
                method="mpesa", reference="UJP3M0L1Z1")
            # Saving a payment activates its subscription from now. Put the
            # dates back: the case is a bundle nine days into its three weeks,
            # well past the window in which a new device may take over.
            # (It renews the very instance passed to it, so the values are
            # spelled out rather than read back from self.subscription.)
            now = timezone.now()
            Subscription.objects.filter(pk=self.subscription.pk).update(
                status="active", start_date=now - timedelta(days=9),
                expiry_date=now + timedelta(days=12))
            self.subscription.refresh_from_db()

    def redeem(self, code, mac):
        with patch("billing.views.enable_customer_access"),              patch("billing.views._kick_device"),              patch("billing.router_service.active_hotspot_macs",
                   return_value=set()),              patch("billing.router_service.safe_connect_router",
                   return_value=None),              patch("billing.tasks.notification_tasks.notify_customer_task.delay"
                   ) as sent,              self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"{self.VALIDATE}?t={self.tenant.public_token}",
                {"code": code, "mac_address": mac}, format="json")
        self.sent = sent
        return response

    def bound(self):
        with tenant_context(self.tenant):
            return set(CustomerDevice.objects.filter(customer=self.customer)
                       .values_list("mac_address", flat=True))

    def test_the_receipt_on_a_strangers_phone_is_refused(self):
        response = self.redeem(self.RECEIPT, STRANGER)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertTrue(response.json().get("held_by_buyer"))
        self.assertIn("Private WiFi address", response.json()["detail"])
        self.assertEqual(self.bound(), {BUYER})

    def test_the_code_on_a_strangers_phone_is_refused_too(self):
        response = self.redeem("R0Q8ZZ", STRANGER)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(self.bound(), {BUYER})

    def test_the_buyers_own_phone_still_gets_in(self):
        response = self.redeem("R0Q8ZZ", BUYER)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.bound(), {BUYER})

    def test_after_a_day_away_the_bundle_moves_and_the_buyer_is_told(self):
        with tenant_context(self.tenant):
            HotspotUsageState.objects.filter(pk=self.state.pk).update(
                last_seen_at=timezone.now() - timedelta(days=2))
        response = self.redeem("R0Q8ZZ", STRANGER)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.bound(), {STRANGER})
        self.sent.assert_called_once()
        self.assertEqual(self.sent.call_args[0][0], "254713335781")
