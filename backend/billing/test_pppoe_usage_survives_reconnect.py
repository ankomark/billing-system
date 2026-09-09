"""
A PPPoE subscriber's usage must not restart when their session does.

A PPPoE session's byte counters live on its own interface, and RouterOS
destroys that interface at disconnect and builds a new one at zero when the
subscriber comes back. The collector's only memory was the last counter values,
so after a reconnect it saw a number smaller than the one it had stored — and a
reconnect and a router reboot look identical through that keyhole. It assumed a
reboot, rebaselined, and `continue`d.

That threw away two things, not one. The traffic since the previous poll went
with the dead interface, and the traffic the new session had already carried was
discarded by the `continue`. A subscriber who dropped and came back had their
meter appear to start over.

The tail is genuinely unrecoverable by a poller: the counters are gone before
anything can read them, and only PPP accounting or RADIUS would have caught
them. The head is recoverable and these tests hold it. Uptime is what separates
the two cases — it rises within a session and restarts with it, so a value below
the stored one means a reconnect and nothing else.
"""

from unittest.mock import patch

from django.test import TestCase

from billing.models import (
    Customer, PPPoEUsageRecord, PPPoEUsageState, RouterDevice, Tenant,
)
from billing.tenancy import tenant_context

USER = "line-1"


class UsageSurvivesAReconnect(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Usage", slug="usage-test")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="box", ip_address="10.8.0.1",
                username="a", password="p", priority=1)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Fibre Line",
                phone="254700800001", connection_type="pppoe",
                router=self.router, status="active", pppoe_username=USER)

    def poll(self, rx, tx, uptime):
        """One collection run seeing the session in the given state."""
        from billing.tasks.usage_tasks import collect_pppoe_usage_for_tenant

        def reader(router):
            return {USER: {"connected": True, "rx_bytes": rx,
                           "tx_bytes": tx, "uptime": uptime,
                           "ip_address": "10.9.9.9", "interface": None}}

        with patch("billing.router_service._tenant_routers",
                   side_effect=lambda tid, *a, **k: [self.router]), \
             patch("billing.tasks.usage_tasks.get_pppoe_sessions", reader):
            collect_pppoe_usage_for_tenant(self.tenant.id)

    def state(self):
        return PPPoEUsageState.objects.all_tenants().get(customer=self.customer)

    def records(self):
        return list(PPPoEUsageRecord.objects.all_tenants()
                    .filter(customer=self.customer).order_by("id"))

    # ---- the regression itself -------------------------------------------

    def test_the_new_session_s_traffic_is_recorded_not_discarded(self):
        """
        The bug in one assertion. After a reconnect the counters are a young
        session's own totals, which is exactly the delta — this used to write
        no row at all.
        """
        self.poll(rx=1_000, tx=5_000, uptime="10m")
        self.poll(rx=200, tx=700, uptime="2m")

        rows = self.records()
        self.assertEqual(len(rows), 2, "the reconnect interval wrote no row")

        after = rows[-1]
        # Crossed over: the router's tx is the subscriber's download.
        self.assertEqual(after.download_bytes, 700)
        self.assertEqual(after.upload_bytes, 200)
        self.assertTrue(after.session_restarted)

    def test_the_running_totals_do_not_reset(self):
        """
        What the subscriber is actually asking about when they reconnect and
        want to know what they have used.
        """
        self.poll(rx=1_000, tx=5_000, uptime="10m")
        self.poll(rx=200, tx=700, uptime="2m")

        s = self.state()
        self.assertEqual(s.total_download_bytes, 5_700)
        self.assertEqual(s.total_upload_bytes, 1_200)

    def test_a_reconnect_is_counted(self):
        self.poll(rx=1_000, tx=5_000, uptime="10m")
        self.assertEqual(self.state().reconnect_count, 0,
                         "a first session is not a reconnect")

        self.poll(rx=200, tx=700, uptime="2m")
        self.assertEqual(self.state().reconnect_count, 1)

    # ---- uptime ----------------------------------------------------------

    def test_uptime_is_stored_per_interval(self):
        self.poll(rx=1_000, tx=5_000, uptime="10m")
        self.poll(rx=1_500, tx=8_000, uptime="15m")

        rows = self.records()
        self.assertEqual(rows[0].uptime_seconds, 600)
        self.assertEqual(rows[1].uptime_seconds, 300,
                         "the second interval added five minutes of connection")
        self.assertEqual(self.state().total_uptime_seconds, 900)

    def test_uptime_after_a_reconnect_counts_only_the_new_session(self):
        """
        Not the drop in uptime, and not the wall clock — the young session has
        been up for exactly its own uptime.
        """
        self.poll(rx=1_000, tx=5_000, uptime="1h")
        self.poll(rx=200, tx=700, uptime="2m")

        self.assertEqual(self.records()[-1].uptime_seconds, 120)
        self.assertEqual(self.state().total_uptime_seconds, 3600 + 120)

    # ---- the case that must still claim nothing ---------------------------

    def test_a_reboot_still_rebaselines_without_inventing_traffic(self):
        """
        Counters fell but uptime did not, so the session did not restart — the
        router's counters did. Nothing can say how much was missed, and a delta
        the size of the whole counter would be invented traffic charged against
        a cap.
        """
        self.poll(rx=1_000, tx=5_000, uptime="10m")
        before = self.state().total_download_bytes

        self.poll(rx=200, tx=700, uptime="20m")

        self.assertEqual(len(self.records()), 1, "a reboot wrote a usage row")
        self.assertEqual(self.state().total_download_bytes, before)
        # Rebaselined, so the next honest interval measures from the new floor.
        self.assertEqual(self.state().last_rx_bytes, 200)

    def test_a_router_that_reports_no_uptime_still_collects(self):
        """
        Uptime is the better witness, not a required one. Without it this must
        behave exactly as it did before — growth is a delta, a fall is a
        rebaseline — or upgrading would stop collection on older hardware.
        """
        self.poll(rx=1_000, tx=5_000, uptime=None)
        self.poll(rx=1_500, tx=8_000, uptime=None)

        rows = self.records()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1].download_bytes, 3_000)
        self.assertFalse(rows[1].session_restarted)

        self.poll(rx=10, tx=20, uptime=None)
        self.assertEqual(len(self.records()), 2,
                         "a fall with no uptime to explain it wrote a row")

    def test_an_ordinary_interval_is_unchanged(self):
        """The common path must keep behaving exactly as it always has."""
        self.poll(rx=1_000, tx=5_000, uptime="10m")
        self.poll(rx=1_500, tx=8_000, uptime="15m")

        row = self.records()[-1]
        self.assertEqual(row.download_bytes, 3_000)
        self.assertEqual(row.upload_bytes, 500)
        self.assertFalse(row.session_restarted)
        self.assertEqual(row.router_id, self.router.id)
