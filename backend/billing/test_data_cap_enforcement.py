"""
A data cap that actually cuts somebody off.

Three separate things had to be true before a cap meant anything, and none of
them were:

  1. The cap could be *expressed*. `monthly_data_cap_gb` was a whole number of
     gigabytes, so the 300 MB and 500 MB bundles these operators sell rounded
     to 0 — which that same field documents as unlimited. Every sub-gigabyte
     package ever created was sold capped and provisioned uncapped.

  2. Something *checked* it. enforce_usage_caps existed, was correct enough,
     and was never added to CELERY_BEAT_SCHEDULE — a comment on it said
     automatic cut-off was a policy decision, left switched off. So an
     operator could set a cap, watch usage climb past it on the dashboard, and
     nothing would ever happen.

  3. The cut-off *stuck*. Four things in this system exist to put subscribers
     back on the hardware when they fall off it, and to all of them a
     disconnected subscriber holding an active paid subscription looks like a
     fault to repair. Disconnecting alone buys minutes.

The third is the one worth the most tests, because it is the one that fails
quietly: the subscriber goes offline, somebody sees it work, and they are back
online by the next sweep with nobody watching.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from billing.models import (
    Customer,
    HotspotUsageRecord,
    Package,
    PPPoEUsageRecord,
    RouterDevice,
    Subscription,
    Tenant,
)
from billing.services.usage import MB, cap_bytes_for, usage_since, window_start
from billing.tasks.usage_tasks import check_cap, enforce_usage_caps
from billing.tenancy import tenant_context

PHONE_MAC = "AA:BB:CC:00:11:22"


class DataCapBase(TestCase):
    """One PPPoE subscriber on a 300 MB bundle."""

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        now = timezone.now()

        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r1", ip_address="10.0.0.1",
                username="u", password="p")

            # The bundle that could not previously be expressed at all.
            self.package = Package.objects.create(
                tenant=self.tenant, name="300MB Daily", download_speed=10,
                upload_speed=5, price=Decimal("20.00"), duration_value=1,
                duration_unit="days", data_cap_mb=300, max_devices=1)

            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Wanjiru", phone="254706568711",
                connection_type="pppoe", router=self.router,
                pppoe_username="wanjiru", pppoe_password="secret")

            self.sub = Subscription.objects.create(
                tenant=self.tenant, customer=self.customer,
                package=self.package, status="active",
                start_date=now, expiry_date=now + timedelta(days=1))
            invoice = self.sub.invoice
            invoice.payment_status = "paid"
            invoice.save(update_fields=["payment_status"])

    def record(self, megabytes, when=None):
        """Put `megabytes` of traffic on the subscriber's account."""
        with tenant_context(self.tenant):
            PPPoEUsageRecord.objects.create(
                tenant=self.tenant, customer=self.customer, router=self.router,
                period_start=when or timezone.now(),
                period_end=when or timezone.now(),
                download_bytes=int(megabytes * MB), upload_bytes=0)

    def run_check(self):
        """check_cap() with the router stubbed out, returning the disable call."""
        with patch("billing.tasks.usage_tasks.disable_customer_access") as disable, \
             patch("billing.tasks.usage_tasks.notify_customer") as notify:
            cut = check_cap(self.customer, self.sub)
        return cut, disable, notify


class TheCapCanBeExpressed(DataCapBase):
    """
    Point 1: a 300 MB bundle is 300 MB, not zero, not a gigabyte.
    """

    def test_a_three_hundred_megabyte_cap_is_three_hundred_megabytes(self):
        self.assertEqual(cap_bytes_for(self.customer, self.sub), 300 * MB)

    def test_a_sub_gigabyte_cap_is_not_unlimited(self):
        """
        The whole bug in one assertion.

        In gigabytes this cap was 0, and 0 is the value the field uses for
        unlimited — so the check that decides whether to cut somebody off
        skipped every subscriber on a bundle smaller than 1 GB.
        """
        self.assertNotEqual(cap_bytes_for(self.customer, self.sub), 0)

    def test_zero_is_still_unlimited(self):
        with tenant_context(self.tenant):
            self.package.data_cap_mb = 0
            self.package.save(update_fields=["data_cap_mb"])
        self.assertEqual(cap_bytes_for(self.customer, self.sub), 0)

    def test_a_customer_override_replaces_the_packages_cap(self):
        with tenant_context(self.tenant):
            self.customer.custom_data_cap_mb = 500
            self.customer.save(update_fields=["custom_data_cap_mb"])
        self.assertEqual(cap_bytes_for(self.customer, self.sub), 500 * MB)

    def test_an_override_of_zero_means_unlimited_not_no_override(self):
        """
        `custom or package` collapses these two, and the subscriber it
        collapses is the one an operator has deliberately exempted.
        """
        with tenant_context(self.tenant):
            self.customer.custom_data_cap_mb = 0
            self.customer.save(update_fields=["custom_data_cap_mb"])
        self.assertEqual(cap_bytes_for(self.customer, self.sub), 0)

    def test_megabytes_are_binary_like_the_routers(self):
        """
        1 MB is 1048576, not 1000000.

        RouterOS counts binary megabytes in limit-bytes-total. A decimal
        reading here would put our ceiling 4.9% below the hardware's, and the
        two would disagree about when the bundle was spent.
        """
        self.assertEqual(MB, 1024 * 1024)
        self.assertEqual(cap_bytes_for(self.customer, self.sub), 314572800)


