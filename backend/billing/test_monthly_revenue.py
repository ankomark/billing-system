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
from billing.reports import _add_months, monthly_revenue, revenue_summary
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

    def test_the_month_in_progress_is_compared_with_the_same_days(self):
        days_in = (timezone.localdate() - self.this_month).days + 1
        # Inside the same days of last month, and after them.
        self.pay(self.alice, "100", local(self.last_month))
        self.pay(self.alice, "9999", local(self.last_month + timedelta(days=27), hour=23))
        self.pay(self.alice, "150", local(self.this_month))
        with tenant_context(self.tenant):
            r = monthly_revenue(12)[0]
        if days_in < 28:   # the late payment falls outside the window compared
            self.assertEqual(r["change"], 50.0)
        self.assertIn("of last month", r["change_against"])

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
