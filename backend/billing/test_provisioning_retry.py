"""
A customer who has paid must not wait a minute on a dropped packet.

`ensure_customer_access_task` retries when no router answers, which is right --
a router being briefly unreachable is ordinary. But the first retry was 60
seconds, and that assumed the reason was a router being *down*.

The reason that actually happens here is loss. skylink3 reaches the internet
over 5G measured at 33% packet loss and 600ms round trip, and an API login is
several round trips, so the first attempt fails routinely against a router that
is sitting there working and answers again seconds later. In that window the
customer has paid, their phone has associated to the Wi-Fi, and they hold no
authorised session: connected, no internet.

Two fast attempts recover the common case in about five seconds. The tail is
unchanged, so a genuine outage -- skylink was seen restarting mid-evening with
every session cleared -- is still ridden out to sixteen minutes.
"""

from unittest.mock import patch

from django.test import TestCase

from billing.models import Customer, RouterDevice, Tenant
from billing.tasks.provisioning import (
    MAX_ATTEMPTS, RETRY_SCHEDULE, ensure_customer_access_task,
)
from billing.tenancy import tenant_context


class ProvisioningRetryScheduleTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r1", ip_address="10.0.0.31",
                username="u", password="p")
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Hotspot 4242",
                phone="0700042424", connection_type="hotspot",
                router=self.router)

    def test_the_first_retry_is_seconds_not_a_minute(self):
        """
        The whole point. A packet lost on a 33%-loss link must not cost the
        customer a minute of having paid for nothing.
        """
        self.assertEqual(RETRY_SCHEDULE[0], 5)
        self.assertLessEqual(RETRY_SCHEDULE[1], 30)

    def test_the_tail_still_rides_out_a_reboot(self):
        """
        A restarting router takes minutes to come back. Front-loading must not
        shorten the outer end -- skylink was observed restarting with every
        hotspot session cleared.
        """
        self.assertGreaterEqual(RETRY_SCHEDULE[-1], 960)
        self.assertGreaterEqual(sum(RETRY_SCHEDULE), 20 * 60)

    def test_the_schedule_and_the_attempt_count_cannot_drift(self):
        """
        max_retries is derived from the schedule, so indexing it can never run
        off the end -- which would raise IndexError inside the retry handler,
        on the path that exists to be reliable.
        """
        self.assertEqual(MAX_ATTEMPTS, len(RETRY_SCHEDULE) + 1)
        self.assertEqual(
            ensure_customer_access_task.max_retries, len(RETRY_SCHEDULE))

    def test_every_retry_index_has_a_wait(self):
        for i in range(ensure_customer_access_task.max_retries):
            self.assertIsInstance(RETRY_SCHEDULE[i], int)

    def test_a_reachable_router_is_provisioned_without_retrying(self):
        with patch("billing.router_service.enable_customer_access",
                   return_value=True) as grant:
            result = ensure_customer_access_task(self.customer.id)
        self.assertTrue(result)
        grant.assert_called_once()

    def test_an_unreachable_router_schedules_the_first_fast_retry(self):
        """
        Not merely that it retries -- that the first wait is the short one.
        """
        with patch("billing.router_service.enable_customer_access",
                   return_value=False), \
             patch.object(ensure_customer_access_task, "retry",
                          side_effect=RuntimeError("retried")) as retry:
            with self.assertRaises(RuntimeError):
                ensure_customer_access_task(self.customer.id)
        retry.assert_called_once()
        self.assertEqual(retry.call_args.kwargs.get("countdown"), 5)
