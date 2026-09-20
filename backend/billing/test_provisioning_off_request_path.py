"""
Redeeming a code must not wait on the router.

`POST /api/hotspot/validate/` did the router work inline: six round trips to
configure the account, inside a request the portal gives fifteen seconds. On a
healthy link that is under a second. On 2026-09-20, with the Airtel circuit at
skylink3 dropping 30-50% of packets, a single read of the hotspot user table
took 5s and timed out, and the profile read took 24.77s:

       validate 402 x 200,  91 x 503,  57 x 429,  41 x 409,  14 x 500

About one redemption in three failed, and what the customer saw was "That took
too long. Please try again." for a code that was good and a payment that had
already cleared. The router then refused their device as "invalid username or
password" -- correctly, because the account had never been written.

So the grant moves to the queue, which already existed as the fallback path
and retries at 5s, 20s, 60s, 240s, 960s.

The part that cannot be done by halves is the portal. After a 200 it calls
`logIn()`, which submits credentials to RouterOS; against an account that is
not there yet that is refused, and the error page RouterOS returns deliberately
does not retry. Answering early to a page that does not expect it would turn a
slow success into a fast failure.

Portal files live on each router and are pushed per site, so some will be older
than this server for as long as that takes. The page therefore *declares* that
it can wait, with `provision_async`, and absent means the old behaviour --
exactly what `auto` on the same endpoint already does.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from billing.models import (
    Customer, Package, RouterDevice, Subscription, Tenant, Voucher,
)
from billing.services.provisioning_state import (
    is_provisioned, mark_provisioned,
)
from billing.tenancy import tenant_context

MAC = "AA:BB:CC:00:44:55"


class RedemptionBase(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.get(slug="skylink")
        now = timezone.now()
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r", ip_address="10.0.0.9",
                username="u", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="2hrs", download_speed=5,
                upload_speed=2, price=Decimal("50.00"), duration_value=2,
                duration_unit="hours", data_cap_mb=0, is_hotspot=True,
                max_devices=1)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Achieng", phone="254700333555",
                connection_type="hotspot", router=self.router)
            self.sub = Subscription.objects.create(
                tenant=self.tenant, customer=self.customer,
                package=self.package, status="active",
                expiry_date=now + timedelta(hours=2))
            inv = self.sub.invoice
            inv.payment_status = "paid"
            inv.save(update_fields=["payment_status"])
            self.voucher = Voucher.objects.create(
                tenant=self.tenant, code="WIFI-ASYNC01",
                subscription=self.sub, expires_at=now + timedelta(hours=2))

    def validate(self, **extra):
        body = {"code": "WIFI-ASYNC01", "mac_address": MAC}
        body.update(extra)
        return APIClient().post(
            f"/api/hotspot/validate/?t={self.tenant.public_token}",
            body, format="json")


class APortalThatCanWait(RedemptionBase):
    """provision_async: the router work goes to the queue."""

    def test_the_request_does_not_touch_the_router(self):
        with patch("billing.views.enable_customer_access") as inline, \
             patch("billing.tasks.provisioning.ensure_customer_access_task"):
            response = self.validate(provision_async=True)

        self.assertEqual(response.status_code, 202)
        inline.assert_not_called()

    def test_the_grant_is_queued_for_the_package_that_was_redeemed(self):
        with patch("billing.views.enable_customer_access"), \
             patch("billing.tasks.provisioning."
                   "ensure_customer_access_task") as task:
            self.validate(provision_async=True)

        task.delay.assert_called_once()
        kwargs = task.delay.call_args.kwargs
        self.assertEqual(task.delay.call_args.args[0], self.customer.pk)
        self.assertEqual(kwargs["subscription_id"], self.sub.pk)

    def test_the_answer_tells_the_page_to_wait(self):
        with patch("billing.views.enable_customer_access"), \
             patch("billing.tasks.provisioning.ensure_customer_access_task"):
            response = self.validate(provision_async=True)

        self.assertTrue(
            response.data.get("provisioning"),
            "without this the page walks straight into a RouterOS login "
            "against an account that has not been written yet")

    def test_the_device_token_still_comes_back(self):
        """
        The connected page needs it to show the customer their code again, and
        it is issued on redemption. Deferring the grant must not cost it.
        """
        with patch("billing.views.enable_customer_access"), \
             patch("billing.tasks.provisioning.ensure_customer_access_task"):
            response = self.validate(provision_async=True)

        self.assertTrue(response.data.get("device_token"))

    def test_a_code_that_is_wrong_is_still_refused_immediately(self):
        """Deferring the grant must not defer the check on the code."""
        with patch("billing.views.enable_customer_access"), \
             patch("billing.tasks.provisioning.ensure_customer_access_task"):
            response = self.validate(code="WIFI-NOPE", provision_async=True)

        self.assertGreaterEqual(response.status_code, 400)


class APortalThatCannotWait(RedemptionBase):
    """
    Anything but an explicit true keeps the behaviour it was written against.

    These are the sites whose files have not been pushed yet. Upgrading the
    server must not change what their page does.
    """

    def test_an_older_portal_is_still_provisioned_inline(self):
        with patch("billing.views.enable_customer_access") as inline, \
             patch("billing.tasks.provisioning.ensure_customer_access_task"):
            response = self.validate()

        inline.assert_called_once()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.get("detail"), "Access granted")

    def test_a_string_does_not_count_as_yes(self):
        with patch("billing.views.enable_customer_access") as inline, \
             patch("billing.tasks.provisioning.ensure_customer_access_task"):
            self.validate(provision_async="true")

        inline.assert_called_once()

    def test_false_does_not_count_as_yes(self):
        with patch("billing.views.enable_customer_access") as inline, \
             patch("billing.tasks.provisioning.ensure_customer_access_task"):
            self.validate(provision_async=False)

        inline.assert_called_once()


class WhenThereIsNoBroker(RedemptionBase):
    """
    A queue that cannot be reached is not a reason to leave a paying customer
    unprovisioned. Fall back to doing it here, slowly, as before.
    """

    def test_it_grants_inline_rather_than_promising_nothing(self):
        with patch("billing.views.enable_customer_access") as inline, \
             patch("billing.tasks.provisioning.ensure_customer_access_task"
                   ) as task:
            task.delay.side_effect = OSError("no broker")
            response = self.validate(provision_async=True)

        inline.assert_called_once()
        self.assertEqual(response.status_code, 200)


class WhatThePortalPollsFor(RedemptionBase):
    """The status endpoint reports whether the account has landed."""

    def status(self):
        return APIClient().get(
            f"/api/hotspot/status/?mac={MAC}&t={self.tenant.public_token}")

    def test_it_reads_false_before_the_grant_lands(self):
        response = self.status()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data.get("provisioned"))

    def test_it_reads_true_once_the_grant_has_landed(self):
        with tenant_context(self.tenant):
            self.customer.hotspot_username = MAC
            self.customer.save(update_fields=["hotspot_username"])
        mark_provisioned(self.tenant.pk, [MAC])

        self.assertTrue(self.status().data.get("provisioned"))

    def test_an_unknown_cache_reads_as_not_yet_rather_than_yes(self):
        """
        The safe direction. Saying yes early sends the page into a RouterOS
        login that is refused; saying no costs it a wait it already has a
        deadline for.
        """
        with patch("billing.services.provisioning_state.cache.get",
                   side_effect=RuntimeError("redis down")):
            self.assertFalse(is_provisioned(self.tenant.pk, MAC))

    def test_addresses_are_tracked_one_at_a_time(self):
        """
        Per device, not per customer: a second handset must not be told it is
        provisioned because the first one was.
        """
        mark_provisioned(self.tenant.pk, [MAC])
        self.assertTrue(is_provisioned(self.tenant.pk, MAC))
        self.assertFalse(is_provisioned(self.tenant.pk, "AA:BB:CC:99:99:99"))

    def test_it_is_scoped_to_one_operator(self):
        mark_provisioned(self.tenant.pk, [MAC])
        self.assertFalse(is_provisioned(self.tenant.pk + 1000, MAC))

    def test_spelling_does_not_change_the_answer(self):
        """
        RouterOS is inconsistent about case and separators, so the key is
        canonical for the same reason every other address comparison is.
        """
        mark_provisioned(self.tenant.pk, [MAC.lower()])
        self.assertTrue(is_provisioned(self.tenant.pk, MAC.upper()))

    def test_a_cache_that_will_not_write_never_fails_the_grant(self):
        with patch("billing.services.provisioning_state.cache.set",
                   side_effect=RuntimeError("redis down")):
            mark_provisioned(self.tenant.pk, [MAC])  # must not raise
        self.assertFalse(is_provisioned(self.tenant.pk, MAC))


class TheGrantRecordsWhatItWrote(RedemptionBase):
    """_grant_hotspot marks each address as it puts it on the router."""

    def test_granting_marks_the_addresses_it_granted(self):
        from billing.router_service import _grant_hotspot

        with patch("billing.router_service.ensure_hotspot_profile",
                   return_value="prof"), \
             patch("billing.router_service.enable_hotspot"), \
             patch("billing.router_service.retry_mac_login"), \
             patch("billing.router_service.macs_to_grant",
                   return_value=[MAC]), \
             tenant_context(self.tenant):
            _grant_hotspot(MagicMock(), self.router, self.customer,
                           self.package, self.sub.expiry_date,
                           subscription=self.sub)

        self.assertTrue(is_provisioned(self.tenant.pk, MAC))

    def test_granting_nothing_marks_nothing(self):
        from billing.router_service import _grant_hotspot

        with patch("billing.router_service.ensure_hotspot_profile",
                   return_value="prof"), \
             patch("billing.router_service.enable_hotspot"), \
             patch("billing.router_service.retry_mac_login"), \
             patch("billing.router_service.macs_to_grant", return_value=[]), \
             tenant_context(self.tenant):
            _grant_hotspot(MagicMock(), self.router, self.customer,
                           self.package, self.sub.expiry_date,
                           subscription=self.sub)

        self.assertFalse(is_provisioned(self.tenant.pk, MAC))