class TheCapIsChecked(DataCapBase):
    """Point 2: something looks at the number."""

    def test_under_the_cap_nothing_happens(self):
        self.record(299)
        cut, disable, _ = self.run_check()
        self.assertFalse(cut)
        disable.assert_not_called()
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, "active")

    def test_exactly_at_the_cap_cuts_off(self):
        """300 MB of a 300 MB bundle is spent, not nearly spent."""
        self.record(300)
        cut, disable, _ = self.run_check()
        self.assertTrue(cut)
        disable.assert_called_once()

    def test_over_the_cap_cuts_off(self):
        self.record(301)
        cut, disable, _ = self.run_check()
        self.assertTrue(cut)
        disable.assert_called_once()

    def test_an_unlimited_package_is_never_cut_off(self):
        with tenant_context(self.tenant):
            self.package.data_cap_mb = 0
            self.package.save(update_fields=["data_cap_mb"])
        self.record(50_000)
        cut, disable, _ = self.run_check()
        self.assertFalse(cut)
        disable.assert_not_called()

    def test_the_subscriber_is_told_why(self):
        self.record(300)
        _, _, notify = self.run_check()
        notify.assert_called_once()
        message = notify.call_args.args[1]
        self.assertIn("300MB", message)

    def test_a_failed_message_does_not_undo_the_cut_off(self):
        """The SMS provider is not allowed a veto over enforcement."""
        self.record(300)
        with patch("billing.tasks.usage_tasks.disable_customer_access"), \
             patch("billing.tasks.usage_tasks.notify_customer",
                   side_effect=RuntimeError("provider down")):
            cut = check_cap(self.customer, self.sub)

        self.assertTrue(cut)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, "suspended")

    def test_usage_before_this_bundle_is_not_counted_against_it(self):
        """
        The window is the subscription, not the calendar month.

        Counting from the first of the month meant somebody who bought a
        300 MB bundle on the 28th was measured against everything they had
        used since the 1st, and cut off before touching what they had paid
        for — while the portal, which counts from the purchase, insisted the
        bundle was untouched.
        """
        self.record(9_000, when=timezone.now() - timedelta(days=30))
        self.record(10)
        cut, disable, _ = self.run_check()
        self.assertFalse(cut)
        disable.assert_not_called()

    def test_the_window_starts_when_the_bundle_was_bought(self):
        self.assertEqual(window_start(self.sub), self.sub.start_date)

    def test_an_abandoned_purchase_does_not_raise_the_cap(self):
        """
        The cap enforced must be the cap of the package they are being served
        on, which is the paid one.

        Every subscription is born active with an unpaid invoice, so "active"
        says nothing about whether money arrived. Picking the longest-running
        active subscription hands the cap to whichever abandoned purchase was
        for the biggest package — so somebody who paid for 300 MB and also
        tapped Buy on a 10 GB weekly gets measured against 10 GB and is never
        cut off at all. This codebase has already been bitten by exactly this
        ordering in enable_customer_access.
        """
        with tenant_context(self.tenant):
            big = Package.objects.create(
                tenant=self.tenant, name="10GB Weekly", download_speed=20,
                upload_speed=10, price=Decimal("500.00"), duration_value=7,
                duration_unit="days", data_cap_mb=10 * 1024, max_devices=1)
            # Created active and unpaid, exactly as the purchase endpoint
            # leaves it when somebody abandons the M-Pesa prompt.
            Subscription.objects.create(
                tenant=self.tenant, customer=self.customer, package=big,
                status="active", start_date=timezone.now(),
                expiry_date=timezone.now() + timedelta(days=7))

        self.record(300)

        # Resolved the way the collectors resolve it — no subscription passed.
        with patch("billing.tasks.usage_tasks.disable_customer_access") as disable,              patch("billing.tasks.usage_tasks.notify_customer"):
            cut = check_cap(self.customer)

        self.assertTrue(
            cut, "the abandoned 10GB purchase was used as the cap")
        disable.assert_called_once()


