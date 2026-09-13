"""
A package sold for one device is granted to one device.

macs_to_grant already stopped old PURCHASES from sharing an allowance. It did
nothing about several devices piling onto one purchase, which is what a handset
rotating its MAC does without anybody deciding it. _remaining_data_bytes
divides the allowance by however many are granted, so the phone in the
subscriber's hand was stopped at a fraction of what they paid for while the
remainder sat on addresses that were never coming back.

38 subscribers were split too many ways on 2026-09-13. Two of them held three
devices against a one-device package and were given 13333 MB each of a 40 GB
bundle.

The trim only narrows what is granted from here. Accounts already on the router
for the devices it drops stay where they are -- cutting somebody off mid-session
to correct an allowance is worse than the allowance.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from billing.models import (
    Customer, CustomerDevice, Invoice, Package, RouterDevice, Subscription,
    Tenant,
)
from billing.router_service import (
    _remaining_data_bytes, _trim_to_package_limit, macs_to_grant,
)
from billing.services.usage import MB
from billing.tenancy import tenant_context


class DevicePlacesAreCapped(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="trim-r", ip_address="10.9.0.70",
                username="u", password="p")
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Rotating", phone="254700160001",
                connection_type="hotspot", router=self.router, status="active")

    def _package(self, *, places, cap_mb=3000, name="3hrs - 3GB"):
        with tenant_context(self.tenant):
            return Package.objects.create(
                tenant=self.tenant, name=name, download_speed=5,
                upload_speed=2, price=Decimal("30.00"), duration_value=3,
                duration_unit="hours", data_cap_mb=cap_mb, is_hotspot=True,
                max_devices=places)

    def _sub(self, package):
        with tenant_context(self.tenant):
            sub = Subscription.objects.create(
                customer=self.customer, package=package, status="active")
            Invoice.objects.filter(subscription=sub).update(
                payment_status="paid")
            sub.refresh_from_db()
        return sub

    def _bind(self, sub, mac, *, minutes_ago=0, blocked=False):
        seen = timezone.now() - timezone.timedelta(minutes=minutes_ago)
        with tenant_context(self.tenant):
            d = CustomerDevice.objects.create(
                tenant=self.tenant, customer=self.customer, subscription=sub,
                mac_address=mac, blocked=blocked)
            CustomerDevice.objects.filter(pk=d.pk).update(last_seen=seen)
        return mac

    # ------------------------------------------------------------ the trim

    def test_one_place_grants_one_device(self):
        """The production shape: a rotating handset bound three times."""
        pkg = self._package(places=1)
        sub = self._sub(pkg)
        self._bind(sub, "AA:AA:AA:00:00:01", minutes_ago=300)
        self._bind(sub, "AA:AA:AA:00:00:02", minutes_ago=120)
        newest = self._bind(sub, "AA:AA:AA:00:00:03", minutes_ago=1)

        granted = macs_to_grant(self.customer, sub)

        self.assertEqual(granted, [newest])

    def test_it_keeps_the_device_most_recently_seen(self):
        """
        The one the subscriber is holding. The addresses a handset rotated away
        from are by definition the older ones, so dropping the oldest drops
        exactly those.
        """
        pkg = self._package(places=2)
        sub = self._sub(pkg)
        self._bind(sub, "AA:AA:AA:00:00:01", minutes_ago=900)
        second = self._bind(sub, "AA:AA:AA:00:00:02", minutes_ago=30)
        first = self._bind(sub, "AA:AA:AA:00:00:03", minutes_ago=2)

        granted = macs_to_grant(self.customer, sub)

        self.assertEqual(granted, [first, second])

    def test_a_package_sold_for_three_still_grants_three(self):
        """The trim is a ceiling, not a new limit of its own."""
        pkg = self._package(places=3)
        sub = self._sub(pkg)
        for i in range(3):
            self._bind(sub, f"AA:AA:AA:00:01:0{i}", minutes_ago=i)

        self.assertEqual(len(macs_to_grant(self.customer, sub)), 3)

    def test_fewer_devices_than_places_is_left_alone(self):
        pkg = self._package(places=3)
        sub = self._sub(pkg)
        only = self._bind(sub, "AA:AA:AA:00:02:01")

        self.assertEqual(macs_to_grant(self.customer, sub), [only])

    def test_blocked_devices_do_not_take_up_a_place(self):
        """
        A blocked device is not being served, so counting it would cost the
        subscriber a place they paid for.
        """
        pkg = self._package(places=1)
        sub = self._sub(pkg)
        self._bind(sub, "AA:AA:AA:00:03:01", minutes_ago=1, blocked=True)
        real = self._bind(sub, "AA:AA:AA:00:03:02", minutes_ago=60)

        self.assertEqual(macs_to_grant(self.customer, sub), [real])

    # ------------------------------------------------- the unbound fallback

    def test_the_unbound_fallback_is_trimmed_too(self):
        """
        A renewal whose devices are not bound yet falls back to every address
        the customer has ever used. That branch is where the 2026-09-11 bug
        lived -- untrimmed, it hands the whole history to the divisor.
        """
        pkg = self._package(places=1)
        old = self._sub(pkg)
        self._bind(old, "AA:AA:AA:00:04:01", minutes_ago=500)
        self._bind(old, "AA:AA:AA:00:04:02", minutes_ago=400)
        renewal = self._sub(pkg)

        granted = macs_to_grant(self.customer, renewal)

        self.assertEqual(len(granted), 1)

    def test_the_fallback_still_grants_somebody(self):
        """
        The one outcome worse than granting too much is granting nobody. A
        customer who has just paid must not come away with an empty list.
        """
        pkg = self._package(places=1)
        old = self._sub(pkg)
        self._bind(old, "AA:AA:AA:00:05:01")
        renewal = self._sub(pkg)

        self.assertTrue(macs_to_grant(self.customer, renewal))

    # ------------------------------------------------------ the arithmetic

    def test_the_allowance_is_divided_by_what_is_granted(self):
        """
        The point of the whole change. Three bound devices on a one-device 3 GB
        package gave each 1000 MB, and the subscriber stopped at a third of
        what they bought.
        """
        pkg = self._package(places=1, cap_mb=3000)
        sub = self._sub(pkg)
        for i in range(3):
            self._bind(sub, f"AA:AA:AA:00:06:0{i}", minutes_ago=i)

        self.assertEqual(_remaining_data_bytes(self.customer, sub), 3000 * MB)

    def test_a_two_device_package_still_splits_in_two(self):
        """Splitting is correct where the places were actually sold."""
        pkg = self._package(places=2, cap_mb=4000)
        sub = self._sub(pkg)
        for i in range(4):
            self._bind(sub, f"AA:AA:AA:00:07:0{i}", minutes_ago=i)

        self.assertEqual(_remaining_data_bytes(self.customer, sub), 2000 * MB)

    def test_the_divisor_matches_what_is_written_to_the_router(self):
        """
        These two have to be the same set. Divide by more than is granted and
        the subscriber is short-changed; by fewer and every device gets the
        whole allowance.
        """
        pkg = self._package(places=2, cap_mb=4000)
        sub = self._sub(pkg)
        for i in range(5):
            self._bind(sub, f"AA:AA:AA:00:08:0{i}", minutes_ago=i)

        granted = macs_to_grant(self.customer, sub, include_blocked=False)
        share = _remaining_data_bytes(self.customer, sub)

        self.assertEqual(share * len(granted), 4000 * MB)

    # -------------------------------------------------------- the edges

    def test_a_missing_limit_is_read_as_one_place(self):
        """
        Not as unlimited. A bad row must not hand the whole allowance to every
        address a customer has ever used -- one place is the field's own
        default and the safe reading.
        """
        self.assertEqual(
            _trim_to_package_limit(["A", "B", "C"], None), ["A"])

    def test_a_zero_limit_is_read_as_one_place(self):
        pkg = self._package(places=1)
        sub = self._sub(pkg)
        with tenant_context(self.tenant):
            Package.objects.filter(pk=pkg.pk).update(max_devices=0)
            sub.package.refresh_from_db()

        self.assertEqual(_trim_to_package_limit(["A", "B"], sub), ["A"])

    def test_no_subscription_is_not_trimmed(self):
        """
        Nothing says how many places were sold, and this path is used where the
        caller has already decided the set.
        """
        self._bind(self._sub(self._package(places=1)), "AA:AA:AA:00:09:01")
        self._bind(self._sub(self._package(places=1)), "AA:AA:AA:00:09:02")

        self.assertEqual(len(macs_to_grant(self.customer, None)), 2)

    def test_the_router_is_given_exactly_the_trimmed_set(self):
        """
        _grant_hotspot writes one account per granted MAC. If the trim did not
        reach it, the dropped devices would keep being provisioned with a share
        computed for a smaller set.
        """
        from billing.router_service import _grant_hotspot

        pkg = self._package(places=1, cap_mb=3000)
        sub = self._sub(pkg)
        for i in range(3):
            self._bind(sub, f"AA:AA:AA:00:0A:0{i}", minutes_ago=i)

        with patch("billing.router_service.enable_hotspot") as enable, \
             patch("billing.router_service.retry_mac_login"):
            granted = _grant_hotspot(
                MagicMock(), self.router, self.customer, pkg,
                sub.expiry_date, subscription=sub)

        self.assertEqual(granted, 1)
        self.assertEqual(enable.call_count, 1)
        self.assertEqual(
            enable.call_args.kwargs["limit_bytes"], 3000 * MB)
