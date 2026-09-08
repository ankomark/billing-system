"""
Finding one hotspot user must not cost the whole table.

Provisioning walked every hotspot user with all eleven of its attributes to
find at most one of them by name. A busy router carries several hundred -- 433
on skylink when this was written -- and the rest of each row is profile,
comment, byte counters and uptime limits that none of this reads.

Where it sits is what made it expensive rather than merely wasteful.
enable_hotspot runs on the path a customer waits on after paying, and
ROUTER_API_TIMEOUT bounds the socket for reads as well as connects. On
2026-09-07 this read timed out part-way through the list on skylink3 -- 5G,
measured at 600ms round trip and 33% loss -- and customers who had paid were
left with no account on the hardware:

    TimeoutError at enable_hotspot: for u in users:

Measured against the live router afterwards: 0.83s for the full list against
0.24s for the two fields actually used, the same 433 rows and the same ids.

The comparison stays canonical and stays in Python. RouterOS is inconsistent
about the case and separators it reports an address in, so filtering
server-side on an exact `name` would silently miss a row spelled differently --
and a miss leaves the stale user behind, which makes the add either duplicate
it or fail. A refusal there is a customer who has paid and is not provisioned,
which is the whole reason the comparison is canonical in the first place.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from billing.models import Package, RouterDevice, Tenant
from billing.tenancy import tenant_context


class _Users(list):
    """A RouterOS path that records how it was queried."""

    def __init__(self, rows):
        super().__init__(rows)
        self.selected = None
        self.removed = []
        self.added = []

    def select(self, *keys):
        # What the caller asked the router to send back.
        self.selected = [str(k) for k in keys]
        return self

    def remove(self, *ids):
        self.removed.extend(ids)

    def add(self, **kwargs):
        self.added.append(kwargs)
        return "*NEW"


class HotspotLookupCostTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r1", ip_address="10.0.0.51",
                username="u", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="3hrs unlimited", download_speed=2,
                upload_speed=2, price=Decimal("10.00"), duration_value=3,
                duration_unit="hours", is_hotspot=True, max_devices=2)

    def _grant(self, existing_rows, mac):
        users = _Users(existing_rows)
        api = MagicMock()
        api.path.return_value = users
        with patch("billing.router_service.ensure_hotspot_profile",
                   return_value="HOTSPOT_PKG_1_D2"):
            from billing.router_service import enable_hotspot
            enable_hotspot(api, self.router, mac, self.package,
                           timezone.now() + timezone.timedelta(hours=3))
        return users

    def test_only_the_fields_it_reads_are_requested(self):
        """
        The whole point: two fields, not eleven, over a link that drops a third
        of its packets.
        """
        users = self._grant([], "11:22:33:44:55:66")
        self.assertIsNotNone(users.selected,
                             "the full row must not be pulled to match a name")
        self.assertEqual(len(users.selected), 2)
        self.assertIn(".id", users.selected)
        self.assertIn("name", users.selected)

    def test_a_stale_user_is_still_found_and_removed(self):
        users = self._grant(
            [{".id": "*7", "name": "11:22:33:44:55:66"}], "11:22:33:44:55:66")
        self.assertEqual(users.removed, ["*7"])
        self.assertEqual(len(users.added), 1)

    def test_a_differently_spelled_address_is_still_matched(self):
        """
        The regression a server-side exact match would have introduced. The
        router reports addresses in whatever case it feels like; a miss here
        leaves the stale user behind and the add then duplicates or fails --
        and a failed add is a paid customer with nothing on the hardware.
        """
        users = self._grant(
            [{".id": "*8", "name": "11-22-33-44-55-66"}], "11:22:33:44:55:66")
        self.assertEqual(users.removed, ["*8"],
                         "matched canonically, not as a string")

    def test_somebody_elses_address_is_left_alone(self):
        users = self._grant(
            [{".id": "*9", "name": "AA:BB:CC:DD:EE:FF"}], "11:22:33:44:55:66")
        self.assertEqual(users.removed, [])
        self.assertEqual(len(users.added), 1)


class HotspotRemovalCostTests(TestCase):
    """disable_hotspot runs on every expiry and every eviction."""

    def test_the_session_sweep_asks_for_three_fields(self):
        from billing.router_service import disable_hotspot

        actives = _Users([{".id": "*1", "user": "11:22:33:44:55:66",
                           "mac-address": "11:22:33:44:55:66"}])
        users = _Users([{".id": "*2", "name": "11:22:33:44:55:66"}])

        api = MagicMock()
        api.path.side_effect = lambda *a: actives if a[-1] == "active" else users

        disable_hotspot(api, "11:22:33:44:55:66")

        self.assertEqual(sorted(actives.selected),
                         sorted([".id", "user", "mac-address"]))
        self.assertEqual(actives.removed, ["*1"], "the live session is ended")
        self.assertEqual(users.removed, ["*2"], "the account is removed")

    def test_a_differently_spelled_session_is_still_ended(self):
        from billing.router_service import disable_hotspot

        actives = _Users([{".id": "*1", "user": "11-22-33-44-55-66",
                           "mac-address": "11-22-33-44-55-66"}])
        users = _Users([])
        api = MagicMock()
        api.path.side_effect = lambda *a: actives if a[-1] == "active" else users

        disable_hotspot(api, "11:22:33:44:55:66")
        self.assertEqual(actives.removed, ["*1"])
