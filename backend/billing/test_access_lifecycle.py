"""
Granting and removing hotspot access, for every device rather than the first.

Both halves of this were written against `customer.hotspot_username`, which
holds one device. Once packages grew a `max_devices` that stopped meaning what
it says, and the two failures it caused are opposites:

* Granting to one device only — a package sold as good for three phones
  provisioned one. The other two were counted against the limit, told they
  were accepted, and had no account on the router to log in with.
* Removing from one device only — which would not have mattered while the
  grant was broken too, and would have become free unmetered internet the
  moment it was fixed.

So they are tested together. Fixing either alone is worse than fixing neither.
"""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from datetime import timedelta

from billing.models import (
    Customer, CustomerDevice, Package, RouterDevice, Subscription, Tenant,
    Voucher,
)
from billing.router_service import hotspot_macs_for
from billing.services.voucher_service import mark_voucher_used
from billing.tenancy import tenant_context


class FakeHotspotRouter:
    """
    Enough of librouteros to watch users appear and disappear.

    Ids are positional, as the real client takes them — see the fakes in
    tests.py, which declared remove(**kwargs) and so raised TypeError on every
    call for as long as they existed.
    """

    class Table:
        def __init__(self, rows, owner):
            self.rows = rows
            self.owner = owner

        def __iter__(self):
            return iter(list(self.rows))

        def add(self, **kwargs):
            self.owner.counter += 1
            row = {".id": f"*{self.owner.counter}", **kwargs}
            self.rows.append(row)
            return row[".id"]

        def remove(self, *ids, **kwargs):
            targets = set(ids)
            if ".id" in kwargs:
                targets.add(kwargs[".id"])
            self.rows[:] = [r for r in self.rows if r[".id"] not in targets]

    def __init__(self):
        self.counter = 0
        self.tables = {}

    def path(self, *parts):
        return self.Table(self.tables.setdefault(tuple(parts), []), self)

    def users(self):
        return self.tables.setdefault(("ip", "hotspot", "user"), [])

    def usernames(self):
        return sorted(u["name"] for u in self.users())


class HotspotDeviceSetTests(TestCase):
    """Which addresses count as a customer's, and when blocked ones do."""

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r", ip_address="10.0.0.1",
                username="a", password="p")
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Asha", phone="254700000001",
                connection_type="hotspot", router=self.router,
                hotspot_username="AA:AA:AA:AA:AA:01")
            for mac in ("AA:AA:AA:AA:AA:02", "AA:AA:AA:AA:AA:03"):
                CustomerDevice.objects.create(
                    tenant=self.tenant, customer=self.customer, mac_address=mac)

    def test_every_device_is_included_not_just_the_first(self):
        with tenant_context(self.tenant):
            macs = hotspot_macs_for(self.customer)
        self.assertEqual(len(macs), 3, macs)
        self.assertIn("AA:AA:AA:AA:AA:01", macs)  # the legacy field
        self.assertIn("AA:AA:AA:AA:AA:03", macs)

    def test_the_legacy_field_is_not_counted_twice(self):
        """A subscriber can have both a device row and the field, same MAC."""
        with tenant_context(self.tenant):
            CustomerDevice.objects.create(
                tenant=self.tenant, customer=self.customer,
                mac_address="AA:AA:AA:AA:AA:01")
            macs = hotspot_macs_for(self.customer)
        self.assertEqual(len(macs), 3, macs)

    def test_granting_skips_a_blocked_device(self):
        with tenant_context(self.tenant):
            CustomerDevice.objects.filter(
                mac_address="AA:AA:AA:AA:AA:02").update(blocked=True)
            macs = hotspot_macs_for(self.customer, include_blocked=False)
        self.assertNotIn("AA:AA:AA:AA:AA:02", macs)

    def test_removing_includes_a_blocked_device(self):
        """
        Blocking is refused at redemption and never reaches the router, so a
        device blocked while already connected keeps its session unless the
        removal takes it off too.
        """
        with tenant_context(self.tenant):
            CustomerDevice.objects.filter(
                mac_address="AA:AA:AA:AA:AA:02").update(blocked=True)
            macs = hotspot_macs_for(self.customer)
        self.assertIn("AA:AA:AA:AA:AA:02", macs)


