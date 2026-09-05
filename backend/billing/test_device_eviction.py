"""
A device that is not connected does not keep its place.

The device limit exists because one code bought for one phone was being passed
around a room — commit 28318f4. That is devices online *at the same time*. The
check counted every address ever bound instead, which cannot tell that room
apart from a customer who changed phone, whose Android rotated its MAC, or who
once opened the portal on a laptop.

All three of those people have paid. On a one-device package they were refused
with "this code is already in use on another phone", about a phone that was
theirs and was switched off. Hit three times in one evening of testing before
it was fixed, which is three times more than a paying customer should have to.

So the limit is now about concurrency: ask the router who is connected, and
release a place nobody is using.
"""

from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from billing.models import (
    AccessAuditLog, Customer, CustomerDevice, Package, RouterDevice,
    Subscription, Tenant, Voucher,
)
from billing.tenancy import tenant_context

LAPTOP = "6C:02:E0:08:B5:23"
PHONE = "3E:5E:04:A6:ED:BD"
OTHER = "AA:BB:CC:DD:EE:01"


class DeviceEvictionTests(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r", ip_address="10.0.0.1",
                username="a", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="1 device", download_speed=5,
                upload_speed=2, price=Decimal("50.00"), duration_value=1,
                duration_unit="hours", data_cap_mb=0, is_hotspot=True,
                max_devices=1)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Asha", phone="254700000001",
                connection_type="hotspot", router=self.router,
                hotspot_username=LAPTOP)
            self.subscription = Subscription.objects.create(
                tenant=self.tenant, customer=self.customer,
                package=self.package, status="active",
                expiry_date=timezone.now() + timedelta(hours=1))
            self.device = CustomerDevice.objects.create(
                tenant=self.tenant, customer=self.customer, mac_address=LAPTOP)

    def _evict(self, online):
        """`online` is what the router reports, or None for unreachable."""
        from billing.views import _evict_idle_device
        with patch("billing.router_service.active_hotspot_macs",
                   return_value=online), \
             patch("billing.router_service.safe_connect_router",
                   return_value=None), \
             tenant_context(self.tenant):
            devices = list(CustomerDevice.objects.filter(customer=self.customer))
            return _evict_idle_device(self.customer, devices)

    def test_an_idle_device_gives_up_its_place(self):
        """The laptop is bound and not connected. The phone has paid."""
        evicted = self._evict(online=set())
        self.assertIsNotNone(evicted, "an idle device kept its place")
        self.assertEqual(evicted.mac_address, LAPTOP)
        with tenant_context(self.tenant):
            self.assertEqual(
                CustomerDevice.objects.filter(customer=self.customer).count(), 0)

    def test_a_connected_device_keeps_its_place(self):
        """
        This is the sharing the limit was built for. Every bound device is
        online, so there is nothing to release and the refusal is right.
        """
        evicted = self._evict(online={LAPTOP})
        self.assertIsNone(evicted)
        with tenant_context(self.tenant):
            self.assertEqual(
                CustomerDevice.objects.filter(customer=self.customer).count(), 1)

    def test_case_does_not_decide_whether_someone_is_online(self):
        """RouterOS is not consistent about the case it reports."""
        evicted = self._evict(online={LAPTOP.lower()})
        self.assertIsNone(
            evicted, "a connected device was evicted over letter case")

    def test_an_unreachable_router_does_not_free_anything(self):
        """
        "Nobody is online" and "I could not find out" must not lead to the
        same decision, or a router that stops answering quietly grants every
        customer unlimited devices.
        """
        evicted = self._evict(online=None)
        self.assertIsNone(evicted)
        with tenant_context(self.tenant):
            self.assertEqual(
                CustomerDevice.objects.filter(customer=self.customer).count(), 1)

    def test_the_longest_idle_device_goes_first(self):
        with tenant_context(self.tenant):
            newer = CustomerDevice.objects.create(
                tenant=self.tenant, customer=self.customer, mac_address=OTHER)
            # last_seen is auto_now, so push the laptop's into the past.
            CustomerDevice.objects.filter(pk=self.device.pk).update(
                last_seen=timezone.now() - timedelta(days=2))
            CustomerDevice.objects.filter(pk=newer.pk).update(
                last_seen=timezone.now())

        evicted = self._evict(online=set())
        self.assertEqual(
            evicted.mac_address, LAPTOP,
            "the device seen most recently was dropped instead of the stalest")

    def test_only_idle_devices_are_candidates(self):
        """One online, one not: the idle one goes, the connected one stays."""
        with tenant_context(self.tenant):
            CustomerDevice.objects.create(
                tenant=self.tenant, customer=self.customer, mac_address=OTHER)

        evicted = self._evict(online={LAPTOP})
        self.assertEqual(evicted.mac_address, OTHER)
        with tenant_context(self.tenant):
            remaining = list(CustomerDevice.objects
                             .filter(customer=self.customer)
                             .values_list("mac_address", flat=True))
        self.assertEqual(remaining, [LAPTOP])

    def test_the_customers_bound_address_is_not_left_dangling(self):
        """
        hotspot_username is what the public status and reconnect endpoints
        resolve a subscriber by. Evicting the device it names without clearing
        it would leave those looking up a binding that no longer exists.
        """
        self._evict(online=set())
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.hotspot_username, "")

    def test_the_operator_can_see_what_happened(self):
        self._evict(online=set())
        with tenant_context(self.tenant):
            entry = (AccessAuditLog.objects
                     .filter(customer=self.customer).order_by("-id").first())
        self.assertIsNotNone(entry)
        self.assertIn(LAPTOP, entry.reason)
        self.assertIn("no live session", entry.reason)

    def test_the_eviction_is_recorded_without_a_tenant_context(self):
        """
        The same record as the test above, written the way production writes it.

        `_evict` wraps everything in tenant_context(), which lets the model
        infer a tenant from the ambient context. Redemption is a *public*
        endpoint and sets no such context — the holder-release a few lines
        further down says so in its own comment and passes tenant_id
        explicitly. This call did not, so it raised KeyError('tenant') on
        every eviction in production while passing here.

        The except around it is deliberate and correct: a customer who has
        paid must not be refused over a missing audit row. That is what made
        it silent. No eviction was recorded for any operator, on the one
        action a subscriber is most likely to ring up about.

        The second tenant below is what makes this test bite. `default_tenant`
        falls back to the only operator when exactly one exists, so on a
        single-operator test database the missing argument is guessed
        correctly and nothing fails. Every real install this runs on has more
        than one, and there it refuses rather than guess — which is the whole
        point of that fallback, and the reason this went unnoticed.
        """
        from billing.views import _evict_idle_device

        Tenant.objects.create(name="Second Operator", slug="second-operator")

        with tenant_context(self.tenant):
            devices = list(
                CustomerDevice.objects.filter(customer=self.customer))

        # Deliberately outside tenant_context, as the captive portal calls it.
        with patch("billing.router_service.active_hotspot_macs",
                   return_value=set()), \
             patch("billing.router_service.safe_connect_router",
                   return_value=None):
            evicted = _evict_idle_device(self.customer, devices)

        self.assertIsNotNone(evicted, "nothing was evicted")

        entry = (AccessAuditLog.objects.all_tenants()
                 .filter(customer=self.customer, action="deactivate")
                 .order_by("-id").first())
        self.assertIsNotNone(
            entry, "the eviction was not recorded on the public path")
        self.assertEqual(entry.tenant_id, self.tenant.id)
        self.assertIn(LAPTOP, entry.reason)

    def test_a_customer_with_no_router_is_asked_about_on_the_operators(self):
        """
        This used to refuse, on the reasoning that a subscriber with no router
        has nothing to ask. What it actually did was answer "I could not find
        out" — which is the answer that makes a place unfreeable — for the
        tenth of hotspot subscribers whose `router` is null. Their first
        address change then locked them out until their package expired.

        The operator's own routers can answer the question. Nothing about a
        null column makes the sessions on them unreadable.
        """
        with tenant_context(self.tenant):
            self.customer.router = None
            self.customer.save(update_fields=["router"])

        evicted = self._evict(online=set())

        self.assertIsNotNone(evicted)
        self.assertEqual(evicted.mac_address, LAPTOP)

    def test_no_router_anywhere_is_still_not_an_answer(self):
        """
        The half of the old rule that was right, and is kept. With nothing to
        ask, "nobody is online" has not been established — and granting on that
        would hand every customer on an unreachable estate unlimited devices,
        silently.
        """
        with tenant_context(self.tenant):
            RouterDevice.objects.all_tenants().filter(
                tenant=self.tenant).delete()
            self.customer.refresh_from_db()

        self.assertIsNone(self._evict(online=set()))
