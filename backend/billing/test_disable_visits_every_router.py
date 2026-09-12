"""
Expiry must clear a customer off every router, not just the one they are on.

`customer.router` records where somebody was last provisioned. It does not
record where accounts for them exist, and on a hotspot estate those are not the
same thing: every re-home writes an account in the new place and leaves the old
one behind, and `login-by=mac` readmits from whichever one survives, whatever
the database believes. Customers move routinely here -- an OLT cable repatched,
auto-failover, an operator moving somebody by hand.

Measured on 2026-09-12: 187 hotspot accounts existed on a router their customer
was not homed to. Two were serving people whose package had ended -- customer
39, whose 3-hour subscription had expired 1.5 hours earlier and who was still
connected on skylink while homed there but also holding an account on skylink3,
and customer 75, suspended for spending a 5GB allowance and still online.
Expiry had run correctly on the home router in both cases and reported success.

This is the third time the single-router assumption has cost service.
kick_device_task already asks every router for exactly this reason.
"""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from billing.models import Customer, CustomerDevice, RouterDevice, Tenant
from billing.router_service import RouterUnreachable, disable_customer_access
from billing.tenancy import tenant_context


class DisableVisitsEveryRouter(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.home = RouterDevice.objects.create(
                tenant=self.tenant, name="home-r", ip_address="10.9.7.1",
                username="u", password="p", priority=1, is_online=True)
            self.elsewhere = RouterDevice.objects.create(
                tenant=self.tenant, name="elsewhere-r", ip_address="10.9.7.2",
                username="u", password="p", priority=2, is_online=True)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Moved About",
                phone="254700940001", connection_type="hotspot",
                router=self.home, status="active",
                hotspot_username="AA:BB:CC:40:00:01")
            CustomerDevice.objects.create(
                tenant=self.tenant, customer=self.customer,
                mac_address="AA:BB:CC:40:00:01")

    def _disable(self, connect_result=None):
        """Run a disable, capturing which routers were actually visited."""
        visited = []

        def connect(router):
            visited.append(router.name)
            if connect_result is None:
                return object()
            return connect_result(router)

        with patch("billing.router_service.safe_connect_router",
                   side_effect=connect), \
             patch("billing.router_service.disable_hotspot",
                   return_value=True) as disable:
            error = None
            try:
                disable_customer_access(self.customer)
            except Exception as exc:
                error = exc
        return visited, disable, error

    def test_both_routers_are_visited(self):
        """
        The whole fix. Clearing only the home router is what left 187 accounts
        behind.
        """
        visited, _, error = self._disable()

        self.assertIsNone(error)
        self.assertIn("home-r", visited)
        self.assertIn("elsewhere-r", visited)

    def test_the_home_router_is_visited_first(self):
        """
        The common case dealt with before time is spent elsewhere, so a failure
        further down still leaves the most likely place clean.
        """
        visited, _, _ = self._disable()

        self.assertEqual(visited[0], "home-r")

    def test_the_device_is_removed_on_every_router(self):
        _, disable, _ = self._disable()

        macs = [call.args[1] for call in disable.call_args_list]
        self.assertEqual(len(macs), 2, "the device was not removed twice")

    def test_an_unreachable_second_router_still_raises(self):
        """
        The customer may still be online there, which is exactly the state
        worth retrying. Reporting success because the home router was fine is
        the failure this whole class keeps repeating.
        """
        def connect(router):
            return None if router.name == "elsewhere-r" else object()

        _, _, error = self._disable(connect_result=connect)

        self.assertIsInstance(error, RouterUnreachable)
        self.assertIn("elsewhere-r", str(error))

    def test_the_reachable_router_is_still_cleared_when_another_is_down(self):
        """
        Collected, not thrown at the first failure. One unreachable router must
        not leave the customer connected on a router that answered.
        """
        def connect(router):
            return None if router.name == "elsewhere-r" else object()

        _, disable, _ = self._disable(connect_result=connect)

        self.assertTrue(disable.called)

    def test_a_router_the_health_sweep_condemned_is_skipped(self):
        """
        fiber1 has answered nothing since 19 August. Blocking every expiry on a
        connect timeout against it would empty the worker pool and stop the
        health sweep -- which is how the dashboard came to report two live
        routers as offline on 2026-09-07. A router nobody can reach is serving
        nobody, and disable_orphan_hotspot_users covers it when it returns.
        """
        with tenant_context(self.tenant):
            self.elsewhere.is_online = False
            self.elsewhere.consecutive_failures = 999
            self.elsewhere.save(
                update_fields=["is_online", "consecutive_failures"])

        visited, _, error = self._disable()

        self.assertEqual(visited, ["home-r"])
        self.assertIsNone(error, "a condemned router failed the disable")

    def test_a_never_probed_router_is_not_treated_as_condemned(self):
        """
        is_online is False on a router nobody has probed yet -- the column
        default -- so reading the flag alone would skip a newly added box, and
        skip every box for the two minutes after a restart, silently doing
        nothing. The failure count is the positive evidence.
        """
        with tenant_context(self.tenant):
            self.elsewhere.is_online = False
            self.elsewhere.consecutive_failures = 0
            self.elsewhere.save(
                update_fields=["is_online", "consecutive_failures"])

        visited, _, _ = self._disable()

        self.assertIn("elsewhere-r", visited)

    def test_another_operators_router_is_never_touched(self):
        """
        _tenant_routers filters explicitly rather than trusting the ambient
        context, because this runs from Celery where no middleware has set one.
        """
        other = Tenant.objects.create(name="Other", slug="other-disable")
        with tenant_context(other):
            RouterDevice.objects.create(
                tenant=other, name="theirs-r", ip_address="10.9.7.9",
                username="u", password="p", is_online=True)

        visited, _, _ = self._disable()

        self.assertNotIn("theirs-r", visited)
