"""
Releasing a device place must take the account off the routers too.

When somebody redeems a code and has no free device slot, the system releases a
place from an idle device rather than refusing them: it deletes the
CustomerDevice row, writes an audit entry, and moves on. It did not touch the
hardware, so the hotspot account stayed behind serving a device the database
had forgotten.

Usually invisible. Accounts are named for the MAC, so when the new claimant is
granted on the same router the account is simply rewritten under their name.
The orphan appears only when the claimant lands on the OTHER router -- and
every one of the 21 orphans found on 2026-09-13 was on skylink3 for that
reason, four of them having served real traffic (709MB, 377MB, 31MB) before
being forgotten.

It is not a rare path either: 1,026 releases are logged, six in the hour this
was written.

The retry hand-off is deliberately suppressed here, and that is the subtle
part. kick_device_task removes by MAC over the following hour, and this exact
address is about to be granted to the new claimant -- so an hour of retries
would come back and take the new owner's account away. The removal happens
synchronously or not at all.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from billing.models import (
    Customer, CustomerDevice, Invoice, Package, RouterDevice, Subscription,
    Tenant,
)
from billing.tenancy import tenant_context

MAC = "AA:BB:CC:E0:00:01"


class ReleasingADevicePlace(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.package = Package.objects.create(
                tenant=self.tenant, name="3hrs - 3GB", download_speed=5,
                upload_speed=2, price=Decimal("10.00"), duration_value=3,
                duration_unit="hours", data_cap_mb=3000, is_hotspot=True,
                max_devices=1)
            self.here = RouterDevice.objects.create(
                tenant=self.tenant, name="rel-here", ip_address="10.9.2.1",
                username="u", password="p", is_active=True, is_online=True)
            self.there = RouterDevice.objects.create(
                tenant=self.tenant, name="rel-there", ip_address="10.9.2.2",
                username="u", password="p", is_active=True, is_online=True)

            self.holder = Customer.objects.create(
                tenant=self.tenant, full_name="First Holder",
                phone="254700110001", connection_type="hotspot",
                router=self.there, status="active", hotspot_username=MAC)
            CustomerDevice.objects.create(
                tenant=self.tenant, customer=self.holder, mac_address=MAC)

            self.claimant = Customer.objects.create(
                tenant=self.tenant, full_name="New Claimant",
                phone="254700110002", connection_type="hotspot",
                router=self.here, status="active",
                hotspot_username="AA:BB:CC:E0:00:02")

    def _release(self):
        """Run the release, recording which routers were visited."""
        from billing.views import _release_device_from_others

        visited = []

        def connect(router):
            visited.append(router.name)
            return object()

        with patch("billing.router_service.connect_router",
                   side_effect=connect), \
             patch("billing.router_service.disable_hotspot",
                   return_value=True) as dropped, \
             patch("billing.tasks.router_tasks.kick_device_task") as retry:
            conflict = _release_device_from_others(self.claimant, MAC)
        return visited, dropped, retry, conflict

    def test_the_account_is_removed_from_every_router(self):
        """
        The fix. The holder is homed to one router and the claimant to the
        other, which is the shape that produced all 21 orphans.
        """
        visited, dropped, _, conflict = self._release()

        self.assertIsNone(conflict)
        self.assertIn("rel-here", visited)
        self.assertIn("rel-there", visited)
        self.assertTrue(dropped.called)

    def test_the_device_row_is_still_deleted(self):
        """The behaviour that was already there, kept."""
        self._release()

        self.assertFalse(
            CustomerDevice.objects.all_tenants()
            .filter(customer=self.holder, mac_address=MAC).exists())

    def test_no_hour_of_retries_is_queued(self):
        """
        The subtle half. kick_device_task removes by MAC, and this address is
        about to belong to the claimant -- retrying for an hour would find the
        new owner's account and take it away.
        """
        _, _, retry, _ = self._release()

        self.assertFalse(
            retry.delay.called,
            "a retry was queued against an address about to change hands")

    def test_an_unreachable_router_does_not_refuse_the_claimant(self):
        """
        The claimant is holding a paid code. A router that cannot be reached is
        worth a log line, not a refusal -- the orphan sweep is what covers it.
        """
        from billing.views import _release_device_from_others

        with patch("billing.router_service.connect_router",
                   side_effect=RuntimeError("down")), \
             patch("billing.tasks.router_tasks.kick_device_task"):
            conflict = _release_device_from_others(self.claimant, MAC)

        self.assertIsNone(conflict)
        self.assertFalse(
            CustomerDevice.objects.all_tenants()
            .filter(customer=self.holder, mac_address=MAC).exists())

    def test_a_device_nobody_else_holds_is_untouched(self):
        """Nothing to release, nothing to visit."""
        from billing.views import _release_device_from_others

        visited = []
        with patch("billing.router_service.connect_router",
                   side_effect=lambda r: visited.append(r.name)), \
             patch("billing.router_service.disable_hotspot", return_value=True):
            conflict = _release_device_from_others(
                self.claimant, "AA:BB:CC:E0:00:09")

        self.assertIsNone(conflict)
        self.assertEqual(visited, [])


class TheRetryFlagItself(TestCase):
    """
    _kick_device keeps its hour of retries for every other caller. Blocking a
    handset while a router is down is exactly what that exists for.
    """

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="retry-r", ip_address="10.9.2.3",
                username="u", password="p", is_active=True, is_online=True)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Blocked",
                phone="254700110003", connection_type="hotspot",
                router=self.router, status="active", hotspot_username=MAC)

    def test_an_ordinary_block_still_retries(self):
        from billing.views import _kick_device

        with patch("billing.router_service.connect_router",
                   side_effect=RuntimeError("down")), \
             patch("billing.tasks.router_tasks.kick_device_task") as retry:
            _kick_device(self.customer, MAC)

        self.assertTrue(retry.delay.called)

    def test_retry_false_suppresses_it(self):
        from billing.views import _kick_device

        with patch("billing.router_service.connect_router",
                   side_effect=RuntimeError("down")), \
             patch("billing.tasks.router_tasks.kick_device_task") as retry:
            _kick_device(self.customer, MAC, retry=False)

        self.assertFalse(retry.delay.called)
