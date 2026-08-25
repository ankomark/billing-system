"""
PPPoE, held to the same standard as the hotspot path.

The hotspot side was hardened one production failure at a time. PPPoE was
written alongside it and then never used — this platform has never carried a
single PPPoE subscriber — so nothing found its gaps the hard way. These are
the three that mattered, written before the first one is sold.

The worst was expiry. disable_customer_access disabled the secret and
stopped, which refuses the *next* authentication and leaves an established
session running. A PPP session has no reason to end on its own and, unlike a
hotspot user, carries no limit-uptime, so a subscriber whose time ran out
stayed online until they rebooted their own router. The dashboard said
expired throughout.

disable_hotspot's docstring asserted "PPPoE has always done this; hotspot
never did", which is the sort of comment that stops somebody checking.
"""

from decimal import Decimal
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from billing.models import (
    Customer, Package, RouterDevice, Subscription, Tenant,
)
from billing.tenancy import tenant_context


class FakePath(list):
    """Enough of librouteros' Path to see what was asked of the router."""

    def __init__(self, rows, on_remove=None):
        super().__init__(rows)
        self.removed = []
        self.added = []
        self.updated = []
        self._on_remove = on_remove

    def remove(self, *ids):
        if self._on_remove:
            self._on_remove(ids)
        self.removed.extend(ids)

    def add(self, **kw):
        # Recorded *and* inserted. A router that accepts an add returns the row
        # on the next read, and create_pppoe_secret then enable_pppoe depend on
        # exactly that: the second call finds the secret the first one made.
        # A fake that only records makes a working sequence look broken.
        self.added.append(kw)
        row = dict(kw)
        row.setdefault(".id", "*%d" % (len(self) + 1))
        self.append(row)

    def update(self, **kw):
        self.updated.append(kw)


class FakeApi:
    def __init__(self, secrets=None, active=None, active_raises=False):
        self.secrets = FakePath(secrets or [])
        self.active = FakePath(active or [])
        self.active_raises = active_raises

    def path(self, *parts):
        if parts == ("ppp", "secret"):
            return self.secrets
        if parts == ("ppp", "active"):
            if self.active_raises:
                raise RuntimeError("router refused the session listing")
            return self.active
        raise AssertionError("unexpected path %r" % (parts,))


class PppoeExpiryTests(TestCase):
    """What happens to a PPPoE subscriber when their time runs out."""

    USER = "SKY-1234-ABC"

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r", ip_address="10.0.0.1",
                username="u", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="home 10mbps", download_speed=10,
                upload_speed=5, price=Decimal("2500.00"), duration_value=1,
                duration_unit="months", monthly_data_cap_gb=0,
                is_hotspot=False, max_devices=1)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Otieno", phone="254700900900",
                connection_type="pppoe", router=self.router,
                pppoe_username=self.USER, pppoe_password="s3cret")

    def _disable(self, api):
        from billing import router_service
        from unittest.mock import patch
        with patch.object(router_service, "safe_connect_router",
                          return_value=api), \
             tenant_context(self.tenant):
            router_service.disable_customer_access(self.customer)

    def test_expiry_ends_the_live_session_not_just_the_secret(self):
        """
        The whole finding. Disabling the secret is future tense; the session
        already up is what keeps a non-paying subscriber online.
        """
        api = FakeApi(
            secrets=[{".id": "*1", "name": self.USER}],
            active=[{".id": "*A", "name": self.USER}],
        )
        self._disable(api)

        self.assertEqual(
            [u.get("disabled") for u in api.secrets.updated], ["yes"],
            "the secret was not disabled")
        self.assertEqual(
            api.active.removed, ["*A"],
            "the PPPoE session survived expiry, so the subscriber stayed on")

    def test_the_secret_goes_before_the_session(self):
        """
        The other order leaves a window where the session is gone and the
        credentials still work, and a PPP client reconnects in under a second.
        """
        order = []
        api = FakeApi(secrets=[{".id": "*1", "name": self.USER}],
                      active=[{".id": "*A", "name": self.USER}])
        api.secrets.update = lambda **kw: order.append("secret")
        api.active._on_remove = lambda ids: order.append("session")

        self._disable(api)
        self.assertEqual(order, ["secret", "session"])

    def test_a_stranger_is_left_connected(self):
        api = FakeApi(
            secrets=[{".id": "*9", "name": "SKY-9999-ZZZ"}],
            active=[{".id": "*B", "name": "SKY-9999-ZZZ"}],
        )
        self._disable(api)
        self.assertEqual(api.active.removed, [], "cut off the wrong customer")
        self.assertEqual(api.secrets.updated, [])

    def test_a_router_that_will_not_end_the_session_is_raised_not_swallowed(self):
        """
        disable_customer_task marks the router offline and re-raises, which is
        what gets this looked at. A subscriber still online after expiry is
        exactly the state worth retrying.
        """
        api = FakeApi(secrets=[{".id": "*1", "name": self.USER}],
                      active_raises=True)
        with self.assertRaises(RuntimeError):
            self._disable(api)


