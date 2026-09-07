"""
An evicted device has to come off the hardware, not just out of the table.

Eviction is ordinary: a subscriber's device limit is reached, another device
connects, and the oldest binding is released to make room. It deletes the
CustomerDevice row, and the router half used to be best effort in a way that
had no floor under it -- a loop over `_routers_to_ask` (the *assigned* router
alone), with an unreachable router handled by a bare `continue`: no log, no
retry, and the row deleted immediately afterwards regardless.

What that left is a hotspot user named after a MAC, with no password, on a
router nobody will ever ask about again. Provisioning authenticates hotspot
users by address, so the evicted handset reassociates and RouterOS lets it
straight back in. Nothing else can see it either: enforce_usage_caps iterates
Subscription, and `limit-uptime` counts session time rather than wall-clock.

On 2026-09-07 there were 44 of them across two routers, all enabled, 7.30GB
served between them. Every one had an eviction record. 20 had been evicted
against a router other than the one holding the account -- subscribers move,
and there are 126 failover rows -- and 24 asked the right router and missed
anyway.

So eviction goes through _kick_device now, which asks every router the operator
owns and hands what it cannot confirm to kick_device_task for about an hour.
"""

from unittest.mock import patch

from django.test import TestCase

from billing.models import Customer, CustomerDevice, RouterDevice, Tenant
from billing.tenancy import tenant_context


class EvictionCleansEveryRouterTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            # Two routers, like the estate this was found on.
            self.assigned = RouterDevice.objects.create(
                tenant=self.tenant, name="skylink", ip_address="10.0.0.2",
                username="u", password="p")
            self.other = RouterDevice.objects.create(
                tenant=self.tenant, name="skylink3", ip_address="10.0.0.5",
                username="u", password="p")
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Hotspot 0001",
                phone="0700000042", connection_type="hotspot",
                router=self.assigned)
            self.victim = CustomerDevice.objects.create(
                tenant=self.tenant, customer=self.customer,
                mac_address="11:22:33:44:55:66")

    def _evict(self, disable_ok=True):
        """Run an eviction, recording which routers were asked."""
        asked = []

        def fake_disable(api, mac):
            asked.append(api._router_name)
            return disable_ok

        def fake_connect(router):
            api = type("API", (), {})()
            api._router_name = router.name
            return api

        from billing import views
        with patch("billing.router_service.connect_router", side_effect=fake_connect), \
             patch("billing.router_service.disable_hotspot", side_effect=fake_disable), \
             patch("billing.tasks.router_tasks.kick_device_task.delay") as queued:
            with tenant_context(self.tenant):
                freed = views._evict_idle_device(
                    self.customer, [self.victim], reclaiming=True)
        return freed, asked, queued

    def test_every_router_the_operator_owns_is_asked(self):
        """
        The 20-of-44 case. The account sat on skylink3 while the customer was
        assigned to skylink, so asking the assigned router alone cleaned
        nothing and deleted the row anyway.
        """
        _, asked, _ = self._evict()
        self.assertEqual(sorted(asked), ["skylink", "skylink3"])

    def test_the_place_is_freed_even_when_no_router_confirms(self):
        """
        The subscriber must not be refused the device they paid for because a
        router of ours is down. kick_device_task carries the router half and
        needs only the address, so the row can go.
        """
        _, _, queued = self._evict(disable_ok=False)
        self.assertFalse(
            CustomerDevice.objects.filter(pk=self.victim.pk).exists(),
            "the place stays freed; the retry is what finishes the router")

    def test_an_unconfirmed_removal_is_retried_rather_than_dropped(self):
        """
        The 24-of-44 case, and the one that used to vanish entirely: the old
        loop logged nothing at all when a router would not connect.
        """
        _, _, queued = self._evict(disable_ok=False)
        queued.assert_called_once_with(self.customer.pk, "11:22:33:44:55:66")

    def test_a_confirmed_removal_queues_no_retry(self):
        """An hour of a worker proving what already happened is waste."""
        _, _, queued = self._evict(disable_ok=True)
        queued.assert_not_called()
