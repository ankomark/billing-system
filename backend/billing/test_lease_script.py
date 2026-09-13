"""
The hotspot session follows the DHCP lease, on the router, as it happens.

A session is keyed by MAC and address together, so when a device's address
changes hands the authorisation stays behind and the device's traffic arrives
unauthorised: "connected, no internet" with a perfectly good voucher. The pool
is 245 addresses and skylink held 222 bound leases against it, so an address
freed by a device that went quiet is handed straight to somebody else. 59
sessions were standing on another handset's address.

The lease time cannot be raised to fix it -- at 90% occupancy a longer lease
runs the pool dry, and a subscriber who cannot get an address never reaches
the portal at all. And the pool cannot be widened without colliding with
pppoe-pool on 192.168.89.x. So the fix is the lease script, which runs at the
one moment that matters.

The script itself was proven against the live routers before it shipped: 80
settled devices re-bound to the address they already held, nothing removed;
a genuinely stranded session removed exactly. These tests cover the Python
that installs it, which is the part that can regress quietly.
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from billing.models import RouterDevice, Tenant
from billing.services.lease_script import (
    LEASE_SCRIPT, MARKER, ensure_lease_script, ensure_lease_script_everywhere,
    hotspot_dhcp_servers,
)
from billing.tenancy import tenant_context


def fake_api(*, hotspots, servers):
    """
    A router that answers the two paths the installer reads, and records the
    updates it is given.
    """
    api = MagicMock()
    updates = []

    def path(*parts):
        node = MagicMock()
        if parts == ("ip", "hotspot"):
            node.__iter__ = lambda s: iter(hotspots)
        elif parts == ("ip", "dhcp-server"):
            node.__iter__ = lambda s: iter(servers)
            node.update = lambda **kw: updates.append(kw)
        else:
            node.__iter__ = lambda s: iter([])
        return node

    api.path.side_effect = path
    api.updates = updates
    return api


class WhichServersGetTheScript(TestCase):

    def test_only_the_servers_serving_a_hotspot_interface(self):
        """
        Matched by interface, not by name. "defconf" is what these two happen
        to be called; a station someone else sets up will not be, and a lease
        script on an interface with no hotspot would reap nothing while
        looking installed.
        """
        api = fake_api(
            hotspots=[{"interface": "bridge", "disabled": "false"}],
            servers=[
                {".id": "*1", "name": "defconf", "interface": "bridge"},
                {".id": "*2", "name": "free-internet", "interface": "ether8"},
            ])

        names = [str(s.get("name")) for s in hotspot_dhcp_servers(api)]

        self.assertEqual(names, ["defconf"])

    def test_a_disabled_hotspot_does_not_claim_its_server(self):
        api = fake_api(
            hotspots=[{"interface": "bridge", "disabled": "true"}],
            servers=[{".id": "*1", "name": "defconf", "interface": "bridge"}])

        self.assertEqual(hotspot_dhcp_servers(api), [])

    def test_several_hotspots_claim_several_servers(self):
        api = fake_api(
            hotspots=[{"interface": "bridge", "disabled": "false"},
                      {"interface": "wlan2", "disabled": "false"}],
            servers=[
                {".id": "*1", "name": "a", "interface": "bridge"},
                {".id": "*2", "name": "b", "interface": "wlan2"},
                {".id": "*3", "name": "c", "interface": "ether8"},
            ])

        names = sorted(str(s.get("name")) for s in hotspot_dhcp_servers(api))

        self.assertEqual(names, ["a", "b"])


class InstallingIt(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="ls-r", ip_address="10.9.0.90",
                username="u", password="p")

    def _api(self, lease_script=None):
        server = {".id": "*1", "name": "defconf", "interface": "bridge"}
        if lease_script is not None:
            server["lease-script"] = lease_script
        return fake_api(
            hotspots=[{"interface": "bridge", "disabled": "false"}],
            servers=[server])

    def test_an_empty_server_gets_the_script(self):
        api = self._api()

        was_set, present, skipped = ensure_lease_script(self.router, api)

        self.assertEqual((was_set, present, skipped), (1, 0, 0))
        self.assertEqual(api.updates[0]["lease-script"], LEASE_SCRIPT)

    def test_a_server_that_already_has_it_is_left_alone(self):
        """
        Idempotent, because this runs hourly. Rewriting an identical script
        every hour is churn on the router's configuration for nothing.
        """
        api = self._api(LEASE_SCRIPT)

        was_set, present, skipped = ensure_lease_script(self.router, api)

        self.assertEqual((was_set, present, skipped), (0, 1, 0))
        self.assertEqual(api.updates, [])

    def test_an_older_version_of_ours_is_replaced(self):
        """Recognised by the marker, so the script can be corrected later."""
        api = self._api(MARKER + "\n:log info oops")

        was_set, present, skipped = ensure_lease_script(self.router, api)

        self.assertEqual((was_set, present, skipped), (1, 0, 0))
        self.assertEqual(api.updates[0]["lease-script"], LEASE_SCRIPT)

    def test_somebody_elses_script_is_never_overwritten(self):
        """
        An operator's own lease script is their business. Clobbering it because
        it occupied the field we wanted is a poor trade for a fix that can wait
        for a human to look at the warning.
        """
        api = self._api(":log info mine")

        was_set, present, skipped = ensure_lease_script(self.router, api)

        self.assertEqual((was_set, present, skipped), (0, 0, 1))
        self.assertEqual(api.updates, [])

    def test_dry_run_writes_nothing(self):
        api = self._api()

        was_set, _, _ = ensure_lease_script(self.router, api, apply=False)

        self.assertEqual(was_set, 1)
        self.assertEqual(api.updates, [])

    def test_a_router_that_refuses_the_write_is_counted_not_raised(self):
        """
        One router failing must not stop the rest of the estate being fixed.
        """
        api = self._api()

        def boom(**kw):
            raise RuntimeError("no such command")

        api.path("ip", "dhcp-server").update = boom
        with patch.object(api, "path") as p:
            node = MagicMock()
            node.__iter__ = lambda s: iter(
                [{".id": "*1", "name": "defconf", "interface": "bridge"}])
            node.update = boom
            hs = MagicMock()
            hs.__iter__ = lambda s: iter(
                [{"interface": "bridge", "disabled": "false"}])
            p.side_effect = lambda *a: hs if a == ("ip", "hotspot") else node

            was_set, present, skipped = ensure_lease_script(self.router, api)

        self.assertEqual((was_set, present, skipped), (0, 0, 1))


class AcrossTheEstate(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.a = RouterDevice.objects.create(
                tenant=self.tenant, name="ls-a", ip_address="10.9.0.91",
                username="u", password="p", is_active=True)
            self.b = RouterDevice.objects.create(
                tenant=self.tenant, name="ls-b", ip_address="10.9.0.92",
                username="u", password="p", is_active=True)

    def test_an_unreachable_router_is_skipped_not_failed(self):
        """
        A thing that should be true, re-checked hourly. A router that is down
        is caught by the next run, and one that is down must not stop the
        other from being put right.
        """
        api = fake_api(
            hotspots=[{"interface": "bridge", "disabled": "false"}],
            servers=[{".id": "*1", "name": "defconf", "interface": "bridge"}])

        def connect(router):
            return None if router.pk == self.a.pk else api

        with patch("billing.router_service.safe_connect_router", connect):
            was_set, present, skipped = ensure_lease_script_everywhere(
                routers=[self.a, self.b])

        self.assertEqual((was_set, present, skipped), (1, 0, 0))


class ItIsScheduled(TestCase):
    """
    The script is router configuration: it survives a reboot but not a reset,
    a restore from an old backup, or a router swapped for a spare. Asserting
    it on a schedule is what makes the fix permanent rather than true once.
    """

    def test_ensure_lease_script_is_in_the_beat_schedule(self):
        from django.conf import settings

        entry = settings.CELERY_BEAT_SCHEDULE["ensure-lease-script"]

        self.assertEqual(
            entry["task"],
            "billing.tasks.router_tasks.ensure_lease_script_task")


class TheScriptItself(TestCase):
    """
    Not a substitute for running it on a router -- it was proven there before
    it shipped. These pin the properties that a careless edit would lose.
    """

    def test_it_carries_the_marker(self):
        self.assertIn(MARKER, LEASE_SCRIPT)

    def test_unbind_matches_the_departing_device_and_its_address(self):
        """
        Keying on the address alone would trust RouterOS to fire the script
        before handing the address on. Matching the MAC as well needs no such
        assumption and removes exactly the session that would otherwise be
        left for the next holder to collide with.
        """
        self.assertIn("address=$ip mac-address=$mac", LEASE_SCRIPT)

    def test_bind_matches_the_device_on_a_different_address(self):
        """
        The moved case: the device has a new lease and its old authorisation
        is what strands it. One device on two addresses is always stale.
        """
        self.assertIn("mac-address=$mac address!=$ip", LEASE_SCRIPT)

    def test_it_removes_the_host_as_well_as_the_session(self):
        """
        RouterOS will not re-run MAC authentication while a stale host stands,
        so removing only the session leaves the device exactly as stuck.
        """
        self.assertIn("/ip hotspot host remove", LEASE_SCRIPT)

    def test_it_does_nothing_without_an_address(self):
        self.assertIn(":if ([:len $ip] = 0) do={ :return }", LEASE_SCRIPT)
