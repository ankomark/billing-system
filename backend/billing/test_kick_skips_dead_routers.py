"""
An hour of retries against a router that has been dead since August.

_kick_device asks every router the operator owns and hands anything it cannot
confirm to kick_device_task, which retries for about an hour. That is right for
a link that dropped, and wrong for fiber1, unreachable since 19 August: every
eviction queued ten attempts against it, each blocking a worker slot on a
connect timeout.

On 2026-09-07 that ran alongside 152 provisioning retries and emptied a
four-slot pool. check_router_health_task carries expires=90, so once it could
not get a slot it was discarded silently, is_online went stale, and the
dashboard reported two live routers as offline.

Nothing is lost by skipping a condemned router: a device left behind on one is
what disable_orphan_hotspot_users exists to find, and it already says to re-run
once the router is up.
"""

from unittest.mock import patch

from django.test import TestCase, override_settings

from billing.models import Customer, RouterDevice, Tenant
from billing.tenancy import tenant_context


@override_settings(ROUTER_OFFLINE_AFTER_FAILURES=3)
class KickSkipsCondemnedRoutersTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.up = RouterDevice.objects.create(
                tenant=self.tenant, name="up", ip_address="10.0.0.41",
                username="u", password="p", is_online=True)
            self.dead = RouterDevice.objects.create(
                tenant=self.tenant, name="dead", ip_address="10.0.0.42",
                username="u", password="p", is_online=False,
                consecutive_failures=13989)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Hotspot 5150",
                phone="0700051500", connection_type="hotspot", router=self.up)

    def test_a_condemned_router_is_not_contacted(self):
        from billing.views import _kick_device
        asked = []

        def fake_connect(router):
            asked.append(router.name)
            return object()

        with patch("billing.router_service.connect_router", side_effect=fake_connect), \
             patch("billing.router_service.disable_hotspot", return_value=True), \
             patch("billing.tasks.router_tasks.kick_device_task.delay") as queued:
            with tenant_context(self.tenant):
                _kick_device(self.customer, "11:22:33:44:55:66")

        self.assertEqual(asked, ["up"],
                         "the dead router costs a connect timeout and answers nothing")
        queued.assert_not_called()

    def test_a_reachable_router_is_still_contacted(self):
        from billing.views import _kick_device
        asked = []
        with patch("billing.router_service.connect_router",
                   side_effect=lambda r: asked.append(r.name) or object()), \
             patch("billing.router_service.disable_hotspot", return_value=True), \
             patch("billing.tasks.router_tasks.kick_device_task.delay"):
            with tenant_context(self.tenant):
                reached = _kick_device(self.customer, "11:22:33:44:55:66")
        self.assertEqual(reached, 1)

    def test_an_unconfirmed_removal_on_a_live_router_still_retries(self):
        """The retry must survive for the case it was written for."""
        from billing.views import _kick_device
        with patch("billing.router_service.connect_router", return_value=object()), \
             patch("billing.router_service.disable_hotspot", return_value=False), \
             patch("billing.tasks.router_tasks.kick_device_task.delay") as queued:
            with tenant_context(self.tenant):
                _kick_device(self.customer, "11:22:33:44:55:66")
        queued.assert_called_once()