class PppoeProvisioningHonestyTests(TestCase):
    """enable_customer_access must not report success it did not achieve."""

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r", ip_address="10.0.0.2",
                username="u", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="home 10mbps", download_speed=10,
                upload_speed=5, price=Decimal("2500.00"), duration_value=1,
                duration_unit="months", monthly_data_cap_gb=0,
                is_hotspot=False, max_devices=1)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Atieno", phone="254700900901",
                connection_type="pppoe", router=self.router,
                pppoe_username="SKY-2222-BBB", pppoe_password="pw")
            self.sub = Subscription.objects.create(
                tenant=self.tenant, customer=self.customer,
                package=self.package, status="active",
                expiry_date=timezone.now() + timedelta(days=30))
            inv = self.sub.invoice
            inv.payment_status = "paid"
            inv.save(update_fields=["payment_status"])

    def _enable(self, api):
        from unittest.mock import patch
        from billing import router_service
        with patch.object(router_service, "pick_working_router",
                          return_value=(self.router, api)), \
             patch.object(router_service, "ensure_pppoe_profile",
                          return_value="PPPOE_PKG_1"), \
             tenant_context(self.tenant):
            return router_service.enable_customer_access(self.customer)

    def test_a_subscriber_with_no_credentials_is_not_reported_as_provisioned(self):
        """
        The one case create_pppoe_secret refuses. It returned None and said
        nothing, and the caller returned True — a customer paid, was activated,
        was told their account was ready, and had nothing on the hardware.
        """
        with tenant_context(self.tenant):
            Customer.objects.filter(pk=self.customer.pk).update(
                pppoe_username="", pppoe_password="")
            self.customer.refresh_from_db()

        api = FakeApi()
        self.assertIs(self._enable(api), False)
        self.assertEqual(api.secrets.added, [])

    def test_a_real_provision_reports_success(self):
        api = FakeApi()
        self.assertIs(self._enable(api), True)
        self.assertEqual(len(api.secrets.added), 1)
        added = api.secrets.added[0]
        self.assertEqual(added["name"], "SKY-2222-BBB")
        self.assertEqual(added["service"], "pppoe")

    def test_an_existing_secret_is_brought_up_to_date(self):
        """
        This returned early on a name match, so a regenerated password or a
        changed package left the router authenticating against a stale secret
        while the dashboard showed the new one — which reads to everybody as
        the customer typing their password wrong.

        Rebuilt rather than updated in place, which is also what resets the
        cumulative uptime that limit-uptime is measured against. See
        PppoeForcedExpiryTests.
        """
        api = FakeApi(secrets=[{".id": "*1", "name": "SKY-2222-BBB",
                                "password": "stale"}])
        self.assertIs(self._enable(api), True)

        self.assertEqual(api.secrets.removed, ["*1"],
                         "the stale secret was left on the router")
        self.assertEqual(len(api.secrets.added), 1,
                         "the secret was not rebuilt exactly once")
        self.assertEqual(api.secrets.added[0]["password"], "pw",
                         "the router kept the old password")


class PppoeForcedExpiryTests(TestCase):
    """
    The router's own copy of when a subscription runs out.

    Until this, nothing on the PPPoE side enforced expiry except our sweep. A
    sweep that cannot reach the router, or does not run, left a subscriber
    connected indefinitely. A hotspot user has carried limit-uptime since the
    day the same problem was found there.
    """

    USER = "SKY-3333-CCC"

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r", ip_address="10.0.0.3",
                username="u", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="home 10mbps", download_speed=10,
                upload_speed=5, price=Decimal("2500.00"), duration_value=1,
                duration_unit="months", monthly_data_cap_gb=0,
                is_hotspot=False, max_devices=6)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Akinyi", phone="254700900902",
                connection_type="pppoe", router=self.router,
                pppoe_username=self.USER, pppoe_password="pw")

    def _create(self, api, expiry, existing=None):
        from unittest.mock import patch
        from billing import router_service
        if existing is not None:
            api.secrets.extend(existing)
        with patch.object(router_service, "ensure_pppoe_profile",
                          return_value="PPPOE_PKG_1"), \
             tenant_context(self.tenant):
            return router_service.create_pppoe_secret(
                api, self.router, self.customer, self.package, expiry)

    def test_the_secret_carries_when_it_runs_out(self):
        api = FakeApi()
        self._create(api, timezone.now() + timedelta(hours=2))
        added = api.secrets.added[0]
        self.assertIn("limit-uptime", added)
        # Hyphenated. As limit_uptime it goes on the wire as a word RouterOS
        # does not know and the guarantee silently does not exist.
        self.assertNotIn("limit_uptime", added)
        seconds = int(added["limit-uptime"].rstrip("s"))
        self.assertTrue(7000 < seconds <= 7200, added["limit-uptime"])

    def test_renewal_resets_the_counter_rather_than_writing_a_new_limit(self):
        """
        limit-uptime is compared against cumulative uptime, which nothing
        resets. Updating in place would measure a renewing subscriber against
        time they used last month and cut them off on reconnect.
        """
        api = FakeApi()
        self._create(api, timezone.now() + timedelta(days=30),
                     existing=[{".id": "*7", "name": self.USER,
                                "password": "stale"}])
        self.assertEqual(
            api.secrets.removed, ["*7"],
            "the old secret was kept, so its used time still counts")
        self.assertEqual(len(api.secrets.added), 1)
        self.assertEqual(api.secrets.added[0]["password"], "pw")

    def test_no_expiry_given_means_no_limit_written(self):
        """Callers without a subscription in hand must not invent one."""
        api = FakeApi()
        self._create(api, None)
        self.assertNotIn("limit-uptime", api.secrets.added[0])


