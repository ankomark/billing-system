"""
Deleting a subscriber has to take their credentials off the routers.

A PPPoE secret is a working username and password for unmetered internet.
Deleting a customer removed the database row and nothing else, so the secret
stayed on every router they had ever been provisioned on -- enabled, with
nothing left to match it against.

Three were live on 2026-09-14: `st.ambrose` on both routers and `enock` on one,
all deleted through the customers page weeks earlier, all still dialling-ready.
The hotspot side had been surviving the same gap on its orphan sweep, which
removes accounts with no customer behind them. There has never been a PPPoE
equivalent, which is why these lasted.

Two halves here. perform_destroy clears the subscriber before the row goes,
which is the fix. The sweep is the backstop for everything that does not go
through it -- a row deleted in the admin, a router unreachable at the moment of
deletion, somebody editing the database directly.
"""

import datetime as dt
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase

from billing.models import Customer, Package, RouterDevice, Tenant
from billing.services.pppoe_orphans import (
    PURGE_AFTER_DAYS, STAMP_PREFIX, find_orphan_secrets, known_usernames,
    stamped_on, sweep_orphan_secrets, sweep_orphan_secrets_everywhere,
)
from billing.tenancy import tenant_context

TODAY = dt.date(2026, 9, 14)


def secret_api(secrets, actives=()):
    """A router whose ppp secret and active lists answer, recording writes."""
    api = MagicMock()
    updated, removed, killed = [], [], []
    rows = list(secrets)

    def path(*parts):
        node = MagicMock()
        if parts == ("ppp", "secret"):
            node.__iter__ = lambda s: iter(list(rows))
            node.update = lambda **kw: updated.append(kw)
            node.remove = lambda rid: removed.append(rid)
        elif parts == ("ppp", "active"):
            node.__iter__ = lambda s: iter(list(actives))
            node.remove = lambda rid: killed.append(rid)
        else:
            node.__iter__ = lambda s: iter([])
        return node

    api.path.side_effect = path
    api.updated, api.removed, api.killed = updated, removed, killed
    return api


class WhichSecretsAreOrphans(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="orph-r", ip_address="10.9.0.70",
                username="u", password="p", is_active=True)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Real One",
                phone="254700180001", connection_type="pppoe",
                pppoe_username="marksilas", router=self.router,
                status="active")

    def test_a_secret_with_a_customer_is_not_an_orphan(self):
        api = secret_api([{".id": "*1", "name": "marksilas"}])

        self.assertEqual(find_orphan_secrets(self.router, api), [])

    def test_a_secret_with_nobody_behind_it_is(self):
        api = secret_api([{".id": "*2", "name": "enock"}])

        found = find_orphan_secrets(self.router, api)

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][0]["name"], "enock")

    def test_an_expired_subscribers_secret_is_left_alone(self):
        """
        Owning credentials is not the same as being entitled to use them. A
        suspended or expired subscriber still holds theirs, and removing them
        would mean rebuilding the account when they pay again.
        """
        with tenant_context(self.tenant):
            Customer.objects.filter(pk=self.customer.pk).update(
                status="expired")
        api = secret_api([{".id": "*1", "name": "marksilas"}])

        self.assertEqual(find_orphan_secrets(self.router, api), [])

    def test_the_match_ignores_case(self):
        api = secret_api([{".id": "*1", "name": "MarkSilas"}])

        self.assertEqual(find_orphan_secrets(self.router, api), [])

    def test_another_operators_subscriber_is_not_an_orphan(self):
        """
        A secret is matched by name alone and two operators can both have a
        "john". Scoping the known set to one operator would disconnect
        somebody who is paying the other.
        """
        other = Tenant.objects.exclude(pk=self.tenant.pk).first()
        if other is None:
            self.skipTest("single-operator fixture")
        with tenant_context(other):
            r = RouterDevice.objects.create(
                tenant=other, name="o-r", ip_address="10.9.0.71",
                username="u", password="p")
            Customer.objects.create(
                tenant=other, full_name="Theirs", phone="254700180002",
                connection_type="pppoe", pppoe_username="shared.name",
                router=r, status="active")

        self.assertIn("shared.name", known_usernames())

    def test_a_router_that_will_not_answer_yields_nothing(self):
        api = MagicMock()
        api.path.side_effect = RuntimeError("no such command")

        self.assertEqual(find_orphan_secrets(self.router, api), [])


