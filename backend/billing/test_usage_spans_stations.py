"""
One bundle, however many stations it is spent at.

A hotspot subscriber has an account at every station they might walk into, and
each keeps counters that start at zero and only climb. The collector asked
tenant_sessions for the live sessions, which keeps the FIRST router a username
turns up on and drops the rest -- so 1 GB used at one station and 2 GB at
another was recorded as whichever was read first.

The second half of the fault was quieter and worse. One baseline was held per
subscriber, so the next poll at the other station saw a counter that had gone
backwards, read it as a router reboot, re-baselined, and threw the interval
away. A subscriber moving between stations could therefore be recorded as
using almost nothing at all, indefinitely.

The baseline now belongs to an account on a station, and the first sight of one
records nothing: there is no delta without a baseline, and treating the first
reading as one would charge a subscriber their whole session in a single
interval.
"""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from billing.models import (
    Customer, CustomerDevice, HotspotUsageRecord, HotspotUsageState, Package,
    RouterDevice, Station, Subscription, Tenant,
)
from billing.router_service import tenant_sessions_everywhere
from billing.services.usage import MB, usage_since
from billing.tasks.usage_tasks import collect_hotspot_usage_for_tenant
from billing.tenancy import tenant_context

GB = 1024 ** 3


class UsageSpansStations(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.a = RouterDevice.objects.create(
                tenant=self.tenant, name="station-a", ip_address="10.9.0.80",
                username="u", password="p", is_active=True)
            self.b = RouterDevice.objects.create(
                tenant=self.tenant, name="station-b", ip_address="10.9.0.81",
                username="u", password="p", is_active=True)
            self.package = Package.objects.create(
                tenant=self.tenant, name="3 weeks - 40GB", download_speed=10,
                upload_speed=5, price=Decimal("250.00"), duration_value=21,
                duration_unit="days", data_cap_mb=40000, is_hotspot=True)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Roamer", phone="254700170001",
                connection_type="hotspot", router=self.a, status="active",
                hotspot_username="AA:BB:CC:DD:EE:01")
            self.sub = Subscription.objects.create(
                customer=self.customer, package=self.package, status="active")
            from billing.models import Invoice
            Invoice.objects.filter(subscription=self.sub).update(
                payment_status="paid")

    MAC = "AA:BB:CC:DD:EE:01"

    def _collect(self, at_a=None, at_b=None, mac=None):
        """
        Run one collection with the given counters at each station.

        Counters are the router's point of view: rx is what the router
        received from the phone, which is the phone's upload. Keyed by MAC,
        because that is what the router names a session after.
        """
        mac = mac or self.MAC

        def reader(router):
            data = at_a if router.pk == self.a.pk else at_b
            if data is None:
                return {}
            rx, tx = data
            return {mac: {
                "connected": True, "rx_bytes": rx, "tx_bytes": tx}}

        with patch("billing.tasks.usage_tasks._tenant_routers",
                   return_value=[self.a, self.b]), \
             patch("billing.tasks.usage_tasks.get_hotspot_sessions_by_mac",
                   reader):
            return collect_hotspot_usage_for_tenant(self.tenant.id)

    def _recorded(self):
        with tenant_context(self.tenant):
            return sum(
                (r.download_bytes or 0) + (r.upload_bytes or 0)
                for r in HotspotUsageRecord.objects.all_tenants().filter(
                    customer=self.customer))

    # -------------------------------------------------------- the sum

    def test_traffic_at_both_stations_is_added_up(self):
        """The question asked of it: 1 GB at one and 2 GB at another is 3 GB."""
        self._collect(at_a=(0, 0), at_b=(0, 0))            # baselines
        self._collect(at_a=(0, 1 * GB), at_b=(0, 2 * GB))

        self.assertEqual(self._recorded(), 3 * GB)

    def test_one_station_alone_still_works(self):
        self._collect(at_a=(0, 0))
        self._collect(at_a=(0, 5 * GB))

        self.assertEqual(self._recorded(), 5 * GB)

    def test_the_second_station_is_not_read_as_a_counter_reset(self):
        """
        The quiet half of the fault. With one baseline per subscriber, the
        smaller counter at the other station looked like a reboot, so the
        interval was discarded -- every poll, forever.
        """
        self._collect(at_a=(0, 0), at_b=(0, 0))
        self._collect(at_a=(0, 9 * GB), at_b=(0, 1 * MB))

        self.assertEqual(self._recorded(), 9 * GB + 1 * MB)

    def test_moving_between_stations_does_not_lose_what_was_used(self):
        """
        Away for a while, then back. The station they left keeps its own
        baseline, so returning to it is an ordinary delta and not a reset.
        """
        self._collect(at_a=(0, 0))
        self._collect(at_a=(0, 2 * GB))                 # 2 GB at A
        self._collect(at_b=(0, 0))                      # arrives at B
        self._collect(at_b=(0, 1 * GB))                 # 1 GB at B
        self._collect(at_a=(0, 3 * GB))                 # back at A, 1 GB more

        self.assertEqual(self._recorded(), 4 * GB)

    def test_the_cap_sees_the_combined_total(self):
        """
        The reason any of this matters: the allowance is measured against what
        usage_since returns, and it must not depend on which station was read.
        """
        self._collect(at_a=(0, 0), at_b=(0, 0))
        self._collect(at_a=(0, 6 * GB), at_b=(0, 4 * GB))

        with tenant_context(self.tenant):
            used = usage_since(self.customer, self.sub.start_date)

        self.assertEqual(used, 10 * GB)

    # --------------------------------------------------- the baseline

    def test_the_first_sight_of_an_account_records_nothing(self):
        """
        There is no delta without a baseline. Counting the first reading as one
        would charge a subscriber their whole session in a single interval --
        which is exactly what every row would have done the first time the
        state was kept per station.
        """
        self._collect(at_a=(0, 30 * GB))

        self.assertEqual(self._recorded(), 0)

    def test_a_baseline_is_kept_for_each_station(self):
        self._collect(at_a=(0, 0), at_b=(0, 0))

        with tenant_context(self.tenant):
            states = HotspotUsageState.objects.all_tenants().filter(
                customer=self.customer)
            self.assertEqual(states.count(), 2)
            self.assertEqual(
                {s.router_id for s in states}, {self.a.pk, self.b.pk})

    def test_a_counter_reset_at_one_station_does_not_disturb_the_other(self):
        """A reboot re-baselines that station only."""
        self._collect(at_a=(0, 0), at_b=(0, 0))
        self._collect(at_a=(0, 4 * GB), at_b=(0, 4 * GB))
        self._collect(at_a=(0, 0), at_b=(0, 6 * GB))      # A rebooted

        self.assertEqual(self._recorded(), 8 * GB + 2 * GB)

    def test_the_record_says_which_station_it_came_from(self):
        self._collect(at_a=(0, 0), at_b=(0, 0))
        self._collect(at_a=(0, 1 * GB), at_b=(0, 2 * GB))

        with tenant_context(self.tenant):
            by_router = {
                r.router_id: (r.download_bytes or 0) + (r.upload_bytes or 0)
                for r in HotspotUsageRecord.objects.all_tenants().filter(
                    customer=self.customer)}

        self.assertEqual(by_router.get(self.a.pk), 1 * GB)
        self.assertEqual(by_router.get(self.b.pk), 2 * GB)

    # ------------------------------------------- the device that was invisible

    def test_a_second_handset_is_counted(self):
        """
        The fault this was built for. Sessions were matched against
        customer.hotspot_username -- one MAC, fixed at the first device a
        subscriber ever used -- while mac-auth-mode names every session after
        the handset that opened it. Anybody online on a different phone
        matched nothing and had their usage recorded as zero.

        85 of 347 entitled devices were in that state on 2026-09-13. Their
        data cap never fired from our side, while the router's own
        limit-bytes-total cut them off with nothing here able to say why.
        """
        other = "AA:BB:CC:DD:EE:99"
        with tenant_context(self.tenant):
            CustomerDevice.objects.create(
                tenant=self.tenant, customer=self.customer,
                subscription=self.sub, mac_address=other)

        self._collect(at_a=(0, 0), mac=other)
        self._collect(at_a=(0, 7 * GB), mac=other)

        self.assertEqual(self._recorded(), 7 * GB)

    def test_two_handsets_on_one_station_both_count(self):
        """
        One account per device, so one baseline per device. Sharing a baseline
        between two phones on the same router would make each one look like the
        other's counter going backwards.
        """
        second = "AA:BB:CC:DD:EE:98"
        with tenant_context(self.tenant):
            CustomerDevice.objects.create(
                tenant=self.tenant, customer=self.customer,
                subscription=self.sub, mac_address=second)

        self._collect(at_a=(0, 0))
        self._collect(at_a=(0, 0), mac=second)
        self._collect(at_a=(0, 2 * GB))
        self._collect(at_a=(0, 3 * GB), mac=second)

        self.assertEqual(self._recorded(), 5 * GB)

    def test_a_device_belonging_to_nobody_is_ignored(self):
        """
        A MAC the registry does not know is not attributed to anyone. Guessing
        would put a stranger's traffic against a paying subscriber's bundle.
        """
        self._collect(at_a=(0, 0), mac="FF:FF:FF:00:00:01")
        self._collect(at_a=(0, 9 * GB), mac="FF:FF:FF:00:00:01")

        self.assertEqual(self._recorded(), 0)

    def test_a_disconnected_station_is_skipped(self):
        self._collect(at_a=(0, 0))

        def reader(router):
            if router.pk == self.a.pk:
                return {self.MAC: {
                    "connected": True, "rx_bytes": 0, "tx_bytes": 1 * GB}}
            return {self.MAC: {"connected": False}}

        with patch("billing.tasks.usage_tasks._tenant_routers",
                   return_value=[self.a, self.b]), \
             patch("billing.tasks.usage_tasks.get_hotspot_sessions_by_mac",
                   reader):
            collect_hotspot_usage_for_tenant(self.tenant.id)

        self.assertEqual(self._recorded(), 1 * GB)


