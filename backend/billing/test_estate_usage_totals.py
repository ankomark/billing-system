"""
What the network carried today, this week, this month, this year.

Usage lives in two places and the whole difficulty is stitching them without
counting a day twice. Whole days are folded into UsageRecord by the nightly
rollup; anything it has not reached is only in the five-minute deltas. The
rollup alone loses today entirely; the raw rows alone lose every day past the
90-day retention.

So this reads which days the rollup has ACTUALLY covered rather than which it
ought to have -- the lesson usage_since already records, having found that
assuming otherwise "silently dropped a whole day from everybody's total for
eighty minutes every night", because the rollup runs at 01:20 and before then
yesterday has none.

rx_bytes is download and tx_bytes is upload. Worth pinning: it was the other
way round until migration 0064, when production held 63GB of "download"
against 718GB of "upload".
"""

import datetime as dt
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from billing.models import (
    Customer, HotspotUsageRecord, Package, PPPoEUsageRecord, RouterDevice,
    Station, Tenant, UsageRecord,
)
from billing.services.usage import estate_usage_totals
from billing.tenancy import tenant_context

GB = 1024 ** 3


class EstateUsageTotals(TestCase):

    # A fixed Wednesday, so the calendar windows do not depend on the day the
    # suite happens to run.
    #
    # These tests put a rolled-up day on "yesterday" and assert it lands in
    # this week. On a Monday the week starts today and yesterday belongs to
    # the previous one, so five of them failed on 2026-09-14 with the code
    # behaving exactly as intended. A calendar-boundary test that only holds
    # six days in seven is not testing what it claims to.
    FIXED_NOW = dt.datetime(2026, 9, 16, 14, 30)

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        self.now = timezone.make_aware(
            self.FIXED_NOW, timezone.get_current_timezone())
        self.today = timezone.localdate(self.now)
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="usage-r", ip_address="10.9.0.50",
                username="u", password="p")
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Heavy User",
                phone="254700140001", connection_type="hotspot",
                router=self.router, status="active")

    def _rolled(self, day, *, down, up, customer=None):
        """A finished day, as the nightly rollup leaves it."""
        with tenant_context(self.tenant):
            return UsageRecord.objects.create(
                tenant=self.tenant, customer=customer or self.customer,
                connection_type="hotspot", date=day,
                rx_bytes=down, tx_bytes=up)

    def _raw(self, when, *, down, up, model=HotspotUsageRecord):
        """A five-minute delta, as a collector leaves it."""
        with tenant_context(self.tenant):
            return model.objects.create(
                tenant=self.tenant, customer=self.customer,
                period_start=when, period_end=when + dt.timedelta(minutes=5),
                download_bytes=down, upload_bytes=up)

    def _at(self, day, hour=12):
        return timezone.make_aware(
            dt.datetime.combine(day, dt.time(hour, 0)),
            timezone.get_current_timezone())

    # ------------------------------------------------------- today vs rollup

    def test_today_comes_from_the_raw_deltas(self):
        """The rollup never covers today -- it runs at 01:20 for finished days."""
        self._raw(self._at(self.today), down=3 * GB, up=1 * GB)

        totals = estate_usage_totals(now=self.now)

        self.assertEqual(totals["today"]["download"], 3 * GB)
        self.assertEqual(totals["today"]["upload"], 1 * GB)
        self.assertEqual(totals["today"]["total"], 4 * GB)

    def test_a_finished_day_comes_from_the_rollup(self):
        yesterday = self.today - dt.timedelta(days=1)
        self._rolled(yesterday, down=5 * GB, up=2 * GB)

        totals = estate_usage_totals(now=self.now)

        self.assertEqual(totals["today"]["total"], 0)
        self.assertEqual(totals["week"]["total"], 7 * GB)

    def test_a_day_is_never_counted_from_both(self):
        """
        The one mistake that would inflate every figure at once: raw rows for a
        day the rollup has already folded still exist until pruned, ninety days
        later.
        """
        yesterday = self.today - dt.timedelta(days=1)
        self._rolled(yesterday, down=5 * GB, up=2 * GB)
        self._raw(self._at(yesterday), down=5 * GB, up=2 * GB)

        totals = estate_usage_totals(now=self.now)

        self.assertEqual(totals["week"]["total"], 7 * GB)

    def test_a_day_the_rollup_missed_still_counts(self):
        """
        Between midnight and 01:20 yesterday has no rollup row. Assuming it
        does is what dropped a whole day from every total for eighty minutes
        each night.
        """
        yesterday = self.today - dt.timedelta(days=1)
        self._raw(self._at(yesterday), down=6 * GB, up=1 * GB)

        totals = estate_usage_totals(now=self.now)

        self.assertEqual(totals["week"]["total"], 7 * GB)

    def test_the_raw_deltas_are_read_from_the_oldest_one_not_from_january(self):
        """
        Every day before the first delta is "uncovered" by the rollup too.
        Starting the raw query at the earliest of those read every raw row on
        every request -- 1.7 s in production. It must start at the oldest delta
        and still count a missed day after it.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        yesterday = self.today - dt.timedelta(days=1)
        three_ago = self.today - dt.timedelta(days=3)
        self._raw(self._at(three_ago), down=2 * GB, up=0)    # oldest delta; rollup missed it
        self._raw(self._at(yesterday), down=6 * GB, up=1 * GB)
        self._rolled(self.today - dt.timedelta(days=2), down=1 * GB, up=0)

        with CaptureQueriesContext(connection) as q:
            totals = estate_usage_totals(now=self.now)

        self.assertEqual(totals["year"]["total"], 10 * GB)
        raw_sums = [x["sql"] for x in q.captured_queries
                    if "SUM" in x["sql"].upper() and "usagerecord" in x["sql"].lower()
                    and "period_start" in x["sql"]]
        # Local midnight of the oldest delta's day, as the database sees it (UTC).
        bound = timezone.make_aware(
            dt.datetime.combine(three_ago, dt.time.min), timezone.get_current_timezone()
        ).astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
        self.assertTrue(raw_sums)
        for sql in raw_sums:
            self.assertNotIn(f"{self.today.year - 1}-12-31 21:00", sql)   # 1 Jan local
            self.assertIn(bound, sql)

    # ------------------------------------------------------------- windows

    def test_the_windows_are_calendar_not_rolling(self):
        """
        "This month" means since the 1st, the way a bill means it. A rolling
        thirty days would move the boundary every time it was looked at.
        """
        totals = estate_usage_totals(now=self.now)

        self.assertEqual(totals["month"]["since"],
                         self.today.replace(day=1).isoformat())
        self.assertEqual(totals["year"]["since"],
                         self.today.replace(month=1, day=1).isoformat())
        self.assertEqual(totals["today"]["since"], self.today.isoformat())

    def test_the_week_starts_on_monday(self):
        totals = estate_usage_totals(now=self.now)
        start = dt.date.fromisoformat(totals["week"]["since"])

        self.assertEqual(start.weekday(), 0)
        self.assertLessEqual(start, self.today)

    def test_the_windows_nest(self):
        """Every window contains the shorter ones inside it."""
        self._raw(self._at(self.today), down=1 * GB, up=0)
        first_of_month = self.today.replace(day=1)
        if first_of_month < self.today:
            self._rolled(first_of_month, down=2 * GB, up=0)

        t = estate_usage_totals(now=self.now)

        self.assertLessEqual(t["today"]["total"], t["week"]["total"])
        self.assertLessEqual(t["week"]["total"], t["month"]["total"])
        self.assertLessEqual(t["month"]["total"], t["year"]["total"])

    def test_last_year_is_not_counted(self):
        self._rolled(self.today.replace(month=1, day=1) - dt.timedelta(days=1),
                     down=99 * GB, up=99 * GB)

        self.assertEqual(estate_usage_totals(now=self.now)["year"]["total"], 0)

    # ------------------------------------------------------------- the rest

    def test_pppoe_and_hotspot_are_both_counted(self):
        self._raw(self._at(self.today), down=1 * GB, up=0)
        self._raw(self._at(self.today), down=2 * GB, up=0,
                  model=PPPoEUsageRecord)

        self.assertEqual(estate_usage_totals(now=self.now)["today"]["download"], 3 * GB)

    def test_download_and_upload_stay_the_right_way_round(self):
        """
        rx is download, tx is upload. The collector had these reversed from the
        first commit until migration 0064 put them back.
        """
        yesterday = self.today - dt.timedelta(days=1)
        self._rolled(yesterday, down=10 * GB, up=1 * GB)

        week = estate_usage_totals(now=self.now)["week"]

        self.assertEqual(week["download"], 10 * GB)
        self.assertEqual(week["upload"], 1 * GB)

    def test_an_empty_estate_is_zeroes(self):
        totals = estate_usage_totals(now=self.now)

        for window in ("today", "week", "month", "year"):
            self.assertEqual(totals[window]["total"], 0)
            self.assertEqual(totals[window]["download"], 0)

    def test_it_can_be_narrowed_to_one_station(self):
        with tenant_context(self.tenant):
            station = Station.objects.create(
                tenant=self.tenant, name="Mtwapa", code="MTW9")
            other_router = RouterDevice.objects.create(
                tenant=self.tenant, name="usage-r2", ip_address="10.9.0.51",
                username="u", password="p", station=station)
            elsewhere = Customer.objects.create(
                tenant=self.tenant, full_name="Elsewhere",
                phone="254700140002", connection_type="hotspot",
                router=other_router, status="active")

        yesterday = self.today - dt.timedelta(days=1)
        self._rolled(yesterday, down=4 * GB, up=0)
        self._rolled(yesterday, down=6 * GB, up=0, customer=elsewhere)

        self.assertEqual(estate_usage_totals(now=self.now)["week"]["download"], 10 * GB)
        self.assertEqual(
            estate_usage_totals(station=station.id, now=self.now)["week"]["download"], 6 * GB)


class TheDailyUsageGraph(EstateUsageTotals):
    """
    The dashboard's daily graph, read the same way as the totals. Inherits the
    fixtures above (and re-runs those tests, which is cheap).
    """

    def graph(self, days=7):
        from unittest.mock import patch

        from rest_framework.test import APIClient

        from billing.auth_tokens import TenantTokenObtainPairSerializer
        from billing.tests import make_admin

        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION="Bearer " + str(
            TenantTokenObtainPairSerializer.get_token(make_admin("graph_admin")).access_token))
        with patch("django.utils.timezone.now", return_value=self.now):
            resp = api.get("/api/admin/usage/daily/", {"days": days})
        self.assertEqual(resp.status_code, 200)
        return {str(r["day"]): r["download_gb"] + r["upload_gb"] for r in resp.data}

    def test_finished_days_come_from_the_rollup_and_today_from_the_deltas(self):
        yesterday = self.today - dt.timedelta(days=1)
        self._rolled(yesterday, down=5 * GB, up=2 * GB)
        self._raw(self._at(yesterday), down=5 * GB, up=2 * GB)   # never counted twice
        self._raw(self._at(self.today), down=3 * GB, up=1 * GB)
        days = self.graph()
        self.assertEqual(days[str(yesterday)], 7)
        self.assertEqual(days[str(self.today)], 4)

    def test_the_oldest_bar_is_a_whole_day(self):
        """Not 7 x 24 hours back from now, which cut the first day short."""
        first = self.today - dt.timedelta(days=6)
        self._raw(self._at(first, hour=1), down=1 * GB, up=0)    # early morning
        self.assertEqual(self.graph()[str(first)], 1)

    def test_a_long_range_reaches_past_the_raw_retention(self):
        old = self.today - dt.timedelta(days=200)
        self._rolled(old, down=9 * GB, up=0)
        self.assertEqual(self.graph(days=365)[str(old)], 9)
