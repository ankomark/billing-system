"""
Why PPPoE subscribers sat connected with no internet, and kept redialling.

Two symptoms with one likely root. PPPoE costs 8 bytes of header, so the usable
MTU is 1492 rather than 1500. A client that negotiates the ordinary 1460-byte
MSS sends segments the tunnel cannot carry, and the ICMP that would say so is
dropped on nearly every path -- so nothing reports an error. Ping works, DNS
works, small pages load, and every HTTPS site and every download hangs. To the
subscriber that is "connected, no internet".

It explains the redialling too: a CPE router that watches for a working path
sees none and dials again. st.ambros recorded 13 session restarts over four
days, most of them carrying ZERO bytes -- the session comes up, passes nothing,
and dies.

RouterOS's own `default` and `default-encryption` profiles ship with
change-tcp-mss on. ensure_pppoe_profile never set it, so every PPPOE_PKG_*
profile was created without it while the built-in ones beside it were correct,
and both live subscribers were on one of ours.

The second measure is the PPPoE server's keepalive-timeout, which RouterOS
ships at 10 seconds. On a wireless backhaul that ends a session over an
ordinary blip.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase

from billing.models import Package, RouterDevice, Tenant
from billing.router_profiles import ensure_pppoe_profile
from billing.services.lease_script import (
    PPPOE_KEEPALIVE, ensure_pppoe_keepalive_everywhere,
    ensure_pppoe_server_keepalive,
)
from billing.tenancy import tenant_context


def profile_api(existing=()):
    """A router whose ppp profile list answers, and records what it is told."""
    api = MagicMock()
    added, updated = [], []
    rows = list(existing)

    def path(*parts):
        node = MagicMock()
        if parts == ("ppp", "profile"):
            node.__iter__ = lambda s: iter(rows)
            node.add = lambda **kw: added.append(kw)
            node.update = lambda **kw: updated.append(kw)
        else:
            node.__iter__ = lambda s: iter([])
        return node

    api.path.side_effect = path
    api.added, api.updated = added, updated
    return api


class TheTunnelClampsItsSegments(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="ppp-r", ip_address="10.9.0.99",
                username="u", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="10mbps", download_speed=10,
                upload_speed=10, price=Decimal("2500.00"), duration_value=1,
                duration_unit="months", is_hotspot=False)

    def test_a_new_profile_is_born_clamping(self):
        """
        The fault at source. Without this the profile inherits nothing useful
        and large packets are black-holed for every subscriber on it.
        """
        api = profile_api()

        with patch("billing.router_profiles.connect_router", return_value=api):
            ensure_pppoe_profile(self.router, self.package)

        self.assertEqual(api.added[0]["change-tcp-mss"], "yes")

    def test_an_existing_profile_without_it_is_repaired(self):
        """
        The two live profiles were already on the routers carrying
        change-tcp-mss unset. The repair path is what reaches them, so this is
        what fixes the estate rather than only new packages.
        """
        api = profile_api(existing=[{
            ".id": "*9",
            "name": f"PPPOE_PKG_{self.package.id}",
            "rate-limit": "10M/10M",
            "only-one": "yes",
            "change-tcp-mss": "default",
            "comment": f"Auto: {self.package.name}",
        }])

        with patch("billing.router_profiles.connect_router", return_value=api):
            ensure_pppoe_profile(self.router, self.package)

        self.assertEqual(len(api.updated), 1)
        self.assertEqual(api.updated[0]["change-tcp-mss"], "yes")

    def test_a_correct_profile_is_not_rewritten(self):
        """Idempotent: provisioning runs on every purchase."""
        api = profile_api(existing=[{
            ".id": "*9",
            "name": f"PPPOE_PKG_{self.package.id}",
            "rate-limit": "10M/10M",
            "only-one": "yes",
            "change-tcp-mss": "yes",
            "comment": f"Auto: {self.package.name}",
        }])

        with patch("billing.router_profiles.connect_router", return_value=api):
            ensure_pppoe_profile(self.router, self.package)

        self.assertEqual(api.updated, [])

    def test_the_rest_of_the_profile_is_unchanged(self):
        """
        only-one is what stops a household sharing one login, and the rate
        limit is what they bought. Adding the clamp must not disturb either.
        """
        api = profile_api()

        with patch("billing.router_profiles.connect_router", return_value=api):
            ensure_pppoe_profile(self.router, self.package)

        self.assertEqual(api.added[0]["only-one"], "yes")
        self.assertIn("rate-limit", api.added[0])


def server_api(servers):
    api = MagicMock()
    updated = []

    def path(*parts):
        node = MagicMock()
        if parts == ("interface", "pppoe-server", "server"):
            node.__iter__ = lambda s: iter(servers)
            node.update = lambda **kw: updated.append(kw)
        else:
            node.__iter__ = lambda s: iter([])
        return node

    api.path.side_effect = path
    api.updated = updated
    return api


class TheKeepaliveSurvivesABlip(TestCase):
    """
    RouterOS ships 10 seconds and both routers were on it. On a wireless
    backhaul that ends a session over an ordinary few-second interruption, and
    the subscriber's router redials -- which is the reported "frequent
    disconnections and reconnections".

    Raising it costs almost nothing the other way: a session that really has
    died lingers up to a minute, and only-one=yes means the redial replaces it.
    """

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="ka-r", ip_address="10.9.0.98",
                username="u", password="p", is_active=True)

    def test_a_ten_second_keepalive_is_raised(self):
        api = server_api([{".id": "*1", "service-name": "skylink",
                           "keepalive-timeout": 10}])

        changed = ensure_pppoe_server_keepalive(self.router, api)

        self.assertEqual(changed, 1)
        self.assertEqual(api.updated[0]["keepalive-timeout"], PPPOE_KEEPALIVE)

    def test_one_already_correct_is_left_alone(self):
        api = server_api([{".id": "*1", "service-name": "skylink",
                           "keepalive-timeout": PPPOE_KEEPALIVE}])

        self.assertEqual(ensure_pppoe_server_keepalive(self.router, api), 0)
        self.assertEqual(api.updated, [])

    def test_dry_run_writes_nothing(self):
        api = server_api([{".id": "*1", "keepalive-timeout": 10}])

        self.assertEqual(
            ensure_pppoe_server_keepalive(self.router, api, apply=False), 1)
        self.assertEqual(api.updated, [])

    def test_a_router_with_no_pppoe_server_is_a_no_op(self):
        self.assertEqual(
            ensure_pppoe_server_keepalive(self.router, server_api([])), 0)

    def test_an_unreadable_router_does_not_raise(self):
        api = MagicMock()
        api.path.side_effect = RuntimeError("no such command")

        self.assertEqual(ensure_pppoe_server_keepalive(self.router, api), 0)

    def test_an_unreachable_router_is_skipped_not_failed(self):
        api = server_api([{".id": "*1", "keepalive-timeout": 10}])

        with patch("billing.router_service.safe_connect_router",
                   side_effect=lambda r: None if r.pk == self.router.pk
                   else api):
            self.assertEqual(
                ensure_pppoe_keepalive_everywhere(routers=[self.router]), 0)

    def test_the_hourly_task_asserts_it(self):
        from billing.tasks.router_tasks import ensure_lease_script_task

        with patch("billing.services.lease_script."
                   "ensure_lease_script_everywhere", return_value=(0, 1, 0)), \
             patch("billing.services.walled_garden."
                   "close_resolver_bypasses_everywhere", return_value=0), \
             patch("billing.services.lease_script."
                   "ensure_pppoe_keepalive_everywhere") as ka:
            ensure_lease_script_task()

        ka.assert_called_once_with(apply=True)

    def test_a_failure_there_does_not_break_the_rest(self):
        from billing.tasks.router_tasks import ensure_lease_script_task

        with patch("billing.services.lease_script."
                   "ensure_lease_script_everywhere", return_value=(2, 0, 0)), \
             patch("billing.services.walled_garden."
                   "close_resolver_bypasses_everywhere", return_value=0), \
             patch("billing.services.lease_script."
                   "ensure_pppoe_keepalive_everywhere",
                   side_effect=RuntimeError("boom")):
            self.assertEqual(ensure_lease_script_task(), 2)
