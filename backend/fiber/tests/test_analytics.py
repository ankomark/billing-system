"""Signal bands, hourly rollups, trend detection, splitter guesses, reports, line check."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from billing.models import Payment
from billing.tenancy import tenant_context

from fiber.models import FiberOutage, Onu, OnuHourly, PortHourly, RepairTicket
from fiber.services import analytics, line_check, splitters
from fiber.services.collector import poll_olt

from .helpers import FiberFixture


class BandTests(TestCase):
    def test_bands(self):
        self.assertEqual(analytics.signal_band(-20), "good")
        self.assertEqual(analytics.signal_band(-24.0), "good")
        self.assertEqual(analytics.signal_band(-25), "warning")
        self.assertEqual(analytics.signal_band(-28), "critical")
        self.assertEqual(analytics.signal_band(-1), "overload")
        self.assertEqual(analytics.signal_band(None), "unknown")

    def test_slope(self):
        t = timezone.now()
        pts = [(t + timedelta(days=d), -20 - 0.5 * d) for d in range(5)]
        self.assertAlmostEqual(analytics.slope_per_day(pts), -0.5)


@patch("billing.tasks.alert_tasks.notify_admin_task.delay")
class RollupAndTrendTests(FiberFixture, TestCase):
    def setUp(self):
        self.make_world(onus_per_port=(3, 0, 0, 0))
        self.hour = timezone.now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)

    def test_an_hour_rolls_up_traffic_power_and_online_share(self, _):
        for m, clock in ((0, 1_000_000), (5, 1_000_300), (10, 1_000_600)):
            self.sim(clock=clock, traffic_bps=8_000_000)
            self.olt.refresh_from_db()
            poll_olt(self.olt, now=self.hour + timedelta(minutes=m + 1))
        with tenant_context(self.tenant):
            analytics.rollup_hour(self.hour)
            h = OnuHourly.objects.filter(onu__onu_index=1).get()
            p = PortHourly.objects.get(pon_port=1)
        self.assertEqual((h.samples, h.online_samples), (3, 3))
        self.assertEqual(h.rx_bytes, 600 * 1_000_000)
        self.assertEqual(h.peak_rx_bps, 8_000_000)
        self.assertEqual(p.peak_rx_bps, 3 * 8_000_000)
        self.assertEqual(p.onus_online_avg, 3)
        with tenant_context(self.tenant):
            analytics.rollup_hour(self.hour)   # idempotent
            self.assertEqual(OnuHourly.objects.count(), 3)

    def test_a_sliding_signal_is_flagged_with_a_ticket_before_it_drops(self, _):
        self.olt.refresh_from_db()
        poll_olt(self.olt)
        now = timezone.now()
        with tenant_context(self.tenant):
            onu = Onu.objects.get(onu_index=2)
            steady = Onu.objects.get(onu_index=1)
            for h in range(14 * 24):
                at = now - timedelta(hours=14 * 24 - h)
                OnuHourly.objects.create(tenant=self.tenant, onu=onu, hour=at, samples=12, online_samples=12,
                                         rx_power_avg=-20 - 0.3 * h / 24)
                OnuHourly.objects.create(tenant=self.tenant, onu=steady, hour=at, samples=12, online_samples=12,
                                         rx_power_avg=-21.0)
            out = analytics.trend_sweep(now)
            onu.refresh_from_db()
            steady.refresh_from_db()
            self.assertEqual(out["flagged"], 1)
            self.assertTrue(onu.degrading)
            self.assertFalse(steady.degrading)
            self.assertAlmostEqual(onu.trend_db_per_day, -0.3, places=2)
            t = RepairTicket.objects.get()
            self.assertEqual((t.kind, t.onu_id), ("degrading", onu.pk))
            analytics.trend_sweep(now)
            self.assertEqual(RepairTicket.objects.count(), 1)


@patch("billing.tasks.alert_tasks.notify_admin_task.delay")
@patch("billing.notifications.send_bulk_sms", return_value={"sent": 0})
class ReportAndPortalTests(FiberFixture, TestCase):
    def setUp(self):
        self.make_world()
        self.c, self.s = self.customer_with_device("AA:BB:CC:00:00:09")
        self.sim(clients={"1:3": ["AA:BB:CC:00:00:09"]})
        self.olt.refresh_from_db()
        poll_olt(self.olt)

    def test_overview_counts_ports_and_flags_unlinked_onus(self, *_):
        with tenant_context(self.tenant):
            Onu.objects.update(first_seen=timezone.now() - timedelta(days=3))
            ov = analytics.overview()
        port1 = ov["olts"][0]["ports"][0]
        self.assertEqual((port1["total"], port1["online"]), (6, 6))
        self.assertEqual(port1["unlinked"], 5)
        self.assertEqual(ov["totals"]["total"], 10)

    def test_port_report_attributes_revenue_to_the_port(self, *_):
        with tenant_context(self.tenant), patch("billing.models.notify_customer"), \
                patch("billing.router_service.enable_customer_access"), \
                patch("billing.router_service.safe_connect_router", return_value=object()):
            Payment.objects.create(tenant=self.tenant, customer=self.c, subscription=self.s,
                                   amount=Decimal("140"), method="mpesa", reference="R1")
            now = timezone.now()
            rows = analytics.port_report(now - timedelta(days=1), now + timedelta(minutes=1), now)
        p1 = next(r for r in rows if r["pon_port"] == 1)
        p2 = next(r for r in rows if r["pon_port"] == 2)
        self.assertEqual((p1["customers"], p1["revenue"]), (1, 140.0))
        self.assertEqual(p2["revenue"], 0.0)

    def test_monthly_report_shape(self, *_):
        now = timezone.localtime()
        with tenant_context(self.tenant):
            rep = analytics.monthly_report(now.year, now.month)
        self.assertEqual(rep["totals"]["onus"], 10)
        self.assertIn("signal_now", rep)

    def test_splitter_suggestions_group_by_distance(self, *_):
        with tenant_context(self.tenant):
            Onu.objects.filter(pon_port=1, onu_index__lte=3).update(distance_m=1200)
            Onu.objects.filter(pon_port=1, onu_index__gt=3).update(distance_m=3000)
            sug = splitters.suggest(self.olt, 1)
        sizes = sorted(len(s["onu_ids"]) for s in sug)
        self.assertEqual(sizes, [3, 3])

    def test_line_check_says_healthy_down_or_area_outage(self, *_):
        with tenant_context(self.tenant):
            self.assertEqual(line_check.check(self.c)["status"], "ok")
            Onu.objects.filter(onu_index=3, pon_port=1).update(status="offline", last_down_reason="dying_gasp")
            self.assertEqual(line_check.check(self.c)["status"], "down")
            onu = Onu.objects.get(onu_index=3, pon_port=1)
            o = FiberOutage.objects.create(tenant=self.tenant, olt=self.olt, pon_port=1, scope="port",
                                           cause="fiber_cut", started_at=timezone.now())
            o.onus.add(onu)
            res = line_check.check(self.c)
        self.assertEqual(res["status"], "outage")
        self.assertIn("already knows", res["message"])
        self.assertNotIn("dBm", res["message"])