class HotspotGrantAndRemoveTests(TestCase):
    """The round trip: three devices on, three devices off."""

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        self.api = FakeHotspotRouter()
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r", ip_address="10.0.0.1",
                username="a", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="3 devices", download_speed=5,
                upload_speed=2, price=Decimal("200.00"), duration_value=1,
                duration_unit="days", data_cap_mb=0, is_hotspot=True,
                max_devices=3)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Asha", phone="254700000002",
                connection_type="hotspot", router=self.router,
                hotspot_username="BB:BB:BB:BB:BB:01")
            for mac in ("BB:BB:BB:BB:BB:02", "BB:BB:BB:BB:BB:03"):
                CustomerDevice.objects.create(
                    tenant=self.tenant, customer=self.customer, mac_address=mac)
            self.subscription = Subscription.objects.create(
                tenant=self.tenant, customer=self.customer,
                package=self.package, status="active",
                expiry_date=timezone.now() + timedelta(days=1))

    def _grant(self):
        """
        enable_hotspot resolves the user profile through
        ensure_hotspot_profile, which opens its *own* connection from the
        RouterDevice row rather than using the api it was handed. Unpatched,
        these tests dial 10.0.0.1 and time out. The profile itself is covered
        by the router_profiles tests; what is under test here is how many
        devices get a user.
        """
        from billing.router_service import _grant_hotspot
        with patch("billing.router_service.ensure_hotspot_profile",
                   return_value="HOTSPOT_PKG_1_D3"):
            with tenant_context(self.tenant):
                return _grant_hotspot(
                    self.api, self.router, self.customer, self.package,
                    self.subscription.expiry_date)

    def test_a_three_device_package_provisions_three_devices(self):
        granted = self._grant()
        self.assertEqual(granted, 3)
        self.assertEqual(len(self.api.usernames()), 3, self.api.usernames())

    def test_expiry_removes_every_device_not_only_the_first(self):
        self._grant()
        from billing.router_service import disable_hotspot
        with tenant_context(self.tenant):
            for mac in hotspot_macs_for(self.customer):
                disable_hotspot(self.api, mac)
        self.assertEqual(self.api.usernames(), [],
                         "devices were left on the router after expiry")

    def test_a_blocked_device_is_not_granted_but_is_removed(self):
        with tenant_context(self.tenant):
            CustomerDevice.objects.filter(
                mac_address="BB:BB:BB:BB:BB:02").update(blocked=True)
        self.assertEqual(self._grant(), 2)

        # It was blocked after connecting, so a session may still exist. The
        # removal has to cover it even though the grant did not.
        with tenant_context(self.tenant):
            self.assertIn("BB:BB:BB:BB:BB:02", hotspot_macs_for(self.customer))


class VoucherFirstUseTests(TestCase):
    """`bound_mac` and `first_used_at` were declared and never written."""

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r", ip_address="10.0.0.1",
                username="a", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="1hr", download_speed=5,
                upload_speed=2, price=Decimal("50.00"), duration_value=1,
                duration_unit="hours", data_cap_mb=0, is_hotspot=True)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Juma", phone="254700000003",
                connection_type="hotspot", router=self.router)
            self.subscription = Subscription.objects.create(
                tenant=self.tenant, customer=self.customer,
                package=self.package, status="active",
                expiry_date=timezone.now() + timedelta(hours=1))
            self.voucher = Voucher.objects.create(
                tenant=self.tenant, code="ABC123",
                subscription=self.subscription,
                expires_at=self.subscription.expiry_date)

    def test_first_use_is_recorded(self):
        with tenant_context(self.tenant):
            mark_voucher_used(self.subscription, "CC:CC:CC:CC:CC:01")
            self.voucher.refresh_from_db()
        self.assertIsNotNone(self.voucher.first_used_at)
        self.assertEqual(self.voucher.bound_mac, "CC:CC:CC:CC:CC:01")

    def test_a_later_device_does_not_overwrite_the_first(self):
        """
        On a multi-device package the second phone is a legitimate use, not a
        correction. The record is of when the code was first redeemed.
        """
        with tenant_context(self.tenant):
            mark_voucher_used(self.subscription, "CC:CC:CC:CC:CC:01")
            self.voucher.refresh_from_db()
            first = self.voucher.first_used_at

            mark_voucher_used(self.subscription, "CC:CC:CC:CC:CC:02")
            self.voucher.refresh_from_db()

        self.assertEqual(self.voucher.first_used_at, first)
        self.assertEqual(self.voucher.bound_mac, "CC:CC:CC:CC:CC:01")

    def test_recording_never_costs_the_customer_their_connection(self):
        """A missing record is a support inconvenience; a raised exception
        here would refuse a working code."""
        with tenant_context(self.tenant):
            mark_voucher_used(None, "CC:CC:CC:CC:CC:01")  # must not raise


class MacAllowedTests(TestCase):
    """
    The guard that refused any second device on a multi-device package.

    Dead in production — the redemption view passes no MAC — but wrong, and a
    caller wiring it up would have silently reduced every package to one
    device.
    """

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r", ip_address="10.0.0.1",
                username="a", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="p", download_speed=5, upload_speed=2,
                price=Decimal("100.00"), duration_value=1, duration_unit="days",
                data_cap_mb=0, is_hotspot=True, max_devices=3)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Asha", phone="254700000004",
                connection_type="hotspot", router=self.router,
                hotspot_username="DD:DD:DD:DD:DD:01")
            CustomerDevice.objects.create(
                tenant=self.tenant, customer=self.customer,
                mac_address="DD:DD:DD:DD:DD:02")
            self.subscription = Subscription.objects.create(
                tenant=self.tenant, customer=self.customer,
                package=self.package, status="active",
                expiry_date=timezone.now() + timedelta(days=1))

    def _allowed(self, mac):
        from billing.services.voucher_service import _mac_allowed
        with tenant_context(self.tenant):
            return _mac_allowed(self.subscription, mac)

    def test_the_first_device_is_allowed(self):
        self.assertTrue(self._allowed("DD:DD:DD:DD:DD:01"))

    def test_a_second_bound_device_is_allowed(self):
        self.assertTrue(self._allowed("DD:DD:DD:DD:DD:02"))

    def test_an_unknown_device_is_not(self):
        self.assertFalse(self._allowed("DD:DD:DD:DD:DD:99"))

    def test_no_mac_is_allowed(self):
        """The redemption view calls it this way and does its own accounting."""
        self.assertTrue(self._allowed(None))
