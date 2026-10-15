"""
The most clients online at once, per day.

RouterDevice.active_sessions is overwritten by every health probe, so until
RouterDailyPeak the busiest moment of a day was never kept anywhere. These pin
down that the mark only ever rises within a day, starts again at local
midnight, and reaches the dashboard as a gap rather than a zero on days
nobody counted.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from billing.analytics import peak_clients_series
from billing.models import RouterDailyPeak, RouterDevice, Station, Tenant
from billing.tenancy import tenant_context


def _local(y, m, d, h=12, mi=0):
    return timezone.make_aware(datetime(y, m, d, h, mi))


class RouterDailyPeakRecordTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="peak-r", ip_address="10.9.4.1",
                username="u", password="p")

    def _row(self, day):
        with tenant_context(self.tenant):
            return RouterDailyPeak.objects.get(router=self.router, day=day)

    def test_the_mark_only_rises_within_a_day(self):
        RouterDailyPeak.record(self.router, 120, _local(2026, 10, 15, 9))
        RouterDailyPeak.record(self.router, 310, _local(2026, 10, 15, 20))
        RouterDailyPeak.record(self.router, 150, _local(2026, 10, 15, 23))

        row = self._row(_local(2026, 10, 15).date())
        self.assertEqual(row.peak_sessions, 310)
        self.assertEqual(row.peak_at, _local(2026, 10, 15, 20))

    def test_an_equal_count_keeps_when_it_was_first_reached(self):
        RouterDailyPeak.record(self.router, 200, _local(2026, 10, 15, 19))
        RouterDailyPeak.record(self.router, 200, _local(2026, 10, 15, 21))

        self.assertEqual(
            self._row(_local(2026, 10, 15).date()).peak_at,
            _local(2026, 10, 15, 19))

    def test_local_midnight_starts_a_new_day(self):
        RouterDailyPeak.record(self.router, 300, _local(2026, 10, 15, 23, 58))
        RouterDailyPeak.record(self.router, 40, _local(2026, 10, 16, 0, 2))

        self.assertEqual(self._row(_local(2026, 10, 15).date()).peak_sessions, 300)
        self.assertEqual(self._row(_local(2026, 10, 16).date()).peak_sessions, 40)

    def test_the_row_belongs_to_the_routers_operator(self):
        RouterDailyPeak.record(self.router, 5, _local(2026, 10, 15))
        self.assertEqual(
            self._row(_local(2026, 10, 15).date()).tenant_id, self.tenant.id)


class PeakClientsSeriesTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.north = Station.objects.create(tenant=self.tenant, name="North")
            self.south = Station.objects.create(tenant=self.tenant, name="South")
            self.a = RouterDevice.objects.create(
                tenant=self.tenant, name="a", ip_address="10.9.4.2",
                username="u", password="p", station=self.north)
            self.b = RouterDevice.objects.create(
                tenant=self.tenant, name="b", ip_address="10.9.4.3",
                username="u", password="p", station=self.south)

    def _series(self, station=None):
        with tenant_context(self.tenant):
            return peak_clients_series(
                _local(2026, 10, 13, 0), _local(2026, 10, 15, 23), station)

    def test_a_day_nobody_counted_is_a_gap_not_a_zero(self):
        RouterDailyPeak.record(self.a, 90, _local(2026, 10, 15))

        series = self._series()
        self.assertEqual([p["day"] for p in series],
                         ["2026-10-13", "2026-10-14", "2026-10-15"])
        self.assertIsNone(series[0]["peak"])
        self.assertIsNone(series[1]["peak"])
        self.assertEqual(series[2]["peak"], 90)

    def test_the_operator_figure_adds_each_routers_peak(self):
        RouterDailyPeak.record(self.a, 90, _local(2026, 10, 15))
        RouterDailyPeak.record(self.b, 40, _local(2026, 10, 15))

        self.assertEqual(self._series()[-1]["peak"], 130)

    def test_a_station_sees_only_its_own_routers(self):
        RouterDailyPeak.record(self.a, 90, _local(2026, 10, 15))
        RouterDailyPeak.record(self.b, 40, _local(2026, 10, 15))

        self.assertEqual(self._series(self.south.id)[-1]["peak"], 40)

    def test_another_operators_peaks_are_not_counted(self):
        other = Tenant.objects.create(name="Other", slug="other-peaks")
        with tenant_context(other):
            theirs = RouterDevice.objects.create(
                tenant=other, name="theirs", ip_address="10.9.4.9",
                username="u", password="p")
        RouterDailyPeak.record(theirs, 500, _local(2026, 10, 15))
        RouterDailyPeak.record(self.a, 90, _local(2026, 10, 15))

        self.assertEqual(self._series()[-1]["peak"], 90)


class HealthProbeRecordsThePeakTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="probe-r", ip_address="10.9.4.5",
                username="u", password="p", is_active=True)

    def _probe(self, count):
        from billing.tasks.router_health import check_single_router_health

        with patch("billing.router_service.safe_connect_router",
                   return_value=MagicMock()), \
             patch("billing.router_service.count_active_sessions",
                   return_value=count):
            return check_single_router_health(self.router.id)

    def _peaks(self):
        with tenant_context(self.tenant):
            return list(RouterDailyPeak.objects.filter(router=self.router)
                        .values_list("peak_sessions", flat=True))

    def test_a_counted_probe_raises_todays_peak(self):
        self._probe(250)
        self._probe(180)
        self.assertEqual(self._peaks(), [250])

    def test_an_unread_count_records_nothing(self):
        self._probe(None)
        self.assertEqual(self._peaks(), [])

    def test_a_failure_to_record_does_not_condemn_the_router(self):
        with patch.object(RouterDailyPeak, "record",
                          side_effect=RuntimeError("db hiccup")):
            self.assertTrue(self._probe(250))
