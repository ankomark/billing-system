"""
PPPoE usage was recorded as zero for every subscriber, always.

get_pppoe_sessions asked /ppp/active for `rx-bytes` and `tx-bytes`. That path
has neither -- RouterOS answers with address, caller-id, uptime, session-id and
the limit-bytes-in/out ceilings, and nothing about traffic. The reader
defaulted both to 0, so every five-minute collection wrote a row of zeroes.

Found on 2026-09-08 with a live client: 1.2GB through the session, nine
consecutive usage rows at 0.00MB.

It is not only a reporting gap. A PPPoE cap is poll-enforced -- there is no
limit-bytes-total to fall back on, which the data-cap work says in its own
commit message -- so a poll that always reads zero enforces nothing at all. A
capped PPPoE package was, in practice, unlimited.

The counters live on the session's interface, which RouterOS names after the
session: `marksilas` appears as `<pppoe-marksilas>`.
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from billing.models import RouterDevice, Tenant
from billing.tenancy import tenant_context


def _api(active_rows, interface_rows):
    api = MagicMock()

    def path(*parts):
        if parts == ("ppp", "active"):
            return active_rows
        if parts == ("interface",):
            return interface_rows
        return []

    api.path.side_effect = path
    return api


class PppoeSessionCounterTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r1", ip_address="10.0.0.61",
                username="u", password="p")

    def _sessions(self, active, interfaces):
        from billing.router_service import get_pppoe_sessions
        with patch("billing.router_service.safe_connect_router",
                   return_value=_api(active, interfaces)):
            return get_pppoe_sessions(self.router)

    # The exact shape RouterOS 7 returns: no byte fields anywhere.
    ACTIVE = [{".id": "*8", "name": "marksilas", "address": "192.168.89.254",
               "uptime": "44m55s", "caller-id": "D0:D0:4B:36:7C:C1",
               "service": "pppoe", "session-id": "0x81100000",
               "limit-bytes-in": "0", "limit-bytes-out": "0"}]

    def test_bytes_come_from_the_interface_not_the_session(self):
        """
        The whole bug. /ppp/active carries no counters, so a reader that
        trusts it records nothing for everybody, for ever.
        """
        s = self._sessions(self.ACTIVE, [
            {"name": "<pppoe-marksilas>", "type": "pppoe-in",
             "rx-byte": "56480627", "tx-byte": "1300371837"},
        ])
        self.assertEqual(s["marksilas"]["rx_bytes"], 56480627)
        self.assertEqual(s["marksilas"]["tx_bytes"], 1300371837)

    def test_rx_is_the_subscribers_upload_not_their_download(self):
        """
        The convention _sessions_by_user documents, which the interface
        counters follow: what the router receives is what the subscriber sent.
        Reversed, the totals still add up and only the graph labels are wrong,
        which is how it survived from the first commit until 0064.
        """
        s = self._sessions(self.ACTIVE, [
            {"name": "<pppoe-marksilas>", "rx-byte": "100", "tx-byte": "900"},
        ])
        self.assertEqual(s["marksilas"]["rx_bytes"], 100, "router rx = upload")
        self.assertEqual(s["marksilas"]["tx_bytes"], 900, "router tx = download")

    def test_the_angle_brackets_are_stripped(self):
        """RouterOS decorates the name and has not always done so."""
        s = self._sessions(self.ACTIVE, [
            {"name": "pppoe-marksilas", "rx-byte": "5", "tx-byte": "7"},
        ])
        self.assertEqual(s["marksilas"]["rx_bytes"], 5)

    def test_a_session_with_no_interface_is_still_reported_connected(self):
        """
        Zero bytes is not the same as gone. Dropping the session would have
        the collector treat a live subscriber as disconnected.
        """
        s = self._sessions(self.ACTIVE, [])
        self.assertTrue(s["marksilas"]["connected"])
        self.assertEqual(s["marksilas"]["rx_bytes"], 0)

    def test_other_interfaces_are_ignored(self):
        s = self._sessions(self.ACTIVE, [
            {"name": "ether1", "rx-byte": "999", "tx-byte": "999"},
            {"name": "bridge", "rx-byte": "888", "tx-byte": "888"},
            {"name": "<pppoe-marksilas>", "rx-byte": "1", "tx-byte": "2"},
        ])
        self.assertEqual(s["marksilas"]["rx_bytes"], 1)
        self.assertEqual(s["marksilas"]["tx_bytes"], 2)

    def test_one_subscribers_counters_are_not_given_to_another(self):
        active = self.ACTIVE + [{"name": "enock", "address": "192.168.89.10"}]
        s = self._sessions(active, [
            {"name": "<pppoe-marksilas>", "rx-byte": "10", "tx-byte": "20"},
            {"name": "<pppoe-enock>", "rx-byte": "30", "tx-byte": "40"},
        ])
        self.assertEqual(s["marksilas"]["tx_bytes"], 20)
        self.assertEqual(s["enock"]["tx_bytes"], 40)

    def test_an_unreachable_router_returns_none(self):
        """
        None, not empty. tenant_sessions skips a router it could not read, so
        that a subscriber on a dead router is left alone rather than recorded
        as disconnected.
        """
        from billing.router_service import get_pppoe_sessions
        with patch("billing.router_service.safe_connect_router", return_value=None):
            self.assertIsNone(get_pppoe_sessions(self.router))
