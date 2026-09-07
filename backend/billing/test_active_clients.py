"""
The live client count an operator reads instead of logging into a MikroTik.

Two things are being protected here, and the second is the one that matters.

The first is that the dashboard contacts no router. Counting sessions means
connecting, and connecting inside an HTTP request blocks a worker for up to the
connect timeout per box -- on the page every operator loads first, with one
dead router enough to hang it for all of them. AdminRouterListView carries the
same warning about is_online.

The second is that a stale number is never presented as a live one. The count
comes from the health sweep every two minutes, so it always has an age; a box
that stopped answering keeps its last figure on screen, labelled, and out of
the site total. "12 clients" from a router that went down an hour ago is worse
than no number at all, because it is the answer the operator would act on.
"""

import datetime as dt
from unittest.mock import MagicMock, patch

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from billing.models import RouterDevice, Station, Tenant, User
from billing.tenancy import tenant_context


class _ActiveClientsBase(APITestCase):
    """Setup shared by the suites below; holds no tests of its own."""

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.user = User.objects.create_user(
                username="op", password="x", role="tenant_admin",
                tenant=self.tenant)
            self.kilifi = Station.objects.create(
                tenant=self.tenant, name="Kilifi Town", code="KLF")
        self.client.force_authenticate(user=self.user)
        self.url = reverse("active-clients")

    def _router(self, name, *, station=None, online=True, count=None,
                age_seconds=0, active=True):
        with tenant_context(self.tenant):
            return RouterDevice.objects.create(
                tenant=self.tenant, name=name, ip_address=f"10.0.0.{self._n()}",
                username="u", password="p", station=station, is_online=online,
                is_active=active, active_sessions=count,
                active_sessions_at=(
                    timezone.now() - dt.timedelta(seconds=age_seconds)
                    if count is not None else None),
            )

    _counter = [10]

    def _n(self):
        self._counter[0] += 1
        return self._counter[0]


class ActiveClientsViewTests(_ActiveClientsBase):
    def test_no_router_is_contacted(self):
        """
        The point of caching the count. If this view ever grows a probe, the
        dashboard starts blocking a worker per router on every load.
        """
        self._router("r1", station=self.kilifi, count=7)
        with patch("billing.router_service.connect_router") as connect, \
             patch("billing.router_service.safe_connect_router") as safe:
            res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        connect.assert_not_called()
        safe.assert_not_called()

    def test_counts_are_grouped_by_station(self):
        self._router("box-a", station=self.kilifi, count=7)
        self._router("box-b", station=self.kilifi, count=5)

        res = self.client.get(self.url)
        station = res.data["stations"][0]
        self.assertEqual(station["station_name"], "Kilifi Town")
        self.assertEqual(station["station_code"], "KLF")
        self.assertEqual(station["active_clients"], 12)
        self.assertEqual(len(station["routers"]), 2)

    def test_a_router_with_no_station_is_still_reported(self):
        """
        Stations are optional and most operators have none. A router without
        one must not vanish from the only page that counts its clients.
        """
        self._router("lonely", station=None, count=4)

        res = self.client.get(self.url)
        groups = res.data["stations"]
        self.assertEqual(len(groups), 1)
        self.assertIsNone(groups[0]["station_name"])
        self.assertEqual(groups[0]["active_clients"], 4)

    def test_an_offline_routers_count_is_kept_out_of_the_total(self):
        """
        The figure it gave before it went down is not the number of people on
        it now. It stays visible, and stops counting.
        """
        self._router("up", station=self.kilifi, count=9)
        self._router("down", station=self.kilifi, count=12, online=False)

        res = self.client.get(self.url)
        station = res.data["stations"][0]
        self.assertEqual(station["active_clients"], 9)
        self.assertFalse(station["complete"])

        down = [r for r in station["routers"] if r["name"] == "down"][0]
        self.assertEqual(down["active_clients"], 12,
                         "still shown, so the operator can see what it was")
        self.assertFalse(down["fresh"])

    def test_a_stale_count_is_flagged_but_not_discarded(self):
        """
        Online is not the same as reporting, and a late count is still marked
        not-fresh so the page can say so.

        It is no longer dropped from the total. That was the original rule and
        it produced the 2026-09-07 failure: the worker pool filled, the health
        sweep stopped running, every count aged out, and two live routers were
        reported as an estate with nobody on it.
        """
        self._router("lagging", station=self.kilifi, count=30,
                     age_seconds=20 * 60)

        res = self.client.get(self.url)
        station = res.data["stations"][0]
        self.assertEqual(station["active_clients"], 30)
        self.assertFalse(station["complete"])
        self.assertFalse(station["routers"][0]["fresh"])
        self.assertEqual(station["routers"][0]["state"], "stale")

    def test_a_never_probed_router_reports_unknown_not_zero(self):
        """
        NULL is not 0. A site with nobody on it and a site we have never asked
        are different answers, and only one of them is good news.
        """
        self._router("brand-new", station=self.kilifi, count=None)

        res = self.client.get(self.url)
        router = res.data["stations"][0]["routers"][0]
        self.assertIsNone(router["active_clients"])
        self.assertFalse(router["fresh"])
        self.assertFalse(res.data["complete"])

    def test_a_deactivated_router_is_not_listed(self):
        self._router("retired", station=self.kilifi, count=3, active=False)
        res = self.client.get(self.url)
        self.assertEqual(res.data["stations"], [])

    def test_the_total_adds_up_across_sites(self):
        with tenant_context(self.tenant):
            malindi = Station.objects.create(
                tenant=self.tenant, name="Malindi", code="MLD")
        self._router("k1", station=self.kilifi, count=6)
        self._router("m1", station=malindi, count=11)

        res = self.client.get(self.url)
        self.assertEqual(res.data["total_active_clients"], 17)
        self.assertTrue(res.data["complete"])

    def test_sites_are_named_first_and_unassigned_last(self):
        self._router("loose", station=None, count=1)
        self._router("sited", station=self.kilifi, count=1)

        res = self.client.get(self.url)
        names = [s["station_name"] for s in res.data["stations"]]
        self.assertEqual(names, ["Kilifi Town", None])


