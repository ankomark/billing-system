"""
A flapping router must not cost a subscriber their own device place.

`_evict_idle_device` refuses to free a place when it cannot find out who is
online -- correctly, because granting on "I could not find out" hands one
customer unlimited devices. `_online_macs` is what tells it, and it returns
None only when *no* router answered.

But it asked `_routers_to_ask`, which is the assigned router alone whenever one
is set. So on a normal subscriber it asked exactly one box, and a single
unreachable box became "I could not find out" -- which the caller turns into
"the device limit stands" and the customer reads as: paid, connected to the
Wi-Fi, no internet.

That matters here because these links flap as a matter of course.
safe_connect_router exists precisely because twenty-six callers failing during
one thirty-second drop was enough to migrate the estate. In the 24 hours before
this was written, 120 device_limit refusals were recorded, 94 of them against
one-device packages whose single place was held by the subscriber's own
previous address -- and the packages worst hit were the two- and three-week
ones, where a phone rotates its randomised MAC many times over the term.

The docstring already promised "every address with a live session on any router
that could be reached". This makes that true.
"""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from billing.models import Customer, Package, RouterDevice, Tenant
from billing.tenancy import tenant_context
from billing.views import _online_macs


class OnlineMacsFallbackTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.assigned = RouterDevice.objects.create(
                tenant=self.tenant, name="assigned", ip_address="10.0.0.21",
                username="u", password="p")
            self.other = RouterDevice.objects.create(
                tenant=self.tenant, name="other", ip_address="10.0.0.22",
                username="u", password="p")
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Hotspot 7788",
                phone="0700077788", connection_type="hotspot",
                router=self.assigned)

    def _run(self, answers):
        """answers: {router name -> set of MACs, or None for unreachable}"""
        calls = []

        def fake(router, max_idle_seconds=None):
            calls.append(router.name)
            return answers.get(router.name)

        with patch("billing.router_service.active_hotspot_macs", side_effect=fake):
            with tenant_context(self.tenant):
                return _online_macs(self.customer), calls

    def test_the_assigned_router_alone_is_asked_when_it_answers(self):
        """
        The normal path must not grow N connect timeouts. A customer waits on
        this synchronously after paying.
        """
        result, calls = self._run({"assigned": {"AA:BB:CC:DD:EE:01"}, "other": set()})
        self.assertEqual(result, {"AA:BB:CC:DD:EE:01"})
        self.assertEqual(calls, ["assigned"],
                         "the second opinion is only worth its latency when the first is silent")

    def test_an_empty_answer_is_still_an_answer(self):
        """
        "Nobody is connected" is a real answer and must not trigger the
        fallback -- it frees the place, which is the whole point.
        """
        result, calls = self._run({"assigned": set(), "other": {"AA:BB:CC:DD:EE:99"}})
        self.assertEqual(result, set())
        self.assertEqual(calls, ["assigned"])

    def test_a_silent_assigned_router_falls_back_to_the_rest(self):
        """
        The bug. One flapping box used to mean "I could not find out", and the
        subscriber lost their own device place for the length of the drop.
        """
        result, calls = self._run({"assigned": None, "other": {"AA:BB:CC:DD:EE:02"}})
        self.assertEqual(result, {"AA:BB:CC:DD:EE:02"})
        self.assertIn("other", calls)

    def test_none_only_when_nothing_at_all_answered(self):
        """
        Still None when the whole estate is silent. Granting on silence would
        hand one customer unlimited devices, which is why the caller refuses.
        """
        result, _ = self._run({"assigned": None, "other": None})
        self.assertIsNone(result)

    def test_addresses_are_canonical_however_the_router_spelled_them(self):
        result, _ = self._run({"assigned": {"aa:bb:cc:dd:ee:03"}})
        self.assertEqual(result, {"AA:BB:CC:DD:EE:03"})

    def test_a_customer_with_no_assigned_router_still_gets_an_answer(self):
        """
        A tenth of hotspot subscribers have no router on their row. They were
        already covered by _routers_to_ask's own fallback; this holds that.
        """
        with tenant_context(self.tenant):
            self.customer.router = None
            self.customer.save(update_fields=["router"])

        result, calls = self._run({"assigned": set(), "other": {"AA:BB:CC:DD:EE:04"}})
        self.assertEqual(result, {"AA:BB:CC:DD:EE:04"})
        self.assertEqual(sorted(calls), ["assigned", "other"])
