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
        """
        api = FakeApi(secrets=[{".id": "*1", "name": "SKY-2222-BBB",
                                "password": "stale"}])
        self.assertIs(self._enable(api), True)

        passwords = [u.get("password") for u in api.secrets.updated
                     if "password" in u]
        self.assertIn("pw", passwords,
                      "the router kept the old password")
        self.assertEqual(api.secrets.added, [], "a duplicate secret was added")
