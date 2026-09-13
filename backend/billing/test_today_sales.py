"""
What has sold since midnight, package by package.

The range panels answer "how is the month going". This answers "how is today
going", which is the question actually asked at 11am with three hundred people
on the network and takings that look thin -- and concurrency is the thing most
likely to mislead about it. On 2026-09-13, 247 customers were connected and
only 81 had bought anything that day; the rest were on weekly and monthly
packages purchased earlier and owed nothing.

Midnight local, not a rolling 24 hours. An operator comparing today with
yesterday means the calendar day they are standing in, and a rolling window
would move the goalposts every time they looked at it.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from billing.analytics import today_by_package
from billing.models import (
    Customer, Invoice, Package, Payment, RouterDevice, Station, Subscription,
    Tenant,
)
from billing.tenancy import tenant_context


class _TodayBase(TestCase):
    """Fixture only. Held apart so each suite below runs its own tests
    once -- subclassing a TestCase that has tests re-runs every one of
    them under the subclass's name, which reads as coverage and is not."""

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="today-r", ip_address="10.9.1.9",
                username="u", password="p")
            self.three = Package.objects.create(
                tenant=self.tenant, name="3hrs - 3GB", download_speed=5,
                upload_speed=2, price=Decimal("10.00"), duration_value=3,
                duration_unit="hours", data_cap_mb=3000, is_hotspot=True)
            self.week = Package.objects.create(
                tenant=self.tenant, name="1 week - 20GB", download_speed=5,
                upload_speed=2, price=Decimal("180.00"), duration_value=1,
                duration_unit="weeks", data_cap_mb=20000, is_hotspot=True)

    def _buy(self, package, *, when=None, customer=None, method="mpesa",
             amount=None):
        with tenant_context(self.tenant):
            c = customer or Customer.objects.create(
                tenant=self.tenant, full_name=f"B{Customer.objects.count()}",
                phone=f"2547001200{Customer.objects.count():02d}",
                connection_type="hotspot", router=self.router, status="active")
            sub = Subscription.objects.create(
                customer=c, package=package, status="active")
            Invoice.objects.filter(subscription=sub).update(
                payment_status="paid")
            with patch("billing.models.notify_customer"), \
                 patch("billing.router_service.enable_customer_access"), \
                 patch("billing.router_service.safe_connect_router",
                       return_value=object()):
                p = Payment.objects.create(
                    tenant=self.tenant, customer=c, subscription=sub,
                    amount=package.price if amount is None else amount,
                    method=method, reference=f"R{Payment.objects.count()}")
            if when is not None:
                Payment.objects.filter(pk=p.pk).update(paid_at=when)
        return c, p


