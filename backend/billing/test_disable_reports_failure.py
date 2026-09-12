"""
A disable that did not happen must not report success.

disable_customer_access returned None when the router could not be reached, and
every caller read that as "done". disable_customer_task then logged "Access
disabled for customer N", called _mark_router_online on the router it had just
failed to reach, and returned True -- so Celery recorded a success and never
retried, because nothing had raised.

The database said expired. The router said welcome. Nothing said they
disagreed.

It only bites while a router is unreachable, which sounds rare and is not:
three simultaneous both-router outages across 2026-09-10 and 11 left 42 expired
subscribers online, several for hours and two for days. Their hotspot accounts
survived with limit-uptime unspent -- it counts connected time, not wall-clock,
so a subscriber offline when their window closed still had time on the router --
and login-by=mac readmitted them silently on the next packet.

The task has carried autoretry_for=(Exception,) with three attempts all along.
It had simply never been given anything to retry on.
"""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from billing.models import Customer, Package, RouterDevice, Tenant
from billing.router_service import disable_customer_access
from billing.tenancy import tenant_context


class WhenTheRouterCannotBeReached(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="dark", ip_address="10.0.9.9",
                username="u", password="p")
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Expired", phone="254700400001",
                connection_type="hotspot", router=self.router,
                status="active", hotspot_username="AA:BB:CC:DD:EE:01")

    def test_it_raises_rather_than_returning_quietly(self):
        """
        The whole bug. Returning None here is indistinguishable from success,
        and the subscriber stays online with nothing recording that they should
        not be.
        """
        with patch("billing.router_service.safe_connect_router",
                   return_value=None):
            with self.assertRaises(RuntimeError) as caught:
                disable_customer_access(self.customer)

        self.assertIn("could not reach", str(caught.exception))

    def test_the_message_names_the_customer_and_says_they_are_still_on(self):
        """
        This ends up in an alert and a log line somebody has to act on. "Failed"
        does not tell them a paying-nothing subscriber is using the network.
        """
        with patch("billing.router_service.safe_connect_router",
                   return_value=None):
            try:
                disable_customer_access(self.customer)
            except RuntimeError as exc:
                message = str(exc)

        self.assertIn(str(self.customer.pk), message)
        self.assertIn("still online", message)

    def test_a_customer_with_no_router_is_still_cleared_everywhere(self):
        """
        This used to assert a quiet no-op, on the reasoning that there was no
        router to remove them from. That reasoning was the bug.

        `customer.router` records where somebody was last provisioned, not
        where accounts for them exist. An account on any of the operator's
        routers grants access on its own -- login-by=mac readmits from it --
        so a null router field means "we do not know where they are", which is
        a reason to look everywhere rather than nowhere.

        On 2026-09-12 there were 187 accounts on routers their customer was
        not homed to, two of them serving people whose package had ended.
        """
        with tenant_context(self.tenant):
            self.customer.router = None
            self.customer.save(update_fields=["router"])

        with patch("billing.router_service.safe_connect_router",
                   return_value=object()),              patch("billing.router_service.disable_hotspot",
                   return_value=True) as disable:
            disable_customer_access(self.customer)

        self.assertTrue(
            disable.called,
            "a customer with no router recorded was left on the hardware")


class WhenTheRouterAnswers(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="live", ip_address="10.0.9.8",
                username="u", password="p")
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Leaving", phone="254700400002",
                connection_type="hotspot", router=self.router,
                status="active", hotspot_username="AA:BB:CC:DD:EE:02")

    def test_a_clean_removal_does_not_raise(self):
        """The guard must not make an ordinary expiry look like a failure."""
        with patch("billing.router_service.safe_connect_router",
                   return_value=object()), \
             patch("billing.router_service.disable_hotspot", return_value=True):
            disable_customer_access(self.customer)

    def test_a_device_that_will_not_come_off_still_raises(self):
        """
        The behaviour that was already right, kept. A partial removal leaves
        somebody connected, which is the same harm by a different route.
        """
        with patch("billing.router_service.safe_connect_router",
                   return_value=object()), \
             patch("billing.router_service.disable_hotspot",
                   side_effect=RuntimeError("router said no")):
            with self.assertRaises(RuntimeError) as caught:
                disable_customer_access(self.customer)

        self.assertIn("could not disable", str(caught.exception))


class TheTaskRetriesWithoutBlamingTheRouter(TestCase):
    """
    Retried, but not counted. Whether a router is up is the health sweep's
    question -- it asks every two minutes behind a guard that needs three
    consecutive failures. Letting every task vote as well is what broke that
    guard on 2026-08-31: twenty-six callers failing at once crossed a
    six-minute threshold in seconds and auto-failover emptied a router that was
    never down. An expiry batch during an outage is that exact shape.
    """

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="voting", ip_address="10.0.9.7",
                username="u", password="p")
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Expired", phone="254700400003",
                connection_type="hotspot", router=self.router,
                status="active", hotspot_username="AA:BB:CC:DD:EE:03")

    def _run(self, error):
        from billing.tasks.router_tasks import disable_customer_task
        with patch("billing.tasks.router_tasks.disable_customer_access",
                   side_effect=error):
            with self.assertRaises(type(error)):
                disable_customer_task.run(self.customer.pk)
        self.router.refresh_from_db()
        return self.router

    def test_unreachable_does_not_mark_the_router_down(self):
        from billing.router_service import RouterUnreachable
        before = self.router.consecutive_failures
        r = self._run(RouterUnreachable("could not reach it"))
        self.assertEqual(r.consecutive_failures, before,
                         "an unreachable disable voted against the router")

    def test_a_refusal_still_marks_the_router_down(self):
        """
        The router answered and refused. That is worth recording, and is the
        behaviour that was already there.
        """
        before = self.router.consecutive_failures
        r = self._run(RuntimeError("router said no"))
        self.assertGreater(r.consecutive_failures, before)

    def test_unreachable_still_propagates_so_celery_retries(self):
        """
        The point of raising at all. autoretry_for=(Exception,) gives it three
        more attempts; swallowing it here would put the bug straight back.
        """
        from billing.router_service import RouterUnreachable
        from billing.tasks.router_tasks import disable_customer_task
        with patch("billing.tasks.router_tasks.disable_customer_access",
                   side_effect=RouterUnreachable("nope")):
            with self.assertRaises(RouterUnreachable):
                disable_customer_task.run(self.customer.pk)
