"""
Does blocking a device actually stop it getting online?

The existing DeviceBlockingTests all patch `_kick_device`, so every one of
them proves the database row changed and none of them proves anything reaches
the hardware. These probe the enforcement itself: the router call, the
re-provisioning paths, and the two places that read a customer's addresses
without honouring the flag.
"""

from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from billing.auth_tokens import TenantTokenObtainPairSerializer
from billing.router_service import disable_hotspot
from billing.models import (
    Customer, CustomerDevice, Package, RouterDevice, Subscription, Tenant,
    User, Voucher,
)
from billing.tenancy import tenant_context

STOLEN = "AA:00:00:00:00:01"
SECOND = "BB:00:00:00:00:02"


class FakePath(list):
    """Enough of librouteros' Path to see what was removed."""

    def __init__(self, rows):
        super().__init__(rows)
        self.removed = []

    def remove(self, *ids):
        self.removed.extend(ids)


class FakeApi:
    def __init__(self, users=None, actives=None, actives_raise=False):
        self.users = FakePath(users or [])
        self.actives = FakePath(actives or [])
        self.actives_raise = actives_raise

    def path(self, *parts):
        if parts[-1] == "active":
            if self.actives_raise:
                raise RuntimeError("router refused the session listing")
            return self.actives
        return self.users


class DisableHotspotReportsTheSessionTests(SimpleTestCase):
    """
    The return value that tells a block whether it actually landed.

    Ending the session is wrapped in its own try/except so that losing it
    cannot stop the account being removed. That is right, but it made the one
    failure that leaves a handset online invisible to every caller.
    """

    def test_it_reports_success_when_the_session_was_ended(self):
        api = FakeApi(
            users=[{".id": "*1", "name": STOLEN}],
            actives=[{".id": "*A", "user": STOLEN}],
        )
        self.assertTrue(disable_hotspot(api, STOLEN))
        self.assertEqual(api.actives.removed, ["*A"])
        self.assertEqual(api.users.removed, ["*1"])

    def test_no_session_at_all_is_also_success(self):
        """This address holds no session here either way."""
        api = FakeApi(users=[{".id": "*1", "name": STOLEN}])
        self.assertTrue(disable_hotspot(api, STOLEN))

    def test_it_reports_failure_when_the_session_could_not_be_ended(self):
        api = FakeApi(users=[{".id": "*1", "name": STOLEN}],
                      actives_raise=True)
        self.assertFalse(
            disable_hotspot(api, STOLEN),
            "a router that kept the session reported success")
        # The account still goes, which is the behaviour this must not change.
        self.assertEqual(api.users.removed, ["*1"])


