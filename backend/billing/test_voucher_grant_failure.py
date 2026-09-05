"""
A router that refuses the grant must not read as a voucher that is wrong.

Eleven times in the seven days to 2026-08-30, `POST /api/hotspot/validate/`
answered 500 with:

    librouteros.exceptions.TrapError: failure: already have user with this
    name for this server

The view called `enable_customer_access` inline and unguarded, so the trap left
DRF as a 500. A 500 body is HTML, `login.html`'s `JSON.parse` fails on it,
`detail` comes back undefined, and the portal falls through to its default
text — the customer was told their code did not match.

Every part of that is wrong for this customer. The line is only reached after
`validate_voucher` has accepted the code, and `enable_customer_access` only
provisions against an invoice marked paid, so the person being told to check
their typing had paid and was holding a working code. The device binding was
already committed too, because the transaction closes before the call: the
record said provisioned, the operator saw an active subscriber, and nothing
retried.

Scope: only a raise is handled. `enable_customer_access` also *returns* False,
and that is left as it was, because False does not mean one thing — it is "no
paid subscription", a refusal retrying cannot help, as readily as it is "no
router answered". Answering both with "your payment is in" tells somebody who
has not paid the opposite of the truth. Separating them means changing what
that function returns, and that is a wider change than this fault.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from librouteros.exceptions import MultiTrapError, TrapError
from rest_framework.test import APIClient

from billing.models import (
    Customer, Package, RouterDevice, Subscription, Tenant, Voucher,
)
from billing.router_service import enable_hotspot
from billing.tenancy import tenant_context

MAC = "AA:BB:CC:00:11:33"
ALREADY = "failure: already have user with this name for this server"


class CollidingPath(list):
    """
    librouteros' Path, with an `add` that refuses the way RouterOS does.

    `refusals` is how many adds trap before one is allowed through, which is
    what separates losing a race once from a router that will never accept
    this user.
    """

    def __init__(self, rows, refusals=1, message=ALREADY, wrap_multi=False):
        super().__init__(rows)
        self.removed = []
        self.added = []
        self.refusals = refusals
        self.message = message
        self.wrap_multi = wrap_multi

    def remove(self, *ids):
        # Recorded, not applied. A real Path re-queries the router on each
        # iteration, so removing does not disturb a scan that is running; a
        # list that deleted its own elements mid-loop would, and the fix
        # rescans before it removes.
        self.removed.extend(ids)

    def add(self, **kwargs):
        if self.refusals > 0:
            self.refusals -= 1
            # The loser of the race sees the winner's row appear.
            super().append({".id": "*99", "name": MAC.lower()})
            if self.wrap_multi:
                raise MultiTrapError(TrapError(self.message))
            raise TrapError(self.message)
        self.added.append(kwargs)


class FakeApi:
    def __init__(self, users):
        self.users = users

    def path(self, *parts):
        return self.users


class EnableHotspotCollisionTests(SimpleTestCase):
    """
    The dedupe loop cannot see a user added after it scanned.

    Two grants for one handset overlap — the portal's inline call and the
    provisioning task retrying, or a customer tapping Connect twice. Both scan,
    both find nothing to remove, both add, and one of them traps.
    """

    def _enable(self, api):
        import billing.router_service as rs

        original = rs.ensure_hotspot_profile
        rs.ensure_hotspot_profile = lambda *a, **k: "prof"
        try:
            enable_hotspot(api, MagicMock(), MAC, MagicMock(),
                           timezone.now() + timedelta(hours=1))
        finally:
            rs.ensure_hotspot_profile = original

    def test_losing_the_race_does_not_raise(self):
        """
        Before the fix this propagated out of the view as a 500, and the
        customer holding a paid code was told it did not match.
        """
        api = FakeApi(CollidingPath([], refusals=1))
        self._enable(api)  # must not raise

    def test_the_colliding_user_is_replaced_not_left_behind(self):
        """
        Treating the trap as "someone else already did it" would be enough to
        stop the 500 and would quietly sell yesterday's package: the row on the
        router is the *other* grant's, carrying its profile and its
        limit-uptime. This grant's own attributes have to be the ones standing.
        """
        api = FakeApi(CollidingPath([], refusals=1))
        self._enable(api)

        self.assertEqual(
            api.users.removed, ["*99"],
            "the row that won the race was left in place, so this customer "
            "keeps whatever profile and expiry that grant wrote")
        self.assertEqual(len(api.users.added), 1)
        self.assertEqual(api.users.added[0]["name"], MAC)

    def test_a_router_that_always_refuses_still_raises(self):
        """
        One retry answers a race. A second refusal means something other than a
        race, and swallowing it would report a customer as provisioned onto a
        router that never accepted them.
        """
        api = FakeApi(CollidingPath([], refusals=2))
        with self.assertRaises(TrapError):
            self._enable(api)

    def test_a_multi_sentence_refusal_is_recovered_too(self):
        """
        MultiTrapError descends from ProtocolError, not TrapError — they are
        siblings. Catching only the obvious one lets a multi-sentence refusal
        past, and while the view would still keep that off the customer's
        screen, they would be left on the retry queue instead of online.
        """
        api = FakeApi(CollidingPath([], refusals=1, wrap_multi=True))
        self._enable(api)

        self.assertEqual(api.users.removed, ["*99"])
        self.assertEqual(len(api.users.added), 1)

    def test_an_unrelated_trap_is_not_retried(self):
        """A bad profile name is not a collision and must surface as itself."""
        api = FakeApi(CollidingPath(
            [], refusals=1, message="failure: no such profile"))
        with self.assertRaises(TrapError):
            self._enable(api)


class VoucherGrantFailureTests(TestCase):
    """What the person standing at the portal is told when the grant fails."""

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        now = timezone.now()
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r", ip_address="10.0.0.9",
                username="u", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="2hrs", download_speed=5,
                upload_speed=2, price=Decimal("50.00"), duration_value=2,
                duration_unit="hours", data_cap_mb=0, is_hotspot=True,
                max_devices=1)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Achieng", phone="254700333444",
                connection_type="hotspot", router=self.router)
            self.sub = Subscription.objects.create(
                tenant=self.tenant, customer=self.customer,
                package=self.package, status="active",
                expiry_date=now + timedelta(hours=2))
            inv = self.sub.invoice
            inv.payment_status = "paid"
            inv.save(update_fields=["payment_status"])
            self.voucher = Voucher.objects.create(
                tenant=self.tenant, code="WIFI-GRANT01",
                subscription=self.sub, expires_at=now + timedelta(hours=2))

    def _validate(self):
        return APIClient().post(
            f"/api/hotspot/validate/?t={self.tenant.public_token}",
            {"code": "WIFI-GRANT01", "mac_address": MAC},
            format="json",
        )

    def test_a_router_trap_is_not_a_500(self):
        with patch("billing.views.enable_customer_access",
                   side_effect=TrapError(ALREADY)), \
             patch("billing.tasks.provisioning.ensure_customer_access_task"):
            response = self._validate()

        self.assertNotEqual(
            response.status_code, 500,
            "a router-side refusal became a server error, and the portal "
            "renders that as 'code does not match'")
        self.assertEqual(response.status_code, 503)

    def test_the_customer_is_not_told_their_paid_code_is_wrong(self):
        with patch("billing.views.enable_customer_access",
                   side_effect=TrapError(ALREADY)), \
             patch("billing.tasks.provisioning.ensure_customer_access_task"):
            response = self._validate()

        detail = (response.data.get("detail") or "").lower()
        self.assertTrue(detail, "no detail, so the portal shows its fallback")
        for wrong in ("match", "invalid", "expired"):
            self.assertNotIn(
                wrong, detail,
                f"the message says {wrong!r} about a valid, paid code")
        self.assertIn("valid", detail)

    def test_the_grant_is_queued_for_retry(self):
        """
        The binding is committed by now, so without this the customer is a
        paid-up subscriber with no account on the hardware and nothing looking
        for them. The task backs off and, failing that, tells the operator.
        """
        with patch("billing.views.enable_customer_access",
                   side_effect=TrapError(ALREADY)), \
             patch("billing.tasks.provisioning."
                   "ensure_customer_access_task") as task:
            self._validate()

        task.delay.assert_called_once()
        self.assertEqual(task.delay.call_args.args[0], self.customer.pk)

    def test_a_grant_that_declines_without_raising_is_left_alone(self):
        """
        The deliberate limit of this fix, pinned so it is not widened by
        accident.

        enable_customer_access returns False for "no paid subscription" as
        readily as for "no router answered". Answering the first with "your
        payment is in, tap Connect again" is a lie told to somebody who has not
        paid, and it costs four retries and an operator alert to establish
        that. Telling them apart means changing what that function returns,
        which is a larger change than the 500 this file exists to fix.
        """
        with patch("billing.views.enable_customer_access", return_value=False):
            response = self._validate()

        self.assertEqual(response.status_code, 200)

    def test_a_working_grant_still_answers_access_granted(self):
        """The path everybody else takes, unchanged."""
        with patch("billing.views.enable_customer_access", return_value=True):
            response = self._validate()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["detail"], "Access granted")
        self.assertTrue(response.data.get("device_token"))