class DisableThenRemove(TestCase):
    """
    Disabling is reversible and deletion is not, so the gap between them is the
    window in which a mistake can be noticed and undone.
    """

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="sweep-r", ip_address="10.9.0.72",
                username="u", password="p", is_active=True)

    def test_an_unstamped_orphan_is_disabled_and_dated(self):
        api = secret_api([{".id": "*2", "name": "enock"}])

        disabled, purged = sweep_orphan_secrets(
            self.router, api, apply=True, today=TODAY)

        self.assertEqual((disabled, purged), (1, 0))
        self.assertEqual(api.updated[0]["disabled"], "yes")
        self.assertIn(f"{STAMP_PREFIX} 2026-09-14", api.updated[0]["comment"])
        self.assertEqual(api.removed, [])

    def test_its_live_session_goes_with_it(self):
        """
        Disabling a secret refuses the NEXT authentication and leaves an
        established session running -- the trap disable_customer_access
        records for PPPoE, where an expired subscriber stayed online for days.
        """
        api = secret_api(
            [{".id": "*2", "name": "enock"}],
            actives=[{".id": "*A", "name": "enock"}])

        sweep_orphan_secrets(self.router, api, apply=True, today=TODAY)

        self.assertEqual(api.killed, ["*A"])

    def test_an_existing_comment_is_kept(self):
        api = secret_api([
            {".id": "*2", "name": "enock", "comment": "installed by hand"}])

        sweep_orphan_secrets(self.router, api, apply=True, today=TODAY)

        self.assertIn("installed by hand", api.updated[0]["comment"])

    def test_one_still_inside_the_waiting_period_is_left_alone(self):
        recent = (TODAY - dt.timedelta(days=PURGE_AFTER_DAYS - 1)).isoformat()
        api = secret_api([{".id": "*2", "name": "enock",
                           "comment": f"{STAMP_PREFIX} {recent}"}])

        disabled, purged = sweep_orphan_secrets(
            self.router, api, apply=True, today=TODAY)

        self.assertEqual((disabled, purged), (0, 0))
        self.assertEqual(api.removed, [])

    def test_one_past_it_is_removed(self):
        old = (TODAY - dt.timedelta(days=PURGE_AFTER_DAYS)).isoformat()
        api = secret_api([{".id": "*2", "name": "enock",
                           "comment": f"{STAMP_PREFIX} {old}"}])

        disabled, purged = sweep_orphan_secrets(
            self.router, api, apply=True, today=TODAY)

        self.assertEqual((disabled, purged), (0, 1))
        self.assertEqual(api.removed, ["*2"])

    def test_a_reclaimed_secret_is_never_removed(self):
        """
        Somebody noticed and re-created the customer. The stamp is still on the
        comment, and it must not decide anything once a customer holds it.
        """
        old = (TODAY - dt.timedelta(days=PURGE_AFTER_DAYS + 5)).isoformat()
        with tenant_context(self.tenant):
            Customer.objects.create(
                tenant=self.tenant, full_name="Back", phone="254700180003",
                connection_type="pppoe", pppoe_username="enock",
                router=self.router, status="active")
        api = secret_api([{".id": "*2", "name": "enock",
                           "comment": f"{STAMP_PREFIX} {old}"}])

        disabled, purged = sweep_orphan_secrets(
            self.router, api, apply=True, today=TODAY)

        self.assertEqual((disabled, purged), (0, 0))
        self.assertEqual(api.removed, [])

    def test_dry_run_writes_nothing(self):
        api = secret_api([{".id": "*2", "name": "enock"}])

        disabled, _ = sweep_orphan_secrets(
            self.router, api, apply=False, today=TODAY)

        self.assertEqual(disabled, 1)
        self.assertEqual(api.updated, [])
        self.assertEqual(api.removed, [])

    def test_a_malformed_stamp_is_treated_as_unstamped(self):
        api = secret_api([{".id": "*2", "name": "enock",
                           "comment": "| orphaned not-a-date"}])

        self.assertIsNone(stamped_on({"comment": "| orphaned not-a-date"}))
        disabled, purged = sweep_orphan_secrets(
            self.router, api, apply=True, today=TODAY)
        self.assertEqual((disabled, purged), (1, 0))

    def test_an_unreachable_router_is_skipped_not_failed(self):
        api = secret_api([{".id": "*2", "name": "enock"}])

        with patch("billing.router_service.safe_connect_router",
                   side_effect=lambda r: None):
            self.assertEqual(
                sweep_orphan_secrets_everywhere(routers=[self.router]), (0, 0))


class DeletingASubscriberClearsTheRouter(TestCase):
    """
    The fix at source. The sweep above is the backstop; this is what stops the
    orphan being created in the first place.
    """

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="del-r", ip_address="10.9.0.73",
                username="u", password="p", is_active=True)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Leaving",
                phone="254700180004", connection_type="pppoe",
                pppoe_username="leaving", router=self.router, status="active")

    def _destroy(self, **patches):
        from billing.views import CustomerViewSet

        view = CustomerViewSet()
        view.request = MagicMock()
        with patch("billing.views.disable_customer_access", **patches) as d:
            view.perform_destroy(self.customer)
        return d

    def test_the_router_is_cleared_before_the_row_goes(self):
        called = self._destroy()

        called.assert_called_once_with(self.customer)
        self.assertFalse(
            Customer.objects.all_tenants().filter(
                pk=self.customer.pk).exists())

    def test_an_unreachable_router_refuses_the_delete(self):
        """
        Deleting now would leave the account live with nothing left to match
        it against, and the evidence gone. Better to refuse and let the
        operator try again.
        """
        from rest_framework.exceptions import ValidationError

        from billing.router_service import RouterUnreachable

        with self.assertRaises(ValidationError):
            self._destroy(side_effect=RouterUnreachable("del-r"))

        self.assertTrue(
            Customer.objects.all_tenants().filter(
                pk=self.customer.pk).exists())

    def test_a_router_that_refuses_does_not_block_the_delete(self):
        """
        Reached it and it said no. The sweeps are the backstop, and blocking
        an operator on one stubborn account leaves them no way forward.
        """
        self._destroy(side_effect=RuntimeError("cannot remove"))

        self.assertFalse(
            Customer.objects.all_tenants().filter(
                pk=self.customer.pk).exists())


class TheSweepIsScheduled(TestCase):

    def test_the_hourly_task_runs_it(self):
        from billing.tasks.router_tasks import ensure_lease_script_task

        with patch("billing.services.lease_script."
                   "ensure_lease_script_everywhere", return_value=(0, 1, 0)), \
             patch("billing.services.walled_garden."
                   "close_resolver_bypasses_everywhere", return_value=0), \
             patch("billing.services.lease_script."
                   "ensure_pppoe_keepalive_everywhere", return_value=0), \
             patch("billing.services.pppoe_orphans."
                   "sweep_orphan_secrets_everywhere",
                   return_value=(0, 0)) as sweep:
            ensure_lease_script_task()

        sweep.assert_called_once_with(apply=True)
