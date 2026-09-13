"""
An orphan that has been disabled long enough is finally removed.

The sweep disables and never deletes, which was the right default while the
leak it reconciles was still being fixed -- disabling is reversible and an
operator can inspect before anything is destroyed. But nothing ever cleared
them, so they accumulated: 24 disabled orphans across two routers by
2026-09-13, several carrying gigabytes of history from before they were
orphaned, all of them inert and none of them going anywhere.

So disabling now stamps the date into the account's own comment, and an
account still disabled after the retention window is removed. The stamp lives
on the router rather than in a table because it survives a database restore,
an operator reading the account can see it, and it cannot drift out of step
with the thing it describes.

The delay is the whole safety property: it is the window in which a wrong
disable can be noticed and undone by setting disabled=no. A deletion cannot be.
"""

import datetime as dt
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from billing.models import Customer, RouterDevice, Tenant
from billing.tenancy import tenant_context

PROVISIONED = "AUTO | WIFI BILLING SYSTEM"


class _Users(list):
    """An ip/hotspot/user path recording what was written and removed."""

    def __init__(self, rows):
        super().__init__(rows)
        self.updates = []
        self.removed = []

    def update(self, **kwargs):
        self.updates.append(kwargs)
        for row in self:
            if row[".id"] == kwargs.get(".id"):
                row.update({k: v for k, v in kwargs.items() if k != ".id"})

    def remove(self, rid):
        self.removed.append(rid)
        for row in list(self):
            if row[".id"] == rid:
                super().remove(row)


class _Api:
    def __init__(self, users):
        self.users = users

    def path(self, *parts):
        if parts[-1] == "user":
            return self.users
        return []


