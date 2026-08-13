"""
The router is not asked to spell an address the way we stored it.

`active_hotspot_macs` says in its own docstring that RouterOS is inconsistent
about the case it reports addresses in, and normalises for that reason. The
three places that act on a router did not: they compared `u.get("name")` and
`session.get("mac-address")` to the stored string exactly.

A miss there is silent, and each one fails in a direction that costs somebody
something:

* `enable_hotspot` did not find the stale user, so it left it and added a
  second under the same name — a duplicate, or a refusal, and a refusal is a
  customer who has paid and is not provisioned.
* `disable_hotspot` did not find the live session, so an expired customer
  stayed online and an evicted device kept its session. That one compounds:
  the device then shows in `active_hotspot_macs` forever, never reads as idle,
  is never evictable again, and its owner's other phone is refused with "this
  code is connected on another device" permanently — the complaint this whole
  change set exists to end, arriving by a route the database cannot see.
* `get_hotspot_live_usage` did not find the session, so a connected subscriber
  was reported offline with no usage.
"""

from datetime import timedelta
from unittest.mock import MagicMock

from django.test import SimpleTestCase
from django.utils import timezone

from billing.router_service import (
    disable_hotspot, enable_hotspot, get_hotspot_live_usage,
)

STORED = "3E:5E:04:A6:ED:BD"
AS_ROUTER_REPORTS_IT = "3e:5e:04:a6:ed:bd"
STRANGER = "AA:BB:CC:DD:EE:01"


class FakePath(list):
    """Enough of librouteros' Path to see what was added and removed."""

    def __init__(self, rows):
        super().__init__(rows)
        self.removed = []
        self.added = []

    def remove(self, *ids):
        self.removed.extend(ids)

    def add(self, **kwargs):
        self.added.append(kwargs)


class FakeApi:
    def __init__(self, users=None, actives=None):
        self.users = FakePath(users or [])
        self.actives = FakePath(actives or [])

    def path(self, *parts):
        return self.actives if parts[-1] == "active" else self.users


class EnableHotspotCaseTests(SimpleTestCase):

    def _enable(self, api):
        package = MagicMock()
        router = MagicMock()
        expiry = timezone.now() + timedelta(hours=1)
        # ensure_hotspot_profile reaches a router; the name it returns is all
        # this needs and the profile itself is not what is under test.
        import billing.router_service as rs
        original = rs.ensure_hotspot_profile
        rs.ensure_hotspot_profile = lambda *a, **k: "prof"
        try:
            enable_hotspot(api, router, STORED, package, expiry)
        finally:
            rs.ensure_hotspot_profile = original

    def test_the_stale_user_is_replaced_whatever_case_it_is_in(self):
        api = FakeApi(users=[{".id": "*1", "name": AS_ROUTER_REPORTS_IT}])
        self._enable(api)

        self.assertEqual(
            api.users.removed, ["*1"],
            "the existing user was left behind, so this adds a duplicate")
        self.assertEqual(len(api.users.added), 1)
        self.assertEqual(api.users.added[0]["name"], STORED)

    def test_another_devices_user_is_not_removed(self):
        api = FakeApi(users=[{".id": "*9", "name": STRANGER}])
        self._enable(api)
        self.assertEqual(api.users.removed, [], "removed a stranger's access")


class DisableHotspotCaseTests(SimpleTestCase):

    def test_the_live_session_is_ended_whatever_case_it_is_in(self):
        api = FakeApi(
            users=[{".id": "*1", "name": AS_ROUTER_REPORTS_IT}],
            actives=[{".id": "*A", "user": AS_ROUTER_REPORTS_IT}],
        )
        disable_hotspot(api, STORED)

        self.assertEqual(
            api.actives.removed, ["*A"],
            "the session survived, so the device stays online and stays "
            "un-evictable")
        self.assertEqual(api.users.removed, ["*1"])

    def test_the_session_matches_on_the_address_field_too(self):
        api = FakeApi(actives=[{".id": "*A", "mac-address": AS_ROUTER_REPORTS_IT}])
        disable_hotspot(api, STORED)
        self.assertEqual(api.actives.removed, ["*A"])

    def test_a_stranger_is_left_connected(self):
        api = FakeApi(
            users=[{".id": "*9", "name": STRANGER}],
            actives=[{".id": "*B", "user": STRANGER}],
        )
        disable_hotspot(api, STORED)
        self.assertEqual(api.actives.removed, [], "cut off the wrong device")
        self.assertEqual(api.users.removed, [])


class LiveUsageCaseTests(SimpleTestCase):

    def _usage(self, api, monkey):
        import billing.router_service as rs
        original = rs.safe_connect_router
        rs.safe_connect_router = lambda *a, **k: api
        try:
            return get_hotspot_live_usage(MagicMock(), monkey)
        finally:
            rs.safe_connect_router = original

    def test_a_connected_subscriber_is_not_reported_offline(self):
        api = FakeApi(actives=[{
            ".id": "*A", "user": AS_ROUTER_REPORTS_IT,
            "bytes-in": 10, "bytes-out": 20, "uptime": "1m",
            "address": "10.5.50.2",
        }])
        data = self._usage(api, STORED)
        self.assertTrue(data["connected"], "read as offline over letter case")
        self.assertEqual(data["rx_bytes"], 10)

    def test_a_stranger_is_not_reported_as_this_subscriber(self):
        api = FakeApi(actives=[{
            ".id": "*B", "user": STRANGER,
            "bytes-in": 1, "bytes-out": 1, "uptime": "1m", "address": "10.5.50.3",
        }])
        self.assertFalse(self._usage(api, STORED)["connected"])
