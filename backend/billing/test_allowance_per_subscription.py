"""
A subscriber's allowance belongs to the subscription they paid for.

Access was granted by customer while places are sold by subscription, and the
two disagree the moment somebody buys twice. CustomerDevice.subscription exists
precisely to keep them apart -- "Counting places against the subscription that
granted them is what makes a second payment worth something" -- and the grant
path ignored it.

It cost twice over. Every handset from every past purchase was put back on the
router by the next one, on a package sold for two devices. And
_remaining_data_bytes divides the allowance by however many devices are
granted, so that same history quietly shrank what the customer got.

Found on 2026-09-11 on a live account: customer 32 paid 250/- for a 5 GB
three-week package, and their television was written to the router with
limit-bytes-total=1000 MB, because four addresses from expired subscriptions
were counted alongside it. Five devices, one fifth each.

The invariant these hold: the set granted and the set divided by are the same
set. Divide by more than is granted and the subscriber is short-changed; by
fewer and every device gets the whole allowance.
"""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from billing.models import (
    Customer, CustomerDevice, Invoice, Package, RouterDevice, Subscription,
    Tenant,
)
from billing.router_service import _remaining_data_bytes, macs_to_grant
from billing.tenancy import tenant_context

OLD = "AA:AA:AA:00:00:01"
OLDER = "AA:AA:AA:00:00:02"
PAID_FOR = "BB:BB:BB:00:00:01"


class DevicesFromExpiredPurchases(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="box", ip_address="10.1.0.9",
                username="u", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="3 weeks unlimited", download_speed=2,
                upload_speed=2, price=Decimal("250.00"), duration_value=3,
                duration_unit="weeks", is_hotspot=True, max_devices=2,
                data_cap_mb=5000)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Returning", phone="254700500001",
                connection_type="hotspot", router=self.router, status="active")

            with patch("billing.models.notify_customer"):
                self.old_sub = Subscription.objects.create(
                    customer=self.customer, package=self.package,
                    status="expired")
                self.sub = Subscription.objects.create(
                    customer=self.customer, package=self.package,
                    status="active")
            Invoice.objects.filter(subscription=self.sub).update(
                payment_status="paid")

            for mac, sub in ((OLD, self.old_sub), (OLDER, self.old_sub),
                             (PAID_FOR, self.sub)):
                CustomerDevice.objects.create(
                    tenant=self.tenant, customer=self.customer,
                    subscription=sub, mac_address=mac)

    def test_only_the_paid_subscription_s_devices_are_granted(self):
        """
        Two handsets from an expired purchase must not be put back on the
        router by a new one. The package sells two places; history is not one
        of them.
        """
        granted = [m.upper() for m in macs_to_grant(self.customer, self.sub)]
        self.assertEqual(granted, [PAID_FOR])

    def test_the_allowance_is_not_divided_by_history(self):
        """
        The bug in one number. Five devices on file meant a fifth of the cap
        each; here it is one device and the whole 5 GB.
        """
        with patch("billing.services.usage.usage_since", return_value=0):
            remaining = _remaining_data_bytes(self.customer, self.sub)
        self.assertEqual(remaining, 5000 * 1024 * 1024)

    def test_two_devices_on_one_subscription_split_it(self):
        """
        The original reasoning still holds: limit-bytes-total is counted per
        hotspot user, so a package good for two phones must not hand each of
        them the whole allowance.
        """
        with tenant_context(self.tenant):
            CustomerDevice.objects.create(
                tenant=self.tenant, customer=self.customer,
                subscription=self.sub, mac_address="BB:BB:BB:00:00:02")

        with patch("billing.services.usage.usage_since", return_value=0):
            remaining = _remaining_data_bytes(self.customer, self.sub)
        self.assertEqual(remaining, 5000 * 1024 * 1024 // 2)

    def test_what_is_granted_and_what_is_divided_by_agree(self):
        """
        The invariant. If these ever disagree the arithmetic is wrong in one
        direction or the other.
        """
        with tenant_context(self.tenant):
            CustomerDevice.objects.create(
                tenant=self.tenant, customer=self.customer,
                subscription=self.sub, mac_address="BB:BB:BB:00:00:02")

        granted = macs_to_grant(self.customer, self.sub)
        with patch("billing.services.usage.usage_since", return_value=0):
            each = _remaining_data_bytes(self.customer, self.sub)
        self.assertEqual(each * len(granted), 5000 * 1024 * 1024)

    def test_a_blocked_device_is_neither_granted_nor_counted(self):
        with tenant_context(self.tenant):
            CustomerDevice.objects.filter(
                customer=self.customer, mac_address=PAID_FOR
            ).update(blocked=True)

        self.assertNotIn(
            PAID_FOR, [m.upper() for m in macs_to_grant(self.customer, self.sub)])


class WhenTheSubscriptionHasNoDeviceYet(TestCase):
    """
    A renewal whose first device has not been bound yet. Granting nobody would
    leave somebody who has just paid with nothing at all, which is the one
    outcome worse than granting too much.
    """

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="box2", ip_address="10.1.0.8",
                username="u", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="24hrs", download_speed=2,
                upload_speed=2, price=Decimal("40.00"), duration_value=24,
                duration_unit="hours", is_hotspot=True, max_devices=2,
                data_cap_mb=4000)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Renewing", phone="254700500002",
                connection_type="hotspot", router=self.router, status="active")
            with patch("billing.models.notify_customer"):
                self.old_sub = Subscription.objects.create(
                    customer=self.customer, package=self.package,
                    status="expired")
                self.sub = Subscription.objects.create(
                    customer=self.customer, package=self.package,
                    status="active")
            CustomerDevice.objects.create(
                tenant=self.tenant, customer=self.customer,
                subscription=self.old_sub, mac_address=OLD)

    def test_their_existing_phone_still_gets_online(self):
        self.assertEqual(
            [m.upper() for m in macs_to_grant(self.customer, self.sub)], [OLD])

    def test_no_subscription_at_all_grants_everything(self):
        self.assertEqual(
            [m.upper() for m in macs_to_grant(self.customer, None)], [OLD])


