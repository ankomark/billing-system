"""
A small SNMPv2c client: GET, GETNEXT and GETBULK walks. Read-only by design.

Written here rather than taken from pysnmp. All the collector needs is to walk
a handful of tables on two boxes on a LAN, and that is a few hundred lines of
BER; pysnmp is a large asyncio stack whose API has changed shape across its
last three majors. There is deliberately no SET: nothing in Smart Mapping
writes to an OLT over SNMP, and a client that cannot is one less way to.

Values come back as Python types: int for INTEGER/Counter/Gauge/TimeTicks,
bytes for OCTET STRING (callers decide whether it is text or a MAC), a dotted
string for OIDs and IpAddress, and None for the three "nothing here" markers.
"""

import itertools
import socket

from .base import OltError

# BER tags
INTEGER, OCTET_STRING, NULL, OID, SEQUENCE = 0x02, 0x04, 0x05, 0x06, 0x30
IPADDRESS, COUNTER32, GAUGE32, TIMETICKS, OPAQUE, COUNTER64 = 0x40, 0x41, 0x42, 0x43, 0x44, 0x46
NO_SUCH_OBJECT, NO_SUCH_INSTANCE, END_OF_MIB = 0x80, 0x81, 0x82
GET, GETNEXT, RESPONSE, GETBULK = 0xA0, 0xA1, 0xA2, 0xA5

_request_ids = itertools.count(0x1000)


class EndOfMib:
    """Marker for endOfMibView, distinct from a value that is legitimately None."""


END = EndOfMib()


# ---------------------------------------------------------------- encoding

def _len(n):
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def _tlv(tag, value):
    return bytes([tag]) + _len(len(value)) + value


