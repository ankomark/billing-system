"""
A subscription's window can be shorter than the package sells, never longer.

Subscription.save only ever computed an expiry when none was supplied, so any
caller passing one kept it verbatim -- and Subscription is editable in Django
admin, where extending a date is one field and a Save. Nothing anywhere
compared the result against the package.

Four such rows were live on 2026-09-12, every one a comp: two 3-week packages
stretched by a week, a 6-hour bundle set to run a month, and a 2-day bundle set
to run thirty. Each was invisible to every sweep in the system, because the row
is active, paid and inside its own expiry. The expiry is simply not the one the
package sells, and nothing was asking that question.

Shorter windows are deliberately left alone. Cutting service early is an act
somebody chose -- 19 rows were truncated in a bulk correction on 2026-08-25 --
and extending them here would invent an entitlement rather than remove one.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from billing.models import Customer, Package, Subscription, Tenant
from billing.tenancy import tenant_context


class TheWindowCannotExceedThePackage(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.six_hours = Package.objects.create(
                tenant=self.tenant, name="6hrs - 6GB", download_speed=5,
                upload_speed=2, price=Decimal("25.00"), duration_value=6,
                duration_unit="hours", data_cap_mb=6000, is_hotspot=True)
            self.month = Package.objects.create(
                tenant=self.tenant, name="1 month - 30GB", download_speed=5,
                upload_speed=2, price=Decimal("598.00"), duration_value=1,
                duration_unit="months", data_cap_mb=30000, is_hotspot=True)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Stretched",
                phone="254700980001", connection_type="hotspot",
                status="active", hotspot_username="AA:BB:CC:80:00:01")

    def _make(self, package, expiry=None):
        with tenant_context(self.tenant):
            return Subscription.objects.create(
                customer=self.customer, package=package, status="active",
                expiry_date=expiry)

    def test_an_expiry_beyond_the_package_is_clamped(self):
        """
        The production case: a six-hour bundle set to run a month. It served
        customer 773 from 27 August to 12 September before anybody noticed.
        """
        sub = self._make(self.six_hours,
                         expiry=timezone.now() + timedelta(days=30))

        window = (sub.expiry_date - sub.start_date).total_seconds() / 3600
        self.assertAlmostEqual(window, 6, delta=0.05)

    def test_no_expiry_still_comes_from_the_package(self):
        """The behaviour that was already there, kept."""
        sub = self._make(self.six_hours)

        window = (sub.expiry_date - sub.start_date).total_seconds() / 3600
        self.assertAlmostEqual(window, 6, delta=0.05)

    def test_a_shorter_window_is_left_alone(self):
        """
        Cutting service early is deliberate. Extending it here would invent an
        entitlement rather than remove one.
        """
        short = timezone.now() + timedelta(hours=2)
        sub = self._make(self.six_hours, expiry=short)

        self.assertAlmostEqual(
            (sub.expiry_date - short).total_seconds(), 0, delta=2)

    def test_a_couple_of_seconds_over_is_not_clamped(self):
        """
        The tolerance exists so ordinary clock skew between computing a date
        and saving it does not read as tampering.
        """
        nearly = timezone.now() + timedelta(hours=6, seconds=30)
        sub = self._make(self.six_hours, expiry=nearly)

        self.assertAlmostEqual(
            (sub.expiry_date - nearly).total_seconds(), 0, delta=2)

    def test_a_month_package_gets_a_month(self):
        """
        Months use relativedelta rather than a fixed number of days, so the
        ceiling has to be computed the same way the package computes it --
        otherwise every monthly subscription would be clamped by a day or two.
        """
        sub = self._make(self.month,
                         expiry=timezone.now() + timedelta(days=365))

        expected = self.month.calculate_expiry(sub.start_date)
        self.assertAlmostEqual(
            (sub.expiry_date - expected).total_seconds(), 0, delta=2)

    def test_clamping_survives_a_later_save(self):
        """
        Editing a subscription in Django admin saves the whole row, which is
        the route the stretched windows came in by.
        """
        sub = self._make(self.six_hours)
        sub.expiry_date = timezone.now() + timedelta(days=30)
        with tenant_context(self.tenant):
            sub.save()

        sub.refresh_from_db()
        window = (sub.expiry_date - sub.start_date).total_seconds() / 3600
        self.assertAlmostEqual(window, 6, delta=0.05)

    def test_a_queryset_update_is_deliberately_not_clamped(self):
        """
        update() bypasses save(), and the sweeps rely on that -- the
        abandoned-checkout cleanup and the window trim both write through it
        precisely to avoid re-running this logic. Recorded so the gap is known
        rather than discovered.
        """
        sub = self._make(self.six_hours)
        far = timezone.now() + timedelta(days=30)
        with tenant_context(self.tenant):
            Subscription.objects.filter(pk=sub.pk).update(expiry_date=far)

        sub.refresh_from_db()
        self.assertAlmostEqual((sub.expiry_date - far).total_seconds(), 0,
                               delta=2)

    def test_a_status_only_save_does_not_claim_to_clamp(self):
        """
        update_fields=["status"] writes that column and nothing else, which is
        how the expiry sweeps and Payment.save mark a row. Clamping there would
        change the in-memory object, never reach the database, and log that it
        had done something it had not -- a log that lies is worse than no log.
        """
        sub = self._make(self.six_hours)
        far = timezone.now() + timedelta(days=30)
        with tenant_context(self.tenant):
            Subscription.objects.filter(pk=sub.pk).update(expiry_date=far)
            sub.refresh_from_db()
            sub.status = "suspended"
            sub.save(update_fields=["status"])

        sub.refresh_from_db()
        self.assertEqual(sub.status, "suspended")
        self.assertAlmostEqual((sub.expiry_date - far).total_seconds(), 0,
                               delta=2)

    def test_a_save_naming_the_expiry_does_clamp(self):
        """The other half: ask to write it and it is checked."""
        sub = self._make(self.six_hours)
        with tenant_context(self.tenant):
            sub.expiry_date = timezone.now() + timedelta(days=30)
            sub.save(update_fields=["expiry_date"])

        sub.refresh_from_db()
        window = (sub.expiry_date - sub.start_date).total_seconds() / 3600
        self.assertAlmostEqual(window, 6, delta=0.05)
