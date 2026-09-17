"""
The reconciler behind expiry, and the device allowance the rule has to respect.

Two faults found on 2026-09-17, both of them the same shape: a device reading
as entitled when granting would never write an account for it, and an account
left enabled when nothing entitles it any more.

  * `subscription_for_device` answered with any live package a device was
    bound to, ignoring how many places that package sells. Forty customers had
    more devices bound to a live package than it sells -- one of them seven
    against a package selling one. Nobody gained service, because the grant
    trims to the allowance, but the uptime sweep kept a limit fresh for those
    devices, expiry declined to take them off, and reconnect answered
    "allowed" to handsets that cannot log in.

  * 91 accounts were enabled on skylink3 with no live package behind them, 5
    online, 31.78 GB served. They were disabled at 08:14 and 32 were back by
    10:17 carrying the provisioning stamp rather than the clean-up's -- so they
    had been re-created. A payment that lands before its code is typed has no
    device of its own, so the grant falls back to the customer's most recently
    seen address; when the code is then typed on another handset, the first one
    keeps a working account nothing covers.

So the rule respects the allowance, and an hourly sweep closes what the two
paths leave behind, in the shape the orphan sweep already uses.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from billing.models import (
    Customer, CustomerDevice, Invoice, Package, RouterDevice, Subscription,
    Tenant,
)
from billing.services.entitlement import subscription_for_device
from billing.services.uncovered_logins import (
    DEFAULT_MAX_DISABLE, close_uncovered_logins, find_uncovered_logins,
)
from billing.tenancy import tenant_context

FIRST = "AA:22:33:44:55:01"
SECOND = "BB:22:33:44:55:02"


class FakeUsers(list):
    """An ip/hotspot/user path that records what was written to it."""

    def __init__(self, rows):
        super().__init__(rows)
        self.updates = []

    def update(self, **kwargs):
        self.updates.append(kwargs)
        for row in self:
            if row[".id"] == kwargs[".id"]:
                row.update({k: v for k, v in kwargs.items() if k != ".id"})


class FakeActives(list):
    def __init__(self, rows):
        super().__init__(rows)
        self.removed = []

    def remove(self, *ids):
        self.removed.extend(ids)


class FakeApi:
    def __init__(self, users, actives=()):
        self.users = FakeUsers(users)
        self.actives = FakeActives(list(actives))

    def path(self, *parts):
        return self.actives if parts[-1] == "active" else self.users


class UncoveredLoginsTests(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="unc-r", ip_address="10.9.6.1",
                username="u", password="p", is_active=True, is_online=True)
            self.package = Package.objects.create(
                tenant=self.tenant, name="3hrs - 3GB", download_speed=5,
                upload_speed=2, price=Decimal("10.00"), duration_value=3,
                duration_unit="hours", data_cap_mb=3000, is_hotspot=True,
                max_devices=1)
            self.two_device = Package.objects.create(
                tenant=self.tenant, name="2 devices", download_speed=5,
                upload_speed=2, price=Decimal("50.00"), duration_value=1,
                duration_unit="days", data_cap_mb=5000, is_hotspot=True,
                max_devices=2)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Sweep Me",
                phone="254700880001", connection_type="hotspot",
                router=self.router, status="active")

    def _sub(self, package, *, expires_in, mac=None, paid=True, last_seen=None):
        with tenant_context(self.tenant):
            sub = Subscription.objects.create(
                tenant=self.tenant, customer=self.customer, package=package,
                status="active")
            Subscription.objects.filter(pk=sub.pk).update(
                expiry_date=timezone.now() + expires_in)
            Invoice.objects.filter(subscription=sub).update(
                payment_status="paid" if paid else "pending")
            sub.refresh_from_db()
            if mac:
                device = CustomerDevice.objects.create(
                    tenant=self.tenant, customer=self.customer,
                    subscription=sub, mac_address=mac)
                if last_seen:
                    CustomerDevice.objects.filter(pk=device.pk).update(
                        last_seen=last_seen)
        return sub

    def _api(self, *macs, online=()):
        rows = [{".id": f"*{i}", "name": m, "disabled": "false",
                 "comment": "AUTO | WIFI BILLING SYSTEM"}
                for i, m in enumerate(macs, start=1)]
        actives = [{".id": f"*A{i}", "mac-address": m, "uptime": "1h"}
                   for i, m in enumerate(online, start=1)]
        return FakeApi(rows, actives)

    def _sweep(self, api, **kwargs):
        with tenant_context(self.tenant):
            return close_uncovered_logins(self.router, api, **kwargs)

    # ---- the allowance the rule has to respect ---------------------------

    def test_a_device_beyond_the_allowance_is_not_covered(self):
        """
        Both handsets are bound to a package selling one place. The grant would
        write an account for the newer one only, so only that one is covered.
        """
        sub = self._sub(self.package, expires_in=timedelta(hours=3), mac=FIRST,
                        last_seen=timezone.now() - timedelta(hours=2))
        with tenant_context(self.tenant):
            CustomerDevice.objects.create(
                tenant=self.tenant, customer=self.customer, subscription=sub,
                mac_address=SECOND)

            self.assertIsNotNone(
                subscription_for_device(self.customer, SECOND),
                "the handset the package would be granted to reads as covered")
            self.assertIsNone(
                subscription_for_device(self.customer, FIRST),
                "a device past what the package sells read as covered, so "
                "expiry would leave it and reconnect would admit it")

    def test_both_devices_are_covered_when_the_package_sells_two(self):
        sub = self._sub(self.two_device, expires_in=timedelta(days=1), mac=FIRST)
        with tenant_context(self.tenant):
            CustomerDevice.objects.create(
                tenant=self.tenant, customer=self.customer, subscription=sub,
                mac_address=SECOND)
            for mac in (FIRST, SECOND):
                self.assertIsNotNone(subscription_for_device(self.customer, mac))

    # ---- what the sweep takes off ----------------------------------------

    def test_it_disables_an_account_no_live_package_covers(self):
        """
        The handset's own three hours are over; the customer's live package
        belongs to their other phone. Nothing covers this one.
        """
        self._sub(self.package, expires_in=timedelta(hours=-1), mac=FIRST)
        self._sub(self.package, expires_in=timedelta(hours=3), mac=SECOND)
        api = self._api(FIRST, SECOND)

        disabled, ended, refused = self._sweep(api)

        self.assertEqual((disabled, refused), (1, False))
        self.assertEqual(api.users.updates[0][".id"], "*1")
        self.assertIs(api.users.updates[0]["disabled"], True)

    def test_the_covered_account_is_left_alone(self):
        self._sub(self.package, expires_in=timedelta(hours=-1), mac=FIRST)
        self._sub(self.package, expires_in=timedelta(hours=3), mac=SECOND)
        api = self._api(FIRST, SECOND)

        self._sweep(api)

        self.assertNotIn("*2", [u[".id"] for u in api.users.updates])

    def test_it_stamps_what_it_did_on_the_account(self):
        """Kept on the router: it survives a restore and an operator can read it."""
        self._sub(self.package, expires_in=timedelta(hours=-1), mac=FIRST)
        self._sub(self.package, expires_in=timedelta(hours=3), mac=SECOND)
        api = self._api(FIRST, SECOND)

        self._sweep(api)

        self.assertIn("uncovered", api.users.updates[0]["comment"])

    def test_it_ends_the_live_session_too(self):
        """
        limit-uptime counts connected time, so a session left running outlives
        the package that paid for it by whatever of that limit is unspent.
        """
        self._sub(self.package, expires_in=timedelta(hours=-1), mac=FIRST)
        self._sub(self.package, expires_in=timedelta(hours=3), mac=SECOND)
        api = self._api(FIRST, SECOND, online=[FIRST])

        disabled, ended, _ = self._sweep(api)

        self.assertEqual((disabled, ended), (1, 1))
        self.assertEqual(api.actives.removed, ["*A1"])

    def test_a_customer_with_nothing_live_is_swept_too(self):
        self._sub(self.package, expires_in=timedelta(hours=-1), mac=FIRST)
        api = self._api(FIRST)

        disabled, _, _ = self._sweep(api)

        self.assertEqual(disabled, 1)

    def test_an_address_belonging_to_nobody_is_swept(self):
        """A device row that is gone leaves an account reachable by no caller."""
        api = self._api("CC:22:33:44:55:03")

        disabled, _, _ = self._sweep(api)

        self.assertEqual(disabled, 1)

    # ---- what it must not touch ------------------------------------------

    def test_a_password_account_is_left_to_the_other_sweep(self):
        """
        `admin` and `default-trial` are judged by whether any purchase could
        stand behind them at all, which is close_unearned_logins' rule.
        """
        api = self._api("admin")

        disabled, _, _ = self._sweep(api)

        self.assertEqual(disabled, 0)
        self.assertEqual(api.users.updates, [])

    def test_an_already_disabled_account_is_not_touched_again(self):
        self._sub(self.package, expires_in=timedelta(hours=-1), mac=FIRST)
        api = self._api(FIRST)
        api.users[0]["disabled"] = "true"

        disabled, _, _ = self._sweep(api)

        self.assertEqual(disabled, 0)

    def test_it_refuses_to_run_when_the_number_is_not_believable(self):
        """
        The failure mode of subtracting a database answer from a router's list:
        an empty answer makes every account look uncovered, and this would
        disable the estate while doing exactly what it was told.
        """
        macs = [f"AA:00:00:00:{i:02X}:{i:02X}" for i in range(1, 8)]
        api = self._api(*macs)

        disabled, ended, refused = self._sweep(api, max_disable=5)

        self.assertTrue(refused)
        self.assertEqual((disabled, ended), (0, 0))
        self.assertEqual(api.users.updates, [],
                         "it disabled accounts past the ceiling")

    def test_reading_changes_nothing(self):
        self._sub(self.package, expires_in=timedelta(hours=-1), mac=FIRST)
        api = self._api(FIRST)

        with tenant_context(self.tenant):
            found = find_uncovered_logins(self.router, api)

        self.assertEqual(len(found), 1)
        self.assertEqual(api.users.updates, [])

    def test_the_ceiling_is_above_the_backlog_that_was_found(self):
        """91 on one router on 2026-09-17; a ceiling under that is useless."""
        self.assertGreaterEqual(DEFAULT_MAX_DISABLE, 91)