class RetryingMacAuthenticationAfterAGrant(TestCase):
    """
    RouterOS tries MAC authentication when a host entry is created, not on
    every packet. A television is already on the network showing "no internet"
    -- that is why somebody is buying for it -- so its host entry predates the
    voucher, the one attempt finds no account, and nothing retries.

    Seen live on 2026-09-11: a valid three-week 5 GB account, unauthenticated
    for 39 minutes, online 50 seconds after the host entry was dropped.
    """

    class _Path(list):
        def __init__(self, rows):
            super().__init__(rows)
            self.removed = []

        def remove(self, rid):
            self.removed.append(rid)

    def _api(self, hosts, actives):
        from unittest.mock import MagicMock
        paths = {
            ("ip", "hotspot", "host"): self._Path(hosts),
            ("ip", "hotspot", "active"): self._Path(actives),
        }
        api = MagicMock()
        api.path.side_effect = lambda *a: paths[tuple(a)]
        api.paths = paths
        return api

    def test_an_unauthenticated_device_is_forgotten_so_it_retries(self):
        from billing.router_service import retry_mac_login
        api = self._api(
            hosts=[{".id": "*1", "mac-address": "04:05:DD:98:11:56"}],
            actives=[])
        self.assertTrue(retry_mac_login(api, "04:05:dd:98:11:56"))
        self.assertEqual(api.paths[("ip", "hotspot", "host")].removed, ["*1"])

    def test_a_device_already_online_is_left_alone(self):
        """
        The important half. _grant_hotspot runs on every renewal, and dropping
        the host of somebody who is connected would interrupt them.
        """
        from billing.router_service import retry_mac_login
        api = self._api(
            hosts=[{".id": "*1", "mac-address": "04:05:DD:98:11:56"}],
            actives=[{"mac-address": "04:05:DD:98:11:56"}])
        self.assertFalse(retry_mac_login(api, "04:05:DD:98:11:56"))
        self.assertEqual(api.paths[("ip", "hotspot", "host")].removed, [])

    def test_case_and_separators_do_not_matter(self):
        from billing.router_service import retry_mac_login
        api = self._api(
            hosts=[{".id": "*1", "mac-address": "04-05-dd-98-11-56"}],
            actives=[])
        self.assertTrue(retry_mac_login(api, "04:05:DD:98:11:56"))

    def test_a_device_not_on_the_router_is_a_no_op(self):
        from billing.router_service import retry_mac_login
        api = self._api(hosts=[], actives=[])
        self.assertFalse(retry_mac_login(api, "04:05:DD:98:11:56"))

    def test_a_router_error_never_fails_the_grant(self):
        """
        The grant is what the customer paid for. This only shortens the wait,
        so it must not be able to undo it.
        """
        from unittest.mock import MagicMock
        from billing.router_service import retry_mac_login
        api = MagicMock()
        api.path.side_effect = RuntimeError("router said no")
        self.assertFalse(retry_mac_login(api, "04:05:DD:98:11:56"))
