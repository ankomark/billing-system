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

from librouteros.exceptions import TrapError
from django.test import TestCase
from django.utils import timezone

from billing.models import (
    Customer, Package, RouterDevice, Subscription, Tenant,
)
from billing.tenancy import tenant_context


# What RouterOS actually accepts on /ppp/secret.
#
# Written down because a fake that takes any keyword cannot fail for the reason
# the router fails. `limit-uptime` was sent here for a week: /ip/hotspot/user
# has that field and /ppp/secret does not, so RouterOS refused every add whole
# and no PPPoE subscriber could be provisioned at all — while the test asserting
# the field was sent passed happily, because this class used to accept it.
PPP_SECRET_FIELDS = frozenset({
    ".id", "name", "password", "service", "caller-id", "profile",
    "local-address", "remote-address", "routes", "ipv6-routes",
    "limit-bytes-in", "limit-bytes-out", "comment", "disabled",
})


class FakePath(list):
    """Enough of librouteros' Path to see what was asked of the router."""

    def __init__(self, rows, on_remove=None, known=None):
        super().__init__(rows)
        self.removed = []
        self.added = []
        self.updated = []
        self._on_remove = on_remove
        # None means "accept anything", which is right for paths whose field
        # list is not the point of the test.
        self._known = known

    def remove(self, *ids):
        if self._on_remove:
            self._on_remove(ids)
        self.removed.extend(ids)

    def _check(self, kw):
        if self._known is None:
            return
        unknown = sorted(set(kw) - self._known)
        if unknown:
            # The wording RouterOS itself uses, so a failure here reads the way
            # it reads in the router log.
            raise TrapError("unknown parameter %s" % unknown[0])

    def add(self, **kw):
        # Recorded *and* inserted. A router that accepts an add returns the row
        # on the next read, and create_pppoe_secret then enable_pppoe depend on
        # exactly that: the second call finds the secret the first one made.
        # A fake that only records makes a working sequence look broken.
        self._check(kw)
        self.added.append(kw)
        row = dict(kw)
        row.setdefault(".id", "*%d" % (len(self) + 1))
        self.append(row)

    def update(self, **kw):
        self._check(kw)
        self.updated.append(kw)


class FakeApi:
    def __init__(self, secrets=None, active=None, active_raises=False):
        self.secrets = FakePath(secrets or [], known=PPP_SECRET_FIELDS)
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
    The secret must not carry a limit the router will refuse.

    This class used to assert the opposite. The reasoning was that a hotspot
    user carries limit-uptime, so a PPPoE secret should too — and /ip/hotspot/
    user does have that field. /ppp/secret does not. RouterOS answers
    `unknown parameter limit-uptime` and refuses the entire add.

    So no secret was ever written, and PPPoE provisioning could not succeed for
    anybody. It stood from 2026-08-25 until 2026-09-01, invisible only because
    there were no PPPoE subscribers; the first one created hit it at once and
    his router repeated `authentication failed` every thirty seconds while
    every record said he was provisioned.

    The old test passed throughout, because FakePath.add accepts any keyword.
    A fake that cannot refuse cannot tell you the router would — which is the
    whole reason a test has to be able to fail for the real reason.

    Expiry is enforced by disable_customer_access: the secret is disabled and
    the live session disconnected. PppoeExpiryTests above covers it.
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

    def test_the_secret_carries_no_uptime_limit(self):
        """
        The field RouterOS refuses. Sending it does not weaken the guarantee,
        it destroys the account: the add is rejected whole and the subscriber
        has no credentials at all.
        """
        api = FakeApi()
        self._create(api, timezone.now() + timedelta(hours=2))
        added = api.secrets.added[0]
        self.assertNotIn(
            "limit-uptime", added,
            "/ppp/secret has no limit-uptime — RouterOS refuses the whole add")
        self.assertNotIn("limit_uptime", added)

    def test_the_secret_still_carries_what_a_subscriber_needs(self):
        """
        Removing the bad field must not take the working ones with it.
        """
        api = FakeApi()
        self._create(api, timezone.now() + timedelta(hours=2))
        added = api.secrets.added[0]
        self.assertEqual(added["name"], self.USER)
        self.assertEqual(added["password"], "pw")
        self.assertEqual(added["service"], "pppoe")
        self.assertEqual(added["profile"], "PPPOE_PKG_1")

    def test_an_expiry_in_hand_changes_nothing_about_what_is_sent(self):
        """
        With and without an expiry the secret is identical. The date is still
        accepted so callers need not care, and is enforced by
        disable_customer_access rather than by the router.
        """
        with_expiry = FakeApi()
        self._create(with_expiry, timezone.now() + timedelta(days=30))
        without = FakeApi()
        self._create(without, None)
        self.assertEqual(with_expiry.secrets.added[0], without.secrets.added[0])

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

    def test_the_generated_profile_inherits_the_resolver(self):
        """
        An address without a resolver is still no internet.

        The first real PPPoE subscriber, 2026-09-01: dns-server was empty on
        every profile, so the client would have authenticated, taken an address
        from the pool, installed a working default route and resolved nothing.
        The customer reports "connected, no internet" and every record — the
        secret, the profile, the router log — says the login succeeded.
        """
        profiles = FakePath([
            {".id": "*0", "name": "default",
             "local-address": "192.168.89.1", "remote-address": "pppoe-pool",
             "dns-server": "192.168.89.1,8.8.8.8"},
        ])
        self._ensure(profiles)

        added = profiles.added[0]
        self.assertEqual(added["dns-server"], "192.168.89.1,8.8.8.8")

    def test_a_profile_made_before_dns_was_set_is_repaired(self):
        """
        Same argument as the addresses above: idempotent by name would leave
        every profile generated before the operator set a resolver carrying
        that emptiness for ever.
        """
        profiles = FakePath([
            {".id": "*0", "name": "default",
             "local-address": "192.168.89.1", "remote-address": "pppoe-pool",
             "dns-server": "192.168.89.1,8.8.8.8"},
            {".id": "*5", "name": "PPPOE_PKG_%s" % self.package.id,
             "rate-limit": "5M/10M", "only-one": "yes",
             "local-address": "192.168.89.1", "remote-address": "pppoe-pool",
             "comment": "Auto: home 10mbps"},
        ])
        self._ensure(profiles)

        self.assertEqual(profiles.added, [], "a duplicate profile was created")
        self.assertEqual(len(profiles.updated), 1)
        self.assertEqual(profiles.updated[0]["dns-server"],
                         "192.168.89.1,8.8.8.8")

    def test_a_router_with_no_resolver_configured_is_not_given_a_blank_one(self):
        """
        Absent is not the same as empty. An operator who has not set a resolver
        on `default` must not have `dns-server=""` written onto every package
        profile, which would look configured and behave worse.
        """
        profiles = FakePath([
            {".id": "*0", "name": "default",
             "local-address": "192.168.89.1", "remote-address": "pppoe-pool"},
        ])
        self._ensure(profiles)

        self.assertNotIn("dns-server", profiles.added[0])

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