class TodaysSales(_TodayBase):
    # ------------------------------------------------------------- the shape

    def test_a_package_reports_revenue_volume_and_buyers(self):
        c, _ = self._buy(self.three)
        self._buy(self.three, customer=c)      # same person, twice
        self._buy(self.week)

        data = today_by_package()
        rows = {p["name"]: p for p in data["packages"]}

        self.assertEqual(rows["3hrs - 3GB"]["purchases"], 2)
        self.assertEqual(rows["3hrs - 3GB"]["customers"], 1)
        self.assertEqual(rows["3hrs - 3GB"]["revenue"], 20.0)
        self.assertEqual(rows["1 week - 20GB"]["revenue"], 180.0)

    def test_the_totals_are_the_sum_of_the_packages(self):
        self._buy(self.three)
        self._buy(self.week)

        data = today_by_package()

        self.assertEqual(data["revenue"], 190.0)
        self.assertEqual(data["purchases"], 2)
        self.assertEqual(
            data["revenue"], sum(p["revenue"] for p in data["packages"]))

    def test_one_buyer_of_two_packages_counts_once_overall(self):
        """
        The headline is people, not rows. Counting a customer once per package
        would report more buyers than exist.
        """
        c, _ = self._buy(self.three)
        self._buy(self.week, customer=c)

        self.assertEqual(today_by_package()["customers"], 1)

    def test_shares_are_computed_here_not_in_the_browser(self):
        """So the table and the pie cannot disagree about the same number."""
        self._buy(self.week)                    # 180
        self._buy(self.three)                   # 10  -> 94.7% / 5.3%

        rows = {p["name"]: p for p in today_by_package()["packages"]}

        self.assertEqual(rows["1 week - 20GB"]["share"], 94.7)
        self.assertEqual(rows["3hrs - 3GB"]["share"], 5.3)

    def test_ordered_by_revenue(self):
        self._buy(self.three)
        self._buy(self.week)

        names = [p["name"] for p in today_by_package()["packages"]]

        self.assertEqual(names[0], "1 week - 20GB")

    # -------------------------------------------------------- the day itself

    def test_yesterday_is_not_counted(self):
        """The whole point of resetting at midnight."""
        yesterday = timezone.localtime(timezone.now()).replace(
            hour=23, minute=30) - timedelta(days=1)
        self._buy(self.week, when=yesterday)
        self._buy(self.three)

        data = today_by_package()

        self.assertEqual(data["revenue"], 10.0)
        self.assertEqual(data["purchases"], 1)

    def test_a_sale_a_minute_after_midnight_is_counted(self):
        just_after = timezone.localtime(timezone.now()).replace(
            hour=0, minute=1, second=0, microsecond=0)
        self._buy(self.three, when=just_after)

        self.assertEqual(today_by_package()["purchases"], 1)

    def test_an_empty_day_is_zeroes_not_an_error(self):
        """
        Every day starts here, and a panel that divides by nothing at 00:05
        breaks the dashboard for whoever opens it first.
        """
        data = today_by_package()

        self.assertEqual(data["revenue"], 0)
        self.assertEqual(data["purchases"], 0)
        self.assertEqual(data["packages"], [])

    # ------------------------------------------------------------ the extras

    def test_a_comp_is_a_sale_at_zero_not_a_hidden_one(self):
        """
        Free internet given away is still a package issued. Dropping comps
        would have hidden the nine granted to one customer on 2026-09-12
        exactly where somebody would have noticed them.
        """
        self._buy(self.week, method="comp", amount=Decimal("0.00"))

        data = today_by_package()
        rows = {p["name"]: p for p in data["packages"]}

        self.assertEqual(rows["1 week - 20GB"]["purchases"], 1)
        self.assertEqual(rows["1 week - 20GB"]["revenue"], 0.0)
        self.assertEqual(rows["1 week - 20GB"]["share"], 0.0)

    def test_it_carries_the_window_it_used(self):
        """
        So the panel can say "since midnight, as of 11:04" rather than leaving
        the reader to assume which hours they are looking at.
        """
        data = today_by_package()

        self.assertIn("since", data)
        self.assertIn("as_of", data)
        self.assertTrue(data["since"] <= data["as_of"])

    def test_it_can_be_narrowed_to_one_station(self):
        with tenant_context(self.tenant):
            station = Station.objects.create(
                tenant=self.tenant, name="Kilifi", code="KLF2")
            other = RouterDevice.objects.create(
                tenant=self.tenant, name="today-r2", ip_address="10.9.1.8",
                username="u", password="p", station=station)
        c, _ = self._buy(self.three)
        with tenant_context(self.tenant):
            Customer.objects.filter(pk=c.pk).update(router=other)

        self.assertEqual(today_by_package(station=station.id)["purchases"], 1)
        self.assertEqual(today_by_package()["purchases"], 1)


