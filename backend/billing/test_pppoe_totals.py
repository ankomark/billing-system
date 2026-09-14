"""
What a PPPoE subscriber has used altogether, not since their last reconnect.

A PPPoE session's byte counters live on its interface, and RouterOS destroys
that interface at disconnect and builds a new one at zero. The admin sessions
page was showing those counters, so on a line that flaps — st.ambros dropped
five times in one morning — the figure fell back to nothing several times a
day and never said what anybody had consumed.

PPPoEUsageState has carried running totals all along, and its own note says
why: "A reconnect does not reset them. That is the whole point: the session
counters restart at zero and these do not." They simply were not being sent.

Summed across stations, because the state is kept per account per router and
a subscriber who has dialled in at two sites holds a row for each.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from billing.models import (
    Customer, PPPoEUsageRecord, PPPoEUsageState, RouterDevice, Tenant,
)
from billing.tenancy import tenant_context

MB = 1024 * 1024


class TotalsSurviveAReconnect(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="tot-r", ip_address="10.9.0.140",
                username="u", password="p", is_active=True)
            self.other = RouterDevice.objects.create(
                tenant=self.tenant, name="tot-r2", ip_address="10.9.0.141",
                username="u", password="p", is_active=True)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Line Holder",
                phone="254700200001", connection_type="pppoe",
                pppoe_username="line.holder", router=self.router,
                status="active")

        U = get_user_model()
        admin = U.objects.create_user(
            username="tot-admin", password="x", role="tenant_admin",
            tenant=self.tenant)
        self.client = APIClient()
        self.client.force_authenticate(user=admin)

    def _state(self, router, down, up, uptime=0, reconnects=0):
        """
        One interval in the ledger, plus the drop count on the state row.

        Written as a recorded interval rather than as a running counter,
        because that is where the totals now come from: the counters on
        PPPoEUsageState were added after the rows existed and count only from
        the day they were switched on, which is how they came to read 11GB
        against the ledger's 29GB for the same subscriber.
        """
        from django.utils import timezone

        with tenant_context(self.tenant):
            now = timezone.now()
            PPPoEUsageRecord.objects.create(
                tenant_id=self.tenant.id, customer=self.customer,
                router=router, period_start=now, period_end=now,
                download_bytes=down, upload_bytes=up, uptime_seconds=uptime)
            PPPoEUsageState.objects.update_or_create(
                customer=self.customer, router=router,
                defaults={
                    "tenant_id": self.tenant.id,
                    "reconnect_count": reconnects,
                })

    def _rows(self, rx=0, tx=0):
        """The page, with one live session reporting the given counters."""
        sessions = [{
            "username": "line.holder",
            "ip_address": "192.168.89.50",
            "uptime": "5m",
            "rx_bytes": rx,
            "tx_bytes": tx,
        }]

        def all_sessions(router):
            return sessions if router.pk == self.router.pk else []

        from django.core.cache import cache
        cache.clear()
        with patch("billing.views.get_all_pppoe_sessions", all_sessions):
            resp = self.client.get("/api/admin/pppoe/sessions/")
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        return resp.data

    # ───────────────────────────────────────────────────── the running total

    def test_the_total_is_sent(self):
        self._state(self.router, down=900 * MB, up=300 * MB)

        row = self._rows()[0]

        self.assertEqual(row["total_download_bytes"], 900 * MB)
        self.assertEqual(row["total_upload_bytes"], 300 * MB)
        self.assertEqual(row["total_bytes"], 1200 * MB)

    def test_it_does_not_move_when_the_session_counters_reset(self):
        """
        The fault itself. A reconnect zeroes the interface, and the figure an
        operator reads must not follow it down.
        """
        self._state(self.router, down=900 * MB, up=300 * MB)

        before = self._rows(rx=50 * MB, tx=200 * MB)[0]["total_bytes"]
        after = self._rows(rx=0, tx=0)[0]["total_bytes"]     # freshly redialled

        self.assertEqual(before, after)
        self.assertEqual(after, 1200 * MB)

    def test_the_session_figure_is_still_there_and_still_resets(self):
        """
        Both are wanted, and they answer different questions: what they have
        used, and what this session is carrying now.
        """
        self._state(self.router, down=900 * MB, up=300 * MB)

        self.assertEqual(self._rows(rx=7 * MB, tx=9 * MB)[0]["rx_bytes"], 7 * MB)
        self.assertEqual(self._rows(rx=0, tx=0)[0]["rx_bytes"], 0)

    def test_stations_are_summed(self):
        """
        The state is per account per router. A subscriber who has dialled in at
        two sites holds a row for each, and one bundle was spent across both.
        """
        self._state(self.router, down=500 * MB, up=100 * MB)
        self._state(self.other, down=200 * MB, up=50 * MB)

        row = self._rows()[0]

        self.assertEqual(row["total_download_bytes"], 700 * MB)
        self.assertEqual(row["total_upload_bytes"], 150 * MB)

    def test_the_total_comes_from_the_ledger_not_the_counters(self):
        """
        They disagree, and the ledger is the one that is right. The counters
        were switched on after the rows already existed, so they miss
        everything before that day — and this page has to agree with the
        customer page, which reads the same rows.
        """
        with tenant_context(self.tenant):
            from django.utils import timezone

            now = timezone.now()
            PPPoEUsageRecord.objects.create(
                tenant_id=self.tenant.id, customer=self.customer,
                router=self.router, period_start=now, period_end=now,
                download_bytes=40 * MB, upload_bytes=10 * MB)
            # A counter that was started late and knows nothing about it.
            PPPoEUsageState.objects.update_or_create(
                customer=self.customer, router=self.router,
                defaults={"tenant_id": self.tenant.id,
                          "total_download_bytes": 1 * MB,
                          "total_upload_bytes": 0})

        self.assertEqual(self._rows()[0]["total_bytes"], 50 * MB)

    def test_a_subscriber_with_no_history_reads_zero_not_missing(self):
        """
        A key the page can rely on. Absent would render as NaN in the total
        across the top.
        """
        row = self._rows(rx=1 * MB, tx=1 * MB)[0]

        self.assertEqual(row["total_bytes"], 0)
        self.assertIn("total_download_bytes", row)

    # ──────────────────────────────────────────────────────────── the drops

    def test_the_reconnect_count_is_sent(self):
        """
        A number climbing with little traffic against it is the shape of a
        fault rather than of usage — which is how st.ambros's flapping line was
        spotted at all.
        """
        self._state(self.router, down=1 * MB, up=1 * MB, reconnects=17)

        self.assertEqual(self._rows()[0]["reconnects"], 17)

    def test_reconnects_are_summed_across_stations_too(self):
        self._state(self.router, down=0, up=0, reconnects=4)
        self._state(self.other, down=0, up=0, reconnects=3)

        self.assertEqual(self._rows()[0]["reconnects"], 7)

    def test_connected_time_is_carried(self):
        self._state(self.router, down=0, up=0, uptime=7200)

        self.assertEqual(self._rows()[0]["total_uptime_seconds"], 7200)