class PppoeProfileAddressTests(TestCase):
    """
    A generated profile has to carry the addresses, not just the speed.

    RouterOS takes address assignment from the profile named on the *secret*,
    not from the PPPoE server's default-profile. PPPOE_PKG_<id> carried only
    rate-limit and only-one, so the first PPPoE subscriber would have
    authenticated, been given no IP, and had correct-looking credentials with
    no internet — a support call nobody can diagnose from the dashboard,
    because every record says they are provisioned.

    The rows below are the shape skylink3 actually returned on 2026-08-25.
    """

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r", ip_address="10.0.0.4",
                username="u", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="home 10mbps", download_speed=10,
                upload_speed=5, price=Decimal("2500.00"), duration_value=1,
                duration_unit="months", monthly_data_cap_gb=0,
                is_hotspot=False, max_devices=6)

    def _ensure(self, profiles):
        from unittest.mock import patch
        from billing import router_profiles

        class Api:
            def path(self, *parts):
                assert parts == ("ppp", "profile"), parts
                return profiles

        with patch.object(router_profiles, "connect_router",
                          return_value=Api()), tenant_context(self.tenant):
            return router_profiles.ensure_pppoe_profile(self.router, self.package)

    def test_the_generated_profile_inherits_the_operators_addresses(self):
        profiles = FakePath([
            {".id": "*0", "name": "default",
             "local-address": "192.168.89.1", "remote-address": "pppoe-pool"},
            {".id": "*1", "name": "default-encryption"},
        ])
        name = self._ensure(profiles)

        self.assertEqual(name, "PPPOE_PKG_%s" % self.package.id)
        added = profiles.added[0]
        self.assertEqual(added["local-address"], "192.168.89.1")
        self.assertEqual(added["remote-address"], "pppoe-pool")
        self.assertEqual(added["rate-limit"], "5M/10M")

    def test_a_profile_made_before_the_addresses_existed_is_repaired(self):
        """
        Idempotent by name is not enough. A profile created on a router that
        had no pool yet keeps its name and its emptiness for ever, and every
        subscriber put on it gets no IP.
        """
        profiles = FakePath([
            {".id": "*0", "name": "default",
             "local-address": "192.168.89.1", "remote-address": "pppoe-pool"},
            {".id": "*5", "name": "PPPOE_PKG_%s" % self.package.id,
             "rate-limit": "5M/10M", "only-one": "yes"},
        ])
        self._ensure(profiles)

        self.assertEqual(profiles.added, [], "a duplicate profile was created")
        self.assertEqual(len(profiles.updated), 1)
        fixed = profiles.updated[0]
        self.assertEqual(fixed[".id"], "*5")
        self.assertEqual(fixed["local-address"], "192.168.89.1")
        self.assertEqual(fixed["remote-address"], "pppoe-pool")

    def test_a_changed_package_speed_reaches_the_profile(self):
        profiles = FakePath([
            {".id": "*0", "name": "default",
             "local-address": "192.168.89.1", "remote-address": "pppoe-pool"},
            {".id": "*5", "name": "PPPOE_PKG_%s" % self.package.id,
             "rate-limit": "1M/1M", "only-one": "yes",
             "local-address": "192.168.89.1", "remote-address": "pppoe-pool",
             "comment": "Auto: home 10mbps"},
        ])
        self._ensure(profiles)
        self.assertEqual(profiles.updated[0]["rate-limit"], "5M/10M")

    def test_an_unconfigured_router_still_gets_a_usable_profile(self):
        """
        No addresses on `default` means the operator has not run the PPPoE
        setup. Refusing here would turn a missing pool into a crash on the
        payment path; the profile is still created, and the omission shows up
        as a client with no IP rather than a 500.
        """
        profiles = FakePath([{".id": "*0", "name": "default"}])
        self._ensure(profiles)
        added = profiles.added[0]
        self.assertNotIn("local-address", added)
        self.assertEqual(added["only-one"], "yes")
