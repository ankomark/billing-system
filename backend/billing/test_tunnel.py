"""
Tunnel provisioning — the part that lets a router be onboarded from a browser.

The Routers page allocates a tunnel address, generates a WireGuard keypair and
asks the host to add a peer, so an operator opening their second site never
touches a terminal. Most of what can go wrong here goes wrong quietly and
somewhere else: an address handed to two operators, a script a RouterOS
terminal cannot parse, a name reaching a shell. Each of those has a test below,
because none of them would be noticed by the person who caused it.

Kept out of tests.py, which is already 800 tests of the billing critical path
and has nothing to do with this.
"""

import base64
import json
import tempfile
from pathlib import Path

from django.test import SimpleTestCase, TestCase, override_settings

from billing.models import RouterDevice, Tenant
from billing.services import tunnel
from billing.tenancy import tenant_context


@override_settings(
    WG_SERVER_PUBLIC_KEY="yjAGscCPP6wDNNLT1bKJyaXdGs23aJA97DqHCcrW9k0=",
    WG_ENDPOINT_HOST="vpn.example.com",
    WG_ENDPOINT_PORT=51820,
    WG_TUNNEL_SUBNET="10.10.0.0/24",
    WG_SERVER_TUNNEL_IP="10.10.0.1",
    WG_INTERFACE_NAME="wg-smartbill",
)
class TunnelKeyAndScriptTests(SimpleTestCase):
    """The pure parts: keys, and the block the operator pastes into WinBox."""

    def test_keypair_is_the_shape_wireguard_expects(self):
        private, public = tunnel.generate_keypair()
        for key in (private, public):
            self.assertEqual(len(key), 44)
            self.assertTrue(tunnel.is_wireguard_key(key))
            self.assertEqual(len(base64.b64decode(key, validate=True)), 32)

    def test_generated_key_satisfies_the_host_side_validator(self):
        """
        The watcher on the server re-checks with a bash regex before running
        anything. If these two ever disagree, the platform queues requests the
        host silently rejects and the only symptom is a tunnel that never
        comes up.
        """
        _, public = tunnel.generate_keypair()
        self.assertRegex(public, r"^[A-Za-z0-9+/]{43}=$")

    def test_junk_is_not_mistaken_for_a_key(self):
        for bad in ("", "nope", "short=", "x" * 44, None,
                    "yjAG; rm -rf /", "A" * 43 + "!"):
            self.assertFalse(tunnel.is_wireguard_key(bad), repr(bad))

    def test_script_is_pure_ascii(self):
        """
        It is pasted into a RouterOS terminal. Box-drawing characters and
        typographic dashes arrive mangled and turn a comment into a parse error
        partway down a block the operator has no way to check.
        """
        private, _ = tunnel.generate_keypair()
        script = tunnel.build_router_script(
            tunnel_ip="10.10.0.7", private_key=private,
            api_username="skylink", api_password="hunter2",
        )
        self.assertTrue(script.isascii(), script)

    def test_script_carries_every_piece_the_router_needs(self):
        private, _ = tunnel.generate_keypair()
        script = tunnel.build_router_script(
            tunnel_ip="10.10.0.7", private_key=private,
            api_username="skylink", api_password="hunter2", api_port=8728,
        )
        for fragment in (
            private,                                            # its own key
            "yjAGscCPP6wDNNLT1bKJyaXdGs23aJA97DqHCcrW9k0=",     # the server's
            "endpoint-address=vpn.example.com",
            "persistent-keepalive=25s",     # the whole reason CGNAT works
            "allowed-address=10.10.0.1/32",  # management traffic only
            "address=10.10.0.7/24",
            "name=skylink",
            "in-interface=wg-smartbill",     # the firewall rule
            "dst-port=8728",
            "ntp",                           # clock, which expiry depends on
        ):
            self.assertIn(fragment, script, fragment)

    def test_refuses_rather_than_emitting_a_script_that_cannot_work(self):
        """
        A script missing the server's key pastes without error and produces a
        tunnel that never comes up — indistinguishable, from where the operator
        is standing, from a router they miswired.
        """
        private, _ = tunnel.generate_keypair()
        with override_settings(WG_SERVER_PUBLIC_KEY=""):
            with self.assertRaises(tunnel.TunnelNotConfigured):
                tunnel.build_router_script(
                    tunnel_ip="10.10.0.7", private_key=private,
                    api_username="a", api_password="b")


