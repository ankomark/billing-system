"""
The dashboard's memory of months that have closed.

At midnight on the 1st, "This month" went back to zero and the month that had
just ended was nowhere on the dashboard. These pin the figures that replace
that: last month in the headline, and a row per month below.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from billing.auth_tokens import TenantTokenObtainPairSerializer
from billing.models import Customer, Payment, Subscription, Tenant
from billing.reports import _add_months, monthly_revenue, revenue_summary, yearly_revenue
from billing.tenancy import tenant_context
from billing.tests import make_admin, make_package, make_router


def local(day, hour=12, minute=0):
    return timezone.make_aware(datetime(day.year, day.month, day.day, hour, minute))


class MonthlyEarnings(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        self.this_month = timezone.localdate().replace(day=1)
        self.last_month = _add_months(self.this_month, -1)
        with patch("billing.models.notify_customer"):
            router = make_router()
            package = make_package()
            self.alice = Customer.objects.create(
                tenant=self.tenant, full_name="Alice", phone="254711000001",
                connection_type="pppoe", router=router)
            self.bob = Customer.objects.create(
                tenant=self.tenant, full_name="Bob", phone="254711000002",
                connection_type="pppoe", router=router)
            self.sub_a = Subscription.objects.create(customer=self.alice, package=package)
            self.sub_b = Subscription.objects.create(customer=self.bob, package=package)
        # Subscription creation may record payments of its own; start clean.
        Payment.objects.all_tenants().all().delete()

    def pay(self, customer, amount, when, method="mpesa"):
        """A payment at `when`, without Payment.save()'s provisioning."""
        sub = self.sub_a if customer is self.alice else self.sub_b
        p = Payment.objects.bulk_create([Payment(
            tenant=self.tenant, customer=customer, subscription=sub,
            amount=Decimal(amount), method=method)])[0]
        Payment.objects.all_tenants().filter(pk=p.pk).update(paid_at=when)

    def row(self, month):
        rows = monthly_revenue(12)
        return next(r for r in rows if r["month"] == f"{month.year}-{month.month:02d}")

    def test_last_month_is_in_the_headline(self):
        self.pay(self.alice, "500", local(self.last_month.replace(day=10)))
        self.pay(self.alice, "300", local(self.this_month))
        with tenant_context(self.tenant):
            summary = revenue_summary()
        self.assertEqual(Decimal(str(summary["last_month"])), Decimal("500"))

    def test_every_month_has_a_row_newest_first(self):
        with tenant_context(self.tenant):
            rows = monthly_revenue(12)
        self.assertEqual(len(rows), 12)
        self.assertTrue(rows[0]["in_progress"])
        self.assertFalse(any(r["in_progress"] for r in rows[1:]))
        self.assertEqual(rows[0]["month"], f"{self.this_month:%Y-%m}")
        self.assertEqual(rows[1]["month"], f"{self.last_month:%Y-%m}")
        self.assertEqual(rows[5]["revenue"], 0)       # an empty month is still a row

    def test_a_month_adds_up_by_method_and_counts_people_once(self):
        day = self.last_month.replace(day=5)
        self.pay(self.alice, "500", local(day), "mpesa")
        self.pay(self.alice, "200", local(day), "cash")
        self.pay(self.bob, "1000", local(day), "bank")
        with tenant_context(self.tenant):
            r = self.row(self.last_month)
        self.assertEqual(r["revenue"], 1700)
        self.assertEqual(r["payments"], 3)
        self.assertEqual(r["customers"], 2)
        self.assertEqual(r["by_method"], {"mpesa": 500, "cash": 200, "bank": 1000})

    def test_free_access_is_counted_but_earns_nothing(self):
        day = self.last_month.replace(day=5)
        self.pay(self.alice, "500", local(day))
        self.pay(self.bob, "0", local(day), "comp")
        with tenant_context(self.tenant):
            r = self.row(self.last_month)
        self.assertEqual(r["revenue"], 500)
        self.assertEqual(r["payments"], 1)
        self.assertEqual(r["customers"], 1)
        self.assertEqual(r["comps"], 1)

    def test_just_after_midnight_on_the_first_is_the_new_month(self):
        """00:30 Nairobi on the 1st is still the previous day in UTC."""
        self.pay(self.alice, "400", local(self.this_month, hour=0, minute=30))
        with tenant_context(self.tenant):
            self.assertEqual(self.row(self.this_month)["revenue"], 400)
            self.assertEqual(self.row(self.last_month)["revenue"], 0)

    def test_a_closed_month_is_compared_with_the_one_before(self):
        two_ago = _add_months(self.this_month, -2)
        self.pay(self.alice, "1000", local(two_ago.replace(day=3)))
        self.pay(self.alice, "1500", local(self.last_month.replace(day=3)))
        with tenant_context(self.tenant):
            r = self.row(self.last_month)
        self.assertEqual(r["change"], 50.0)
        self.assertEqual(r["change_against"], "previous month")

    def test_the_month_in_progress_is_compared_with_the_same_point(self):
        """
        Up to the same day AND time of day. Whole days compared a morning
        with a full day, so the month in progress always looked down.
        """
        now = timezone.now()
        elapsed = now - local(self.this_month, hour=0)
        last_start = local(self.last_month, hour=0)
        # Just before and just after "this point" last month.
        self.pay(self.alice, "100", last_start + elapsed - timedelta(minutes=5))
        self.pay(self.alice, "9999", last_start + elapsed + timedelta(minutes=5))
        self.pay(self.alice, "150", now - timedelta(minutes=1))
        with tenant_context(self.tenant):
            r = monthly_revenue(12)[0]
        if last_start + elapsed + timedelta(minutes=5) < local(self.this_month, hour=0):
            self.assertEqual(r["change"], 50.0)
        self.assertEqual(r["change_against"], "same point last month")

    def test_one_operator_never_sees_anothers_months(self):
        other = Tenant.objects.create(name="Other ISP", slug="other-monthly")
        with tenant_context(other):
            router = make_router(name="o-r", ip="10.9.9.9", tenant=other)
            theirs = Customer.objects.create(
                tenant=other, full_name="Theirs", phone="254711000009",
                connection_type="pppoe", router=router)
            with patch("billing.models.notify_customer"):
                sub = Subscription.objects.create(
                    customer=theirs, package=make_package(name="o-pkg", tenant=other))
        Payment.objects.all_tenants().filter(tenant=other).delete()
        p = Payment.objects.bulk_create([Payment(
            tenant=other, customer=theirs, subscription=sub,
            amount=Decimal("7777"), method="mpesa")])[0]
        Payment.objects.all_tenants().filter(pk=p.pk).update(
            paid_at=local(self.last_month.replace(day=2)))
        self.pay(self.alice, "500", local(self.last_month.replace(day=2)))

        client = APIClient()
        token = TenantTokenObtainPairSerializer.get_token(make_admin("monthly_admin")).access_token
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        resp = client.get("/api/reports/revenue/monthly/?months=3")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["results"]), 3)
        self.assertEqual(resp.data["results"][1]["revenue"], 500)

    def test_the_window_is_bounded(self):
        with tenant_context(self.tenant):
            self.assertEqual(len(monthly_revenue(500)), 36)
            self.assertEqual(len(monthly_revenue(0)), 1)


