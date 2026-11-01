"""
Finding a subscriber by the number they actually give you.

SearchFilter matches substrings, and the forms a Kenyan number is written in
share none: `254701071435` does not contain `0701071435`. So an operator
holding a counter slip that read 0701071435 searched for it, found nothing,
and had to know to retype it with 254 on the front.

It failed both ways, because the database holds both — 2344 rows begin 254 and
two begin 07 — and neither form finds the other.

What every form does share is the nine digits after the country code, and
matching on those finds a subscriber however the number was typed.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from billing.models import Customer, RouterDevice, Tenant
from billing.tenancy import tenant_context
from billing.utils import phone_fragment


class TheFragment(TestCase):
    """What the search actually matches on."""

    def test_every_form_of_one_number_gives_the_same_fragment(self):
        for written in ("0701071435", "254701071435", "+254701071435",
                        "+254 701 071 435", "00254701071435", "701071435",
                        "0701 071 435"):
            with self.subTest(written=written):
                self.assertEqual(phone_fragment(written), "701071435")

    def test_the_newer_01_prefix_works_too(self):
        """01 numbers are in use now and were never handled."""
        for written in ("0110123456", "254110123456", "110123456"):
            with self.subTest(written=written):
                self.assertEqual(phone_fragment(written), "110123456")

    def test_a_partial_number_still_searches(self):
        """Somebody typing the first part of a number should see it narrow."""
        self.assertEqual(phone_fragment("0701071"), "701071")

    def test_a_name_is_not_a_number(self):
        for term in ("Wanjiru", "st.ambros", "WIFI-CARD01", ""):
            with self.subTest(term=term):
                self.assertIsNone(phone_fragment(term))

    def test_too_short_to_be_useful_is_refused(self):
        """
        Three digits of a subscriber number matches half the estate, which is
        not a search result anybody can use.
        """
        for term in ("07", "0701", "701"):
            with self.subTest(term=term):
                self.assertIsNone(phone_fragment(term))

    def test_a_number_with_no_mobile_prefix_is_refused(self):
        """
        Kenyan mobiles start 7 or 1. Anything else is a landline, a till, or a
        typo, and guessing at it would widen the search for no one's benefit.
        """
        self.assertIsNone(phone_fragment("0301071435"))


class FindingACustomer(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="find-r", ip_address="10.9.0.150",
                username="u", password="p")
            # The two shapes the database actually holds.
            self.intl = Customer.objects.create(
                tenant=self.tenant, full_name="Stored As Intl",
                phone="254701071435", connection_type="hotspot",
                router=self.router, status="active")
            self.local = Customer.objects.create(
                tenant=self.tenant, full_name="Stored As Local",
                phone="0735690558", connection_type="pppoe",
                pppoe_username="local.one", router=self.router,
                status="active")

        U = get_user_model()
        admin = U.objects.create_user(
            username="find-admin", password="x", role="tenant_admin",
            tenant=self.tenant)
        self.client = APIClient()
        self.client.force_authenticate(user=admin)

    def search(self, term):
        resp = self.client.get("/api/customers/", {"search": term})
        self.assertEqual(resp.status_code, 200, resp.content[:200])
        return {r["id"] for r in resp.data["results"]}

    # ─────────────────────────────────────────── a number stored as 254…

    def test_the_local_form_finds_a_number_stored_as_254(self):
        """The complaint itself."""
        self.assertIn(self.intl.id, self.search("0701071435"))

    def test_the_international_form_still_finds_it(self):
        self.assertIn(self.intl.id, self.search("254701071435"))

    def test_the_written_form_finds_it(self):
        self.assertIn(self.intl.id, self.search("+254 701 071 435"))

    def test_the_bare_subscriber_digits_find_it(self):
        self.assertIn(self.intl.id, self.search("701071435"))

    # ─────────────────────────────────────────── a number stored as 07…

    def test_the_international_form_finds_a_number_stored_locally(self):
        """The other direction, which was equally broken."""
        self.assertIn(self.local.id, self.search("254735690558"))

    def test_the_local_form_finds_it(self):
        self.assertIn(self.local.id, self.search("0735690558"))

    # ──────────────────────────────────────────────── it stays a search

    def test_a_name_still_works(self):
        self.assertIn(self.intl.id, self.search("Stored As Intl"))

    def test_a_pppoe_username_still_works(self):
        self.assertIn(self.local.id, self.search("local.one"))

    def test_one_number_does_not_return_the_other(self):
        """
        A fragment long enough to be a number must still be specific. If it
        matched broadly the page would be useless at a counter.
        """
        found = self.search("0701071435")

        self.assertIn(self.intl.id, found)
        self.assertNotIn(self.local.id, found)

    def test_a_search_that_matches_nothing_returns_nothing(self):
        self.assertEqual(self.search("254799999999"), set())


class SearchingIdentifiersOnOtherTables(TestCase):
    """
    Voucher codes and payment references live on their own tables. They are
    searched as subqueries rather than joins -- a join multiplied every
    customer by every voucher and payment they had, which is what made the
    search slow -- and this holds the results to what the join gave.
    """

    def setUp(self):
        from datetime import timedelta
        from decimal import Decimal
        from unittest.mock import patch

        from django.utils import timezone

        from billing.models import Package, Payment, Subscription, Voucher

        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant), \
                patch("billing.models.notify_customer"):
            router = RouterDevice.objects.create(
                tenant=self.tenant, name="find-r2", ip_address="10.9.0.151",
                username="u", password="p")
            package = Package.objects.create(
                tenant=self.tenant, name="Search 2hr", download_speed=5,
                upload_speed=2, price=Decimal("50.00"), duration_value=2,
                duration_unit="hours", data_cap_mb=0, is_hotspot=True)
            self.regular = Customer.objects.create(
                tenant=self.tenant, full_name="Many Vouchers",
                phone="254722000111", connection_type="hotspot",
                router=router, status="active")
            self.other = Customer.objects.create(
                tenant=self.tenant, full_name="Someone Else",
                phone="254722000222", connection_type="hotspot",
                router=router, status="active")

            # Several of each, so a join would have returned this customer
            # once per combination.
            for i in range(3):
                sub = Subscription.objects.create(
                    tenant=self.tenant, customer=self.regular, package=package)
                Voucher.objects.create(
                    tenant=self.tenant, subscription=sub, code=f"ZQX{i}AB",
                    expires_at=timezone.now() + timedelta(hours=2))
                # bulk_create: Payment.save() provisions access, which is not
                # what is under test here.
                Payment.objects.bulk_create([Payment(
                    tenant=self.tenant, customer=self.regular,
                    subscription=sub, amount=package.price, method="mpesa",
                    reference=f"TKR{i}QWERTY")])

        U = get_user_model()
        admin = U.objects.create_user(
            username="find-admin2", password="x", role="tenant_admin",
            tenant=self.tenant)
        self.client = APIClient()
        self.client.force_authenticate(user=admin)

    def search(self, term):
        resp = self.client.get("/api/customers/", {"search": term})
        self.assertEqual(resp.status_code, 200, resp.content[:200])
        return [r["id"] for r in resp.data["results"]], resp.data["count"]

    def test_a_voucher_code_finds_its_customer(self):
        ids, _ = self.search("zqx1ab")
        self.assertEqual(ids, [self.regular.id])

    def test_a_payment_reference_finds_its_customer(self):
        ids, _ = self.search("TKR2QWERTY")
        self.assertEqual(ids, [self.regular.id])

    def test_a_customer_with_many_matches_is_listed_once(self):
        """Every voucher matches "ZQX"; the customer is still one row."""
        ids, count = self.search("ZQX")
        self.assertEqual(ids, [self.regular.id])
        self.assertEqual(count, 1)

    def test_every_word_must_match_somewhere(self):
        ids, _ = self.search("Many ZQX0AB")
        self.assertEqual(ids, [self.regular.id])
        ids, _ = self.search("Someone ZQX0AB")
        self.assertEqual(ids, [])

    def test_a_number_still_finds_its_customer(self):
        """The phone match is OR-ed in, so it still finds the number."""
        ids, _ = self.search("0722000222")
        self.assertEqual(ids, [self.other.id])