class TheCutOffSticks(DataCapBase):
    """
    Point 3, and the one that matters.

    A subscriber who is disconnected but still holds an active paid
    subscription is, to every recovery path in this system, a fault to repair.
    """

    def test_the_subscription_is_suspended_not_merely_disconnected(self):
        self.record(300)
        self.run_check()
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, "suspended")
        self.assertIsNotNone(self.sub.capped_at)

    def test_a_capped_subscriber_is_not_reprovisioned(self):
        """
        The test this whole feature rests on.

        enable_customer_access is what auto-failover, the router-health
        recovery sweep, the provisioning retry and tenant re-enabling all call.
        If it grants to somebody who has spent their allowance, the cut-off
        lasts until the next sweep and no longer.
        """
        self.record(300)
        self.run_check()

        with patch("billing.router_service.safe_connect_router",
                   return_value=MagicMock()), \
             patch("billing.router_service.pick_working_router",
                   return_value=(self.router, MagicMock())), \
             patch("billing.router_service.create_pppoe_secret") as create, \
             patch("billing.router_service.enable_pppoe") as enable, \
             tenant_context(self.tenant):
            from billing.router_service import enable_customer_access
            granted = enable_customer_access(self.customer)

        self.assertFalse(
            granted, "a subscriber over their data cap was put back online")
        create.assert_not_called()
        enable.assert_not_called()

    def test_cutting_off_twice_only_counts_once(self):
        """
        Two collectors can land on the same subscriber at once. One
        suspension, one message, one log line.
        """
        self.record(300)
        first, _, notify_first = self.run_check()
        second, disable_second, notify_second = self.run_check()

        self.assertTrue(first)
        self.assertFalse(second)
        notify_first.assert_called_once()
        notify_second.assert_not_called()
        disable_second.assert_not_called()

    def test_an_unreachable_router_does_not_unwind_the_suspension(self):
        """
        The allowance really is spent.

        Leaving the subscription active so a later sweep can re-grant it is
        how a cap becomes a suggestion. Suspended-but-still-connected resolves
        itself; suspended-and-then-un-suspended does not.
        """
        self.record(300)
        with patch("billing.tasks.usage_tasks.disable_customer_access",
                   side_effect=RuntimeError("router unreachable")), \
             patch("billing.tasks.usage_tasks.notify_customer"):
            cut = check_cap(self.customer, self.sub)

        self.assertTrue(cut)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, "suspended")

    def test_the_customer_row_stops_claiming_they_are_active(self):
        """
        The console must not show somebody as connected who has been off for
        days. The expiry sweep would never correct it either — that only looks
        at subscriptions which are still active, and this one is suspended.
        """
        self.record(300)
        self.run_check()
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.status, "expired")

    def test_a_second_live_bundle_keeps_them_connected(self):
        """
        A top-up bought while a bundle was still running is a second live
        subscription. Closing out one allowance must not take the time they
        just paid for with it.
        """
        now = timezone.now()
        with tenant_context(self.tenant):
            unlimited = Package.objects.create(
                tenant=self.tenant, name="Day Pass", download_speed=10,
                upload_speed=5, price=Decimal("50.00"), duration_value=1,
                duration_unit="days", data_cap_mb=0, max_devices=1)
            topup = Subscription.objects.create(
                tenant=self.tenant, customer=self.customer, package=unlimited,
                status="active", start_date=now,
                expiry_date=now + timedelta(days=1))
            invoice = topup.invoice
            invoice.payment_status = "paid"
            invoice.save(update_fields=["payment_status"])

        self.record(300)
        with patch("billing.tasks.usage_tasks.disable_customer_access") as disable,              patch("billing.tasks.usage_tasks.notify_customer"):
            check_cap(self.customer, self.sub)

        disable.assert_not_called()
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.status, "active")
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, "suspended")

    def test_renewing_restores_access_on_a_fresh_allowance(self):
        """
        The way back on is to buy more, and it must work without anything
        having to clear the old cut-off: a renewal is a new subscription row,
        so it brings its own window and its own allowance.
        """
        self.record(300)
        self.run_check()

        now = timezone.now()
        with tenant_context(self.tenant):
            renewal = Subscription.objects.create(
                tenant=self.tenant, customer=self.customer,
                package=self.package, status="active",
                start_date=now, expiry_date=now + timedelta(days=1))
            invoice = renewal.invoice
            invoice.payment_status = "paid"
            invoice.save(update_fields=["payment_status"])

        # The old bundle's traffic is not charged against the new one.
        self.assertEqual(usage_since(self.customer, window_start(renewal)), 0)

        with patch("billing.tasks.usage_tasks.disable_customer_access") as disable, \
             patch("billing.tasks.usage_tasks.notify_customer"):
            self.assertFalse(check_cap(self.customer, renewal))
        disable.assert_not_called()

        with patch("billing.router_service.safe_connect_router",
                   return_value=MagicMock()), \
             patch("billing.router_service.pick_working_router",
                   return_value=(self.router, MagicMock())), \
             patch("billing.router_service.create_pppoe_secret",
                   return_value=True), \
             patch("billing.router_service.enable_pppoe", return_value=True), \
             tenant_context(self.tenant):
            from billing.router_service import enable_customer_access
            self.assertTrue(enable_customer_access(self.customer))