class OrphansAreEventuallyRemoved(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="purge-r", ip_address="10.9.4.1",
                username="u", password="p", is_active=True, is_online=True)
            # The sweep refuses a tenant with no hotspot customers at all, as a
            # guard against acting on an empty database query.
            Customer.objects.create(
                tenant=self.tenant, full_name="Anchor", phone="254700100001",
                connection_type="hotspot", router=self.router,
                status="active", hotspot_username="AA:BB:CC:F0:00:01")

    def _row(self, mac, *, disabled, comment=PROVISIONED, rid="*1"):
        return {".id": rid, "name": mac, "profile": "HOTSPOT_PKG_21_D1",
                "disabled": "true" if disabled else "false",
                "comment": comment, "bytes-in": "0", "bytes-out": "0"}

    def _run(self, rows, *, fix=True, extra=None):
        users = _Users(rows)
        from unittest import mock
        out = StringIO()
        with mock.patch(
                "billing.management.commands.disable_orphan_hotspot_users"
                ".safe_connect_router", return_value=_Api(users)):
            call_command("disable_orphan_hotspot_users", fix=fix,
                         stdout=out, stderr=out, **(extra or {}))
        return users, out.getvalue()

    def _stamp(self, days_ago):
        d = timezone.localdate() - dt.timedelta(days=days_ago)
        return f"{PROVISIONED} | orphaned {d.isoformat()}"

    # ------------------------------------------------------------ stamping

    def test_disabling_stamps_the_date(self):
        users, _ = self._run([self._row("AA:BB:CC:F0:99:01", disabled=False)])

        self.assertTrue(users.updates)
        comment = users.updates[0]["comment"]
        self.assertIn("orphaned", comment)
        self.assertIn(timezone.localdate().isoformat(), comment)

    def test_a_stamped_account_is_still_recognised_as_ours(self):
        """
        The comment used to be matched exactly. Stamping it would then make the
        sweep read its own account as somebody else's and leave it alone for
        ever -- the stamp would have disabled the very cleanup it enables.
        """
        users, output = self._run(
            [self._row("AA:BB:CC:F0:99:02", disabled=True,
                       comment=self._stamp(3))])

        self.assertNotIn("not created by this system", output)
        self.assertIn("removed in", output)

    # ------------------------------------------------------------- purging

    def test_an_orphan_past_the_window_is_removed(self):
        users, output = self._run(
            [self._row("AA:BB:CC:F0:99:03", disabled=True,
                       comment=self._stamp(31))])

        self.assertEqual(len(users.removed), 1)
        self.assertIn("removed (orphaned 31d ago)", output)

    def test_an_orphan_inside_the_window_is_kept(self):
        users, output = self._run(
            [self._row("AA:BB:CC:F0:99:04", disabled=True,
                       comment=self._stamp(10))])

        self.assertEqual(users.removed, [])
        self.assertIn("removed in 20d", output)

    def test_the_window_is_configurable(self):
        users, _ = self._run(
            [self._row("AA:BB:CC:F0:99:05", disabled=True,
                       comment=self._stamp(10))],
            extra={"purge_after_days": 7})

        self.assertEqual(len(users.removed), 1)

    def test_reporting_removes_nothing(self):
        users, output = self._run(
            [self._row("AA:BB:CC:F0:99:06", disabled=True,
                       comment=self._stamp(60))],
            fix=False)

        self.assertEqual(users.removed, [])
        self.assertIn("WOULD REMOVE", output)

    # --------------------------------------------------- the unstamped ones

    def test_an_unstamped_disabled_orphan_starts_its_clock(self):
        """
        The 24 that existed before stamping. Deleting them on sight would skip
        the window entirely -- and the window is the only thing that makes
        deletion safe.
        """
        users, output = self._run(
            [self._row("AA:BB:CC:F0:99:07", disabled=True)])

        self.assertEqual(users.removed, [])
        self.assertIn("stamped today", output)
        self.assertIn("orphaned", users.updates[0]["comment"])

    def test_an_unstamped_orphan_is_removed_a_window_later(self):
        """Stamped on one run, removed on a much later one."""
        rows = [self._row("AA:BB:CC:F0:99:08", disabled=True)]
        users, _ = self._run(rows)
        # The clock started today, so a run 31 days from now takes it.
        users2, output = self._run(
            [self._row("AA:BB:CC:F0:99:08", disabled=True,
                       comment=users.updates[0]["comment"])],
            extra={"purge_after_days": 0})

        self.assertEqual(len(users2.removed), 1)

    # ------------------------------------------------------------- safety

    def test_an_account_that_is_not_ours_is_never_removed(self):
        """
        The router's own admin account, or anything an operator made by hand.
        No stamp, no provisioning comment, no business being touched.
        """
        users, output = self._run(
            [self._row("AA:BB:CC:F0:99:09", disabled=True,
                       comment="set up by hand")])

        self.assertEqual(users.removed, [])
        self.assertEqual(users.updates, [])
        self.assertIn("not created by this system", output)

    def test_a_live_orphan_is_disabled_not_removed(self):
        """
        Removal only ever follows a disable that has aged. An account still
        enabled has not served its window yet, whatever its comment says.
        """
        users, _ = self._run(
            [self._row("AA:BB:CC:F0:99:10", disabled=False,
                       comment=self._stamp(99))])

        self.assertEqual(users.removed, [])
        self.assertEqual(users.updates[0]["disabled"], "yes")

    def test_a_corrupt_stamp_is_treated_as_unstamped(self):
        """
        A date nobody can parse must not read as "infinitely old" and take the
        account with it.
        """
        users, output = self._run(
            [self._row("AA:BB:CC:F0:99:11", disabled=True,
                       comment=f"{PROVISIONED} | orphaned 2026-13-45")])

        self.assertEqual(users.removed, [])
        self.assertIn("stamped today", output)

    def test_removals_respect_the_ceiling(self):
        """
        The same guard that stops a broken database query disabling the estate
        stops it deleting the estate.
        """
        rows = [self._row(f"AA:BB:CC:F0:A0:{i:02X}", disabled=True,
                          comment=self._stamp(40), rid=f"*{i}")
                for i in range(30)]
        users, output = self._run(rows, extra={"max_disable": 5})

        self.assertEqual(len(users.removed), 5)
        self.assertIn("budget", output)
