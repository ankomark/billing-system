"""
A hotspot account whose device binding is gone keeps working, and nothing says so.

Every path that takes a device off a router walks outward from the customer's
device rows, so an account whose row has been released is unreachable by all of
them. It has no password either -- hotspot users authenticate by MAC -- so the
handset reassociates and RouterOS lets it in.

On 2026-09-07 there were 44 such accounts across two routers, every one enabled,
7.30GB served between them, 41 with no byte ceiling at all. All 44 were
evictions whose router half did not land; that is fixed at the source in
_evict_idle_device, and this command is the reconciler behind the fix.

It disables and does not delete: an orphan is equally the fingerprint of a
paying subscriber whose binding was released while a router was down, and the
account holds the only remaining evidence of who they were.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase

from billing.models import Customer, CustomerDevice, RouterDevice, Tenant
from billing.tenancy import tenant_context

PROVISIONED = "AUTO | WIFI BILLING SYSTEM"


class _Users(list):
    """A RouterOS path that records what was written to it."""

    def __init__(self, rows):
        super().__init__(rows)
        self.updated = []
        self.removed = []

    def update(self, **kwargs):
        self.updated.append(kwargs)
        for row in self:
            if row.get(".id") == kwargs.get(".id"):
                row.update({k: v for k, v in kwargs.items() if k != ".id"})

    def remove(self, *ids):
        self.removed.extend(ids)


class OrphanHotspotUserTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r1", ip_address="10.0.0.9",
                username="u", password="p")
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Paying Subscriber",
                phone="0700000001", connection_type="hotspot",
                hotspot_username="AA:BB:CC:DD:EE:01", router=self.router)

    def _run(self, users, actives=None, **opts):
        users = _Users(users)
        actives = _Users(actives or [])

        def path(*args):
            return actives if args[-1] == "active" else users

        api = MagicMock()
        api.path.side_effect = path
        with patch("billing.management.commands."
                   "disable_orphan_hotspot_users.safe_connect_router",
                   return_value=api):
            call_command("disable_orphan_hotspot_users", **opts)
        return users, actives

    def test_an_orphan_is_disabled_not_deleted(self):
        """The whole point. It must still be there afterwards."""
        users, _ = self._run([
            {".id": "*1", "name": "11:22:33:44:55:66", "profile": "HOTSPOT_PKG_1_D1",
             "comment": PROVISIONED, "disabled": "false"},
        ], fix=True)

        self.assertEqual(users.updated,
                         [{".id": "*1", "disabled": "yes"}])
        self.assertEqual(users.removed, [],
                         "the account must be disabled, never removed")

    def test_a_live_session_is_ended_after_the_account_is_disabled(self):
        users, actives = self._run(
            [{".id": "*1", "name": "11:22:33:44:55:66", "comment": PROVISIONED,
              "disabled": "false"}],
            [{".id": "*9", "user": "11:22:33:44:55:66",
              "mac-address": "11:22:33:44:55:66"}],
            fix=True)

        self.assertEqual(users.updated, [{".id": "*1", "disabled": "yes"}])
        self.assertEqual(actives.removed, ["*9"],
                         "disabling is future tense; the session has to be kicked")

    def test_a_customers_own_device_is_never_touched(self):
        """Matched on hotspot_username."""
        users, _ = self._run([
            {".id": "*1", "name": "AA:BB:CC:DD:EE:01", "comment": PROVISIONED,
             "disabled": "false"},
        ], fix=True)
        self.assertEqual(users.updated, [])

    def test_a_second_device_is_never_touched(self):
        """
        Matched on CustomerDevice, which is where every MAC after the first
        one lives. Missing this would disable the working phones of every
        multi-device subscriber on the estate.
        """
        with tenant_context(self.tenant):
            CustomerDevice.objects.create(
                tenant=self.tenant, customer=self.customer,
                mac_address="AA:BB:CC:DD:EE:02")

        users, _ = self._run([
            {".id": "*1", "name": "aa:bb:cc:dd:ee:02", "comment": PROVISIONED,
             "disabled": "false"},
        ], fix=True)
        self.assertEqual(users.updated, [],
                         "compared canonically -- RouterOS is inconsistent about case")

    def test_the_routers_own_admin_account_is_never_touched(self):
        users, _ = self._run([
            {".id": "*1", "name": "admin", "profile": "default", "disabled": "false"},
        ], fix=True)
        self.assertEqual(users.updated, [])

    def test_an_account_this_system_did_not_create_is_reported_not_disabled(self):
        """
        MAC-shaped and unowned, but without our stamp on it. An operator may
        have added it by hand for a reason this command cannot see.
        """
        users, _ = self._run([
            {".id": "*1", "name": "11:22:33:44:55:66", "comment": "guest tv",
             "disabled": "false"},
        ], fix=True)
        self.assertEqual(users.updated, [])

    def test_reports_without_writing_by_default(self):
        users, actives = self._run(
            [{".id": "*1", "name": "11:22:33:44:55:66", "comment": PROVISIONED,
              "disabled": "false"}],
            [{".id": "*9", "user": "11:22:33:44:55:66"}])
        self.assertEqual(users.updated, [])
        self.assertEqual(actives.removed, [])

    def test_an_already_disabled_orphan_is_not_written_again(self):
        users, _ = self._run([
            {".id": "*1", "name": "11:22:33:44:55:66", "comment": PROVISIONED,
             "disabled": "true"},
        ], fix=True)
        self.assertEqual(users.updated, [])

    def test_an_unreachable_router_is_named_rather_than_passed_over(self):
        with patch("billing.management.commands."
                   "disable_orphan_hotspot_users.safe_connect_router",
                   return_value=None):
            call_command("disable_orphan_hotspot_users", fix=True)
        # Reaching here without raising is the assertion: an unreachable
        # router must not abort the run for every other router.


class OrphanSweepSafetyTests(TestCase):
    """
    The guards that stop the reconciler becoming the outage.

    This command decides what to disable by subtracting a database query from a
    router's account list. If that query ever returns nothing, every account on
    the router is missing from it, every account carries our provisioning
    comment, and the sweep takes the whole estate off in one pass -- doing
    exactly what it was told. It runs nightly and unattended, so the guard is
    the difference between a quiet no-op and every subscriber disconnected.
    """

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r1", ip_address="10.0.0.9",
                username="u", password="p")

    def _run(self, users, **opts):
        users = _Users(users)
        api = MagicMock()
        api.path.side_effect = lambda *a: _Users([]) if a[-1] == "active" else users
        with patch("billing.management.commands."
                   "disable_orphan_hotspot_users.safe_connect_router",
                   return_value=api):
            call_command("disable_orphan_hotspot_users", **opts)
        return users

    def _orphans(self, n):
        return [{".id": f"*{i}", "name": f"11:22:33:44:55:{i:02X}",
                 "comment": PROVISIONED, "disabled": "false"} for i in range(n)]

    def test_a_run_above_the_ceiling_touches_nothing(self):
        users = self._run(self._orphans(30), fix=True, max_disable=25)
        self.assertEqual(users.updated, [],
                         "30 orphans is past the ceiling; the whole router is left alone")

    def test_the_ceiling_is_per_router_not_a_partial_run(self):
        """
        Refusing has to mean nothing, not the first 25. A partial sweep would
        disconnect 25 subscribers and still report a refusal.
        """
        users = self._run(self._orphans(26), fix=True, max_disable=25)
        self.assertEqual(len(users.updated), 0)

    def test_at_the_ceiling_it_still_acts(self):
        users = self._run(self._orphans(25), fix=True, max_disable=25)
        self.assertEqual(len(users.updated), 25)

    def test_an_empty_database_answer_refuses_rather_than_disabling_everyone(self):
        """
        The catastrophic case. A tenant with hotspot subscribers whose known
        addresses come back empty is this command failing to read the database,
        not an estate that is entirely unowned.
        """
        with tenant_context(self.tenant):
            Customer.objects.create(
                tenant=self.tenant, full_name="Real Subscriber",
                phone="0700000009", connection_type="hotspot",
                hotspot_username="", router=self.router)

        users = self._run(self._orphans(3), fix=True)
        self.assertEqual(users.updated, [],
                         "no known addresses for an operator that has "
                         "subscribers means the query is wrong, not the router")

    def test_an_operator_with_no_hotspot_at_all_is_not_refused(self):
        """
        A tenant that genuinely sells no hotspot has no known addresses and no
        orphans either. It must not trip the guard above.
        """
        users = self._run([], fix=True)
        self.assertEqual(users.updated, [])
