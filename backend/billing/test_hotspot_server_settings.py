"""
Three hotspot server settings nothing was checking.

They are answered by hand in the MikroTik setup wizard when a site is built,
and never looked at again. All three were wrong on live hardware on 2026-09-10,
and each was taking customers off the air while looking like something else:

  * address-pool pointed at default-dhcp, the same range the DHCP server hands
    leases from. /ip/pool/used read {'DHCP': 119, 'hotspot': 126} = 245 of 245,
    and the router refused every new arrival with "failed to get IP address for
    host: pool empty". No address means no host entry, and no host entry means
    the device cannot be authenticated by ANY method -- so it showed "connected,
    no internet" with no sign-in page.

  * addresses-per-mac=2 halved the site's capacity again, whatever the lease
    time was.

  * login-by=cookie,http-chap. Both need the DEVICE to open a browser, which a
    television cannot do -- so a voucher bound to a TV created the account,
    configured the router, and the set stayed dark.

The checker reports rather than rewrites, and these hold it to that.
"""

from unittest.mock import MagicMock

from django.test import TestCase

from billing.services import hotspot_server


class _Path(list):
    """A RouterOS path that records writes."""

    def __init__(self, rows):
        super().__init__(rows)
        self.updated = []

    def update(self, **kwargs):
        self.updated.append(kwargs)


def fake_api(server, profile):
    paths = {
        ("ip", "hotspot"): _Path([server]),
        ("ip", "hotspot", "profile"): _Path([profile]),
    }
    api = MagicMock()
    api.path.side_effect = lambda *a: paths[tuple(a)]
    api.paths = paths
    return api


GOOD_SERVER = {".id": "*1", "name": "hotspot1", "profile": "hsprof1",
               "address-pool": "none", "addresses-per-mac": "1"}
GOOD_PROFILE = {".id": "*2", "name": "hsprof1",
                "login-by": "mac,cookie,http-chap"}


class ARouterThatIsAlreadyRight(TestCase):

    def test_nothing_is_reported(self):
        api = fake_api(dict(GOOD_SERVER), dict(GOOD_PROFILE))
        self.assertEqual(hotspot_server.check(api), {})

    def test_nothing_is_written(self):
        api = fake_api(dict(GOOD_SERVER), dict(GOOD_PROFILE))
        hotspot_server.apply(api, hotspot_server.check(api))
        self.assertEqual(api.paths[("ip", "hotspot")].updated, [])
        self.assertEqual(api.paths[("ip", "hotspot", "profile")].updated, [])

    def test_the_pool_may_be_spelled_several_ways(self):
        """
        RouterOS answers an unset pool as None, "none" or "" depending on
        version and on whether it was ever set. Treating those as drift would
        rewrite a correct router on every run.
        """
        for spelling in ("none", "None", "", "false"):
            with self.subTest(spelling=spelling):
                api = fake_api(dict(GOOD_SERVER, **{"address-pool": spelling}),
                               dict(GOOD_PROFILE))
                self.assertNotIn("address-pool", hotspot_server.check(api))


class TheFaultsFoundOnLiveHardware(TestCase):

    def test_a_pool_shared_with_dhcp_is_reported(self):
        api = fake_api(dict(GOOD_SERVER, **{"address-pool": "default-dhcp"}),
                       dict(GOOD_PROFILE))
        drift = hotspot_server.check(api)
        self.assertEqual(drift["address-pool"], ("default-dhcp", "none"))

    def test_two_addresses_per_mac_is_reported(self):
        api = fake_api(dict(GOOD_SERVER, **{"addresses-per-mac": "2"}),
                       dict(GOOD_PROFILE))
        self.assertEqual(hotspot_server.check(api)["addresses-per-mac"],
                         ("2", "1"))

    def test_a_login_that_needs_a_browser_is_reported(self):
        api = fake_api(dict(GOOD_SERVER),
                       dict(GOOD_PROFILE, **{"login-by": "cookie,http-chap"}))
        self.assertIn("login-by", hotspot_server.check(api))

    def test_all_three_are_corrected(self):
        api = fake_api(
            dict(GOOD_SERVER, **{"address-pool": "default-dhcp",
                                 "addresses-per-mac": "2"}),
            dict(GOOD_PROFILE, **{"login-by": "cookie,http-chap"}))
        changed = hotspot_server.apply(api, hotspot_server.check(api))

        self.assertEqual(changed["address-pool"], ("default-dhcp", "none"))
        self.assertEqual(changed["addresses-per-mac"], ("2", "1"))
        writes = api.paths[("ip", "hotspot")].updated
        self.assertEqual({w["address-pool"] for w in writes if "address-pool" in w},
                         {"none"})

    def test_mac_is_added_without_removing_the_others(self):
        """
        A walk-up with no account still needs the portal to buy one, so cookie
        and http-chap must survive. Replacing them would sell nothing.
        """
        api = fake_api(dict(GOOD_SERVER),
                       dict(GOOD_PROFILE, **{"login-by": "cookie,http-chap"}))
        hotspot_server.apply(api, hotspot_server.check(api))
        wrote = api.paths[("ip", "hotspot", "profile")].updated[0]["login-by"]
        self.assertTrue(wrote.startswith("mac"))
        self.assertIn("cookie", wrote)
        self.assertIn("http-chap", wrote)

    def test_mac_is_not_added_twice(self):
        api = fake_api(dict(GOOD_SERVER),
                       dict(GOOD_PROFILE, **{"login-by": "cookie,mac"}))
        self.assertNotIn("login-by", hotspot_server.check(api))

    def test_a_router_with_no_hotspot_at_all_is_reported(self):
        api = fake_api(None, dict(GOOD_PROFILE))
        api.paths[("ip", "hotspot")] = _Path([])
        self.assertIn("hotspot", hotspot_server.check(api))
