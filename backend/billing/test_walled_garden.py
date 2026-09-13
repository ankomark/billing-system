"""
An unauthorised device must not be able to resolve names for itself.

A captive portal is the only thing a device can reach until it logs in, and the
redirect that puts the signup page in front of somebody depends on the router
answering their DNS. Both routers were carrying walled-garden rules accepting
tcp/443 to 1.1.1.1, 1.0.0.1, 8.8.8.8 and 8.8.4.4 -- DNS-over-HTTPS to Cloudflare
and Google -- so a phone resolved names perfectly well while unauthorised.

That is the reported fault exactly. The operating system never concludes it is
behind a captive portal, so it never offers the sign-in prompt, while every
real connection is dropped because the device is not logged in: "connected, no
internet", no page, nothing to tap. A subscriber whose bundle ran out is in the
same place -- the system suspends them, removes the account and ends the
session, all correctly, and the phone still will not show them where to buy
another.

The rules carried an "AUTO | WIFI BILLING SYSTEM" comment and nothing in this
repository creates them, so they outlived whatever added them.
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from billing.models import RouterDevice, Tenant
from billing.services.walled_garden import (
    PUBLIC_RESOLVERS, close_resolver_bypasses,
    close_resolver_bypasses_everywhere, find_resolver_bypasses,
)
from billing.tenancy import tenant_context

HOSTS = ("ip", "hotspot", "walled-garden")
ADDRS = ("ip", "hotspot", "walled-garden", "ip")


def fake_api(*, hostname_rules=(), address_rules=()):
    """A router whose two walled-garden lists answer, and record removals."""
    api = MagicMock()
    removed = []
    lists = {HOSTS: list(hostname_rules), ADDRS: list(address_rules)}

    def path(*parts):
        node = MagicMock()
        rows = lists.get(parts, [])
        node.__iter__ = lambda s: iter(list(rows))
        node.remove = lambda rid: removed.append((parts, rid))
        return node

    api.path.side_effect = path
    api.removed = removed
    return api


class WhatCountsAsABypass(TestCase):

    def test_doh_to_a_public_resolver_is_found(self):
        api = fake_api(address_rules=[
            {".id": "*2", "dst-address": "1.1.1.1", "dst-port": 443,
             "protocol": "tcp", "comment": "cloudflare doh"}])

        found = find_resolver_bypasses(api)

        self.assertEqual(len(found), 1)

    def test_dot_is_found_too(self):
        """853 is DNS-over-TLS, which is what Android calls Private DNS."""
        api = fake_api(address_rules=[
            {".id": "*2", "dst-address": "8.8.8.8", "dst-port": 853}])

        self.assertEqual(len(find_resolver_bypasses(api)), 1)

    def test_both_walled_garden_lists_are_searched(self):
        """
        RouterOS keeps hostname rules in one list and address rules in another,
        and these were in both. Clearing one and not the other leaves it open.
        """
        api = fake_api(
            hostname_rules=[{".id": "*4", "dst-address": "1.1.1.1",
                             "dst-port": 443}],
            address_rules=[{".id": "*2", "dst-address": "1.1.1.1",
                            "dst-port": 443, "protocol": "tcp"}])

        self.assertEqual(len(find_resolver_bypasses(api)), 2)

    def test_every_well_known_resolver_is_covered(self):
        """Not only the four that happened to be on these two routers."""
        api = fake_api(address_rules=[
            {".id": f"*{i}", "dst-address": addr, "dst-port": 443}
            for i, addr in enumerate(sorted(PUBLIC_RESOLVERS))])

        self.assertEqual(len(find_resolver_bypasses(api)),
                         len(PUBLIC_RESOLVERS))

    # ------------------------------------------------ what it must not touch

    def test_the_billing_api_rule_is_left_alone(self):
        """
        The portal cannot load without it. Removing this would replace one
        blank page with another.
        """
        api = fake_api(address_rules=[
            {".id": "*1", "dst-address": "167.233.247.151",
             "comment": "Billing API direct"}])

        self.assertEqual(find_resolver_bypasses(api), [])

    def test_a_hostname_rule_for_the_portal_is_left_alone(self):
        api = fake_api(hostname_rules=[
            {".id": "*1", "dst-host": "api.smartbillsolution.com"}])

        self.assertEqual(find_resolver_bypasses(api), [])

    def test_a_resolver_address_without_a_port_is_left_alone(self):
        """
        1.1.1.1 is also an ordinary host. Both halves have to match, or an
        operator who allowed it for some other reason loses that rule.
        """
        api = fake_api(address_rules=[
            {".id": "*9", "dst-address": "1.1.1.1"}])

        self.assertEqual(find_resolver_bypasses(api), [])

    def test_port_443_to_somewhere_else_is_left_alone(self):
        """An ordinary allowed site, which is what a walled garden is for."""
        api = fake_api(address_rules=[
            {".id": "*9", "dst-address": "196.201.214.200", "dst-port": 443,
             "comment": "M-Pesa"}])

        self.assertEqual(find_resolver_bypasses(api), [])

    def test_plain_dns_is_not_touched(self):
        """
        53 is the fallback the device needs once the encrypted paths stop
        working, and the hotspot answers it itself. Treating it as a bypass
        would take away the thing that makes the portal appear.
        """
        api = fake_api(address_rules=[
            {".id": "*9", "dst-address": "8.8.8.8", "dst-port": 53}])

        self.assertEqual(find_resolver_bypasses(api), [])


class RemovingThem(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="wg-r", ip_address="10.9.0.95",
                username="u", password="p", is_active=True)

    def _api(self):
        return fake_api(
            hostname_rules=[
                {".id": "*4", "dst-address": "1.1.1.1", "dst-port": 443},
                {".id": "*1", "dst-host": "api.smartbillsolution.com"},
            ],
            address_rules=[
                {".id": "*2", "dst-address": "8.8.8.8", "dst-port": 443,
                 "protocol": "tcp"},
                {".id": "*1", "dst-address": "167.233.247.151"},
            ])

    def test_it_removes_the_bypasses_and_nothing_else(self):
        api = self._api()

        removed = close_resolver_bypasses(self.router, api)

        self.assertEqual(removed, 2)
        self.assertEqual(sorted(rid for _, rid in api.removed), ["*2", "*4"])

    def test_dry_run_removes_nothing(self):
        api = self._api()

        removed = close_resolver_bypasses(self.router, api, apply=False)

        self.assertEqual(removed, 2)
        self.assertEqual(api.removed, [])

    def test_a_clean_router_is_a_no_op(self):
        """Idempotent, because this runs hourly."""
        api = fake_api(address_rules=[
            {".id": "*1", "dst-address": "167.233.247.151"}])

        self.assertEqual(close_resolver_bypasses(self.router, api), 0)
        self.assertEqual(api.removed, [])

    def test_one_rule_refusing_to_go_does_not_stop_the_others(self):
        api = fake_api(address_rules=[
            {".id": "*2", "dst-address": "8.8.8.8", "dst-port": 443},
            {".id": "*3", "dst-address": "1.1.1.1", "dst-port": 443},
        ])

        def remove(rid):
            if rid == "*2":
                raise RuntimeError("busy")
            api.removed.append((ADDRS, rid))

        node = MagicMock()
        node.__iter__ = lambda s: iter([
            {".id": "*2", "dst-address": "8.8.8.8", "dst-port": 443},
            {".id": "*3", "dst-address": "1.1.1.1", "dst-port": 443},
        ])
        node.remove = remove
        empty = MagicMock()
        empty.__iter__ = lambda s: iter([])
        api.path.side_effect = lambda *p: node if p == ADDRS else empty

        removed = close_resolver_bypasses(self.router, api)

        self.assertEqual(removed, 1)
        self.assertEqual([rid for _, rid in api.removed], ["*3"])

    def test_an_unreadable_list_does_not_raise(self):
        """
        A router that will not answer is left as it is. Guessing at the rules
        would mean removing things on no evidence.
        """
        api = MagicMock()

        def path(*parts):
            raise RuntimeError("no such command")

        api.path.side_effect = path

        self.assertEqual(find_resolver_bypasses(api), [])


class AcrossTheEstate(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.a = RouterDevice.objects.create(
                tenant=self.tenant, name="wg-a", ip_address="10.9.0.96",
                username="u", password="p", is_active=True)
            self.b = RouterDevice.objects.create(
                tenant=self.tenant, name="wg-b", ip_address="10.9.0.97",
                username="u", password="p", is_active=True)

    def test_an_unreachable_router_is_skipped_not_failed(self):
        api = fake_api(address_rules=[
            {".id": "*2", "dst-address": "1.1.1.1", "dst-port": 443}])

        def connect(router):
            return None if router.pk == self.a.pk else api

        with patch("billing.router_service.safe_connect_router", connect):
            closed = close_resolver_bypasses_everywhere(
                routers=[self.a, self.b])

        self.assertEqual(closed, 1)


class ItIsSwept(TestCase):
    """
    A restore from an old backup brings the rules straight back, and nothing
    about the symptom points at the walled garden. So it is re-asserted rather
    than fixed once.
    """

    def test_the_hourly_task_closes_them(self):
        from billing.tasks.router_tasks import ensure_lease_script_task

        with patch("billing.services.lease_script."
                   "ensure_lease_script_everywhere", return_value=(0, 2, 0)), \
             patch("billing.services.walled_garden."
                   "close_resolver_bypasses_everywhere") as closer:
            ensure_lease_script_task()

        closer.assert_called_once_with(apply=True)

    def test_a_failure_there_does_not_break_the_lease_script_half(self):
        from billing.tasks.router_tasks import ensure_lease_script_task

        with patch("billing.services.lease_script."
                   "ensure_lease_script_everywhere", return_value=(1, 0, 0)), \
             patch("billing.services.walled_garden."
                   "close_resolver_bypasses_everywhere",
                   side_effect=RuntimeError("boom")):
            self.assertEqual(ensure_lease_script_task(), 1)