class SessionCountingTests(APITestCase):
    """The read itself, on the health probe's already-open connection."""

    def test_hotspot_and_pppoe_are_counted_together(self):
        from billing.router_service import count_active_sessions

        api = MagicMock()
        api.path.side_effect = lambda *a: (
            [{"x": 1}, {"x": 2}, {"x": 3}] if a[-1] == "active" and "hotspot" in a
            else [{"y": 1}, {"y": 2}]
        )
        self.assertEqual(count_active_sessions(api), 5)

    def test_an_unreadable_list_returns_unknown_not_a_partial_sum(self):
        """
        Half a count is not a count. Returning 3 when PPPoE could not be read
        would understate the site and look exactly like a real number.
        """
        from billing.router_service import count_active_sessions

        def path(*a):
            if "ppp" in a:
                raise RuntimeError("no such command")
            return [{"x": 1}, {"x": 2}, {"x": 3}]

        api = MagicMock()
        api.path.side_effect = path
        self.assertIsNone(count_active_sessions(api))


class StaleIsNotOfflineTests(_ActiveClientsBase):
    """
    A late count must never be reported as an outage.

    On 2026-09-07 the worker pool filled, check_router_health_task (expires=90)
    stopped being scheduled, and every count aged past the threshold. Both
    routers were up and serving; the panel excluded them from the total,
    reported 0 active across the estate, and told the operator their live
    network was offline while they were looking at the running hardware.
    """

    def test_a_late_count_from_a_live_router_still_counts(self):
        self._router("lagging", station=self.kilifi, count=63, age_seconds=20 * 60)

        res = self.client.get(self.url)
        station = res.data["stations"][0]
        self.assertEqual(station["active_clients"], 63,
                         "an online router's figure is the best anyone has")
        self.assertEqual(res.data["total_active_clients"], 63)
        self.assertFalse(station["complete"], "still flagged as lagging")
        self.assertEqual(station["routers"][0]["state"], "stale")
        self.assertFalse(station["routers"][0]["fresh"])

    def test_an_offline_routers_count_is_still_excluded(self):
        """The exclusion that was right: nobody can reach that box."""
        self._router("down", station=self.kilifi, count=12, online=False)

        res = self.client.get(self.url)
        station = res.data["stations"][0]
        self.assertEqual(station["active_clients"], 0)
        self.assertEqual(station["routers"][0]["state"], "offline")

    def test_state_names_the_four_cases_apart(self):
        with tenant_context(self.tenant):
            other = Station.objects.create(tenant=self.tenant, name="Zed")
        self._router("ok",      station=self.kilifi, count=5)
        self._router("late",    station=other, count=7, age_seconds=20 * 60)
        self._router("dead",    station=other, count=9, online=False)
        self._router("nocount", station=other, count=None)

        res = self.client.get(self.url)
        states = {r["name"]: r["state"]
                  for s in res.data["stations"] for r in s["routers"]}
        self.assertEqual(states["ok"], "fresh")
        self.assertEqual(states["late"], "stale")
        self.assertEqual(states["dead"], "offline")
        self.assertEqual(states["nocount"], "unknown")
