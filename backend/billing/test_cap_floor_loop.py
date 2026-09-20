"""
The last megabyte of a bundle, which used to be served forever.

A subscriber whose remaining allowance falls below MIN_BYTE_CEILING is in a
band where the two halves of cap enforcement disagreed:

  * `_remaining_data_bytes` said they had less than a megabyte left, and
    `enable_hotspot` floored that to a megabyte, because RouterOS reads
    `limit-bytes-total=0` as no limit at all. So the router was told to serve
    them MORE than they were entitled to.

  * `check_cap` compared used against the cap itself, found them still under
    it, and left the subscription active.

Neither half is wrong on its own and together they are a loop. Observed on
skylink3 on 2026-09-20, customer 1757, 966,879 bytes short of a 20 GB bundle:

    17:42:04  logged out: traffic limit reached
    17:44:47  hotspot user added ... limit-bytes-total=1048576
    17:44:48  logged in
    17:47:50  logged out: traffic limit reached
    17:49:44  hotspot user added ... limit-bytes-total=1048576

Every couple of minutes, all day. The captive portal still open on the handset
re-requested access each time the router dropped them, and each grant removes
and re-adds the account, which resets the router's byte counters -- so the
collector re-baselined, threw the interval away, and their recorded usage never
climbed the last megabyte to where the cap would have bitten. Two subscribers
were in the band at once; every subscriber passes through it on the way out of
every bundle.

The fix is one number read by both halves. Below it the cap has bitten, and
the hardware is not asked to pretend otherwise.
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
    RouterDevice,
    Subscription,
    Tenant,
)
from billing.services.usage import MB, MIN_BYTE_CEILING
from billing.tasks.usage_tasks import _cap_threshold, check_cap
from billing.tenancy import tenant_context

PHONE_MAC = "AA:BB:CC:00:11:33"


class LastMegabyteBase(TestCase):
    """One hotspot subscriber on a 300 MB bundle."""

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        now = timezone.now()

        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r1", ip_address="10.0.0.1",
                username="u", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="300MB Hotspot", download_speed=10,
                upload_speed=5, price=Decimal("20.00"), duration_value=1,
                duration_unit="days", data_cap_mb=300, is_hotspot=True,
                max_devices=1)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Otieno", phone="254706568714",
                connection_type="hotspot", router=self.router,
                hotspot_username=PHONE_MAC)
            self.sub = Subscription.objects.create(
                tenant=self.tenant, customer=self.customer,
                package=self.package, status="active", start_date=now,
                expiry_date=now + timedelta(days=1))
            invoice = self.sub.invoice
            invoice.payment_status = "paid"
            invoice.save(update_fields=["payment_status"])

    def spend(self, byte_count):
        """Put `byte_count` of recorded traffic on the subscriber."""
        with tenant_context(self.tenant):
            HotspotUsageRecord.objects.create(
                tenant=self.tenant, customer=self.customer, router=self.router,
                period_start=timezone.now(), period_end=timezone.now(),
                download_bytes=int(byte_count), upload_bytes=0)

    def run_check(self):
        with patch("billing.tasks.usage_tasks.disable_customer_access"), \
             patch("billing.tasks.usage_tasks.notify_customer"):
            return check_cap(self.customer, self.sub)

    def grant(self):
        """_grant_hotspot against a stub router, as _provision calls it."""
        from billing.router_service import _grant_hotspot

        with patch("billing.router_service.ensure_hotspot_profile",
                   return_value="prof"), \
             patch("billing.router_service.retry_mac_login"), \
             patch("billing.router_service.enable_hotspot") as enable, \
             tenant_context(self.tenant):
            _grant_hotspot(MagicMock(), self.router, self.customer,
                           self.package, self.sub.expiry_date,
                           subscription=self.sub)
        return enable


class TheCapBitesBeforeTheFloor(LastMegabyteBase):
    """Fix 2: an allowance too small to express is an allowance that is spent."""

    def test_a_subscriber_inside_the_last_megabyte_is_cut_off(self):
        """
        The assertion the loop turned on. 966,879 bytes short of the cap read
        as under it, every sweep, forever.
        """
        self.spend(300 * MB - 500_000)
        self.assertTrue(
            self.run_check(),
            "a subscriber with less left than the smallest ceiling the router "
            "can be given was left active")

    def test_a_subscriber_with_room_to_spare_is_left_alone(self):
        """The floor must not become a reason to disconnect people early."""
        self.spend(200 * MB)
        self.assertFalse(self.run_check())

    def test_the_megabyte_before_the_cap_is_the_only_ground_given(self):
        """
        Exactly at the threshold, and one byte below it. A megabyte early is
        the whole of the concession -- anything more would be cutting short a
        bundle somebody paid for.
        """
        self.spend(300 * MB - MIN_BYTE_CEILING - 1)
        self.assertFalse(self.run_check(), "cut off more than a megabyte early")

        self.spend(1)  # now exactly at the threshold
        self.assertTrue(self.run_check())

    def test_a_cap_smaller_than_the_floor_is_its_own_threshold(self):
        """
        A bundle smaller than the smallest ceiling we can write is a
        misconfiguration, not a case to be clever about. Subtracting the floor
        from it would cut the subscriber off before they had used a byte.
        """
        self.assertEqual(_cap_threshold(MIN_BYTE_CEILING, "hotspot"),
                         MIN_BYTE_CEILING)
        self.assertEqual(_cap_threshold(MIN_BYTE_CEILING // 2, "hotspot"),
                         MIN_BYTE_CEILING // 2)

    def test_an_ordinary_cap_is_reduced_by_exactly_the_floor(self):
        self.assertEqual(_cap_threshold(300 * MB, "hotspot"),
                         300 * MB - MIN_BYTE_CEILING)

    def test_a_pppoe_bundle_is_not_shortened(self):
        """
        The megabyte comes off because `limit-bytes-total` cannot express
        less, and PPPoE has no limit-bytes-total. Taking it from them anyway
        would be charging them for a constraint their connection has not got.
        """
        self.assertEqual(_cap_threshold(300 * MB, "pppoe"), 300 * MB)


class TheHardwareIsNotAskedToPretend(LastMegabyteBase):
    """Fix 1: the grant refuses what the ceiling cannot express."""

    def test_a_spent_allowance_is_refused_rather_than_floored(self):
        from billing.router_service import NotEntitled

        self.spend(300 * MB - 500_000)
        with self.assertRaises(NotEntitled):
            self.grant()

    def test_the_refusal_names_the_reason(self):
        """
        An operator reading the log needs to tell this apart from the other
        thing that raises NotEntitled -- an unpaid or expired subscription.
        """
        from billing.router_service import NotEntitled

        self.spend(300 * MB - 500_000)
        with self.assertRaises(NotEntitled) as caught:
            self.grant()
        self.assertIn("allowance is spent", str(caught.exception))

    def test_an_allowance_above_the_floor_is_granted_untouched(self):
        self.spend(200 * MB)
        enable = self.grant()
        enable.assert_called_once()
        self.assertEqual(enable.call_args.kwargs["limit_bytes"], 100 * MB)

    def test_an_uncapped_package_is_never_refused(self):
        """None is unlimited, not zero. It must not trip a floor check."""
        with tenant_context(self.tenant):
            Subscription.objects.all_tenants().filter(
                pk=self.sub.pk).update(data_cap_mb=0)
            self.sub.refresh_from_db()

        enable = self.grant()
        enable.assert_called_once()
        self.assertIsNone(enable.call_args.kwargs["limit_bytes"])


class TheRefusalReachesTheCallerAsAnAnswer(LastMegabyteBase):
    """
    enable_customer_access answers True or False, and must go on doing so.

    Every caller from the payment path to the captive portal reads it as "did
    they get on". A refusal raised through it would reach a subscriber
    standing at a portal as a 500 instead.
    """

    def test_enable_customer_access_returns_false_rather_than_raising(self):
        from billing.router_service import enable_customer_access

        self.spend(300 * MB - 500_000)
        with patch("billing.router_service.pick_working_router",
                   return_value=(self.router, MagicMock())), \
             patch("billing.router_service.ensure_hotspot_profile",
                   return_value="prof"), \
             patch("billing.router_service.retry_mac_login"), \
             tenant_context(self.tenant):
            granted = enable_customer_access(self.customer, self.sub)

        self.assertFalse(
            granted,
            "a spent allowance must be reported as not provisioned, not "
            "raised at whoever asked")

    def test_a_subscriber_with_allowance_left_still_gets_on(self):
        from billing.router_service import enable_customer_access

        self.spend(100 * MB)
        with patch("billing.router_service.pick_working_router",
                   return_value=(self.router, MagicMock())), \
             patch("billing.router_service.ensure_hotspot_profile",
                   return_value="prof"), \
             patch("billing.router_service.retry_mac_login"), \
             patch("billing.router_service.enable_hotspot"), \
             tenant_context(self.tenant):
            granted = enable_customer_access(self.customer, self.sub)

        self.assertTrue(granted)
