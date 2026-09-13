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


class _Leases(list):
    pass


class _ApiWithLeases:
    """A router that also answers for its DHCP leases and host table."""

    def __init__(self, actives, leases, hosts=None):
        self.actives = actives
        self.leases = leases
        self.hosts = hosts if hosts is not None else _Actives([])

    def path(self, *parts):
        if parts[-1] == "active":
            return self.actives
        if parts[-1] == "lease":
            return self.leases
        if parts[-1] == "host":
            return self.hosts
        return []


class FreeingStrandedSessions(TestCase):
    """
    A device whose DHCP lease moved, with its authorisation left behind.

    The session is keyed by MAC and address together, so traffic from the new
    address is unauthorised while the old session holds the authorisation and
    never times out -- keepalive is off deliberately. The customer is connected
    with a valid voucher and no internet.

    A power cut does it to the whole estate at once. Ten devices were stranded
    on 2026-09-13 after both routers rebooted, idle between 55 minutes and five
    hours, every one entitled. The duplicate sweep could not help: there is
    only ONE session and it is simply on the wrong address.
    """

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="strand-r", ip_address="10.9.3.2",
                username="u", password="p", is_active=True, is_online=True)

    def _run(self, actives, leases, hosts=None, *, apply=True, min_idle=600):
        from billing.services.duplicate_sessions import clear_stranded_sessions
        a = _Actives(actives)
        h = _Actives(hosts or [])
        api = _ApiWithLeases(a, _Leases(leases), h)
        with patch("billing.services.duplicate_sessions.safe_connect_router",
                   return_value=api):
            freed = clear_stranded_sessions(
                apply=apply, routers=[self.router], min_idle=min_idle)
        return a, h, freed

    def _session(self, address, idle, rid="*1"):
        return {".id": rid, "mac-address": MAC, "address": address,
                "uptime": "6h", "idle-time": idle}

    def _lease(self, address, status="bound"):
        return {"mac-address": MAC, "address": address, "status": status}

    def test_a_session_on_a_lost_address_is_freed(self):
        """The production case, to the letter: D6:AC:FC:2E:1F:34."""
        a, _, freed = self._run(
            [self._session("192.168.88.231", "4h25m")],
            [self._lease("192.168.88.164")])

        self.assertEqual(freed, 1)
        self.assertEqual(a.removed, ["*1"])

    def test_the_host_entry_goes_too(self):
        """
        Not tidiness. RouterOS will not re-run MAC authentication while a stale
        host is standing -- which is exactly why retry_mac_login exists -- so
        removing only the session leaves the device as stuck as it was.
        """
        hosts = [{".id": "*h1", "mac-address": MAC,
                  "address": "192.168.88.231"}]
        _, h, _ = self._run(
            [self._session("192.168.88.231", "4h25m")],
            [self._lease("192.168.88.164")], hosts)

        self.assertEqual(h.removed, ["*h1"])

    def test_a_session_on_the_right_address_is_left_alone(self):
        a, _, freed = self._run(
            [self._session("192.168.88.164", "4h25m")],
            [self._lease("192.168.88.164")])

        self.assertEqual(freed, 0)
        self.assertEqual(a.removed, [])

    def test_a_mismatch_that_is_still_busy_is_left_alone(self):
        """A lease renewal caught mid-flight is not a stranded device."""
        a, _, freed = self._run(
            [self._session("192.168.88.231", "12s")],
            [self._lease("192.168.88.164")])

        self.assertEqual(freed, 0)
        self.assertEqual(a.removed, [])

    def test_a_device_with_no_bound_lease_is_never_touched(self):
        """
        No lease is no evidence. A device on a static address would otherwise
        be cut off for not appearing in a table it was never in.
        """
        a, _, freed = self._run(
            [self._session("192.168.88.231", "4h")], [])

        self.assertEqual(freed, 0)
        self.assertEqual(a.removed, [])

    def test_an_unbound_lease_does_not_count_as_evidence(self):
        a, _, freed = self._run(
            [self._session("192.168.88.231", "4h")],
            [self._lease("192.168.88.164", status="waiting")])

        self.assertEqual(freed, 0)

    def test_reporting_frees_nothing(self):
        a, _, freed = self._run(
            [self._session("192.168.88.231", "4h25m")],
            [self._lease("192.168.88.164")], apply=False)

        self.assertEqual(freed, 1)
        self.assertEqual(a.removed, [])

    def test_a_router_whose_leases_cannot_be_read_is_skipped(self):
        """
        Without the lease table there is nothing to compare against, and
        guessing would mean disconnecting people on no evidence at all.
        """
        from billing.services.duplicate_sessions import clear_stranded_sessions

        class Broken(_ApiWithLeases):
            def path(self, *parts):
                if parts[-1] == "lease":
                    raise RuntimeError("no dhcp server here")
                return super().path(*parts)

        a = _Actives([self._session("192.168.88.231", "4h")])
        api = Broken(a, _Leases([]))
        with patch("billing.services.duplicate_sessions.safe_connect_router",
                   return_value=api):
            freed = clear_stranded_sessions(apply=True, routers=[self.router])

        self.assertEqual(freed, 0)
        self.assertEqual(a.removed, [])

    def test_an_address_reissued_to_another_handset_is_freed(self):
        """
        The commonest shape, and the one a bound-lease check alone misses: the
        device has NO lease of its own -- it went away and its 15-minute lease
        expired -- and the address it still authorises now belongs to somebody
        else. 59 sessions were in exactly this state on 2026-09-13.
        """
        a, _, freed = self._run(
            [self._session("192.168.88.175", "5h")],
            [{"mac-address": "F2:39:9D:B9:5E:30",
              "address": "192.168.88.175", "status": "bound"}])

        self.assertEqual(freed, 1)
        self.assertEqual(a.removed, ["*1"])

    def test_an_address_leased_to_nobody_is_left_alone(self):
        """
        No lease for the device and none for the address either. That is what a
        static address looks like from here, and cutting it off would be acting
        on the absence of evidence rather than on evidence.
        """
        a, _, freed = self._run(
            [self._session("192.168.88.231", "5h")], [])

        self.assertEqual(freed, 0)
        self.assertEqual(a.removed, [])

    def test_a_device_still_holding_its_own_address_is_left_alone(self):
        """Even when another lease exists for a different address entirely."""
        a, _, freed = self._run(
            [self._session("192.168.88.164", "5h")],
            [self._lease("192.168.88.164"),
             {"mac-address": "AA:BB:CC:D0:00:09",
              "address": "192.168.88.200", "status": "bound"}])

        self.assertEqual(freed, 0)
        self.assertEqual(a.removed, [])

    def test_a_recycled_address_still_respects_the_idle_floor(self):
        """
        A device that has just moved address and is actively using the new one
        must not have the transition interrupted.
        """
        a, _, freed = self._run(
            [self._session("192.168.88.175", "8s")],
            [{"mac-address": "F2:39:9D:B9:5E:30",
              "address": "192.168.88.175", "status": "bound"}])

        self.assertEqual(freed, 0)
        self.assertEqual(a.removed, [])

    def test_the_reason_says_which_case_matched(self):
        """
        The row carries why, not a value whose meaning depends on the branch.
        The first version passed the new holder's MAC through the same slot as
        the lease address, so the log read "its lease is C2:41:D9:5F:D0:A3" --
        a MAC where the sentence says lease.
        """
        from billing.services.duplicate_sessions import find_stranded_sessions

        a = _Actives([self._session("192.168.88.175", "5h")])
        api = _ApiWithLeases(a, _Leases([
            {"mac-address": "F2:39:9D:B9:5E:30",
             "address": "192.168.88.175", "status": "bound"}]))
        with patch("billing.services.duplicate_sessions.safe_connect_router",
                   return_value=api):
            found = find_stranded_sessions(routers=[self.router])

        why = found[0][4]
        self.assertIn("belongs to", why)
        self.assertIn("F2:39:9D:B9:5E:30", why)

    def test_a_moved_device_says_where_its_lease_went(self):
        from billing.services.duplicate_sessions import find_stranded_sessions

        a = _Actives([self._session("192.168.88.231", "5h")])
        api = _ApiWithLeases(a, _Leases([self._lease("192.168.88.164")]))
        with patch("billing.services.duplicate_sessions.safe_connect_router",
                   return_value=api):
            found = find_stranded_sessions(routers=[self.router])

        self.assertIn("192.168.88.164", found[0][4])
        self.assertIn("lease", found[0][4])
