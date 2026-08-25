"""
Which router a subscriber is standing at, and why the portal has to say.

A captive portal request carries the operator's token and nothing else. With
one router that is enough. With two it is not: selection falls to
pick_best_router_for_new_customer, which sorts by PPPoE sessions then
priority — and on a hotspot-only estate every router ties at zero, so the
winner is whichever the database happened to return first.

Observed in production on 2026-08-25: an operator running two sites had
subscribers standing at one of them provisioned onto the other. The record
said active, the voucher was valid, and no hotspot account existed on the
hardware in front of them. One customer paid three times in ten minutes.

config.js is uploaded per router, so it carries that router's token and the
guess becomes a fact.
"""

from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from billing.models import (
    Customer, CustomerDevice, Package, RouterDevice, Subscription, Tenant,
    Voucher,
)
from billing.tenancy import tenant_context

PHONE_MAC = "AA:11:22:33:44:55"


class PortalRouterIdentityTests(TestCase):
    """One operator, two sites, a subscriber at the second one."""

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.site_a = RouterDevice.objects.create(
                tenant=self.tenant, name="Homabay A", ip_address="10.10.0.2",
                username="a", password="p", is_active=True)
            self.site_b = RouterDevice.objects.create(
                tenant=self.tenant, name="Homabay B", ip_address="10.10.0.5",
                username="b", password="p", is_active=True)
            self.package = Package.objects.create(
                tenant=self.tenant, name="3hrs", download_speed=5,
                upload_speed=2, price=Decimal("20.00"), duration_value=3,
                duration_unit="hours", monthly_data_cap_gb=0, is_hotspot=True,
                max_devices=1)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Achieng", phone="254700111222",
                connection_type="hotspot", router=self.site_a)
            self.sub = Subscription.objects.create(
                tenant=self.tenant, customer=self.customer,
                package=self.package, status="active",
                expiry_date=timezone.now() + timedelta(hours=3))
            inv = self.sub.invoice
            inv.payment_status = "paid"
            inv.save(update_fields=["payment_status"])
            self.voucher = Voucher.objects.create(
                tenant=self.tenant, code="WIFI-ROAM01", subscription=self.sub,
                expires_at=self.sub.expiry_date)

    def redeem(self, mac, router_token=None):
        body = {"code": "WIFI-ROAM01", "mac_address": mac,
                "t": self.tenant.public_token}
        if router_token is not None:
            body["r"] = router_token
        return APIClient().post("/api/hotspot/validate/", body, format="json")

    # -- the token itself ---------------------------------------------------

    def test_every_router_gets_a_token(self):
        self.assertTrue(self.site_a.public_token)
        self.assertTrue(self.site_b.public_token)
        self.assertNotEqual(self.site_a.public_token, self.site_b.public_token)

    def test_a_health_probe_does_not_lose_an_unsaved_token(self):
        """
        record_health saves with update_fields, and a named save drops
        everything it does not name. A router polled every two minutes would
        generate a token on each probe and persist none of them.
        """
        with tenant_context(self.tenant):
            router = RouterDevice.objects.create(
                tenant=self.tenant, name="probe me", ip_address="10.10.0.9",
                username="u", password="p")
            RouterDevice.objects.filter(pk=router.pk).update(public_token=None)
            router.refresh_from_db()
            self.assertIsNone(router.public_token)

            router.record_health(True)
            router.refresh_from_db()

        self.assertTrue(
            router.public_token,
            "the token was generated but never written")

    # -- roaming between an operator's sites --------------------------------

    def test_a_valid_code_presented_at_another_site_re_homes_the_subscriber(self):
        """
        The subscriber bought at Homabay A and walked to Homabay B. Their code
        is still valid, so it is accepted — and the access has to be built
        where they are standing, not where they were.
        """
        with patch("billing.views.enable_customer_access") as grant:
            resp = self.redeem(PHONE_MAC, self.site_b.public_token)

        self.assertEqual(resp.status_code, 200, resp.data)
        self.customer.refresh_from_db()
        self.assertEqual(
            self.customer.router_id, self.site_b.id,
            "the subscriber was left on the router they had walked away from")
        # And provisioned only after the move, so nothing is built at the
        # old site and left behind.
        self.assertTrue(grant.called)
        self.assertEqual(grant.call_args.args[0].router_id, self.site_b.id)

    def test_redeeming_at_the_same_site_changes_nothing(self):
        with patch("billing.views.enable_customer_access"):
            self.assertEqual(
                self.redeem(PHONE_MAC, self.site_a.public_token).status_code, 200)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.router_id, self.site_a.id)

    # -- the compatibility that matters -------------------------------------

    def test_a_portal_that_sends_no_token_behaves_exactly_as_before(self):
        """
        Every portal deployed before config.js carried a router token. If this
        stranded them, upgrading the platform would take every existing site
        offline until somebody re-uploaded files to it by hand.
        """
        with patch("billing.views.enable_customer_access"):
            self.assertEqual(self.redeem(PHONE_MAC).status_code, 200)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.router_id, self.site_a.id)

    def test_an_unknown_token_is_ignored_rather_than_refused(self):
        """
        The shipped config.js carries a placeholder. An operator who uploads it
        unedited must get the old behaviour, not a subscriber who cannot buy.
        """
        with patch("billing.views.enable_customer_access"):
            self.assertEqual(
                self.redeem(PHONE_MAC, "YOUR-ROUTER-TOKEN").status_code, 200)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.router_id, self.site_a.id)

    # -- what a token must not be able to do --------------------------------

    def test_another_operators_router_token_is_refused(self):
        """
        Tokens are unguessable, but a leaked one must not be usable to point a
        subscriber at hardware belonging to somebody else.
        """
        other = Tenant.objects.create(name="Rival WiFi", slug="rival")
        with tenant_context(other):
            theirs = RouterDevice.objects.create(
                tenant=other, name="not yours", ip_address="10.10.0.7",
                username="x", password="p")

        with patch("billing.views.enable_customer_access"):
            self.assertEqual(
                self.redeem(PHONE_MAC, theirs.public_token).status_code, 200)

        self.customer.refresh_from_db()
        self.assertEqual(
            self.customer.router_id, self.site_a.id,
            "a subscriber was moved onto another operator's router")

    def test_a_deactivated_router_is_not_a_place_to_send_anybody(self):
        """
        An operator taking a box out of service should not have subscribers
        provisioned onto it by a portal still running on it.
        """
        with tenant_context(self.tenant):
            RouterDevice.objects.filter(pk=self.site_b.pk).update(is_active=False)

        with patch("billing.views.enable_customer_access"):
            self.assertEqual(
                self.redeem(PHONE_MAC, self.site_b.public_token).status_code, 200)

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.router_id, self.site_a.id)

    # -- buying, not just redeeming -----------------------------------------

    def test_a_purchase_records_the_site_it_was_made_at(self):
        """
        Payment.save() picks a router only when the customer has none, by
        PPPoE load then priority — a tie at zero across a hotspot estate. The
        purchase knows better, because it happened on one of them.
        """
        with tenant_context(self.tenant):
            walk_up = Customer.objects.create(
                tenant=self.tenant, full_name="Walk up",
                phone="254700333444", connection_type="hotspot")
        self.assertIsNone(walk_up.router_id)

        # The till is not what is under test here; without this the purchase
        # is refused before it ever reaches the router attribution.
        with patch("billing.views.initiate_stk_push_task.delay"), \
             patch("billing.views.missing_mpesa_keys", return_value=[]):
            resp = APIClient().post("/api/hotspot/purchase/", {
                "t": self.tenant.public_token,
                "r": self.site_b.public_token,
                "package_id": self.package.id,
                "phone": "254700333444",
            }, format="json")

        # 202: the STK prompt has been sent, the money has not arrived yet.
        self.assertEqual(resp.status_code, 202, resp.data)
        walk_up.refresh_from_db()
        self.assertEqual(
            walk_up.router_id, self.site_b.id,
            "the purchase was not attributed to the site it was made at")