class YearlyEarnings(MonthlyEarnings):
    """
    The year-level record. Inherits the fixtures above (and re-runs their
    tests, which is harmless and cheap).
    """

    def setUp(self):
        super().setUp()
        self.this_year = timezone.localdate().replace(month=1, day=1)
        self.last_year = self.this_year.replace(year=self.this_year.year - 1)

    def year_row(self, year):
        with tenant_context(self.tenant):
            return next((r for r in yearly_revenue() if r["year"] == year), None)

    def test_the_year_in_progress_always_has_a_row(self):
        with tenant_context(self.tenant):
            rows = yearly_revenue()
        self.assertEqual(rows[0]["year"], self.this_year.year)
        self.assertTrue(rows[0]["in_progress"])
        self.assertEqual(rows[0]["revenue"], 0)

    def test_a_closed_year_stays_with_its_total(self):
        self.pay(self.alice, "1000", local(self.last_year.replace(month=3, day=1)))
        self.pay(self.bob, "2500", local(self.last_year.replace(month=12, day=31), hour=23))
        r = self.year_row(self.last_year.year)
        self.assertEqual(r["revenue"], 3500)
        self.assertEqual(r["payments"], 2)
        self.assertEqual(r["customers"], 2)
        self.assertFalse(r["in_progress"])

    def test_just_after_midnight_on_new_years_day_is_the_new_year(self):
        self.pay(self.alice, "700", local(self.this_year, hour=0, minute=30))
        self.assertEqual(self.year_row(self.this_year.year)["revenue"], 700)
        self.assertIsNone(self.year_row(self.last_year.year))

    def test_the_year_total_is_the_sum_of_its_months(self):
        self.pay(self.alice, "400", local(self.this_month))
        if self.last_month.year == self.this_year.year:
            self.pay(self.bob, "600", local(self.last_month.replace(day=3)))
        with tenant_context(self.tenant):
            months = [m for m in monthly_revenue(12) if m["month"].startswith(str(self.this_year.year))]
        self.assertEqual(self.year_row(self.this_year.year)["revenue"],
                         sum(m["revenue"] for m in months))

    def test_free_access_earns_nothing_in_a_year_either(self):
        self.pay(self.alice, "0", local(self.last_year.replace(month=6, day=1)), "comp")
        self.pay(self.alice, "300", local(self.last_year.replace(month=6, day=1)))
        r = self.year_row(self.last_year.year)
        self.assertEqual((r["revenue"], r["payments"], r["comps"]), (300, 1, 1))

    def test_the_endpoint_answers(self):
        client = APIClient()
        token = TenantTokenObtainPairSerializer.get_token(make_admin("yearly_admin")).access_token
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        resp = client.get("/api/reports/revenue/yearly/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["results"][0]["year"], self.this_year.year)


class AtTheEdgesOfTheCalendar(TestCase):
    """
    The clock frozen at the moments these figures are most likely to be
    wrong: the turn of the year, the end of a long month after a short one,
    and a leap year.
    """

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with patch("billing.models.notify_customer"):
            router = make_router()
            package = make_package()
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Edge", phone="254711000077",
                connection_type="pppoe", router=router)
            self.sub = Subscription.objects.create(customer=self.customer, package=package)
        Payment.objects.all_tenants().all().delete()

    def pay(self, amount, when, method="mpesa"):
        p = Payment.objects.bulk_create([Payment(
            tenant=self.tenant, customer=self.customer, subscription=self.sub,
            amount=Decimal(amount), method=method)])[0]
        Payment.objects.all_tenants().filter(pk=p.pk).update(paid_at=when)

    def at(self, y, m, d, hh=0, mm=0, ss=0):
        return timezone.make_aware(datetime(y, m, d, hh, mm, ss))

    def frozen(self, when):
        return patch("django.utils.timezone.now", return_value=when)

    def test_new_years_morning(self):
        self.pay("1000", self.at(2026, 12, 31, 23, 59, 59))   # last second of the year
        self.pay("50", self.at(2027, 1, 1, 0, 0, 0))          # first second of the next
        with self.frozen(self.at(2027, 1, 1, 0, 10)), tenant_context(self.tenant):
            months = monthly_revenue(3)
            years = yearly_revenue()
            summary = revenue_summary()
        self.assertEqual([m["month"] for m in months], ["2027-01", "2026-12", "2026-11"])
        self.assertEqual((months[0]["revenue"], months[1]["revenue"]), (50, 1000))
        self.assertEqual([(y["year"], y["revenue"], y["in_progress"]) for y in years],
                         [(2027, 50, True), (2026, 1000, False)])
        self.assertEqual(summary["last_month_start"], "2026-12-01")
        self.assertEqual(Decimal(str(summary["last_month"])), Decimal("1000"))
        self.assertEqual(Decimal(str(summary["this_year"])), Decimal("50"))

    def test_the_end_of_march_is_compared_with_all_of_february(self):
        self.pay("100", self.at(2027, 2, 1, 9))
        self.pay("100", self.at(2027, 2, 28, 23))
        self.pay("300", self.at(2027, 3, 2, 9))
        with self.frozen(self.at(2027, 3, 31, 22)), tenant_context(self.tenant):
            now_row = monthly_revenue(1)[0]
        # Past the end of February: the whole of it, 200, never March's days.
        self.assertEqual(now_row["change"], 50.0)

    def test_mid_month_is_compared_to_the_minute(self):
        self.pay("100", self.at(2027, 4, 15, 9, 59))    # before "this point" in April
        self.pay("999", self.at(2027, 4, 15, 10, 1))    # after it
        self.pay("150", self.at(2027, 5, 15, 9, 0))
        with self.frozen(self.at(2027, 5, 15, 10, 0)), tenant_context(self.tenant):
            self.assertEqual(monthly_revenue(1)[0]["change"], 50.0)

    def test_a_leap_year_compares_by_date_not_by_day_count(self):
        """1 March 2028 against 1 March 2027 -- not 2 March, a day later."""
        self.pay("100", self.at(2027, 3, 1, 12))
        self.pay("999", self.at(2027, 3, 2, 12))        # would count if a day off
        self.pay("150", self.at(2028, 1, 10))
        with self.frozen(self.at(2028, 3, 1, 23, 0)), tenant_context(self.tenant):
            # 2027 up to 1 March 23:00 holds the 100 only.
            self.assertEqual(yearly_revenue()[0]["change"], 50.0)

    def test_the_leap_day_itself(self):
        self.pay("100", self.at(2027, 2, 28, 23))
        self.pay("999", self.at(2027, 3, 1, 0, 30))
        self.pay("150", self.at(2028, 2, 29, 8))
        with self.frozen(self.at(2028, 2, 29, 9)), tenant_context(self.tenant):
            self.assertEqual(yearly_revenue()[0]["change"], 50.0)
