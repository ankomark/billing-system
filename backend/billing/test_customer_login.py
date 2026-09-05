"""
Giving a PPPoE subscriber a way in.

The renewal portal has existed all along — PPPoECustomerPortalView, the
packages endpoint, the renew endpoint with its STK push, usage and controls,
every one behind IsAuthenticated resolving the subscriber through
`user.customer_profile`. And no subscriber could reach any of it: every account
on the platform belonged to the operator or their staff, because nothing has
ever created a login for a customer. Finished software with nobody able to sign
in, and PPPoE renewal done by hand every month as a result.

They sign in with their PPPoE username. Their router is already configured with
it and it was sent to them when the line was set up, so it is the one string
they demonstrably already have. It is unique within an operator by database
constraint — but Django usernames are unique across the whole platform, which
is the seam these tests spend most of their time on.

The password is shown to the operator once and sent to the subscriber. Shown,
because this operator's SMS credit has run out before now and a password that
exists only in a message nobody received is worse than none. Sent, because
reading one down a phone line is how it gets written down wrong.
"""

from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient
from unittest.mock import patch

from billing.models import Customer, Package, RouterDevice, Tenant, User
from billing.tenancy import tenant_context


class CustomerLoginAccountTests(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.admin = User.objects.create_user(
                username="boss2", password="pw", role="tenant_admin",
                tenant=self.tenant)
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r", ip_address="10.0.0.11",
                username="u", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="home 10mbps", download_speed=10,
                upload_speed=10, price=Decimal("2500.00"), duration_value=1,
                duration_unit="months", data_cap_mb=0,
                is_hotspot=False, max_devices=4)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Enock", phone="254700111333",
                connection_type="pppoe", router=self.router,
                pppoe_username="SKY-1333-ABC", pppoe_password="routerpw")

        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.url = reverse("customer-login-account", args=[self.customer.id])

    def _post(self, client=None):
        # The channels are stubbed, not the endpoint: what is asserted below is
        # that their outcome is reported honestly, which is the whole reason
        # this does not go through notify_customer.
        with patch("billing.views.send_sms", return_value=True) as sms, \
             patch("billing.views.send_whatsapp", return_value=True) as wa, \
             tenant_context(self.tenant):
            r = (client or self.client).post(self.url, {}, format="json")
        return r, sms, wa

    # ---- what it is for -------------------------------------------------

    def test_it_creates_a_login_the_subscriber_can_use(self):
        r, _, _ = self._post()
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["username"], "SKY-1333-ABC")
        self.assertTrue(r.data["created"])

        self.customer.refresh_from_db()
        user = self.customer.user
        self.assertIsNotNone(user, "the login was not linked to the customer")
        self.assertEqual(user.username, "SKY-1333-ABC")
        self.assertEqual(user.role, User.CUSTOMER)
        self.assertEqual(user.tenant_id, self.tenant.id)
        self.assertTrue(user.check_password(r.data["password"]),
                        "the password handed to the operator does not work")

    def test_the_subscriber_must_replace_the_password_we_chose(self):
        """Somebody else picked it, so the holder is made to replace it."""
        self._post()
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.user.must_change_password)

    def test_the_password_is_only_ever_returned_once(self):
        """
        It is hashed the moment it is set, so the response is the only place it
        exists in readable form. Nothing may store it back on the customer.
        """
        r, _, _ = self._post()
        password = r.data["password"]
        self.customer.refresh_from_db()
        self.assertNotEqual(self.customer.pppoe_password, password)
        self.assertNotIn(password, str(self.customer.__dict__))

    def test_the_portal_password_is_not_the_router_password(self):
        """
        pppoe_password is kept recoverable because it has to be pushed to the
        router. A portal login must not inherit that property.
        """
        r, _, _ = self._post()
        self.assertNotEqual(r.data["password"], "routerpw")
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.user.check_password("routerpw"))

    # ---- resetting ------------------------------------------------------

    def test_a_second_call_resets_rather_than_failing(self):
        first, _, _ = self._post()
        second, _, _ = self._post()

        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.data["created"])
        self.assertNotEqual(first.data["password"], second.data["password"])

        self.customer.refresh_from_db()
        self.assertTrue(
            self.customer.user.check_password(second.data["password"]))
        self.assertFalse(
            self.customer.user.check_password(first.data["password"]),
            "the old password still works after a reset")

    def test_a_reset_signs_the_old_sessions_out(self):
        self._post()
        self.customer.refresh_from_db()
        before = User.objects.get(pk=self.customer.user_id).token_version

        self._post()
        after = User.objects.get(pk=self.customer.user_id).token_version
        self.assertGreater(
            after, before,
            "an old token still works after the password was reset")

    def test_only_one_login_is_ever_made(self):
        self._post()
        self._post()
        self.assertEqual(
            User.objects.filter(role=User.CUSTOMER).count(), 1)

    # ---- what it refuses ------------------------------------------------

    def test_a_hotspot_subscriber_has_nothing_to_log_in_to(self):
        """
        They are anonymous by design — identified by MAC at the captive portal
        and buying without an account at all.
        """
        with tenant_context(self.tenant):
            walkup = Customer.objects.create(
                tenant=self.tenant, full_name="Walk-up", phone="254700111444",
                connection_type="hotspot", router=self.router)
        r = self.client.post(
            reverse("customer-login-account", args=[walkup.id]), {},
            format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(User.objects.filter(role=User.CUSTOMER).count(), 0)

    def test_a_line_with_no_pppoe_username_yet_is_refused(self):
        with tenant_context(self.tenant):
            fresh = Customer.objects.create(
                tenant=self.tenant, full_name="No Creds",
                phone="254700111555", connection_type="pppoe",
                router=self.router)
        r = self.client.post(
            reverse("customer-login-account", args=[fresh.id]), {},
            format="json")
        self.assertEqual(r.status_code, 400)

    def test_a_username_taken_elsewhere_is_refused_not_worked_around(self):
        """
        PPPoE usernames are unique per operator; Django usernames are unique
        across the platform. Two operators can each have a SKY-1333-ABC.
        Inventing SKY-1333-ABC2 to get past it would hand somebody a login they
        will mistype for ever, so the operator is told to change the line's
        username instead.
        """
        other = Tenant.objects.create(name="Other ISP", slug="other-isp")
        User.objects.create_user(
            username="SKY-1333-ABC", password="x", role="tenant_admin",
            tenant=other)

        r = self.client.post(self.url, {}, format="json")
        self.assertEqual(r.status_code, 409)
        self.customer.refresh_from_db()
        self.assertIsNone(self.customer.user)

    def test_staff_may_not_mint_a_login(self):
        """
        The account can spend money through the STK push, so it is not a
        decision for whoever is on the counter.
        """
        with tenant_context(self.tenant):
            staff = User.objects.create_user(
                username="counter2", password="pw", role="tenant_staff",
                tenant=self.tenant)
        client = APIClient()
        client.force_authenticate(staff)
        r = client.post(self.url, {}, format="json")
        self.assertIn(r.status_code, (401, 403))
        self.assertEqual(User.objects.filter(role=User.CUSTOMER).count(), 0)

    # ---- telling the operator the truth ---------------------------------

    def test_delivery_is_reported_per_channel(self):
        with patch("billing.views.send_sms", return_value=False), \
             patch("billing.views.send_whatsapp", return_value=True), \
             tenant_context(self.tenant):
            r = self.client.post(self.url, {}, format="json")

        self.assertFalse(r.data["sms_sent"])
        self.assertTrue(r.data["whatsapp_sent"])
        self.assertIn("password", r.data)

    def test_a_dead_sms_provider_does_not_lose_the_login(self):
        """
        The credit on this account has run out before. A login that was created
        must still be reported, with the password, or the operator is left with
        an account nobody can get into.
        """
        with patch("billing.views.send_sms", side_effect=RuntimeError("no credit")), \
             patch("billing.views.send_whatsapp", side_effect=RuntimeError("down")), \
             tenant_context(self.tenant):
            r = self.client.post(self.url, {}, format="json")

        self.assertEqual(r.status_code, 201)
        self.assertFalse(r.data["sms_sent"])
        self.assertFalse(r.data["whatsapp_sent"])
        self.customer.refresh_from_db()
        self.assertTrue(
            self.customer.user.check_password(r.data["password"]))

    @override_settings(PORTAL_URL="")
    def test_no_portal_url_configured_leaves_the_line_out(self):
        """A URL guessed wrong is worse than none, and characters cost money."""
        _, sms, _ = self._post()
        message = sms.call_args[0][1]
        self.assertNotIn("Sign in at", message)
        self.assertIn("SKY-1333-ABC", message)

    @override_settings(PORTAL_URL="https://app.example.com/")
    def test_the_portal_url_is_included_when_it_is_set(self):
        _, sms, _ = self._post()
        message = sms.call_args[0][1]
        self.assertIn("Sign in at https://app.example.com", message)
        self.assertNotIn("app.example.com//", message)
