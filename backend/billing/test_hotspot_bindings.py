"""
The way onto skylink3 that needed no account at all.

`hotspot_logins` closed the login that needed no voucher and `uncovered_logins`
closes the account whose package ended. Both start from `/ip/hotspot/user`. A
`type=bypassed` ip-binding is not an account, so neither of them could ever
have seen the one skylink3 was carrying on 2026-09-23:

    ip hotspot ip-binding  6C:02:E0:08:B5:23  type=bypassed  "admin laptop"

These tests fix the two halves of the judgement: that an unmarked bypass is
reported, and that a binding an operator marked KEEP is left alone -- because
the till and the camera are bypassed on purpose and cannot sign in.

No database: the sweep reads the router and writes the router, and `router` is
only ever a name in a log line.
"""

from django.test import SimpleTestCase

from billing.services.hotspot_bindings import (
    close_bypassing_bindings, find_bypassing_bindings,
)


class FakeApi:
    """The one path this reads, and a record of what it wrote."""

    def __init__(self, bindings):
        self.bindings = bindings
        self.updates = []

    def path(self, *parts):
        key = "/".join(parts)
        api = self

        class Path:
            def __iter__(self):
                if key == "ip/hotspot/ip-binding":
                    return iter(api.bindings)
                return iter(())

            def update(self, **kwargs):
                api.updates.append(kwargs)

        return Path()


def binding(mac, **extra):
    row = {".id": f"*{abs(hash(mac)) % 900 + 10}", "mac-address": mac,
           "type": "bypassed", "disabled": "false"}
    row.update(extra)
    return row


LAPTOP = binding("6C:02:E0:08:B5:23", comment="admin laptop")
TILL = binding("AA:BB:CC:00:00:01", comment="KEEP | till printer")
REGULAR = binding("AA:BB:CC:00:00:02", type="regular")


class WhatCounts(SimpleTestCase):

    def test_the_binding_actually_found_on_skylink3_is_reported(self):
        found = find_bypassing_bindings(FakeApi([LAPTOP, REGULAR]))

        self.assertEqual([r["mac-address"] for r in found],
                         ["6C:02:E0:08:B5:23"])

    def test_a_binding_marked_keep_is_left_alone(self):
        """
        The till has no browser. Switching it off on a schedule is the
        failure this sweep must not have.
        """
        found = find_bypassing_bindings(FakeApi([TILL]))

        self.assertEqual(found, [])

    def test_keep_is_read_however_it_was_written(self):
        """An operator marking one by hand should not have to match a format."""
        for comment in ("KEEP | camera", "keep - camera", "camera (Keep)"):
            with self.subTest(comment=comment):
                row = binding("AA:BB:CC:00:00:09", comment=comment)
                self.assertEqual(find_bypassing_bindings(FakeApi([row])), [])

    def test_a_binding_that_does_not_bypass_is_not_this_sweeps_business(self):
        """`regular` and `blocked` bindings grant nothing."""
        found = find_bypassing_bindings(FakeApi([REGULAR]))

        self.assertEqual(found, [])

    def test_one_already_closed_is_not_reported_again(self):
        closed = binding("AA:BB:CC:00:00:03", disabled="true")

        self.assertEqual(find_bypassing_bindings(FakeApi([closed])), [])


class WhatItWrites(SimpleTestCase):

    def test_reporting_writes_nothing(self):
        """
        The default, and the whole reason this sweep is safe to run hourly
        before anybody has marked their tills.
        """
        api = FakeApi([LAPTOP])

        found = close_bypassing_bindings("skylink3", api, apply=False)

        self.assertEqual(found, 1)
        self.assertEqual(api.updates, [])

    def test_closing_disables_and_says_who_did_it(self):
        api = FakeApi([LAPTOP])

        close_bypassing_bindings("skylink3", api, apply=True)

        self.assertEqual(len(api.updates), 1)
        wrote = api.updates[0]
        self.assertEqual(wrote[".id"], LAPTOP[".id"])
        self.assertEqual(wrote["disabled"], "yes")
        self.assertIn("bypass closed", wrote["comment"])

    def test_closing_leaves_the_marked_ones_connected(self):
        api = FakeApi([LAPTOP, TILL])

        close_bypassing_bindings("skylink3", api, apply=True)

        self.assertEqual([u[".id"] for u in api.updates], [LAPTOP[".id"]])

    def test_a_router_with_nothing_bypassed_writes_nothing(self):
        api = FakeApi([REGULAR, TILL])

        self.assertEqual(
            close_bypassing_bindings("skylink3", api, apply=True), 0)
        self.assertEqual(api.updates, [])
