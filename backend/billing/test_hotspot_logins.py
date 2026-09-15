"""
The way onto skylink3 that needed no voucher at all.

Every check in this system asks whether a subscriber has paid: entitlement
reads the invoice, the trim counts the places they bought, align_uptime_limits
holds them to their window. All of it is reached through an account named after
a device address, because that is the only kind provisioning writes.

None of it applied to `ip hotspot user admin`. The MikroTik setup wizard writes
that account, it sits on the `default` user profile -- no limit-uptime, no
limit-bytes-total, no rate-limit -- and skylink3 was serving it enabled with a
password set. Anybody who typed it into the portal was on, unmetered, for ever.

The distinction that makes closing it safe: /ip/hotspot/user is who may pass
through the captive portal, /user is who may configure the router. The operator
loses no access to anything.
"""

from unittest.mock import patch

from django.test import TestCase

from billing.models import RouterDevice, Tenant
from billing.services.hotspot_logins import (
    close_unearned_logins, close_unearned_logins_everywhere,
    find_unearned_logins,
)
from billing.tenancy import tenant_context


class FakeApi:
    """The two paths this reads, and a record of what it wrote."""

    def __init__(self, users, profiles=None):
        self.users = users
        self.profiles = profiles or [{"name": "hsprof1"}]
        self.updates = []

    def path(self, *parts):
        key = "/".join(parts)
        api = self

        class Path:
            def __iter__(self):
                if key == "ip/hotspot/user":
                    return iter(api.users)
                if key == "ip/hotspot/profile":
                    return iter(api.profiles)
                return iter(())

            def update(self, **kwargs):
                api.updates.append(kwargs)

        return Path()


def account(name, **extra):
    row = {".id": f"*{abs(hash(name)) % 900 + 10}", "name": name,
           "disabled": "false"}
    row.update(extra)
    return row


MAC = account("AA:BB:CC:DD:EE:01")
ADMIN = account("admin", password="wizard-set", profile="default")


class WhatCounts(TestCase):

    def test_the_wizards_admin_account_is_found(self):
        """The account actually found on skylink3 on 2026-09-15."""
        found = find_unearned_logins(FakeApi([MAC, ADMIN]))

        self.assertEqual([r["name"] for r in found], ["admin"])

    def test_a_provisioned_account_is_never_touched(self):
        """
        Every paying subscriber is one of these. Sweeping one would take a
        customer off the network for having paid.
        """
        self.assertEqual(find_unearned_logins(FakeApi([MAC])), [])

    def test_every_punctuation_of_an_address_is_recognised(self):
        """
        RouterOS and this system do not agree on separators, and a MAC read as
        a hand-made name would be disabled as a freeloader.
        """
        for written in ("AA:BB:CC:DD:EE:01", "AA-BB-CC-DD-EE-01",
                        "aabbccddee01", "aa:bb:cc:dd:ee:01"):
            with self.subTest(written=written):
                self.assertEqual(
                    find_unearned_logins(FakeApi([account(written)])), [])

    def test_one_already_disabled_is_left_alone(self):
        """Nothing to do, and rewriting it hourly would say there was."""
        shut = account("admin", password="x", disabled="true")

        self.assertEqual(find_unearned_logins(FakeApi([shut])), [])

    def test_a_hand_made_account_with_no_password_still_counts(self):
        """
        An empty password is how MAC authentication works, so a named account
        without one is not harmless -- it is a login anybody can complete.
        """
        found = find_unearned_logins(FakeApi([account("guest")]))

        self.assertEqual([r["name"] for r in found], ["guest"])

    # the stock trial row

    def test_the_stock_trial_row_is_ignored_while_trial_is_off(self):
        """
        default-trial is where RouterOS keeps the trial feature's counters. It
        admits nobody unless a server profile sets trial-uptime, and disabling
        a stock row on every router hourly is noise, not security.
        """
        api = FakeApi([account("default-trial")], profiles=[{"name": "p"}])

        self.assertEqual(find_unearned_logins(api), [])

    def test_a_trial_that_is_actually_switched_on_is_closed(self):
        """Then it is a way onto the network without paying, and it counts."""
        api = FakeApi([account("default-trial")],
                      profiles=[{"name": "p", "trial-uptime": "30m"}])

        self.assertEqual([r["name"] for r in find_unearned_logins(api)],
                         ["default-trial"])

    def test_a_trial_uptime_of_zero_is_off(self):
        for value in ("0s", "0", "", "none"):
            with self.subTest(value=value):
                api = FakeApi([account("default-trial")],
                              profiles=[{"name": "p", "trial-uptime": value}])
                self.assertEqual(find_unearned_logins(api), [])

    # closing it

    def test_closing_disables_rather_than_deletes(self):
        """
        The account is the only record of what it was for, and this is undone
        by setting one flag back. Same reasoning as the orphan sweep.
        """
        api = FakeApi([MAC, ADMIN])

        self.assertEqual(close_unearned_logins("r", api), 1)
        self.assertEqual(api.updates,
                         [{".id": ADMIN[".id"], "disabled": "yes"}])

    def test_nothing_is_written_when_only_reporting(self):
        api = FakeApi([ADMIN])

        self.assertEqual(close_unearned_logins("r", api, apply=False), 1)
        self.assertEqual(api.updates, [])

    def test_a_clean_router_writes_nothing(self):
        api = FakeApi([MAC])

        self.assertEqual(close_unearned_logins("r", api), 0)
        self.assertEqual(api.updates, [])

    def test_one_that_will_not_disable_does_not_stop_the_rest(self):
        """
        A router refusing one row must not leave the others open, and the
        count has to report what actually happened.
        """
        stubborn, ordinary = account("admin"), account("guest")
        api = FakeApi([stubborn, ordinary])
        real = api.path

        def fail_one(*parts):
            p = real(*parts)
            update = p.update

            def guarded(**kwargs):
                if kwargs.get(".id") == stubborn[".id"]:
                    raise RuntimeError("no")
                update(**kwargs)

            p.update = guarded
            return p

        api.path = fail_one
        self.assertEqual(close_unearned_logins("r", api), 1)


