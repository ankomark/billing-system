"""
Two ways the same afternoon is not allowed to repeat.

On 2026-08-31 `skylink`'s management tunnel dropped four times for a few
minutes each, and auto-failover moved 111 hotspot subscribers onto `skylink3`
in another building. Each of them kept a valid, paid code that the portal
accepted and the router in front of them then refused, because the account had
been rebuilt somewhere they could not see. The station fix stopped the move
being permanent. These two stop it happening at all.

FIRST — a flap is not an outage, and only the health probe gets a vote.

record_health has always refused to condemn a router on one missed probe, and
its docstring does the sum: three failures in a row is "about six minutes at
the default — because the health sweep runs every two minutes". That arithmetic
assumes one caller. safe_connect_router has twenty-six — provisioning, usage
and tethering sweeps, the portal's own validate path, PPPoE — and every one of
them cast a vote whenever it happened to fail. Once the sweeps were fanned out
to run concurrently, a thirty-second drop had a dozen callers failing at once
and crossed the threshold in seconds. The guard that exists to absorb exactly
this was defeated by the number of things calling it.

SECOND — hotspot subscribers are never migrated.

Which router serves a hotspot subscriber is not an assignment anybody gets to
make; it is decided by the radio they are standing in front of. If their router
is down they have no network to reach another one over, so the move cannot help
them, and when their router returns their account is somewhere else. The portal
already re-homes them from its own router token the moment they present a code,
which is direct evidence of where they are.

PPPoE is genuinely different — those subscribers are routed rather than
associated — so it still migrates, and the tests below hold that line too.
"""

from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from billing.models import Customer, RouterDevice, Tenant
from billing.router_service import migrate_customer_router, safe_connect_router
from billing.tasks.auto_failover import recheck_offline_router_task
from billing.tenancy import tenant_context


@override_settings(ROUTER_OFFLINE_AFTER_FAILURES=3)
class OnlyTheHealthProbeVotes(TestCase):
    """
    A router that is briefly unreachable must not be declared down by whoever
    happened to be talking to it at the time.
    """

    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(name="Votes", slug="votes-test")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="box", ip_address="10.7.0.1",
                username="a", password="p", priority=1, is_online=True)

    def test_an_ordinary_caller_failing_does_not_count(self):
        with patch("billing.router_service.is_router_reachable", return_value=False):
            self.assertIsNone(safe_connect_router(self.router))

        self.router.refresh_from_db()
        self.assertEqual(self.router.consecutive_failures, 0)
        self.assertTrue(self.router.is_online)

    def test_the_health_probe_failing_does_count(self):
        with patch("billing.router_service.is_router_reachable", return_value=False):
            self.assertIsNone(
                safe_connect_router(self.router, count_failure=True))

        self.router.refresh_from_db()
        self.assertEqual(self.router.consecutive_failures, 1)
        self.assertTrue(
            self.router.is_online,
            "one missed probe must not be enough to declare it down")

    def test_a_storm_of_ordinary_callers_never_declares_it_down(self):
        """
        The regression, stated directly. Twelve concurrent sweeps failing
        against a router that is merely rebinding its address used to take
        every subscriber off it.
        """
        with patch("billing.router_service.is_router_reachable", return_value=False):
            for _ in range(12):
                safe_connect_router(self.router)

        self.router.refresh_from_db()
        self.assertEqual(self.router.consecutive_failures, 0)
        self.assertTrue(self.router.is_online, "a flap was read as an outage")

    def test_the_probe_still_declares_a_real_outage(self):
        """Being cautious must not become being blind."""
        with patch("billing.router_service.is_router_reachable", return_value=False):
            for _ in range(3):
                safe_connect_router(self.router, count_failure=True)

        self.router.refresh_from_db()
        self.assertEqual(self.router.consecutive_failures, 3)
        self.assertFalse(self.router.is_online)

    def test_success_is_recorded_whoever_sees_it(self):
        """
        Coming back is immediate. Any caller that gets an answer is proof the
        router is up, and waiting for the probe would just be a second outage.
        """
        self.router.is_online = False
        self.router.consecutive_failures = 9
        self.router.save(update_fields=["is_online", "consecutive_failures"])

        with patch("billing.router_service.is_router_reachable", return_value=True), \
             patch("billing.router_service.connect", return_value=object()):
            self.assertIsNotNone(safe_connect_router(self.router))

        self.router.refresh_from_db()
        self.assertEqual(self.router.consecutive_failures, 0)
        self.assertTrue(self.router.is_online)


class FailoverLeavesHotspotAlone(TestCase):
    """
    A down router takes its hotspot subscribers off the air by itself. Moving
    their records as well is what stops them coming back.
    """

    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(name="Homabay", slug="homabay-test")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="site-a", ip_address="10.6.0.1",
                username="a", password="p", priority=1)

            self.tv = Customer.objects.create(
                tenant=self.tenant, full_name="Television", phone="254700700001",
                connection_type="hotspot", router=self.router, status="active")
            self.line = Customer.objects.create(
                tenant=self.tenant, full_name="Fibre Line", phone="254700700002",
                connection_type="pppoe", router=self.router, status="active")

    def _dispatched(self):
        with patch("billing.router_service.is_router_reachable", return_value=False), \
             patch("billing.tasks.auto_failover.migrate_single_customer_task.delay") as delay:
            recheck_offline_router_task.run(self.router.id)
            return [c.args[0] for c in delay.call_args_list]

    def test_the_hotspot_subscriber_is_not_dispatched(self):
        self.assertNotIn(
            self.tv.id, self._dispatched(),
            "a hotspot subscriber was migrated off the router they stand at")

    def test_the_pppoe_subscriber_still_is(self):
        self.assertIn(
            self.line.id, self._dispatched(),
            "PPPoE failover was lost along with the hotspot fix")

    def test_migrate_refuses_a_hotspot_subscriber_on_the_automatic_path(self):
        """
        Guarded where every caller passes, not only in the task — the
        router_failover management command calls straight into here.
        """
        ok, msg = migrate_customer_router(self.tv, reason="auto_failover")
        self.assertFalse(ok)
        self.assertIn("not migrated automatically", msg)

    def test_a_deliberate_move_is_still_the_operator_s_to_make(self):
        """
        The refusal is about the automatic path. An admin re-homing somebody by
        hand can see what they are doing, and is not stopped here — this asserts
        the guard did not fire, not that the move succeeded.
        """
        ok, msg = migrate_customer_router(self.tv, reason="admin_manual")
        self.assertNotIn("not migrated automatically", msg)