@override_settings(
    WG_TUNNEL_SUBNET="10.10.0.0/24",
    WG_SERVER_TUNNEL_IP="10.10.0.1",
)
class TunnelSpoolTests(SimpleTestCase):
    """What is written for the host to pick up, and what never should be."""

    def setUp(self):
        self.spool = tempfile.mkdtemp()

    def _queue(self, name, key, ip):
        with override_settings(WG_SPOOL_DIR=self.spool):
            return tunnel.queue_peer(name, key, ip)

    def test_a_name_cannot_carry_shell_metacharacters(self):
        _, public = tunnel.generate_keypair()
        request_id = self._queue("sky link!!; rm -rf /", public, "10.10.0.7")

        written = json.loads(Path(self.spool, f"{request_id}.json").read_text())
        for forbidden in (";", " ", "/", "!"):
            self.assertNotIn(forbidden, written["name"])
        self.assertRegex(written["name"], r"^[A-Za-z0-9_-]{1,40}$")
        self.assertEqual(written["public_key"], public)
        self.assertEqual(written["tunnel_ip"], "10.10.0.7")

    def test_an_address_outside_the_tunnel_subnet_is_refused(self):
        _, public = tunnel.generate_keypair()
        for bad in ("192.168.1.5", "8.8.8.8", "10.11.0.2"):
            with self.assertRaises(ValueError, msg=bad):
                self._queue("router", public, bad)

    def test_something_that_is_not_a_key_is_refused(self):
        with self.assertRaises(ValueError):
            self._queue("router", "definitely-not-a-key", "10.10.0.7")

    def test_nothing_half_written_is_left_for_the_watcher(self):
        """
        Requests are renamed into place, so the host never reads a partial JSON
        document — and no .tmp is left behind that nothing would ever clean up.
        """
        _, public = tunnel.generate_keypair()
        self._queue("router", public, "10.10.0.7")

        names = [p.name for p in Path(self.spool).iterdir()]
        self.assertEqual(len(names), 1, names)
        self.assertTrue(names[0].endswith(".json"), names)


@override_settings(
    WG_TUNNEL_SUBNET="10.10.0.0/24",
    WG_SERVER_TUNNEL_IP="10.10.0.1",
)
class TunnelAddressAllocationTests(TestCase):
    """
    Allocation, which has to see past the tenant scoping to be correct.

    A tunnel address belongs to one router on the whole platform. Everything
    else about RouterDevice is per-operator, and getting this one wrong hands
    10.10.0.2 to two of them — the second tunnel never works, and both
    configurations look entirely correct.
    """

    def setUp(self):
        self.t1 = Tenant.objects.get(slug="skylink")
        self.t2 = Tenant.objects.create(name="Acme WiFi", slug="acme-tunnel")

    def _router(self, tenant, ip, name):
        with tenant_context(tenant):
            return RouterDevice.objects.create(
                tenant=tenant, name=name, ip_address=ip,
                username="a", password="p")

    def test_first_allocation_skips_the_server_itself(self):
        with tenant_context(self.t1):
            self.assertEqual(tunnel.allocate_tunnel_ip(), "10.10.0.2")

    def test_an_address_already_used_is_not_handed_out_again(self):
        self._router(self.t1, "10.10.0.2", "one")
        with tenant_context(self.t1):
            self.assertEqual(tunnel.allocate_tunnel_ip(), "10.10.0.3")

    def test_another_operators_address_is_not_handed_out(self):
        """
        The regression this file exists for.

        `RouterDevice.objects.all_tenants()` alone only lifts the ORM filter —
        Postgres RLS goes on filtering to whichever operator the request is
        acting for, so the query reads as cross-operator and silently is not.
        Provisioning for t2 would then reuse t1's address.
        """
        self._router(self.t1, "10.10.0.2", "t1-router")

        with tenant_context(self.t2):
            allocated = tunnel.allocate_tunnel_ip()

        self.assertNotEqual(allocated, "10.10.0.2")
        self.assertEqual(allocated, "10.10.0.3")

    def test_addresses_outside_the_subnet_do_not_consume_one(self):
        """A router on a LAN or a public address is not on the tunnel."""
        self._router(self.t1, "192.168.88.1", "lan-router")
        with tenant_context(self.t1):
            self.assertEqual(tunnel.allocate_tunnel_ip(), "10.10.0.2")

    @override_settings(WG_TUNNEL_SUBNET="10.10.0.0/30",
                       WG_SERVER_TUNNEL_IP="10.10.0.1")
    def test_a_full_subnet_says_so_rather_than_returning_nothing(self):
        self._router(self.t1, "10.10.0.2", "only-one")
        with tenant_context(self.t1):
            with self.assertRaises(tunnel.TunnelAddressExhausted):
                tunnel.allocate_tunnel_ip()
