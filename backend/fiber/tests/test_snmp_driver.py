"""
The SNMP client and the EPON driver, against a fake agent on localhost.

The agent answers from a small MIB laid out the way an EPON OLT lays out its
standard tables: PON ports and ONUs as IF-MIB interfaces, device MACs in the
bridge FDB. The vendor optics column is a placeholder OID, set through
profile_overrides exactly as a real one found with fiber_discover would be.
"""

import socket
import threading

from django.test import SimpleTestCase

from fiber.drivers import snmp as S
from fiber.drivers.epon import EponSnmpDriver, convert, mac_from_index, merged_profile, parse_cli_table

VENDOR_RX = "1.3.6.1.4.1.99999.1.1.6"


def _enc_value(v):
    if isinstance(v, tuple):  # (tag, int)
        tag, n = v
        length = max(1, (n.bit_length() + 7) // 8)
        return S._tlv(tag, n.to_bytes(length, "big"))
    if isinstance(v, int):
        return S.enc_int(v)
    if isinstance(v, bytes):
        return S._tlv(S.OCTET_STRING, v)
    return S.enc_str(str(v))


class FakeAgent:
    def __init__(self, mib, community="public"):
        self.mib = dict(mib)
        self.keys = sorted(self.mib, key=S._oid_tuple)
        self.community = community
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.settimeout(0.2)
        self.running = True
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        self.thread.join(1)
        self.sock.close()

    def _next(self, oid):
        t = S._oid_tuple(oid)
        for k in self.keys:
            if S._oid_tuple(k) > t:
                return k
        return None

    def _serve(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            _, msg, _ = S._read(data, 0)
            _, _, i = S._read(msg, 0)
            _, community, i = S._read(msg, i)
            if community.decode() != self.community:
                continue
            ptype, pdu, _ = S._read(msg, i)
            _, rid, j = S._read(pdu, 0)
            _, a, j = S._read(pdu, j)
            _, b, j = S._read(pdu, j)
            _, vbl, _ = S._read(pdu, j)
            oids, k = [], 0
            while k < len(vbl):
                _, vb, k = S._read(vbl, k)
                _, raw, _ = S._read(vb, 0)
                oids.append(S.dec_oid(raw))
            out = []
            if ptype == S.GET:
                for o in oids:
                    out.append((o, _enc_value(self.mib[o]) if o in self.mib else b"\x80\x00"))
            else:
                reps = int.from_bytes(b, "big") if ptype == S.GETBULK else 1
                cur = oids[0]
                for _ in range(reps):
                    nxt = self._next(cur)
                    if nxt is None:
                        out.append((cur, b"\x82\x00"))
                        break
                    out.append((nxt, _enc_value(self.mib[nxt])))
                    cur = nxt
            vbs = b"".join(S._tlv(S.SEQUENCE, S.enc_oid(o) + v) for o, v in out)
            resp_pdu = S._tlv(S.RESPONSE, S._tlv(S.INTEGER, rid) + S.enc_int(0) + S.enc_int(0)
                              + S._tlv(S.SEQUENCE, vbs))
            self.sock.sendto(S._tlv(S.SEQUENCE, S.enc_int(1) + S.enc_str(community) + resp_pdu), addr)


def epon_mib():
    mib = {
        "1.3.6.1.2.1.1.1.0": b"V1600D-MINI EPON OLT",
        "1.3.6.1.2.1.1.3.0": (S.TIMETICKS, 123456),
        "1.3.6.1.2.1.1.5.0": b"vsol",
    }
    names = {1: b"EPON0/1", 2: b"EPON0/2", 11: b"EPON0/1:1", 12: b"EPON0/1:2", 21: b"EPON0/2:1",
             50: b"GE0/1"}
    for idx, name in names.items():
        mib[f"1.3.6.1.2.1.31.1.1.1.1.{idx}"] = name
        mib[f"1.3.6.1.2.1.2.2.1.8.{idx}"] = 2 if idx == 12 else 1
        mib[f"1.3.6.1.2.1.31.1.1.1.6.{idx}"] = (S.COUNTER64, 10 ** 12 + idx)
        mib[f"1.3.6.1.2.1.31.1.1.1.10.{idx}"] = (S.COUNTER64, 10 ** 11 + idx)
    mib["1.3.6.1.2.1.2.2.1.6.11"] = bytes.fromhex("e06701000001")
    mib["1.3.6.1.2.1.31.1.1.1.18.11"] = b"Mama Njeri"
    # bridge port 3 -> ifIndex 11; FDB: two MACs on bridge port 3, one on 9 (unknown)
    mib["1.3.6.1.2.1.17.1.4.1.2.3"] = 11
    mib["1.3.6.1.2.1.17.4.3.1.2.170.187.204.0.0.1"] = 3
    mib["1.3.6.1.2.1.17.4.3.1.2.170.187.204.0.0.2"] = 3
    mib["1.3.6.1.2.1.17.4.3.1.2.1.2.3.4.5.6"] = 9
    # vendor optics in hundredths of a dBm, indexed by ifIndex
    mib[f"{VENDOR_RX}.11"] = -2150
    mib[f"{VENDOR_RX}.21"] = -2785
    return mib


class Olt:
    def __init__(self, port, overrides=None):
        self.mgmt_ip, self.snmp_port, self.snmp_community = "127.0.0.1", port, "public"
        self.cli_username = ""
        self.profile_overrides = overrides or {}


class BerTests(SimpleTestCase):
    def test_oid_round_trip(self):
        for oid in ("1.3.6.1.2.1.1.1.0", "1.3.6.1.4.1.37950.1.1.5.12.200.1"):
            raw = S.enc_oid(oid)
            _, body, _ = S._read(raw, 0)
            self.assertEqual(S.dec_oid(body), oid)

    def test_negative_and_large_integers(self):
        for n in (0, -1, 127, 128, -2150, 2 ** 31 - 1):
            _, body, _ = S._read(S.enc_int(n), 0)
            self.assertEqual(S.dec_value(S.INTEGER, body), n)

    def test_unit_conversions(self):
        self.assertEqual(convert(-2150, "dbm_x100"), -21.5)
        self.assertEqual(convert(-215, "dbm_x10"), -21.5)
        self.assertEqual(convert(b"-21.50 dBm", "text"), -21.5)
        self.assertAlmostEqual(convert(100, "uw_x10"), -20.0)   # 10 uW
        self.assertIsNone(convert(0, "uw_x10"))
        self.assertEqual(mac_from_index("0.170.187.204.0.0.1"), "AA:BB:CC:00:00:01")


class AgentTests(SimpleTestCase):
    def setUp(self):
        self.agent = FakeAgent(epon_mib())
        self.addCleanup(self.agent.stop)

    def test_get_and_walk(self):
        c = S.SnmpClient("127.0.0.1", "public", port=self.agent.port, timeout=1)
        self.assertEqual(c.get("1.3.6.1.2.1.1.5.0")["1.3.6.1.2.1.1.5.0"], b"vsol")
        names = c.table("1.3.6.1.2.1.31.1.1.1.1")
        self.assertEqual(names["11"], b"EPON0/1:1")
        self.assertEqual(len(names), 6)

    def test_wrong_community_times_out_as_an_olt_error(self):
        c = S.SnmpClient("127.0.0.1", "wrong", port=self.agent.port, timeout=0.3, retries=0)
        with self.assertRaises(S.OltError):
            c.get("1.3.6.1.2.1.1.5.0")

    def test_driver_reads_onus_status_traffic_fdb_and_vendor_power(self):
        overrides = {"onu_columns": {"rx_power_dbm": {"oid": VENDOR_RX, "index": "ifindex", "unit": "dbm_x100"}}}
        olt = Olt(self.agent.port, overrides)
        result = EponSnmpDriver(olt, merged_profile({}, overrides)).poll()
        onus = {o.key: o for o in result.onus}
        self.assertEqual(sorted(onus), [(1, 1), (1, 2), (2, 1)])
        self.assertTrue(onus[(1, 1)].online)
        self.assertFalse(onus[(1, 2)].online)
        self.assertEqual(onus[(1, 1)].rx_power_dbm, -21.5)
        self.assertEqual(onus[(2, 1)].rx_power_dbm, -27.85)
        self.assertEqual(onus[(1, 1)].description, "Mama Njeri")
        self.assertEqual(onus[(1, 1)].mac_address, "E0:67:01:00:00:01")
        self.assertEqual(sorted(onus[(1, 1)].client_macs), ["AA:BB:CC:00:00:01", "AA:BB:CC:00:00:02"])
        self.assertEqual(onus[(1, 1)].rx_bytes, 10 ** 12 + 11)
        self.assertEqual({p.pon_port for p in result.ports}, {1, 2})
        self.assertTrue(result.coverage["fdb"] and result.coverage["rx_power_dbm"])

    def test_without_vendor_columns_power_is_unknown_not_zero(self):
        olt = Olt(self.agent.port)
        result = EponSnmpDriver(olt, merged_profile({}, {})).poll()
        self.assertTrue(all(o.rx_power_dbm is None for o in result.onus))


class CliTableTests(SimpleTestCase):
    def test_header_aliases_and_ids_found_by_pattern(self):
        text = """
OnuId        MAC Address          Status    Rx Power(dBm)  Distance(m)
-----------  -------------------  --------  -------------  -----------
EPON0/1:1    E0:67:01:00:00:01    online    -21.43         1250
EPON0/1:2    E0:67:01:00:00:02    offline   N/A            0
EPON0/3:12   E0:67:01:00:00:0C    online    -27.90         3410
"""
        rows = parse_cli_table(text)
        self.assertEqual(sorted(rows), [(1, 1), (1, 2), (3, 12)])
        self.assertEqual(rows[(3, 12)]["rx"], "-27.90")
        self.assertEqual(rows[(1, 1)]["distance"], "1250")
        self.assertEqual(rows[(1, 2)]["status"], "offline")
