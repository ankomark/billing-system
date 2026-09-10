"""
A PPPoE subscriber taking their line back.

Their devices cannot be managed from here — everything behind their own
router's NAT reaches us as one address and one session, which is the wall
services/tethering.py documents for hotspot and which PPPoE hits harder. There
is no device to list and none to block. Changing the password is what a
subscriber can actually do about somebody else using their line.

Which makes one property load-bearing: the old password has to stop working
EVERYWHERE. A secret left behind on another router is a way back in, and
migrate_customer_router has never deleted the old one — on 2026-09-09 two
subscribers were found with live secrets on both boxes at once. Rewriting only
the assigned router would tell the subscriber they had locked someone out while
leaving the door open on the box next door.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from billing.models import Customer, RouterDevice, Tenant
from billing.services.pppoe_service import (
    PasswordRejected, change_pppoe_password, validate_pppoe_password,
)
from billing.tenancy import tenant_context


class FakeApi:
    """Stands in for a reachable router. Records nothing it is not asked."""


class ChangingAPPPoEPassword(TestCase):

    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(name="Pass", slug="pass-test")
        with tenant_context(self.tenant):
            self.here = RouterDevice.objects.create(
                tenant=self.tenant, name="here", ip_address="10.4.0.1",
                username="a", password="p", priority=1)
            self.there = RouterDevice.objects.create(
                tenant=self.tenant, name="there", ip_address="10.4.0.2",
                username="a", password="p", priority=2)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="School", phone="254700600001",
                connection_type="pppoe", router=self.here, status="active",
                pppoe_username="school-1", pppoe_password="oldpassword")

    def _change(self, password="brandnewpass1", reachable=("here", "there")):
        written, dropped = [], []

        def connect(router, **kw):
            return FakeApi() if router.name in reachable else None

        def secret(api, router, customer, package, expiry_date=None):
            written.append((router.name, customer.pppoe_password))
            return True

        with patch("billing.router_service.safe_connect_router", connect), \
             patch("billing.router_service.create_pppoe_secret", secret), \
             patch("billing.router_service.disconnect_pppoe_session",
                   side_effect=lambda api, u: dropped.append(u)):
            updated, failed = change_pppoe_password(self.customer, password)
        return updated, failed, written, dropped

    # ---- the property that matters ---------------------------------------

    def test_every_router_is_rewritten_not_only_the_assigned_one(self):
        updated, failed, written, _ = self._change()

        self.assertEqual(sorted(updated), ["here", "there"])
        self.assertEqual(failed, [])
        self.assertEqual(
            sorted(r for r, _ in written), ["here", "there"],
            "a router kept the old password, so the old credentials still work")

    def test_the_new_password_is_what_reaches_the_router(self):
        _, _, written, _ = self._change(password="freshpass99")
        for _, sent in written:
            self.assertEqual(sent, "freshpass99")

    def test_the_live_session_is_dropped(self):
        """
        PPP authenticates once, at dial time, and nothing re-checks the secret
        afterwards. Without this the freeloader stays connected on a password
        that no longer exists.
        """
        _, _, _, dropped = self._change()
        self.assertEqual(dropped, ["school-1", "school-1"])

    def test_an_unreachable_router_is_reported_not_hidden(self):
        """
        That router still honours the old password. The subscriber is entitled
        to know the change did not land everywhere.
        """
        updated, failed, _, _ = self._change(reachable=("here",))
        self.assertEqual(updated, ["here"])
        self.assertEqual(failed, ["there"])

    def test_the_password_is_saved_even_if_a_router_is_unreachable(self):
        self._change(reachable=("here",))
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.pppoe_password, "brandnewpass1")

    # ---- what a subscriber may type ---------------------------------------

    def test_a_password_they_cannot_retype_is_refused(self):
        """
        They have to enter this into their own router, often from a phone.
        Symbols are where that goes wrong.
        """
        for bad in ("short1", "has spaces here", "sym#bols!", "a" * 40):
            with self.subTest(bad=bad):
                with self.assertRaises(PasswordRejected):
                    validate_pppoe_password(bad)

    def test_a_reasonable_password_is_accepted(self):
        self.assertEqual(validate_pppoe_password("  Fibre2026x  "), "Fibre2026x")

    def test_a_hotspot_customer_cannot_use_this(self):
        self.customer.connection_type = "hotspot"
        self.customer.save(update_fields=["connection_type"])
        with self.assertRaises(PasswordRejected):
            change_pppoe_password(self.customer, "brandnewpass1")


class ThePasswordEndpoint(TestCase):

    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(name="Api", slug="api-test")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="box", ip_address="10.5.0.1",
                username="a", password="p", priority=1)
            self.user = get_user_model().objects.create_user(
                username="sub", password="x", tenant=self.tenant)
            self.customer = Customer.objects.create(
                tenant=self.tenant, user=self.user, full_name="School",
                phone="254700600002", connection_type="pppoe",
                router=self.router, status="active",
                pppoe_username="school-2", pppoe_password="oldpassword")
        # JWT only — DEFAULT_AUTHENTICATION_CLASSES has no session backend,
        # so a Django session login is a 401 here.
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = reverse("pppoe-password")

    def _ok(self):
        return patch.multiple(
            "billing.router_service",
            safe_connect_router=lambda router, **kw: FakeApi(),
            create_pppoe_secret=lambda *a, **k: True,
            disconnect_pppoe_session=lambda *a, **k: None,
        )

    def test_it_suggests_one_so_the_common_case_is_one_tap(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["username"], "school-2")
        self.assertGreaterEqual(len(r.json()["suggested_password"]), 8)

    def test_the_response_says_they_are_now_offline(self):
        """
        The one thing that must not surprise them: the line is down until they
        retype it at their end.
        """
        with self._ok():
            r = self.client.post(self.url, {"password": "brandnewpass1"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("disconnected", r.json()["detail"])
        self.assertEqual(r.json()["password"], "brandnewpass1")

    def test_a_second_change_straight_away_is_throttled(self):
        with self._ok():
            self.client.post(self.url, {"password": "brandnewpass1"})
            again = self.client.post(self.url, {"password": "anotherpass22"})
        self.assertEqual(again.status_code, 429)

    def test_no_router_reachable_reports_failure_rather_than_success(self):
        """
        Every router still honours the old password. Reporting success here
        leaves the subscriber retyping one that cannot log in.
        """
        with patch("billing.router_service.safe_connect_router",
                   lambda router, **kw: None):
            r = self.client.post(self.url, {"password": "brandnewpass1"})
        self.assertEqual(r.status_code, 503)

    def test_a_bad_password_is_refused_with_a_reason(self):
        with self._ok():
            r = self.client.post(self.url, {"password": "no"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("at least", r.json()["detail"])
