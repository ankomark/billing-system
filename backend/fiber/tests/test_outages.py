"""
Polling, linking, incidents, texts and compensation, end to end on a simulated OLT.

The clock is passed in, so "ten minutes later" is a number, not a sleep.
"""

from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from billing.models import Subscription
from billing.tenancy import tenant_context

from fiber.models import (CompensationGrant, FiberOutage, MaintenanceWindow, Onu, OnuCustomer,
                          OnuReading, RepairTicket)
from fiber.services.collector import poll_olt

from .helpers import FiberFixture

SMS = "billing.notifications.send_bulk_sms"
ADMIN = "billing.tasks.alert_tasks.notify_admin_task.delay"
PROVISION = "billing.tasks.provisioning.ensure_customer_access_task.delay"


@patch(PROVISION)
@patch(ADMIN)
class PollingTests(FiberFixture, TestCase):
    def setUp(self):
        self.make_world()
        self.t0 = timezone.now()

    def poll(self, minutes=0):
        self.olt.refresh_from_db()
        return poll_olt(self.olt, now=self.t0 + timedelta(minutes=minutes))

    def onus(self):
        with tenant_context(self.tenant):
            return list(Onu.objects.filter(olt=self.olt).order_by("pon_port", "onu_index"))

    def test_first_poll_creates_every_onu_with_power_and_distance(self, *_):
        out = self.poll()
        self.assertTrue(out["ok"])
        onus = self.onus()
        self.assertEqual(len(onus), 10)
        self.assertTrue(all(o.status == Onu.STATUS_ONLINE for o in onus))
        self.assertTrue(all(o.distance_m and o.rx_power_dbm < 0 for o in onus))
        with tenant_context(self.tenant):
            self.assertEqual(OnuReading.objects.count(), 10)

    def test_traffic_is_a_delta_between_polls(self, *_):
        self.sim(clock=1_000_000, traffic_bps=8_000_000)
        self.poll()
        self.sim(clock=1_000_300, traffic_bps=8_000_000)
        self.poll(5)
        with tenant_context(self.tenant):
            r = OnuReading.objects.filter(rx_delta__isnull=False).first()
        self.assertEqual(r.rx_delta, 300 * 8_000_000 // 8)

    def test_one_missed_poll_is_not_an_outage(self, *_):
        self.poll()
        self.sim(unreachable=True)
        self.poll(5)
        self.olt.refresh_from_db()
        self.assertTrue(self.olt.is_reachable)
        with tenant_context(self.tenant):
            self.assertFalse(FiberOutage.objects.exists())

    def test_two_missed_polls_open_one_olt_outage_and_recovery_closes_it(self, admin, _):
        self.poll()
        self.sim(unreachable=True)
        self.poll(5)
        self.poll(10)
        with tenant_context(self.tenant):
            o = FiberOutage.objects.get()
        self.assertEqual((o.scope, o.cause, o.onus_affected), ("olt", "olt_down", 10))
        admin.assert_called()
        self.sim(unreachable=False)
        self.poll(15)
        o.refresh_from_db()
        self.assertIsNotNone(o.ended_at)

    def test_device_behind_an_onu_links_its_customer(self, *_):
        c, _ = self.customer_with_device("AA:BB:CC:00:00:01")
        self.sim(clients={"1:2": ["aa:bb:cc:00:00:01"]})
        self.poll()
        with tenant_context(self.tenant):
            link = OnuCustomer.objects.get()
        self.assertEqual((link.customer_id, link.onu.position), (c.pk, "PON1:2"))

    def test_an_onu_that_vanishes_from_the_table_counts_as_down(self, *_):
        self.poll()
        self.sim(onus_per_port=[5, 4, 0, 0])
        self.poll(5)
        with tenant_context(self.tenant):
            gone = Onu.objects.get(pon_port=1, onu_index=6)
        self.assertEqual((gone.status, gone.present), (Onu.STATUS_OFFLINE, False))


@patch(PROVISION)
@patch(ADMIN)
@patch(SMS, return_value={"sent": 1, "failed": 0, "errors": []})
class IncidentTests(FiberFixture, TestCase):
    def setUp(self):
        # Settings are cached across tests; a switch flipped in one must not
        # leak into the next.
        cache.clear()
        self.addCleanup(cache.clear)
        self.make_world()
        self.t0 = timezone.now()
        self.c1, self.s1 = self.customer_with_device("AA:BB:CC:00:00:01")
        self.c2, self.s2 = self.customer_with_device("AA:BB:CC:00:00:02")
        self.sim(clients={"1:1": ["AA:BB:CC:00:00:01"], "1:4": ["AA:BB:CC:00:00:02"]})
        self.poll(0)

    def poll(self, minutes):
        self.olt.refresh_from_db()
        return poll_olt(self.olt, now=self.t0 + timedelta(minutes=minutes))

    def outages(self):
        with tenant_context(self.tenant):
            return list(FiberOutage.objects.all())

    def test_a_cut_port_is_one_fiber_incident_with_its_customers(self, sms, admin, _):
        self.sim(port_down=[1], cause="los")
        self.poll(5)
        [o] = self.outages()
        self.assertEqual((o.scope, o.cause, o.pon_port), ("port", "fiber_cut", 1))
        self.assertEqual((o.onus_affected, o.customers_affected), (6, 2))
        self.assertEqual(float(o.revenue_at_risk_per_day), 40.0)  # 2 x 140 / 7
        self.assertIsNotNone(o.nearest_m)
        admin.assert_called()
        with tenant_context(self.tenant):
            self.assertEqual(RepairTicket.objects.filter(outage=o).count(), 1)

    def test_customers_are_texted_only_once_it_has_lasted(self, sms, *_):
        self.sim(port_down=[1], cause="los")
        self.poll(5)
        sms.assert_not_called()
        self.poll(11)
        sms.assert_called_once()
        phones = sorted(p for p, _ in sms.call_args.args[0])
        self.assertEqual(phones, sorted([self.c1.phone, self.c2.phone]))

    def test_restoring_compensates_by_moving_the_whole_window(self, sms, admin, provision):
        before = {s.pk: (s.start_date, s.expiry_date) for s in (self.s1, self.s2)}
        self.sim(port_down=[1], cause="los")
        self.poll(5)
        self.poll(11)
        self.sim(port_down=[])
        self.poll(45)
        [o] = self.outages()
        self.assertIsNotNone(o.ended_at)
        self.assertEqual(o.compensated_count, 2)
        delta = o.ended_at - o.started_at
        for sub in (self.s1, self.s2):
            sub.refresh_from_db()
            self.assertEqual(sub.start_date, before[sub.pk][0] + delta)
            self.assertEqual(sub.expiry_date, before[sub.pk][1] + delta)
        self.assertEqual(provision.call_count, 2)
        self.assertEqual(sms.call_count, 2)  # outage, then restored
        self.assertIn("added", sms.call_args.args[0][0][1])
        with tenant_context(self.tenant):
            self.assertEqual(RepairTicket.objects.get().status, RepairTicket.STATUS_RESOLVED)

    def test_compensation_is_never_given_twice(self, *_):
        from fiber.services.compensation import for_outage

        self.sim(port_down=[1], cause="los")
        self.poll(5)
        self.sim(port_down=[])
        self.poll(40)
        [o] = self.outages()
        o.compensated_at = None
        o.save()
        with tenant_context(self.tenant):
            self.assertEqual(for_outage(o, timezone.now()), 0)
            self.assertEqual(CompensationGrant.objects.count(), 2)

    def test_a_short_blip_is_not_compensated(self, *_):
        self.sim(port_down=[1], cause="los")
        self.poll(5)
        self.sim(port_down=[])
        self.poll(9)
        [o] = self.outages()
        self.assertEqual(o.compensated_count, 0)

    def test_an_area_power_cut_is_not_ours(self, sms, admin, provision):
        self.sim(port_down=[1], cause="dying_gasp")
        self.poll(5)
        self.poll(15)
        self.sim(port_down=[])
        self.poll(60)
        [o] = self.outages()
        self.assertEqual(o.cause, "power_cut")
        sms.assert_not_called()
        provision.assert_not_called()
        with tenant_context(self.tenant):
            self.assertFalse(RepairTicket.objects.exists())

    def test_one_house_losing_power_is_the_customers_own(self, sms, *_):
        self.sim(down=["1:1"], cause="dying_gasp")
        self.poll(5)
        self.poll(40)
        [o] = self.outages()
        self.assertEqual((o.scope, o.cause), ("onu", "customer_power"))
        sms.assert_not_called()

    def test_houses_dropping_minutes_apart_merge_into_one_cut(self, *_):
        self.sim(down=["1:1"], cause="los")
        self.poll(5)
        self.sim(down=["1:1", "1:2"], cause="los")
        self.poll(7)
        self.sim(down=["1:1", "1:2", "1:3"], cause="los")
        self.poll(9)
        outs = self.outages()
        self.assertEqual(len(outs), 1)
        self.assertEqual((outs[0].scope, outs[0].onus_affected), ("port", 3))
        self.assertEqual(outs[0].started_at, self.t0 + timedelta(minutes=5))

    def test_a_single_cut_drop_cable_gets_a_ticket_after_half_an_hour(self, *_):
        self.sim(down=["2:1"], cause="los")
        self.poll(5)
        with tenant_context(self.tenant):
            self.assertFalse(RepairTicket.objects.exists())
        self.poll(40)
        with tenant_context(self.tenant):
            self.assertEqual(RepairTicket.objects.count(), 1)

    def test_announced_maintenance_is_not_a_fault(self, sms, admin, provision):
        with tenant_context(self.tenant):
            MaintenanceWindow.objects.create(
                tenant=self.tenant, olt=self.olt, pon_port=1, reason="splicing",
                starts_at=self.t0, ends_at=self.t0 + timedelta(hours=2), compensate=False)
        self.sim(port_down=[1], cause="los")
        self.poll(5)
        self.poll(20)
        [o] = self.outages()
        self.assertEqual(o.cause, "maintenance")
        sms.assert_not_called()

    @override_settings(DEBUG=False)
    def test_compensation_can_be_switched_off(self, sms, admin, provision):
        from billing.models import SystemSetting

        with tenant_context(self.tenant):
            SystemSetting.objects.create(tenant=self.tenant, key="FIBER_COMPENSATE", value="false")
        cache.clear()
        self.sim(port_down=[1], cause="los")
        self.poll(5)
        self.sim(port_down=[])
        self.poll(60)
        provision.assert_not_called()
        self.s1.refresh_from_db()
        self.assertEqual(Subscription.objects.get(pk=self.s1.pk).expiry_date, self.s1.expiry_date)