class BlockEnforcementTests(TestCase):
    """One operator, one customer, one stolen handset."""

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        self.admin = User.objects.create_user(
            username="block_admin", password="pw", role=User.TENANT_ADMIN,
            tenant=self.tenant, is_staff=True)
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r1", ip_address="10.0.0.1",
                username="a", password="p", is_active=True)
            self.package = Package.objects.create(
                tenant=self.tenant, name="one device", download_speed=5,
                upload_speed=2, price=Decimal("50.00"), duration_value=1,
                duration_unit="days", monthly_data_cap_gb=0, is_hotspot=True,
                max_devices=1)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Asha", phone="254700000501",
                connection_type="hotspot", router=self.router,
                hotspot_username=STOLEN)
            self.sub = Subscription.objects.create(
                tenant=self.tenant, customer=self.customer,
                package=self.package, status="active",
                expiry_date=timezone.now() + timedelta(days=1))
            inv = self.sub.invoice
            inv.payment_status = "paid"
            inv.save(update_fields=["payment_status"])
            self.voucher = Voucher.objects.create(
                tenant=self.tenant, code="WIFI-ENF001", subscription=self.sub,
                expires_at=self.sub.expiry_date)
            self.device = CustomerDevice.objects.create(
                tenant=self.tenant, customer=self.customer,
                subscription=self.sub, mac_address=STOLEN)

    # -- helpers ------------------------------------------------------------

    def auth(self):
        client = APIClient()
        token = TenantTokenObtainPairSerializer.get_token(self.admin).access_token
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return client

    def block(self, reason="Handset reported stolen", device=None):
        return self.auth().post(
            f"/api/admin/devices/{(device or self.device).id}/",
            {"action": "block", "reason": reason}, format="json")

    def unblock(self, device=None):
        return self.auth().post(
            f"/api/admin/devices/{(device or self.device).id}/",
            {"action": "unblock"}, format="json")

    def redeem(self, mac):
        return APIClient().post(
            f"/api/hotspot/validate/?t={self.tenant.public_token}",
            {"code": "WIFI-ENF001", "mac_address": mac}, format="json")

    # -- 1. does the block reach the hardware? ------------------------------

    def test_blocking_takes_the_device_off_the_router(self):
        """
        Not mocked at _kick_device like every existing test. The block must
        end the live session, or the stolen handset stays online until the
        uptime limit fires.
        """
        with patch("billing.router_service.connect_router",
                   return_value=MagicMock()) as connect, \
             patch("billing.router_service.disable_hotspot") as disable:
            resp = self.block()

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(connect.called, "no router was contacted at all")
        self.assertEqual(
            [c.args[1] for c in disable.call_args_list], [STOLEN],
            "the blocked address was not taken off the router")
        self.assertEqual(resp.data["routers_reached"], 1)

    def test_an_unreachable_router_is_reported_not_swallowed(self):
        """The operator must be told the session is probably still up."""
        with patch("billing.router_service.connect_router",
                   side_effect=Exception("boom")), \
             patch("billing.tasks.router_tasks.kick_device_task.delay"):
            resp = self.block()
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["routers_reached"], 0)

    def test_a_router_that_kept_the_session_does_not_count_as_reached(self):
        """
        disable_hotspot ends the session and then removes the account. Losing
        the first while succeeding at the second leaves the handset online,
        and that used to be indistinguishable from a clean block.
        """
        with patch("billing.router_service.connect_router",
                   return_value=MagicMock()), \
             patch("billing.router_service.disable_hotspot",
                   return_value=False), \
             patch("billing.tasks.router_tasks.kick_device_task.delay"):
            resp = self.block()

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(
            resp.data["routers_reached"], 0,
            "a router that kept the session was counted as a success")

    # -- the retry that outlives the request --------------------------------

    def test_a_block_that_could_not_reach_a_router_is_retried(self):
        with patch("billing.router_service.connect_router",
                   side_effect=Exception("boom")), \
             patch("billing.tasks.router_tasks.kick_device_task.delay") as retry:
            self.block()

        retry.assert_called_once_with(self.customer.pk, STOLEN)

    def test_a_block_that_landed_queues_nothing(self):
        with patch("billing.router_service.connect_router",
                   return_value=MagicMock()), \
             patch("billing.router_service.disable_hotspot",
                   return_value=True), \
             patch("billing.tasks.router_tasks.kick_device_task.delay") as retry:
            self.block()

        retry.assert_not_called()

    def test_the_retry_raises_until_the_device_is_confirmed_off(self):
        """
        A return would look like success to Celery and end the retries with
        the handset still connected.
        """
        from billing.tasks.router_tasks import kick_device_task

        with patch("billing.router_service.connect_router",
                   side_effect=Exception("still down")):
            with self.assertRaises(RuntimeError):
                kick_device_task.run(self.customer.pk, STOLEN)

    def test_the_retry_stops_once_the_router_answers(self):
        with patch("billing.router_service.connect_router",
                   return_value=MagicMock()), \
             patch("billing.router_service.disable_hotspot",
                   return_value=True) as disable:
            from billing.tasks.router_tasks import kick_device_task
            self.assertTrue(kick_device_task.run(self.customer.pk, STOLEN))

        self.assertEqual([c.args[1] for c in disable.call_args_list], [STOLEN])

    def test_an_operator_with_no_router_is_not_retried_for_an_hour(self):
        """Nothing is holding the device online, so there is nothing to retry."""
        with tenant_context(self.tenant):
            RouterDevice.objects.filter(pk=self.router.pk).update(is_active=False)

        with patch("billing.tasks.router_tasks.kick_device_task.delay") as retry:
            resp = self.block()

        self.assertEqual(resp.data["routers_reached"], 0)
        retry.assert_not_called()

    # -- 2. can anything put it back? ---------------------------------------

    def test_a_blocked_device_is_not_reprovisioned_by_a_later_grant(self):
        """
        Blocking removes the hotspot user. Any later grant for this customer —
        a renewal, the provisioning retry, another device reconnecting — must
        not put the blocked address back on the router.
        """
        with patch("billing.views._kick_device", return_value=1):
            self.assertEqual(self.block().status_code, 200)

        # Reloaded, as every real caller does — enable_customer_task fetches
        # the row itself. A stale in-memory copy would still be carrying the
        # hotspot_username the block endpoint has just cleared, and would
        # prove nothing about the grant path.
        self.customer.refresh_from_db()

        from billing.router_service import enable_customer_access
        with patch("billing.router_service.safe_connect_router",
                   return_value=MagicMock()), \
             patch("billing.router_service.ensure_hotspot_profile",
                   return_value="prof"), \
             patch("billing.router_service.enable_hotspot") as enable, \
             tenant_context(self.tenant):
            enable_customer_access(self.customer)

        granted = [c.args[2] for c in enable.call_args_list]
        self.assertNotIn(
            STOLEN, granted,
            "a blocked device was provisioned back onto the router")

    def test_the_customer_row_cannot_smuggle_a_blocked_device_through(self):
        """
        hotspot_macs_for promises in its own docstring that granting access
        skips a blocked device. It applies that filter to the CustomerDevice
        rows and then appends customer.hotspot_username unconditionally, so
        whenever that field names a blocked address the promise does not hold.

        The block view clears the field as a defence, which hides this at one
        call site. The guarantee belongs in the function that claims it.
        """
        from billing.router_service import hotspot_macs_for
        with tenant_context(self.tenant):
            self.device.blocked = True
            self.device.save(update_fields=["blocked"])
            # Left naming the blocked handset, as it is on every row written
            # before the block endpoint existed.
            self.customer.hotspot_username = STOLEN
            self.customer.save(update_fields=["hotspot_username"])

            macs = hotspot_macs_for(self.customer, include_blocked=False)

        self.assertNotIn(
            STOLEN, [m.upper() for m in macs],
            "the blocked address came back through customer.hotspot_username")

    # -- 3. the switch is the operator's, and nothing else's ----------------

    def test_the_operator_can_always_unblock(self):
        """
        Blocked until the operator unblocks it — so the unblock itself must
        not be refusable. Even with the freed place taken by another handset,
        the switch goes back the way it came.
        """
        with patch("billing.views._kick_device", return_value=1):
            self.assertEqual(self.block().status_code, 200)
            self.assertEqual(self.redeem(SECOND).status_code, 200)
            resp = self.unblock()

        self.assertEqual(resp.status_code, 200, resp.data)
        with tenant_context(self.tenant):
            self.device.refresh_from_db()
        self.assertFalse(self.device.blocked)
        self.assertEqual(self.redeem(STOLEN).status_code, 200)

    # -- 4. the public surface ----------------------------------------------

    def test_a_blocked_device_gets_nothing_from_status(self):
        with patch("billing.views._kick_device", return_value=1):
            self.block()
        resp = APIClient().get("/api/hotspot/status/", {
            "t": self.tenant.public_token, "mac": STOLEN})
        self.assertEqual(resp.data["status"], "not_found")

    def test_a_blocked_device_is_refused_a_reconnect(self):
        with patch("billing.views._kick_device", return_value=1):
            self.block()
        resp = APIClient().post("/api/hotspot/reconnect/", {
            "t": self.tenant.public_token, "mac": STOLEN}, format="json")
        self.assertEqual(resp.status_code, 403, resp.data)
        self.assertEqual(resp.data["reason"], "not_registered")

    # -- 5. the block and the voucher are separate switches ------------------

    def test_blocking_leaves_the_subscription_and_the_voucher_alone(self):
        """
        Blocking answers "may this handset connect", nothing else. The
        customer has paid for a period of access and blocking one of their
        devices is not a refund, a suspension or a retirement of the code.
        """
        with tenant_context(self.tenant):
            before_expiry = self.sub.expiry_date
            before_status = self.sub.status
            before_voucher = self.voucher.is_active

        with patch("billing.views._kick_device", return_value=1):
            self.assertEqual(self.block().status_code, 200)

        with tenant_context(self.tenant):
            self.sub.refresh_from_db()
            self.voucher.refresh_from_db()
        self.assertEqual(self.sub.expiry_date, before_expiry,
                         "blocking moved the expiry date")
        self.assertEqual(self.sub.status, before_status,
                         "blocking changed the subscription status")
        self.assertEqual(self.voucher.is_active, before_voucher,
                         "blocking retired the code")

    def test_the_clock_keeps_running_while_a_device_is_blocked(self):
        """
        The countdown is the customer's paid-for time. It is not paused,
        extended or restarted by a device being blocked — the sweep expires
        the subscription on the date it always would have.
        """
        from billing.tasks.subscription_tasks import enforce_subscription_expiry

        with patch("billing.views._kick_device", return_value=1):
            self.assertEqual(self.block().status_code, 200)

        with tenant_context(self.tenant):
            # Wind the clock past the date the customer bought up to.
            Subscription.objects.filter(pk=self.sub.pk).update(
                expiry_date=timezone.now() - timedelta(minutes=1))
        with patch("billing.router_service.safe_connect_router",
                   return_value=None):
            enforce_subscription_expiry()

        with tenant_context(self.tenant):
            self.sub.refresh_from_db()
        self.assertEqual(
            self.sub.status, "expired",
            "a blocked device stopped the subscription clock")

    def test_retiring_the_code_does_not_touch_the_block(self):
        """
        The other direction: voucher functionality only touches the voucher.
        Retiring a leaked code says nothing about which handsets may connect.
        """
        with patch("billing.views._kick_device", return_value=1):
            self.assertEqual(self.block().status_code, 200)

        resp = self.auth().post(
            f"/api/admin/vouchers/{self.voucher.code}/deactivate/",
            {"reason": "Code leaked"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)

        with tenant_context(self.tenant):
            self.device.refresh_from_db()
        self.assertTrue(
            self.device.blocked,
            "retiring the code cleared the device block")

    def test_a_blocked_device_stays_blocked_under_a_brand_new_voucher(self):
        """
        The block is on the address, not on the code. A stolen handset that
        simply buys again must still be refused — otherwise the block lasts
        exactly as long as the subscription it was made under, and the way
        around it costs whatever the cheapest package costs.
        """
        with patch("billing.views._kick_device", return_value=1):
            self.assertEqual(self.block().status_code, 200)

        with tenant_context(self.tenant):
            fresh_sub = Subscription.objects.create(
                tenant=self.tenant, customer=self.customer,
                package=self.package, status="active",
                expiry_date=timezone.now() + timedelta(days=7))
            inv = fresh_sub.invoice
            inv.payment_status = "paid"
            inv.save(update_fields=["payment_status"])
            Voucher.objects.create(
                tenant=self.tenant, code="WIFI-ENF002",
                subscription=fresh_sub, expires_at=fresh_sub.expiry_date)

        resp = APIClient().post(
            f"/api/hotspot/validate/?t={self.tenant.public_token}",
            {"code": "WIFI-ENF002", "mac_address": STOLEN}, format="json")
        self.assertEqual(
            resp.status_code, 403,
            "a new code got the blocked handset back on: %s" % resp.data)
        self.assertTrue(resp.data.get("blocked"))

    def test_blocking_one_device_leaves_the_others_connected(self):
        """
        Only that address. The customer's other handset keeps its access and
        keeps being provisioned.
        """
        with tenant_context(self.tenant):
            self.package.max_devices = 2
            self.package.save(update_fields=["max_devices"])
        self.assertEqual(self.redeem(SECOND).status_code, 200)

        with patch("billing.views._kick_device", return_value=1):
            self.assertEqual(self.block().status_code, 200)

        self.customer.refresh_from_db()
        from billing.router_service import hotspot_macs_for
        with tenant_context(self.tenant):
            macs = [m.upper() for m in
                    hotspot_macs_for(self.customer, include_blocked=False)]
        self.assertIn(SECOND, macs, "the other device lost its access too")
        self.assertNotIn(STOLEN, macs)

    def test_a_blocked_second_device_is_refused_a_reconnect(self):
        """
        The first device stays on the customer row; the rest are rows only.
        Blocking a *second* phone clears nothing, so this leans entirely on
        the device-row filter in the lookup.
        """
        with tenant_context(self.tenant):
            second = CustomerDevice.objects.create(
                tenant=self.tenant, customer=self.customer,
                subscription=self.sub, mac_address=SECOND)
        with patch("billing.views._kick_device", return_value=1):
            self.assertEqual(self.block(device=second).status_code, 200)

        resp = APIClient().post("/api/hotspot/reconnect/", {
            "t": self.tenant.public_token, "mac": SECOND}, format="json")
        self.assertEqual(resp.status_code, 403, resp.data)
