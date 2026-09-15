"""
Re-homing a whole site when its router has gone.

pick_best_router_for_new_customer only considers routers at the station the
subscriber is already on, because "re-homing an existing subscriber must not
move them towns". That is right for the automatic path and for a routine move.

It is exactly wrong when a site goes down. skylink lost its link on
2026-09-15 with 157 paid subscribers homed on it; every one of them had a
single candidate — skylink — and every migration reported "No router online"
while skylink3 sat healthy at another station.

So a caller may now name the destination. A named router is a decision
somebody made and can see. The automatic path names none, and is refused for
hotspot subscribers before it gets this far.

The paid guard stays in front of all of it, and that one was learned the
expensive way: re-homing after an OLT cable moved on 2026-09-09 provisioned 14
of 90 subscribers from a purchase nobody had paid for, because a migration is a
fresh grant with a fresh limit-uptime.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from billing.models import (
    Customer, Invoice, Package, RouterDevice, RouterFailoverLog, Station,
    Subscription, Tenant,
)
from billing.router_service import migrate_customer_router
from billing.tenancy import tenant_context


class MovingASiteToAnother(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.here = Station.objects.create(
                tenant=self.tenant, name="Here", code="HRE")
            self.there = Station.objects.create(
                tenant=self.tenant, name="There", code="THR")
            self.dead = RouterDevice.objects.create(
                tenant=self.tenant, name="dead-r", ip_address="10.9.0.160",
                username="u", password="p", is_active=True, station=self.here)
            self.alive = RouterDevice.objects.create(
                tenant=self.tenant, name="alive-r", ip_address="10.9.0.161",
                username="u", password="p", is_active=True, station=self.there)
            self.package = Package.objects.create(
                tenant=self.tenant, name="3hrs - 3GB", download_speed=4,
                upload_speed=2, price=Decimal("10.00"), duration_value=3,
                duration_unit="hours", data_cap_mb=3000, is_hotspot=True)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Stranded", phone="254700210001",
                connection_type="hotspot", router=self.dead, status="active")
            self.sub = Subscription.objects.create(
                customer=self.customer, package=self.package, status="active")
            Invoice.objects.filter(subscription=self.sub).update(
                payment_status="paid")

    def _run(self, **kwargs):
        """Only the living router answers, as on the night this happened."""
        def connect(router):
            return MagicMock() if router.pk == self.alive.pk else None

        with patch("billing.router_service.safe_connect_router", connect), \
             patch("billing.router_service._grant_hotspot") as grant, \
             tenant_context(self.tenant):
            ok, msg = migrate_customer_router(
                self.customer, reason="site_down_manual", **kwargs)
        return ok, msg, grant

    # ─────────────────────────────────────────────────── without a destination

    def test_without_a_destination_it_still_refuses_to_cross_stations(self):
        """
        The guard is not removed. A move that names nowhere is still confined
        to the subscriber's own site, so nothing routine starts moving people
        between towns.
        """
        ok, msg, _ = self._run()

        self.assertFalse(ok)
        self.assertIn("No router online", msg)

    # ────────────────────────────────────────────────────── with a destination

    def test_a_named_router_may_be_at_another_station(self):
        ok, msg, grant = self._run(target_router=self.alive)

        self.assertTrue(ok, msg)
        self.assertIn("alive-r", msg)
        grant.assert_called_once()

    def test_the_home_router_is_updated(self):
        self._run(target_router=self.alive)
        self.customer.refresh_from_db()

        self.assertEqual(self.customer.router_id, self.alive.id)

    def test_the_move_is_recorded(self):
        """
        An operator looking at this next week needs to see that somebody moved
        them and why, not guess from the router field.
        """
        self._run(target_router=self.alive)

        log = RouterFailoverLog.objects.all_tenants().filter(
            customer=self.customer).order_by("-id").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.from_router_id, self.dead.id)
        self.assertEqual(log.to_router_id, self.alive.id)
        self.assertEqual(log.reason, "site_down_manual")

    def test_a_destination_that_is_down_is_refused_plainly(self):
        """
        Not "no router online", which would send somebody looking at the whole
        estate rather than at the one box they named.
        """
        def nothing(router):
            return None

        with patch("billing.router_service.safe_connect_router", nothing), \
             tenant_context(self.tenant):
            ok, msg = migrate_customer_router(
                self.customer, target_router=self.alive,
                reason="site_down_manual")

        self.assertFalse(ok)
        self.assertIn("not reachable", msg)

    # ───────────────────────────────────────────────── the guard in front of it

    def test_an_unpaid_subscription_is_still_refused(self):
        """
        The 2026-09-09 lesson, and naming a destination must not step around
        it: a migration is a fresh grant with a fresh limit-uptime, so
        re-homing an unpaid purchase hands out the whole package.
        """
        with tenant_context(self.tenant):
            Invoice.objects.filter(subscription=self.sub).update(
                payment_status="pending")

        ok, msg, grant = self._run(target_router=self.alive)

        self.assertFalse(ok)
        self.assertIn("No active subscription", msg)
        grant.assert_not_called()

    def test_an_expired_subscription_is_still_refused(self):
        with tenant_context(self.tenant):
            Subscription.objects.filter(pk=self.sub.pk).update(
                status="expired")

        ok, msg, grant = self._run(target_router=self.alive)

        self.assertFalse(ok)
        grant.assert_not_called()

    def test_the_automatic_path_still_will_not_touch_a_hotspot_subscriber(self):
        """
        Unchanged, and it must stay that way: a hotspot subscriber's router is
        whichever one they are associated to, and moving them automatically is
        what emptied a router that was never down.
        """
        with tenant_context(self.tenant):
            ok, msg = migrate_customer_router(
                self.customer, reason="auto_failover",
                target_router=self.alive)

        self.assertFalse(ok)
        self.assertIn("not migrated automatically", msg)
