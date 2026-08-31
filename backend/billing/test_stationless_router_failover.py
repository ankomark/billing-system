"""
A router with no station is a site, not a wildcard.

The complaint, in the operator's words: clients' televisions stopped joining.
The portal opens, the code is entered, it is accepted, and the set spins
without ever connecting.

Skylink run two peer sites in Homabay. They are separate buildings with no
overlapping coverage — a subscriber standing at one cannot reach the other's
radio at all. Only `skylink3` had ever been given a Station; `skylink` had
none, because stations were added after it was registered.

`_tenant_routers` read a station of None as "do not narrow". So the two
routers were scoped asymmetrically:

* a subscriber on `skylink3` was pinned to station 1 and could only ever be
  selected onto `skylink3`;
* a subscriber on `skylink` belonged to no site, so *every* router in the
  operator was a candidate for them — including `skylink3`.

That asymmetry makes the drift one-way. On 2026-08-31 `skylink`'s management
tunnel flapped four times; auto-failover confirmed it unreachable and migrated
111 subscribers onto `skylink3`, rebuilding their hotspot accounts on hardware
in another building. Nothing could ever select them back, because once their
router was `skylink3` they were pinned to station 1.

The people this stranded were standing in front of `skylink`. Their codes were
valid, so `/hotspot/validate/` returned 200 and the portal showed its tick —
then RouterOS refused the login, because the account had been built elsewhere.
The portal has no visibility into that POST, so nothing was ever shown to them
and nothing was recorded in ConnectionAttempt. Fifty-eight were still stranded
when it was found; the forty who recovered did so only by typing their code
again, which re-homes them through the portal's own router token. That is why
it read as a television fault: a phone owner retypes a code readily, and
somebody holding a television remote does not.

None now narrows to the routers that share its lack of a site, which is what
the _UNSET sentinel above it always claimed it meant.
"""

from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from billing.models import Customer, RouterDevice, Station, Tenant
from billing.router_service import (
    _station_of,
    _tenant_routers,
    pick_best_router_for_new_customer,
    pick_failover_router,
    pick_working_router,
)
from billing.tenancy import tenant_context


class StationlessRouterIsItsOwnSite(TestCase):
    """
    The Skylink shape exactly: one router with a station, one without, in the
    same operator, serving physically separate places.
    """

    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(name="Skylink", slug="skylink-test")

        with tenant_context(self.tenant):
            self.homabay = Station.objects.create(
                tenant=self.tenant, name="Homabay East")

            # Registered before stations existed, so it never got one.
            self.skylink = RouterDevice.objects.create(
                tenant=self.tenant, name="skylink", ip_address="10.10.0.2",
                username="a", password="p", priority=1, station=None)
            self.skylink3 = RouterDevice.objects.create(
                tenant=self.tenant, name="skylink3", ip_address="10.10.0.5",
                username="a", password="p", priority=1, station=self.homabay)

            self.on_skylink = Customer.objects.create(
                tenant=self.tenant, full_name="At Skylink",
                phone="254700800001", connection_type="hotspot",
                router=self.skylink)

    def test_a_station_less_router_keeps_only_its_own_company(self):
        routers = list(_tenant_routers(self.tenant.id, None))
        self.assertEqual({r.name for r in routers}, {"skylink"})

    def test_a_subscriber_on_a_station_less_router_has_that_site(self):
        self.assertIsNone(_station_of(self.on_skylink))

    @patch("billing.router_service.safe_connect_router")
    def test_failover_refuses_rather_than_cross_to_the_other_building(
            self, connect):
        """
        The bug, stated directly. `skylink` is down and `skylink3` is up, and
        the only correct answer is None. Returning `skylink3` looks like a
        successful failover and leaves the subscriber with an account on a box
        whose radio they cannot hear.
        """
        connect.side_effect = (
            lambda r: object() if r.name == "skylink3" else None)

        router, api = pick_failover_router(
            exclude_router_id=self.skylink.id, customer=self.on_skylink)

        self.assertIsNone(
            router, "failover crossed from a station-less router into a station")

    @patch("billing.router_service.safe_connect_router")
    def test_a_tunnel_flap_does_not_re_home_them_to_the_other_site(
            self, connect):
        """
        What actually happened, at the level that moved people: `skylink` does
        not answer for a moment, and the picker must not offer `skylink3` as
        the next best thing.
        """
        connect.side_effect = (
            lambda r: object() if r.name == "skylink3" else None)

        router, api = pick_working_router(customer=self.on_skylink)

        self.assertIsNone(router, "a flap re-homed a subscriber to another site")

    @patch("billing.router_service.safe_connect_router")
    def test_their_own_router_is_still_chosen_when_it_answers(self, connect):
        """The narrowing must not cost them the router they are actually on."""
        connect.side_effect = (
            lambda r: object() if r.name == "skylink" else None)

        router, api = pick_working_router(customer=self.on_skylink)

        self.assertIsNotNone(router)
        self.assertEqual(router.name, "skylink")

    @patch("billing.router_service.safe_connect_router")
    def test_a_subscriber_with_a_station_is_pinned_as_before(self, connect):
        """The half that was already correct stays correct."""
        with tenant_context(self.tenant):
            on_skylink3 = Customer.objects.create(
                tenant=self.tenant, full_name="At Skylink3",
                phone="254700800002", connection_type="hotspot",
                router=self.skylink3)

        connect.side_effect = (
            lambda r: object() if r.name == "skylink" else None)

        router, api = pick_working_router(customer=on_skylink3)

        self.assertIsNone(router)


