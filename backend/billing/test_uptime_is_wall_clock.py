"""
Three hours means three hours from the moment they paid, spent or not.

`limit-uptime` is written at grant time as the wall-clock seconds left on the
subscription, and RouterOS measures it against the account's cumulative
*connected* time. Those are different quantities, and the difference is free
service: buy a three-hour window, be online for one of those hours, and the
router still holds two hours of credit after the window has shut.

That is what kept customer 39 connected at 16:33 on 2026-09-12 against a
subscription that ended at 15:05 -- their account read limit-uptime=2h59m36s
against a session at 2h58m31s, nearly three hours of credit on a package whose
window had closed ninety minutes earlier.

The wall clock is enforced by enforce_subscription_expiry every five minutes.
This keeps the router-side backstop underneath it honest, so an outage that
stops the sweep does not hand out the difference -- on 2026-09-10 and 11 three
simultaneous outages left 42 subscribers connected, several for hours, which is
why the backstop exists at all and is corrected rather than removed.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from billing.models import (
    Customer, CustomerDevice, Invoice, Package, RouterDevice, Subscription,
    Tenant,
)
from billing.services.uptime_alignment import align_uptime_limits
from billing.tenancy import tenant_context

MAC = "AA:BB:CC:50:00:01"


class _FakeUsers(list):
    """An ip/hotspot/user path that records what was written to it."""

    def __init__(self, rows):
        super().__init__(rows)
        self.updates = []

    def update(self, **kwargs):
        self.updates.append(kwargs)


class LimitUptimeFollowsTheWallClock(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.package = Package.objects.create(
                tenant=self.tenant, name="3hrs - 3GB", download_speed=5,
                upload_speed=2, price=Decimal("10.00"), duration_value=3,
                duration_unit="hours", data_cap_mb=3000, is_hotspot=True)
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="wall-r", ip_address="10.9.8.1",
                username="u", password="p", is_active=True, is_online=True)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Window Buyer",
                phone="254700950001", connection_type="hotspot",
                router=self.router, status="active", hotspot_username=MAC)
            CustomerDevice.objects.create(
                tenant=self.tenant, customer=self.customer, mac_address=MAC)

    def _subscribe(self, *, expires_in, paid=True):
        with tenant_context(self.tenant):
            sub = Subscription.objects.create(
                customer=self.customer, package=self.package, status="active")
            Subscription.objects.filter(pk=sub.pk).update(
                expiry_date=timezone.now() + expires_in)
            Invoice.objects.filter(subscription=sub).update(
                payment_status="paid" if paid else "pending")
        return sub

    def _run(self, *, uptime, limit, apply=True, session=None):
        """
        `uptime` is what the USER row banks from ended sessions; `session` is
        what the ACTIVE row is running now. RouterOS counts both against the
        limit, and the user row does not include the second until the session
        ends.
        """
        users = _FakeUsers([{
            ".id": "*1", "name": MAC, "uptime": uptime, "limit-uptime": limit,
        }])
        actives = ([{"mac-address": MAC, "uptime": session}]
                   if session is not None else [])

        def path(*parts):
            return actives if parts[-1] == "active" else users

        api = MagicMock()
        api.path.side_effect = path

        with patch("billing.services.uptime_alignment.safe_connect_router",
                   return_value=api):
            result = align_uptime_limits(apply=apply, routers=[self.router])
        return users, result

    # ------------------------------------------------------------ the fix

    def test_credit_earned_while_disconnected_is_taken_back(self):
        """
        The whole bug. One hour used of a three-hour window that has one hour
        left: the account is holding two hours, and should hold one.
        """
        self._subscribe(expires_in=timedelta(hours=1))

        users, (checked, corrected, overdue) = self._run(
            uptime="1h", limit="3h")

        self.assertEqual(corrected, 1)
        written = users.updates[0]["limit-uptime"]
        # 1h connected + 1h of window left = 2h total allowance. Compared with
        # a tolerance because real time passes between the fixture writing the
        # expiry and the sweep reading it, so an exact match would fail on the
        # second boundary rather than on anything meaningful.
        self.assertAlmostEqual(int(written.rstrip("s")), 7200, delta=5)

    def test_an_account_already_on_the_clock_is_left_alone(self):
        """
        A router carrying 400 accounts must not take 400 writes every five
        minutes to correct values that are already right.
        """
        self._subscribe(expires_in=timedelta(hours=2))

        users, (_, corrected, _) = self._run(uptime="1h", limit="3h")

        self.assertEqual(corrected, 0)
        self.assertEqual(users.updates, [])

    def test_a_subscriber_who_has_been_connected_throughout_is_unchanged(self):
        """
        Somebody online for the whole window has no credit to reclaim -- their
        connected time already tracks the wall clock. The fix must cost them
        nothing.
        """
        self._subscribe(expires_in=timedelta(hours=2))

        _, (_, corrected, _) = self._run(uptime="1h", limit="10800s")

        self.assertEqual(corrected, 0)

    def test_reporting_writes_nothing(self):
        self._subscribe(expires_in=timedelta(hours=1))

        users, (_, corrected, _) = self._run(
            uptime="1h", limit="3h", apply=False)

        self.assertEqual(corrected, 1)
        self.assertEqual(users.updates, [])

    # ------------------------------------------------------- the edges

    def test_a_closed_window_is_left_to_the_disable_sweep(self):
        """
        Shortening the limit here would only slow down what
        disable_customer_access does properly -- remove the account from every
        router. This must not become a second, quieter way of cutting people
        off.
        """
        self._subscribe(expires_in=timedelta(hours=-1))

        users, (checked, corrected, overdue) = self._run(
            uptime="1h", limit="3h")

        self.assertEqual(corrected, 0)
        self.assertEqual(users.updates, [])
        self.assertEqual(len(overdue), 1)
        self.assertEqual(overdue[0][2], self.customer.pk)

    def test_an_unpaid_subscription_is_not_given_a_corrected_limit(self):
        """
        Correcting it would imply it was entitled to one. granting_subscription
        is the single definition, and it says no.
        """
        self._subscribe(expires_in=timedelta(hours=1), paid=False)

        _, (checked, corrected, overdue) = self._run(uptime="1h", limit="3h")

        self.assertEqual(checked, 0)
        self.assertEqual(corrected, 0)
        self.assertEqual(len(overdue), 1)

    def test_the_limit_never_goes_to_zero(self):
        """
        RouterOS reads limit-uptime=0 as no limit at all on some builds -- the
        same trap documented for limit-bytes-total. An account whose window is
        closing must not be handed "unlimited" on the way out.
        """
        self._subscribe(expires_in=timedelta(seconds=1))

        users, _ = self._run(uptime="0s", limit="3h")

        self.assertEqual(users.updates[0]["limit-uptime"], "60s")

    def test_an_account_matching_no_customer_is_untouched(self):
        """
        Residue, and not this sweep's business. Removing or shortening it is
        the account sweep's decision, which deliberately leaves unknowns alone.
        """
        self._subscribe(expires_in=timedelta(hours=1))

        users = _FakeUsers([{
            ".id": "*9", "name": "FF:FF:FF:FF:FF:FF",
            "uptime": "1h", "limit-uptime": "3h",
        }])
        api = MagicMock()
        api.path.side_effect = lambda *p: [] if p[-1] == "active" else users
        with patch("billing.services.uptime_alignment.safe_connect_router",
                   return_value=api):
            checked, corrected, overdue = align_uptime_limits(
                apply=True, routers=[self.router])

        self.assertEqual((checked, corrected, overdue), (0, 0, []))
        self.assertEqual(users.updates, [])

    def test_an_unreachable_router_is_skipped_not_fatal(self):
        self._subscribe(expires_in=timedelta(hours=1))

        with patch("billing.services.uptime_alignment.safe_connect_router",
                   return_value=None):
            checked, corrected, overdue = align_uptime_limits(
                apply=True, routers=[self.router])

        self.assertEqual((checked, corrected), (0, 0))

    def test_a_write_that_fails_is_not_counted_as_corrected(self):
        """
        Reporting a correction that did not happen is how this class of bug
        stays hidden -- see disable_customer_access returning None.
        """
        self._subscribe(expires_in=timedelta(hours=1))

        users = _FakeUsers([{
            ".id": "*1", "name": MAC, "uptime": "1h", "limit-uptime": "3h",
        }])
        users.update = MagicMock(side_effect=RuntimeError("router said no"))
        api = MagicMock()
        api.path.side_effect = lambda *p: [] if p[-1] == "active" else users

        with patch("billing.services.uptime_alignment.safe_connect_router",
                   return_value=api):
            _, corrected, _ = align_uptime_limits(
                apply=True, routers=[self.router])

        self.assertEqual(corrected, 0)

    def test_a_live_session_is_counted_before_the_limit_is_shortened(self):
        """
        The mistake this nearly shipped with.

        `uptime` on the user row is banked from sessions that have ENDED. The
        one running now is not in it, and RouterOS counts it against the limit
        regardless -- confirmed on 2026-09-12, where a limit of 1w5d4h14m50s
        less a banked 13h57m15s and a live 2h31m gave exactly the
        session-time-left the router reported.

        Omit the session term and the limit is short by however long the
        subscriber has been connected, which on a live session is hours. The
        sweep would have cut off paying customers the moment it ran, across
        790 accounts.
        """
        self._subscribe(expires_in=timedelta(hours=1))

        users, _ = self._run(uptime="2h", limit="9h", session="3h")

        written = int(users.updates[0]["limit-uptime"].rstrip("s"))
        # 2h banked + 3h running + 1h of window left = 6h.
        self.assertAlmostEqual(written, 6 * 3600, delta=5)

    def test_a_disconnected_account_counts_no_session(self):
        """The other half: nobody connected, so there is nothing to add."""
        self._subscribe(expires_in=timedelta(hours=1))

        users, _ = self._run(uptime="2h", limit="9h", session=None)

        written = int(users.updates[0]["limit-uptime"].rstrip("s"))
        self.assertAlmostEqual(written, 3 * 3600, delta=5)