class OperatorResume(DataCapBase):
    """
    The manual way back on, which never worked.

    Suspend and resume shared one subscription lookup filtered on status
    "active" — so resume searched for the state suspend had just removed,
    found nothing, and left the subscription suspended while reporting
    success. It matters more now that a data cap suspends too.
    """

    def resume(self):
        from rest_framework.test import APIClient
        from django.contrib.auth import get_user_model

        User = get_user_model()
        with tenant_context(self.tenant):
            admin = User.objects.create_user(
                username="op", password="x", tenant=self.tenant,
                role=User.TENANT_ADMIN)

        client = APIClient()
        client.force_authenticate(admin)
        with patch("billing.views.enable_customer_access") as enable,              patch("billing.views.disable_customer_task"):
            resp = client.post(
                f"/api/admin/customers/{self.customer.id}/action/",
                {"action": "resume"}, format="json")
        return resp, enable

    def test_resume_actually_reactivates_the_subscription(self):
        self.record(300)
        self.run_check()

        resp, _ = self.resume()
        self.assertEqual(resp.status_code, 200, resp.data)

        self.sub.refresh_from_db()
        self.assertEqual(
            self.sub.status, "active",
            "resume reported success and left the subscription suspended")

    def test_resume_clears_the_cap_marker(self):
        """A row must not claim to be both live and cut off for data."""
        self.record(300)
        self.run_check()
        self.resume()

        self.sub.refresh_from_db()
        self.assertIsNone(self.sub.capped_at)


