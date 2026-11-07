"""
Stopping hotspot sharing before it is useful, rather than noticing it after.

Detection (services/tethering.py) needs a quarter of an hour of evidence before
it acts, and a phone sharing its connection is useful to the people behind it
for every minute of that. Prevention sets the hop counter on traffic headed to
logged-in clients to 1: the phone receives it, anything the phone forwards it
to does not. These hold the rule to the shape that makes that true, and to
coming off the router again when it is switched off.
"""

from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from billing.models import Customer, SystemSetting
from billing.tenancy import tenant_context
from billing.tests import FakeMikrotik, TwoOperatorMixin


class _Base(TwoOperatorMixin, TestCase):

    def setUp(self):
        cache.clear()
        self.build_operators()
        self.api = FakeMikrotik()
        self.router = self.data["t1"]["router"]
        with tenant_context(self.t1):
            Customer.objects.create(
                tenant=self.t1, full_name="Phone", phone="254711000444",
                connection_type="hotspot", hotspot_username="AA:BB:CC:DD:EE:04",
                router=self.router, status="active")

    def set(self, **values):
        with tenant_context(self.t1):
            for key, value in values.items():
                SystemSetting.objects.update_or_create(
                    tenant=self.t1, key=key, defaults={"value": str(value)})
        cache.clear()

    def sweep(self):
        from billing.services import tethering

        with patch("billing.router_service.safe_connect_router",
                   return_value=self.api), \
             patch("billing.tasks.notification_tasks.notify_customer_task.delay"):
            with tenant_context(self.t1):
                return tethering.sweep_router(self.router, now=timezone.now())

    def prevent_rules(self):
        from billing.services import tethering

        return [r for r in self.api.mangle()
                if r.get("comment") == tethering.PREVENT_COMMENT]

class TetheringPreventionTests(_Base):

    def test_prevention_sets_ttl_1_towards_logged_in_clients(self):
        self.set(TETHERING_POLICY="kick", TETHERING_PREVENT="true")
        self.sweep()

        [rule] = self.prevent_rules()
        self.assertEqual(rule["action"], "change-ttl")
        self.assertEqual(rule["new-ttl"], "set:1")
        # Forward only: the login page and the router's own DNS answers are
        # output traffic, and a subscriber has to be able to load them.
        self.assertEqual(rule["chain"], "forward")
        # Only logged-in hotspot clients. Without this the operator's own
        # access points and anything bypassed would be cut off from the
        # network they are part of.
        self.assertEqual(rule["hotspot"], "to-client,auth")

    def test_off_by_default(self):
        """It changes every subscriber's traffic, so it must be asked for."""
        self.set(TETHERING_POLICY="kick")
        self.sweep()
        self.assertEqual(self.prevent_rules(), [])

    def test_detection_rules_still_go_in_alongside(self):
        """
        A rooted phone can raise the counter again before forwarding. Its
        upstream traffic still arrives a hop short, so detection is what
        catches it -- the two are meant to run together.
        """
        self.set(TETHERING_POLICY="kick", TETHERING_PREVENT="true")
        self.sweep()
        lists = {r.get("address-list") for r in self.api.mangle()}
        self.assertIn("tether-hop1", lists)

    def test_installing_twice_leaves_one_rule(self):
        self.set(TETHERING_POLICY="kick", TETHERING_PREVENT="true")
        self.sweep()
        self.sweep()
        self.assertEqual(len(self.prevent_rules()), 1)

    def test_a_hand_edited_rule_is_put_back(self):
        self.set(TETHERING_POLICY="kick", TETHERING_PREVENT="true")
        self.sweep()
        self.prevent_rules()[0]["new-ttl"] = "set:64"
        self.sweep()
        self.assertEqual(self.prevent_rules()[0]["new-ttl"], "set:1")

    # ---- coming off again --------------------------------------------------

    def test_switching_prevention_off_takes_the_rule_off(self):
        self.set(TETHERING_POLICY="kick", TETHERING_PREVENT="true")
        self.sweep()
        self.set(TETHERING_PREVENT="false")
        self.sweep()
        self.assertEqual(self.prevent_rules(), [])

    def test_switching_the_whole_feature_off_takes_the_rule_off(self):
        """
        Off means the sweep no longer runs, which is exactly the moment a rule
        left behind would go on stopping traffic with nothing explaining it.
        The off path has to reach the router and remove it.
        """
        from billing.tasks.tethering_tasks import detect_tethering_for_tenant

        self.set(TETHERING_POLICY="kick", TETHERING_PREVENT="true")
        self.sweep()
        self.assertEqual(len(self.prevent_rules()), 1)

        self.set(TETHERING_POLICY="off")
        with patch("billing.router_service.safe_connect_router",
                   return_value=self.api):
            detect_tethering_for_tenant.run(self.t1.id)

        self.assertEqual(self.prevent_rules(), [])

    def test_operator_rules_survive(self):
        self.api.mangle().append(
            {".id": "*900", "chain": "prerouting", "action": "mark-routing",
             "comment": "STARLINK | send marked connections out Starlink"})
        self.set(TETHERING_POLICY="kick", TETHERING_PREVENT="true")
        self.sweep()
        self.set(TETHERING_POLICY="off")
        from billing.tasks.tethering_tasks import detect_tethering_for_tenant
        with patch("billing.router_service.safe_connect_router",
                   return_value=self.api):
            detect_tethering_for_tenant.run(self.t1.id)
        self.assertIn("*900", [r[".id"] for r in self.api.mangle()])