class AcrossTheEstate(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.up = RouterDevice.objects.create(
                tenant=self.tenant, name="logins-up", ip_address="10.9.0.170",
                username="u", password="p", is_active=True)
            self.down = RouterDevice.objects.create(
                tenant=self.tenant, name="logins-down", ip_address="10.9.0.171",
                username="u", password="p", is_active=True)

    def test_an_unreachable_router_is_skipped_not_fatal(self):
        """
        skylink was unreachable during this scan. A sweep that raised there
        would leave every other router carrying the account.
        """
        api = FakeApi([ADMIN])

        def connect(router):
            return api if router.pk == self.up.pk else None

        with patch("billing.router_service.safe_connect_router", connect), \
             tenant_context(self.tenant):
            closed = close_unearned_logins_everywhere(
                routers=[self.down, self.up])

        self.assertEqual(closed, 1)


class ItRunsOnASchedule(TestCase):
    """
    Not closed once. It is written by the setup wizard, so it survives a
    reboot and returns with a restore or a router swapped for a spare --
    exactly the reasoning the lease script sweep was built on.
    """

    def _quiet(self):
        return (
            patch("billing.services.lease_script."
                  "ensure_lease_script_everywhere", return_value=(0, 0, 0)),
            patch("billing.services.walled_garden."
                  "close_resolver_bypasses_everywhere", return_value=0),
            patch("billing.services.lease_script."
                  "ensure_pppoe_keepalive_everywhere", return_value=0),
            patch("billing.services.pppoe_orphans."
                  "sweep_orphan_secrets_everywhere", return_value=(0, 0)),
        )

    def test_the_hourly_sweep_calls_it(self):
        from billing.tasks import router_tasks

        a, b, c, d = self._quiet()
        with a, b, c, d, patch(
                "billing.services.hotspot_logins."
                "close_unearned_logins_everywhere", return_value=0) as sweep:
            router_tasks.ensure_lease_script_task()

        sweep.assert_called_once_with(apply=True)

    def test_a_failure_there_does_not_stop_the_task(self):
        """
        It is the last of four sweeps on one task. Raising would take the
        others' reported result with it.
        """
        from billing.tasks import router_tasks

        a, b, c, d = self._quiet()
        with a, b, c, d, patch(
                "billing.services.hotspot_logins."
                "close_unearned_logins_everywhere",
                side_effect=RuntimeError("router said no")):
            router_tasks.ensure_lease_script_task()