class TheSweep(DataCapBase):
    """enforce_usage_caps, which is now actually scheduled."""

    def test_the_sweep_cuts_off_an_over_cap_subscriber(self):
        self.record(400)
        with patch("billing.tasks.usage_tasks.disable_customer_access") as disable, \
             patch("billing.tasks.usage_tasks.notify_customer"):
            capped = enforce_usage_caps()

        self.assertEqual(capped, 1)
        disable.assert_called_once()
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, "suspended")

    def test_the_sweep_measures_the_paid_subscription(self):
        """Same ordering trap as check_cap, one level up."""
        with tenant_context(self.tenant):
            big = Package.objects.create(
                tenant=self.tenant, name="10GB Weekly", download_speed=20,
                upload_speed=10, price=Decimal("500.00"), duration_value=7,
                duration_unit="days", data_cap_mb=10 * 1024, max_devices=1)
            Subscription.objects.create(
                tenant=self.tenant, customer=self.customer, package=big,
                status="active", start_date=timezone.now(),
                expiry_date=timezone.now() + timedelta(days=7))

        self.record(400)
        with patch("billing.tasks.usage_tasks.disable_customer_access"),              patch("billing.tasks.usage_tasks.notify_customer"):
            self.assertEqual(enforce_usage_caps(), 1)

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, "suspended")

    def test_the_sweep_leaves_everybody_else_alone(self):
        self.record(100)
        with patch("billing.tasks.usage_tasks.disable_customer_access") as disable, \
             patch("billing.tasks.usage_tasks.notify_customer"):
            capped = enforce_usage_caps()

        self.assertEqual(capped, 0)
        disable.assert_not_called()

    def test_the_sweep_is_idempotent(self):
        """Running it twice must not cut the same person off twice."""
        self.record(400)
        with patch("billing.tasks.usage_tasks.disable_customer_access"), \
             patch("billing.tasks.usage_tasks.notify_customer"):
            self.assertEqual(enforce_usage_caps(), 1)
            self.assertEqual(enforce_usage_caps(), 0)

    def test_one_unreachable_router_does_not_end_the_sweep(self):
        """
        The subscriber after the failure is the one this protects. Letting the
        exception escape left everybody later in the ordering uncapped, which
        is a silent, ordering-dependent hole.
        """
        with tenant_context(self.tenant):
            other = Customer.objects.create(
                tenant=self.tenant, full_name="Otieno", phone="254706568712",
                connection_type="pppoe", router=self.router,
                pppoe_username="otieno", pppoe_password="secret")
            now = timezone.now()
            other_sub = Subscription.objects.create(
                tenant=self.tenant, customer=other, package=self.package,
                status="active", start_date=now,
                expiry_date=now + timedelta(days=1))
            invoice = other_sub.invoice
            invoice.payment_status = "paid"
            invoice.save(update_fields=["payment_status"])

            PPPoEUsageRecord.objects.create(
                tenant=self.tenant, customer=other, router=self.router,
                period_start=now, period_end=now,
                download_bytes=400 * MB, upload_bytes=0)

        self.record(400)

        with patch("billing.tasks.usage_tasks.check_cap",
                   side_effect=[RuntimeError("boom"), True]), \
             patch("billing.tasks.usage_tasks.notify_customer"):
            capped = enforce_usage_caps()

        self.assertEqual(
            capped, 1, "the sweep stopped at the first unreachable router")