class SingleSiteOperatorsAreUnaffected(TestCase):
    """
    Most operators never make a station, so all their routers are station-less
    and must go on being interchangeable. Narrowing None to "the routers with
    no site" is exactly what keeps that true — it groups them together rather
    than isolating each one.
    """

    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(name="Plain", slug="plain-test")

        with tenant_context(self.tenant):
            self.a = RouterDevice.objects.create(
                tenant=self.tenant, name="box-a", ip_address="10.9.0.1",
                username="a", password="p", priority=1)
            self.b = RouterDevice.objects.create(
                tenant=self.tenant, name="box-b", ip_address="10.9.0.2",
                username="a", password="p", priority=2)

            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Plain Person",
                phone="254700800003", connection_type="hotspot", router=self.a)

    def test_both_routers_remain_candidates(self):
        routers = list(_tenant_routers(self.tenant.id, None))
        self.assertEqual({r.name for r in routers}, {"box-a", "box-b"})

    @patch("billing.router_service.safe_connect_router")
    def test_failover_between_them_still_works(self, connect):
        connect.side_effect = lambda r: object() if r.name == "box-b" else None

        router, api = pick_failover_router(
            exclude_router_id=self.a.id, customer=self.customer)

        self.assertIsNotNone(router, "a single-site operator lost its failover")
        self.assertEqual(router.name, "box-b")


class ASubscriberWithNoRouterIsStillUnknown(TestCase):
    """
    The one case that must go on widening. Somebody with no router tells us
    nothing about where they are, and ruling out every router with a site would
    leave a new subscriber unprovisionable.
    """

    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(name="Mixed", slug="mixed-test")

        with tenant_context(self.tenant):
            self.town = Station.objects.create(
                tenant=self.tenant, name="Town")
            self.sited = RouterDevice.objects.create(
                tenant=self.tenant, name="sited", ip_address="10.8.0.1",
                username="a", password="p", priority=1, station=self.town)
            self.plain = RouterDevice.objects.create(
                tenant=self.tenant, name="plain", ip_address="10.8.0.2",
                username="a", password="p", priority=2)

            self.stray = Customer.objects.create(
                tenant=self.tenant, full_name="Unassigned",
                phone="254700800004", connection_type="hotspot")

    def test_nothing_is_narrowed_for_them(self):
        routers = list(_tenant_routers(self.tenant.id, _station_of(self.stray)))
        self.assertEqual({r.name for r in routers}, {"sited", "plain"})

    @patch("billing.router_service.safe_connect_router")
    def test_a_new_subscriber_can_still_be_steered_to_a_station(self, connect):
        connect.side_effect = lambda r: object()
        with patch("billing.router_service.count_pppoe_sessions",
                   return_value=0):
            router, api = pick_best_router_for_new_customer(
                customer=self.stray, station_id=self.town.id)

        self.assertIsNotNone(router)
        self.assertEqual(router.station_id, self.town.id)

    @patch("billing.router_service.safe_connect_router")
    def test_an_existing_subscriber_on_a_plain_router_is_not_steered_away(
            self, connect):
        """
        The docstring on pick_best_router_for_new_customer promises that an
        existing subscriber's own site beats the argument. Tested against None,
        that promise excluded exactly the people this bug stranded — somebody
        on a station-less router could be moved towns by a caller passing a
        station.
        """
        with tenant_context(self.tenant):
            settled = Customer.objects.create(
                tenant=self.tenant, full_name="Settled",
                phone="254700800005", connection_type="hotspot",
                router=self.plain)

        connect.side_effect = lambda r: object()
        with patch("billing.router_service.count_pppoe_sessions",
                   return_value=0):
            router, api = pick_best_router_for_new_customer(
                customer=settled, station_id=self.town.id)

        self.assertIsNotNone(router)
        self.assertEqual(router.name, "plain")
