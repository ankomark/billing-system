"""
The session that outlived its account.

On 2026-09-23, hours after a sweep disabled 149 accounts on skylink3, the
router was still serving this:

    C6:59:D0:A8:9B:3B  192.168.88.94  up 11h26m
    bytes-out 1.19 GB   session-time-left 4d7h55m   limit-bytes-total 16.44 GB

No account stood behind it. `find_uncovered_logins` iterates accounts and
there was none; `clear_duplicate_sessions` compares sessions against each
other and this one was unique. Disabling refuses the next login, and RouterOS
counts down `session-time-left` for whoever is already on -- so it had four
days left.

The tests that matter most here are the two that end nothing: the empty
account table, and the count past what is believable. This decides what to
disconnect by subtracting one router read from another, and getting that
backwards takes a whole site off the air.
"""

from django.test import SimpleTestCase

from billing.services.stale_sessions import (
    close_stale_sessions, find_stale_sessions, uptime_seconds,
)


class FakeApi:
    """The two paths this reads, and a record of what it ended."""

    def __init__(self, users, actives):
        self.users = users
        self.actives = actives
        self.removed = []

    def path(self, *parts):
        key = "/".join(parts)
        api = self

        class Path:
            def __iter__(self):
                if key == "ip/hotspot/user":
                    return iter(api.users)
                if key == "ip/hotspot/active":
                    return iter(api.actives)
                return iter(())

            def remove(self, ident):
                api.removed.append(ident)

        return Path()


def account(name, disabled="false"):
    return {".id": f"*A{abs(hash(name)) % 900 + 10}", "name": name,
            "disabled": disabled}


def session(user, uptime="11h26m27s", address="192.168.88.94"):
    return {".id": f"*S{abs(hash(user)) % 900 + 10}", "user": user,
            "mac-address": user, "address": address, "uptime": uptime}


GHOST = session("C6:59:D0:A8:9B:3B")
PAYING = session("AA:BB:CC:DD:EE:01")


class ReadingTheClock(SimpleTestCase):

    def test_routeros_uptime_is_understood(self):
        self.assertEqual(uptime_seconds("45s"), 45)
        self.assertEqual(uptime_seconds("11h26m27s"), 41187)
        self.assertEqual(uptime_seconds("4d7h55m42s"), 374142)
        self.assertEqual(uptime_seconds("1w2d"), 777600)

    def test_an_unreadable_uptime_reads_as_new_rather_than_old(self):
        """
        Which is the safe direction: a session this cannot age is left
        running rather than disconnected on a parse it did not understand.
        """
        self.assertEqual(uptime_seconds(None), 0)
        self.assertEqual(uptime_seconds("unknown"), 0)


class WhatCounts(SimpleTestCase):

    def test_the_session_actually_found_on_skylink3_is_caught(self):
        api = FakeApi([account("AA:BB:CC:DD:EE:01")], [GHOST, PAYING])

        stale, seen = find_stale_sessions(api)

        self.assertEqual([s["user"] for s in stale], ["C6:59:D0:A8:9B:3B"])
        self.assertEqual(seen, 1)

    def test_a_session_on_a_disabled_account_is_caught_too(self):
        """
        The backstop for close_uncovered_logins, which disables the account
        and logs "disabled but its session would not end" when the removal
        fails. That session is this sweep's on the next pass.
        """
        api = FakeApi([account("AA:BB:CC:DD:EE:01", disabled="true")],
                      [PAYING])

        stale, _ = find_stale_sessions(api)

        self.assertEqual([s["user"] for s in stale], ["AA:BB:CC:DD:EE:01"])

    def test_a_paying_session_is_never_touched(self):
        api = FakeApi([account("AA:BB:CC:DD:EE:01")], [PAYING])

        stale, _ = find_stale_sessions(api)

        self.assertEqual(stale, [])

    def test_a_session_seconds_old_is_mid_provisioning_not_stranded(self):
        """The 600s floor the estate scan uses, for the same reason."""
        fresh = session("DE:AD:BE:EF:00:01", uptime="45s")
        api = FakeApi([account("AA:BB:CC:DD:EE:01")], [fresh])

        stale, _ = find_stale_sessions(api)

        self.assertEqual(stale, [])


class WhatItRefusesToDo(SimpleTestCase):

    def test_an_empty_account_table_ends_nothing(self):
        """
        Every session reads stale when the user table does not come back.
        Acting on that disconnects the entire site.
        """
        api = FakeApi([], [GHOST, PAYING])

        ended, refused = close_stale_sessions("skylink3", api, apply=True)

        self.assertEqual(ended, 0)
        self.assertTrue(refused)
        self.assertEqual(api.removed, [])

    def test_more_than_is_believable_ends_nothing(self):
        many = [session(f"AA:BB:CC:00:{i:02X}:01") for i in range(12)]
        api = FakeApi([account("held")], many)

        ended, refused = close_stale_sessions(
            "skylink3", api, apply=True, max_end=10)

        self.assertEqual(ended, 0)
        self.assertTrue(refused)
        self.assertEqual(api.removed, [])

    def test_reporting_ends_nothing(self):
        api = FakeApi([account("AA:BB:CC:DD:EE:01")], [GHOST, PAYING])

        found, refused = close_stale_sessions("skylink3", api, apply=False)

        self.assertEqual(found, 1)
        self.assertFalse(refused)
        self.assertEqual(api.removed, [])


class WhatItEnds(SimpleTestCase):

    def test_the_ghost_is_ended_and_the_payer_is_not(self):
        api = FakeApi([account("AA:BB:CC:DD:EE:01")], [GHOST, PAYING])

        ended, refused = close_stale_sessions("skylink3", api, apply=True)

        self.assertEqual(ended, 1)
        self.assertFalse(refused)
        self.assertEqual(api.removed, [GHOST[".id"]])

    def test_a_clean_router_is_left_entirely_alone(self):
        api = FakeApi([account("AA:BB:CC:DD:EE:01")], [PAYING])

        ended, refused = close_stale_sessions("skylink3", api, apply=True)

        self.assertEqual((ended, refused), (0, False))
        self.assertEqual(api.removed, [])