class HotspotCapsReachTheHardware(TestCase):
    """
    The half of enforcement that does not wait for a poll.

    Polling every five minutes is fine against a 50 GB monthly package and
    absurd against a 300 MB bundle, which a subscriber on a decent link can
    spend many times over inside one collection interval. RouterOS counts the
    bytes itself and drops the session when the total is reached.
    """

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        now = timezone.now()
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r1", ip_address="10.0.0.2",
                username="u", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="300MB Hotspot", download_speed=10,
                upload_speed=5, price=Decimal("20.00"), duration_value=1,
                duration_unit="days", data_cap_mb=300, is_hotspot=True,
                max_devices=1)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Achieng", phone="254706568713",
                connection_type="hotspot", router=self.router,
                hotspot_username=PHONE_MAC)
            self.sub = Subscription.objects.create(
                tenant=self.tenant, customer=self.customer,
                package=self.package, status="active", start_date=now,
                expiry_date=now + timedelta(days=1))
            invoice = self.sub.invoice
            invoice.payment_status = "paid"
            invoice.save(update_fields=["payment_status"])

    def _grant(self):
        with patch("billing.router_service.safe_connect_router",
                   return_value=MagicMock()), \
             patch("billing.router_service.pick_working_router",
                   return_value=(self.router, MagicMock())), \
             patch("billing.router_service.ensure_hotspot_profile",
                   return_value="prof"), \
             patch("billing.router_service.hotspot_macs_for",
                   return_value=[PHONE_MAC]), \
             tenant_context(self.tenant):
            from billing.router_service import enable_customer_access
            api = MagicMock()
            with patch("billing.router_service.enable_hotspot") as enable:
                enable_customer_access(self.customer)
            return enable, api

    def test_the_byte_limit_is_pushed_onto_the_hotspot_user(self):
        enable, _ = self._grant()
        enable.assert_called_once()
        self.assertEqual(
            enable.call_args.kwargs["limit_bytes"], 300 * MB,
            "the router was not told the subscriber's data allowance")

    def test_the_limit_is_what_is_left_not_the_whole_bundle(self):
        """
        Re-provisioning happens mid-bundle — failover moves a subscriber onto
        different hardware. Handing the new router the full allowance would
        restart the count and serve the bundle twice.
        """
        with tenant_context(self.tenant):
            HotspotUsageRecord.objects.create(
                tenant=self.tenant, customer=self.customer, router=self.router,
                period_start=timezone.now(), period_end=timezone.now(),
                download_bytes=200 * MB, upload_bytes=0)

        enable, _ = self._grant()
        self.assertEqual(enable.call_args.kwargs["limit_bytes"], 100 * MB)

    def test_an_unlimited_package_gets_no_byte_limit(self):
        with tenant_context(self.tenant):
            self.package.data_cap_mb = 0
            self.package.save(update_fields=["data_cap_mb"])

        enable, _ = self._grant()
        self.assertIsNone(
            enable.call_args.kwargs["limit_bytes"],
            "an uncapped package was given a byte ceiling")

    def test_the_attribute_written_to_routeros_is_hyphenated(self):
        """
        limit-bytes-total, not limit_bytes_total.

        RouterOS answers `unknown parameter` and refuses the whole add, and
        this codebase has already shipped exactly that bug once with
        limit-uptime — every hotspot session stopped expiring on the router
        and nobody noticed until the nightly sweep was the only thing ending
        them.
        """
        from billing.router_service import enable_hotspot

        api = MagicMock()
        users = MagicMock()
        users.__iter__ = lambda self: iter([])
        api.path.return_value = users

        with patch("billing.router_service.ensure_hotspot_profile",
                   return_value="prof"), tenant_context(self.tenant):
            enable_hotspot(api, self.router, PHONE_MAC, self.package,
                           timezone.now() + timedelta(days=1),
                           limit_bytes=300 * MB)

        users.add.assert_called_once()
        attrs = users.add.call_args.kwargs
        self.assertEqual(attrs["limit-bytes-total"], str(300 * MB))

    def test_a_spent_allowance_never_becomes_no_limit(self):
        """
        RouterOS reads limit-bytes-total=0 as unlimited, so a subscriber with
        nothing left must not be handed a zero. The floor is what keeps an
        exhausted allowance exhausted if this is ever reached.
        """
        from billing.router_service import enable_hotspot

        api = MagicMock()
        users = MagicMock()
        users.__iter__ = lambda self: iter([])
        api.path.return_value = users

        with patch("billing.router_service.ensure_hotspot_profile",
                   return_value="prof"), tenant_context(self.tenant):
            enable_hotspot(api, self.router, PHONE_MAC, self.package,
                           timezone.now() + timedelta(days=1), limit_bytes=0)

        self.assertNotEqual(users.add.call_args.kwargs["limit-bytes-total"], "0")

    def test_the_allowance_is_split_across_a_customers_devices(self):
        """
        limit-bytes-total is counted per hotspot user, and a package good for
        three phones creates three of them. Giving each the whole remaining
        allowance sells 300 MB and serves 900.
        """
        macs = ["AA:BB:CC:00:00:01", "AA:BB:CC:00:00:02", "AA:BB:CC:00:00:03"]
        with patch("billing.router_service.safe_connect_router",
                   return_value=MagicMock()), \
             patch("billing.router_service.pick_working_router",
                   return_value=(self.router, MagicMock())), \
             patch("billing.router_service.ensure_hotspot_profile",
                   return_value="prof"), \
             patch("billing.router_service.hotspot_macs_for",
                   return_value=macs), \
             patch("billing.router_service.enable_hotspot") as enable, \
             tenant_context(self.tenant):
            from billing.router_service import enable_customer_access
            enable_customer_access(self.customer)

        self.assertEqual(enable.call_count, 3)
        for call in enable.call_args_list:
            self.assertEqual(call.kwargs["limit_bytes"], (300 * MB) // 3)


class DryRun(DataCapBase):
    """
    The switch that makes the first deploy survivable.

    Caps have never been enforced here, so the first sweep after switching
    them on finds everyone who has gone over since their subscription started
    -- not the few who went over in the last five minutes -- and disconnects
    all of them at once. This lets that be measured before it is done.
    """

    def test_dry_run_reports_but_does_not_disconnect(self):
        self.record(400)
        with patch("billing.tasks.usage_tasks.USAGE_CAPS_DRY_RUN", True),              patch("billing.tasks.usage_tasks.disable_customer_access") as disable,              patch("billing.tasks.usage_tasks.notify_customer") as notify:
            cut = check_cap(self.customer, self.sub)

        self.assertFalse(cut, "dry run must not count as an action taken")
        disable.assert_not_called()
        notify.assert_not_called()

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, "active")
        self.assertIsNone(self.sub.capped_at)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.status, "active")

    def test_enforcement_is_on_unless_asked_otherwise(self):
        """
        The default must be to enforce.

        A safety flag that quietly defaults to off is how a data cap becomes
        decoration again, which is the entire bug this change exists to fix.
        """
        from billing.tasks import usage_tasks

        self.assertFalse(usage_tasks.USAGE_CAPS_DRY_RUN)