class SessionsFromEveryStation(TestCase):
    """
    tenant_sessions_everywhere keeps them all; tenant_sessions keeps the first.

    The older one is right for PPPoE, where a session is exclusive, and it is
    still used there.
    """

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.a = RouterDevice.objects.create(
                tenant=self.tenant, name="sess-a", ip_address="10.9.0.82",
                username="u", password="p")
            self.b = RouterDevice.objects.create(
                tenant=self.tenant, name="sess-b", ip_address="10.9.0.83",
                username="u", password="p")

    def test_a_username_on_two_routers_yields_both(self):
        with patch("billing.router_service._tenant_routers",
                   return_value=[self.a, self.b]):
            found = tenant_sessions_everywhere(
                self.tenant.id, lambda r: {"john": {"connected": True}})

        self.assertEqual(len(found["john"]), 2)
        self.assertEqual({r.pk for r, _ in found["john"]},
                         {self.a.pk, self.b.pk})

    def test_an_unreadable_router_is_skipped_not_treated_as_empty(self):
        """
        Same rule as tenant_sessions. A subscriber on a router that could not
        be read is left alone rather than recorded as disconnected.
        """
        def reader(router):
            return None if router.pk == self.a.pk else {
                "john": {"connected": True}}

        with patch("billing.router_service._tenant_routers",
                   return_value=[self.a, self.b]):
            found = tenant_sessions_everywhere(self.tenant.id, reader)

        self.assertEqual(len(found["john"]), 1)

    def test_nothing_anywhere_is_an_empty_map(self):
        with patch("billing.router_service._tenant_routers",
                   return_value=[self.a, self.b]):
            self.assertEqual(
                tenant_sessions_everywhere(self.tenant.id, lambda r: {}), {})
