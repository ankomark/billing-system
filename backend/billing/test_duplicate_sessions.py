"""
One device, one session.

A hotspot session is keyed by MAC and address together, so a device that picks
up a new DHCP lease opens a second session while the first stays up, and
shared-users=2 on a two-device profile lets RouterOS keep both. Keepalive would
close the dead one and is deliberately off -- turning it on logged 44
subscribers out in a day -- so nothing ever did.

87 MACs were carrying a duplicate on 2026-09-13. The cost is not an address in
the pool: RouterOS counts every session's uptime against the account's
limit-uptime, so a dead session keeps spending a window the customer paid for.
Customer 729 had 29 hours of session time against a package bought 90 minutes
earlier -- two sessions, one idle since it opened.

Which to keep is not a guess. In every duplicate observed, the stale session's
idle-time was close to its entire uptime while the live one was idle seconds,
so the rule is least-idle wins -- not newest, not longest.
"""

from unittest.mock import patch

from django.test import TestCase

from billing.models import RouterDevice, Tenant
from billing.services.duplicate_sessions import clear_duplicate_sessions
from billing.tenancy import tenant_context

MAC = "AA:BB:CC:D0:00:01"


class _Actives(list):
    def __init__(self, rows):
        super().__init__(rows)
        self.removed = []

    def remove(self, rid):
        self.removed.append(rid)
        for row in list(self):
            if row[".id"] == rid:
                super().remove(row)


class _Api:
    def __init__(self, actives):
        self.actives = actives

    def path(self, *parts):
        return self.actives if parts[-1] == "active" else []


class ClearingDuplicateSessions(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="dup-r", ip_address="10.9.3.1",
                username="u", password="p", is_active=True, is_online=True)

    def _session(self, rid, address, uptime, idle, mac=MAC):
        return {".id": rid, "mac-address": mac, "address": address,
                "uptime": uptime, "idle-time": idle}

    def _run(self, rows, *, apply=True, min_idle=600):
        actives = _Actives(rows)
        with patch("billing.services.duplicate_sessions.safe_connect_router",
                   return_value=_Api(actives)):
            closed, busy = clear_duplicate_sessions(
                apply=apply, routers=[self.router], min_idle=min_idle)
        return actives, closed, busy

    # ------------------------------------------------------------- the fix

    def test_the_stale_session_is_closed(self):
        """
        The production shape: same MAC, two addresses, one idle since it
        opened.
        """
        actives, closed, _ = self._run([
            self._session("*1", "192.168.88.107", "13h44m53s", "0s"),
            self._session("*2", "192.168.88.123", "15h55m29s", "15h44m20s"),
        ])

        self.assertEqual(closed, 1)
        self.assertEqual(actives.removed, ["*2"])

    def test_the_least_idle_session_is_kept_even_when_it_is_the_newer(self):
        """
        Not newest, not longest -- least idle. The live session is frequently
        the younger one, because it is the lease the device just took.
        """
        actives, _, _ = self._run([
            self._session("*1", "192.168.88.146", "16h25m7s", "11h58m30s"),
            self._session("*2", "192.168.88.206", "38m22s", "1s"),
        ])

        self.assertEqual(actives.removed, ["*1"])

    def test_a_lone_session_is_never_touched(self):
        actives, closed, _ = self._run([
            self._session("*1", "192.168.88.10", "3h", "2h"),
        ])

        self.assertEqual(closed, 0)
        self.assertEqual(actives.removed, [])

    def test_three_sessions_leave_only_the_live_one(self):
        actives, closed, _ = self._run([
            self._session("*1", "192.168.88.10", "5h", "4h"),
            self._session("*2", "192.168.88.11", "3h", "12s"),
            self._session("*3", "192.168.88.12", "9h", "8h"),
        ])

        self.assertEqual(closed, 2)
        self.assertEqual(sorted(actives.removed), ["*1", "*3"])

    def test_different_macs_are_not_duplicates(self):
        actives, closed, _ = self._run([
            self._session("*1", "192.168.88.10", "5h", "4h"),
            self._session("*2", "192.168.88.11", "5h", "4h",
                          mac="AA:BB:CC:D0:00:02"),
        ])

        self.assertEqual(closed, 0)

    # ----------------------------------------------------------- the guard

    def test_a_busy_second_session_is_left_alone(self):
        """
        Two sessions both carrying traffic is not what this cleans up, and
        removing either would cut off something in use. Reported for a human
        instead.
        """
        actives, closed, busy = self._run([
            self._session("*1", "192.168.88.10", "5h", "3s"),
            self._session("*2", "192.168.88.11", "5h", "20s"),
        ])

        self.assertEqual(closed, 0)
        self.assertEqual(actives.removed, [])
        self.assertEqual(len(busy), 1)

    def test_the_idle_floor_is_configurable(self):
        actives, closed, _ = self._run([
            self._session("*1", "192.168.88.10", "5h", "1s"),
            self._session("*2", "192.168.88.11", "5h", "30s"),
        ], min_idle=10)

        self.assertEqual(closed, 1)
        self.assertEqual(actives.removed, ["*2"])

    def test_reporting_closes_nothing(self):
        actives, closed, _ = self._run([
            self._session("*1", "192.168.88.10", "5h", "0s"),
            self._session("*2", "192.168.88.11", "9h", "8h"),
        ], apply=False)

        self.assertEqual(closed, 1)
        self.assertEqual(actives.removed, [])

    def test_an_unreachable_router_is_skipped(self):
        with patch("billing.services.duplicate_sessions.safe_connect_router",
                   return_value=None):
            closed, busy = clear_duplicate_sessions(
                apply=True, routers=[self.router])

        self.assertEqual((closed, busy), (0, []))

    def test_a_missing_idle_time_reads_as_zero_not_as_stale(self):
        """
        A router build that does not report idle-time must not have every
        session read as freshly active and then, by the same token, never be
        cleaned -- but it must certainly not have them read as stale and
        removed. Zero is the safe direction.
        """
        actives, closed, _ = self._run([
            {".id": "*1", "mac-address": MAC, "address": "192.168.88.10",
             "uptime": "5h"},
            {".id": "*2", "mac-address": MAC, "address": "192.168.88.11",
             "uptime": "9h"},
        ])

        self.assertEqual(closed, 0)
        self.assertEqual(actives.removed, [])