class WhatWasGivenAway(_TodayBase):
    """
    Comps beside the buyers, with the operator's own reason.

    A count alone answers the smaller half: three free weeks could be three
    apologies for an outage or three people let on for nothing, and the word
    the operator typed at the counter is what separates them. Per package,
    because a comped month and a comped three hours are not the same giveaway.
    """

    def test_a_comp_is_counted_against_its_package(self):
        self._buy(self.week, method="comp", amount=Decimal("0.00"))
        self._buy(self.week)

        rows = {p["name"]: p for p in today_by_package()["packages"]}

        self.assertEqual(rows["1 week - 20GB"]["purchases"], 2)
        self.assertEqual(rows["1 week - 20GB"]["comps"], 1)

    def test_a_package_nobody_comped_reports_none(self):
        self._buy(self.three)

        rows = {p["name"]: p for p in today_by_package()["packages"]}

        self.assertEqual(rows["3hrs - 3GB"]["comps"], 0)
        self.assertEqual(rows["3hrs - 3GB"]["comp_reasons"], [])

    def test_the_reason_comes_through(self):
        with tenant_context(self.tenant):
            c, p = self._buy(self.week, method="comp", amount=Decimal("0.00"))
            Payment.objects.filter(pk=p.pk).update(reference="Router fail")

        rows = {p["name"]: p for p in today_by_package()["packages"]}

        self.assertEqual(
            rows["1 week - 20GB"]["comp_reasons"],
            [{"reason": "Router fail", "count": 1}])

    def test_reasons_are_grouped_and_counted(self):
        """
        Three apologies for one outage should read as one reason given three
        times, not as three separate notes to work through.
        """
        with tenant_context(self.tenant):
            for _ in range(3):
                _, p = self._buy(self.week, method="comp",
                                 amount=Decimal("0.00"))
                Payment.objects.filter(pk=p.pk).update(reference="Refund")
            _, p = self._buy(self.week, method="comp", amount=Decimal("0.00"))
            Payment.objects.filter(pk=p.pk).update(reference="trial")

        rows = {p["name"]: p for p in today_by_package()["packages"]}
        reasons = {r["reason"]: r["count"]
                   for r in rows["1 week - 20GB"]["comp_reasons"]}

        self.assertEqual(rows["1 week - 20GB"]["comps"], 4)
        self.assertEqual(reasons, {"Refund": 3, "trial": 1})

    def test_a_comp_with_no_reason_is_counted_but_not_invented(self):
        """
        Comps issued before the form required a reason have none. Showing
        "unknown" would read as a word somebody actually typed.
        """
        with tenant_context(self.tenant):
            _, p = self._buy(self.week, method="comp", amount=Decimal("0.00"))
            Payment.objects.filter(pk=p.pk).update(reference="")

        rows = {p["name"]: p for p in today_by_package()["packages"]}

        self.assertEqual(rows["1 week - 20GB"]["comps"], 1)
        self.assertEqual(rows["1 week - 20GB"]["comp_reasons"], [])

    def test_the_day_carries_a_total_given_away(self):
        self._buy(self.week, method="comp", amount=Decimal("0.00"))
        self._buy(self.three, method="comp", amount=Decimal("0.00"))
        self._buy(self.three)

        self.assertEqual(today_by_package()["comps"], 2)

    def test_a_paid_purchase_is_never_counted_as_given(self):
        self._buy(self.week)

        rows = {p["name"]: p for p in today_by_package()["packages"]}

        self.assertEqual(rows["1 week - 20GB"]["comps"], 0)


class WhyAFullNetworkCanBeAQuietTill(_TodayBase):
    """
    The line the panel exists for.

    On 2026-09-13 at 10:54 there were 341 sessions and KSh 1,865 taken, which
    reads as a collapse until you can see that 166 of the 247 people connected
    had bought on earlier days and owed nothing today. Selling week and month
    bundles makes concurrency and daily revenue come apart permanently, and an
    operator should not rediscover that every morning.
    """

    def _router_count(self, n, *, age=timedelta(minutes=1)):
        with tenant_context(self.tenant):
            RouterDevice.objects.filter(pk=self.router.pk).update(
                active_sessions=n, active_sessions_at=timezone.now() - age)

    def test_it_counts_everybody_holding_a_live_package(self):
        """Not just today's buyers -- that is the whole point of the line."""
        yesterday = timezone.now() - timedelta(days=1)
        self._buy(self.week, when=yesterday)
        self._buy(self.three)

        data = today_by_package()

        self.assertEqual(data["covered"], 2)
        self.assertEqual(data["bought_today"], 1)

    def test_sessions_come_from_the_cached_count(self):
        """
        Never a live probe. ActiveClientsView says why in its own docstring: a
        dashboard that asks the routers blocks a worker per router on every
        page load.
        """
        self._router_count(341)

        self.assertEqual(today_by_package()["sessions"], 341)

    def test_a_stale_count_is_dropped_rather_than_shown(self):
        """
        A wrong number here would discredit the two beside it, which are
        exact. Six minutes is the health sweep's own cycle.
        """
        self._router_count(341, age=timedelta(minutes=30))

        self.assertIsNone(today_by_package()["sessions"])

    def test_a_router_never_counted_contributes_nothing(self):
        with tenant_context(self.tenant):
            RouterDevice.objects.filter(pk=self.router.pk).update(
                active_sessions=None, active_sessions_at=None)

        self.assertIsNone(today_by_package()["sessions"])

    def test_counts_are_summed_across_routers(self):
        self._router_count(170)
        with tenant_context(self.tenant):
            RouterDevice.objects.create(
                tenant=self.tenant, name="today-r3", ip_address="10.9.1.7",
                username="u", password="p", is_active=True,
                active_sessions=171, active_sessions_at=timezone.now())

        self.assertEqual(today_by_package()["sessions"], 341)

    def test_an_unpaid_subscription_is_not_counted_as_covered(self):
        with tenant_context(self.tenant):
            c = Customer.objects.create(
                tenant=self.tenant, full_name="Abandoned", phone="254700130001",
                connection_type="hotspot", router=self.router, status="active")
            Subscription.objects.create(
                customer=c, package=self.week, status="active")

        self.assertEqual(today_by_package()["covered"], 0)