class TheCutover(DataCapBase):
    """
    A cap is a promise about what you get when you buy, so it binds what is
    bought from here on.

    Switching caps on for the first time judged every bundle already running
    against a ceiling that did not exist when it was sold, and those bundles
    had been accumulating usage while nothing counted. On 2026-09-05 that
    disconnected 143 paying subscribers in six minutes, several sitting at 9GB
    against a 500MB cap nobody had told them about.
    """

    def enforce_from(self, when):
        return patch.dict(
            "os.environ", {"USAGE_CAPS_ENFORCE_FROM": when.isoformat()})

    def test_a_bundle_sold_before_the_cutover_is_not_capped(self):
        self.record(400)  # well over the 300MB cap
        with self.enforce_from(timezone.now() + timedelta(hours=1)),              patch("billing.tasks.usage_tasks.disable_customer_access") as disable,              patch("billing.tasks.usage_tasks.notify_customer"):
            cut = check_cap(self.customer, self.sub)

        self.assertFalse(cut, "a bundle sold before the cutover was cut off")
        disable.assert_not_called()
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, "active")

    def test_a_bundle_sold_after_the_cutover_is_capped(self):
        self.record(400)
        with self.enforce_from(timezone.now() - timedelta(hours=1)),              patch("billing.tasks.usage_tasks.disable_customer_access") as disable,              patch("billing.tasks.usage_tasks.notify_customer"):
            cut = check_cap(self.customer, self.sub)

        self.assertTrue(cut)
        disable.assert_called_once()

    def test_the_sweep_honours_the_cutover_too(self):
        self.record(400)
        with self.enforce_from(timezone.now() + timedelta(hours=1)),              patch("billing.tasks.usage_tasks.disable_customer_access"),              patch("billing.tasks.usage_tasks.notify_customer"):
            self.assertEqual(enforce_usage_caps(), 0)

    def test_no_cutover_set_means_every_subscription_is_capped(self):
        """The default must stay 'enforce', not 'enforce nothing'."""
        self.record(400)
        with patch.dict("os.environ", {}, clear=False),              patch("billing.tasks.usage_tasks.disable_customer_access"),              patch("billing.tasks.usage_tasks.notify_customer"):
            import os
            os.environ.pop("USAGE_CAPS_ENFORCE_FROM", None)
            self.assertTrue(check_cap(self.customer, self.sub))

    def test_a_malformed_cutover_does_not_silently_uncap_everyone(self):
        """
        A typo in an env var must not turn every cap off.

        Failing open here would be indistinguishable from the original bug --
        caps configured, nothing enforced, nobody told.
        """
        self.record(400)
        with patch.dict("os.environ", {"USAGE_CAPS_ENFORCE_FROM": "not-a-date"}),              patch("billing.tasks.usage_tasks.disable_customer_access"),              patch("billing.tasks.usage_tasks.notify_customer"):
            self.assertTrue(check_cap(self.customer, self.sub))


class TheCutoverReachesTheRouter(HotspotCapsReachTheHardware):
    """
    The quiet half.

    If the cutover is honoured by our tasks but not by the byte ceiling we
    write onto the hotspot user, nothing of ours decides anything and the
    router simply stops passing traffic. Same outcome, no log line.
    """

    def test_a_pre_cutover_bundle_gets_no_byte_limit(self):
        with patch.dict("os.environ", {
            "USAGE_CAPS_ENFORCE_FROM": (timezone.now() + timedelta(hours=1)).isoformat()
        }):
            enable, _ = self._grant()

        self.assertIsNone(
            enable.call_args.kwargs["limit_bytes"],
            "a bundle sold before the cutover was given a router byte ceiling")


class TheSweepIsScheduled(TestCase):
    """
    The cap was decoration for as long as nothing ran the check.

    Asserted against the settings rather than the task, because the task was
    always fine — it was the missing beat entry that made it dead code.
    """

    def test_enforce_usage_caps_is_in_the_beat_schedule(self):
        from django.conf import settings

        tasks = {entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()}
        self.assertIn("billing.tasks.usage_tasks.enforce_usage_caps", tasks)