class TetheringRedirectTests(_Base):
    """
    Sending the device behind the phone to a page that sells it a bundle,
    rather than leaving it with a hotspot that has no internet.
    """

    SERVER = "203.0.113.10"

    def sweep(self):
        with patch("socket.gethostbyname", return_value=self.SERVER):
            return super().sweep()

    def nat(self):
        from billing.services import tethering
        return [r for r in self.api.rows("ip", "firewall", "nat")
                if (r.get("comment") or "").startswith(tethering.RULE_COMMENT)]

    def redirect_marks(self):
        from billing.services import tethering
        return [r for r in self.api.mangle()
                if (r.get("comment") or "").startswith(tethering.REDIRECT_COMMENT)]

    def allowed(self):
        return [r for r in self.api.rows("ip", "firewall", "address-list")
                if r.get("list") == "tether-allowed"]

    def on(self):
        self.set(TETHERING_POLICY="kick", TETHERING_PREVENT="true",
                 TETHERING_REDIRECT="true")

    def test_tethered_http_is_sent_to_our_server(self):
        self.on()
        self.sweep()
        [rule] = self.nat()
        self.assertEqual(rule["action"], "dst-nat")
        self.assertEqual(rule["to-addresses"], self.SERVER)
        self.assertEqual(rule["to-ports"], "80")
        self.assertEqual(rule["packet-mark"], "tether-redirect")

    def test_only_traffic_one_hop_short_is_marked(self):
        """The phone's own browsing arrives at 64 and must never be redirected."""
        self.on()
        self.sweep()
        ttls = sorted(r["ttl"] for r in self.redirect_marks())
        self.assertEqual(ttls, ["equal:127", "equal:254", "equal:63"])
        for r in self.redirect_marks():
            self.assertEqual(r["dst-port"], "80")
            self.assertEqual(r["hotspot"], "auth")

    def test_a_packet_mark_not_a_connection_mark(self):
        """
        Connection marks steer skylink3's Starlink overflow. Overwriting one
        would send somebody's connection out the wrong uplink.
        """
        self.on()
        self.sweep()
        for r in self.redirect_marks():
            self.assertEqual(r["action"], "mark-packet")
            self.assertNotIn("new-connection-mark", r)

    def test_our_server_is_exempt_from_ttl_1(self):
        """Otherwise the page itself would die at the phone."""
        self.on()
        self.sweep()
        self.assertEqual([e["address"] for e in self.allowed()], [self.SERVER])
        self.assertEqual(self.prevent_rules()[0]["src-address-list"],
                         "!tether-allowed")

    def test_no_redirect_without_prevention(self):
        """Without prevention the friend has internet and no reason to buy."""
        self.set(TETHERING_POLICY="kick", TETHERING_REDIRECT="true")
        self.sweep()
        self.assertEqual(self.nat(), [])
        self.assertEqual(self.redirect_marks(), [])

    def test_off_by_default(self):
        self.set(TETHERING_POLICY="kick", TETHERING_PREVENT="true")
        self.sweep()
        self.assertEqual(self.nat(), [])

    def test_goes_under_the_hotspots_dynamic_rules(self):
        """
        RouterOS refuses place-before against a dynamic rule, and a refused
        add is a redirect that never got installed.
        """
        self.api.rows("ip", "firewall", "nat").extend([
            {".id": "*1A", "chain": "dstnat", "action": "jump", "dynamic": "true"},
            {".id": "*1B", "chain": "srcnat", "action": "masquerade"},
        ])
        self.on()
        self.sweep()
        self.assertEqual(self.nat()[0].get("place-before"), "*1B")

    def test_switching_off_takes_it_all_off(self):
        from billing.tasks.tethering_tasks import detect_tethering_for_tenant

        self.on()
        self.sweep()
        self.set(TETHERING_POLICY="off")
        with patch("billing.router_service.safe_connect_router",
                   return_value=self.api):
            detect_tethering_for_tenant.run(self.t1.id)
        self.assertEqual(self.nat(), [])
        self.assertEqual(self.redirect_marks(), [])
        self.assertEqual(self.allowed(), [])

    def test_routeros_reordering_a_list_is_not_a_change(self):
        """
        It reads hotspot=to-client,auth back as auth,to-client. Treated as a
        change, every sweep rewrote the rule.
        """
        self.on()
        self.sweep()
        self.prevent_rules()[0]["hotspot"] = "auth,to-client"
        with patch.object(FakeMikrotik.Table, "update") as update:
            self.sweep()
        update.assert_not_called()


class SharedPageTests(TestCase):

    def test_page_is_public_and_uncached(self):
        response = self.client.get("/shared/", secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Disconnect from this hotspot", response.content)
        self.assertEqual(response["Cache-Control"], "no-store")


class IPv6OpenTests(TestCase):
    """The warning that went off on a router with no IPv6 anywhere."""

    def answer(self, rows):
        from billing.services import tethering

        api = FakeMikrotik()
        api.rows("ipv6", "address").extend(rows)
        return tethering.ipv6_is_open(api)

    def test_loopback_and_link_local_are_not_open(self):
        self.assertFalse(self.answer([
            {"address": "::1/128", "interface": "lo"},
            {"address": "fe80::f61e:57ff:fee2:8098/64", "interface": "bridge1"},
        ]))

    def test_a_global_address_is_open(self):
        self.assertTrue(self.answer([
            {"address": "::1/128", "interface": "lo"},
            {"address": "2001:db8::1/64", "interface": "bridge1"},
        ]))