def enc_int(v, tag=INTEGER):
    length = max(1, (v.bit_length() + 8) // 8)
    return _tlv(tag, v.to_bytes(length, "big", signed=True))


def enc_str(v):
    return _tlv(OCTET_STRING, v if isinstance(v, bytes) else v.encode())


def enc_oid(oid):
    parts = [int(p) for p in oid.strip(".").split(".")]
    if len(parts) < 2:
        raise ValueError(f"bad OID {oid!r}")
    out = bytearray([parts[0] * 40 + parts[1]])
    for p in parts[2:]:
        chunk = [p & 0x7F]
        p >>= 7
        while p:
            chunk.append(0x80 | (p & 0x7F))
            p >>= 7
        out.extend(reversed(chunk))
    return _tlv(OID, bytes(out))


def enc_null():
    return b"\x05\x00"


def build_request(pdu_type, community, oids, request_id, non_repeaters=0, max_repetitions=0):
    varbinds = b"".join(_tlv(SEQUENCE, enc_oid(o) + enc_null()) for o in oids)
    if pdu_type == GETBULK:
        head = enc_int(request_id) + enc_int(non_repeaters) + enc_int(max_repetitions)
    else:
        head = enc_int(request_id) + enc_int(0) + enc_int(0)
    pdu = _tlv(pdu_type, head + _tlv(SEQUENCE, varbinds))
    return _tlv(SEQUENCE, enc_int(1) + enc_str(community) + pdu)


# ---------------------------------------------------------------- decoding

def _read(buf, i):
    tag = buf[i]
    i += 1
    first = buf[i]
    i += 1
    if first & 0x80:
        n = first & 0x7F
        length = int.from_bytes(buf[i:i + n], "big")
        i += n
    else:
        length = first
    return tag, buf[i:i + length], i + length


def dec_oid(raw):
    if not raw:
        return ""
    parts = [raw[0] // 40, raw[0] % 40]
    n = 0
    for b in raw[1:]:
        n = (n << 7) | (b & 0x7F)
        if not b & 0x80:
            parts.append(n)
            n = 0
    return ".".join(str(p) for p in parts)


def dec_value(tag, raw):
    if tag == INTEGER:
        return int.from_bytes(raw, "big", signed=True) if raw else 0
    if tag in (COUNTER32, GAUGE32, TIMETICKS, COUNTER64):
        return int.from_bytes(raw, "big", signed=False) if raw else 0
    if tag in (OCTET_STRING, OPAQUE):
        return bytes(raw)
    if tag == OID:
        return dec_oid(raw)
    if tag == IPADDRESS:
        return ".".join(str(b) for b in raw)
    if tag == END_OF_MIB:
        return END
    return None  # NULL, noSuchObject, noSuchInstance


def parse_response(data, expect_id):
    """Return [(oid, value)] from a Response PDU, or raise OltError."""
    try:
        tag, msg, _ = _read(data, 0)
        if tag != SEQUENCE:
            raise ValueError("not a sequence")
        _, _, i = _read(msg, 0)            # version
        _, _, i = _read(msg, i)            # community
        tag, pdu, _ = _read(msg, i)
        if tag != RESPONSE:
            raise ValueError(f"unexpected PDU 0x{tag:02x}")
        _, rid, j = _read(pdu, 0)
        _, err, j = _read(pdu, j)
        _, _, j = _read(pdu, j)            # error index
        _, vbl, _ = _read(pdu, j)
    except (IndexError, ValueError) as exc:
        raise OltError(f"malformed SNMP reply: {exc}") from exc

    if int.from_bytes(rid, "big", signed=True) != expect_id:
        return None  # a late reply to an earlier request; caller keeps waiting
    status = int.from_bytes(err, "big", signed=True) if err else 0
    if status:
        raise OltError(f"SNMP error-status {status}")

    out = []
    k = 0
    while k < len(vbl):
        _, vb, k = _read(vbl, k)
        _, oid_raw, m = _read(vb, 0)
        vtag, vraw, _ = _read(vb, m)
        out.append((dec_oid(oid_raw), dec_value(vtag, vraw)))
    return out


# ---------------------------------------------------------------- client

def _oid_tuple(oid):
    return tuple(int(p) for p in oid.strip(".").split("."))


class SnmpClient:
    def __init__(self, host, community, port=161, timeout=2.0, retries=2):
        if not community:
            raise OltError("no SNMP community set for this OLT")
        self.host, self.port = host, port
        self.community = community
        self.timeout, self.retries = timeout, retries

    def _exchange(self, pdu_type, oids, **kw):
        rid = next(_request_ids) & 0x7FFFFFFF
        packet = build_request(pdu_type, self.community, oids, rid, **kw)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(self.timeout)
        try:
            for _ in range(self.retries + 1):
                sock.sendto(packet, (self.host, self.port))
                try:
                    while True:
                        data, _ = sock.recvfrom(65535)
                        got = parse_response(data, rid)
                        if got is not None:
                            return got
                except socket.timeout:
                    continue
        finally:
            sock.close()
        raise OltError(f"no SNMP reply from {self.host}:{self.port}")

    def get(self, *oids):
        return dict(self._exchange(GET, list(oids)))

    def walk(self, base, max_repetitions=25, limit=50000):
        """Every (oid, value) under `base`, in order. Stops at the subtree's end."""
        base_t = _oid_tuple(base)
        current = base.strip(".")
        rows = []
        while len(rows) < limit:
            batch = self._exchange(GETBULK, [current], max_repetitions=max_repetitions)
            if not batch:
                break
            for oid, value in batch:
                if value is END or _oid_tuple(oid)[:len(base_t)] != base_t:
                    return rows
                if rows and _oid_tuple(oid) <= _oid_tuple(rows[-1][0]):
                    return rows  # an agent going backwards would loop forever
                rows.append((oid, value))
            current = batch[-1][0]
        return rows

    def table(self, base):
        """{index_suffix: value} for a single column under `base`."""
        n = len(_oid_tuple(base))
        return {".".join(oid.split(".")[n:]): v for oid, v in self.walk(base)}
