"""
EPON OLTs over SNMP, with an optional CLI pass for what SNMP does not carry.

Two layers, deliberately:

1. Standard MIBs, which every EPON OLT we have met answers. IF-MIB lists each
   PON port and, on VSOL and HiOSO, each registered ONU as its own interface
   ("EPON0/1:7"), with an up/down status and byte counters. BRIDGE-MIB's
   forwarding table says which device MACs were learned on which of those
   interfaces -- and that is what ties a paying customer to an ONU without
   anyone typing a name in. This layer works without knowing the vendor.

2. Vendor columns: optical power, distance, last-down reason. These live in
   each vendor's private MIB or only in its CLI, and their OIDs and command
   wording differ by firmware. A profile names them; an Olt's
   profile_overrides can replace any of them. Until a column is filled in,
   its value is None and the dashboard says "not reported", which is true.
   `manage.py fiber_discover` dumps what a real OLT answers so the profile can
   be finished against the box rather than guessed.
"""

import math
import re

from .base import OltDriver, OltError, OnuSnapshot, PollResult, PortSnapshot
from .snmp import SnmpClient
from .telnet import TelnetCli

SYS_DESCR = "1.3.6.1.2.1.1.1.0"
SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
SYS_NAME = "1.3.6.1.2.1.1.5.0"
IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
IF_OPER = "1.3.6.1.2.1.2.2.1.8"
IF_PHYS = "1.3.6.1.2.1.2.2.1.6"
IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"
IF_HC_IN = "1.3.6.1.2.1.31.1.1.1.6"
IF_HC_OUT = "1.3.6.1.2.1.31.1.1.1.10"
IF_IN = "1.3.6.1.2.1.2.2.1.10"
IF_OUT = "1.3.6.1.2.1.2.2.1.16"
IF_ALIAS = "1.3.6.1.2.1.31.1.1.1.18"
BRIDGE_PORT_IFINDEX = "1.3.6.1.2.1.17.1.4.1.2"
FDB_PORT = "1.3.6.1.2.1.17.4.3.1.2"
QFDB_PORT = "1.3.6.1.2.1.17.7.1.2.2.1.2"
ENTERPRISES = "1.3.6.1.4.1"

DEFAULT_PROFILE = {
    # How interface names spell a PON port and an ONU on it.
    "port_name_re": r"(?i)^\s*(?:g?e?pon)\s*(\d+)/(\d+)\s*$",
    "onu_name_re": r"(?i)^\s*(?:g?e?pon)\s*(\d+)/(\d+)\s*[:/.]\s*(\d+)\s*$",
    # Vendor columns, each {"oid": ..., "index": "ifindex"|"port.onu", "unit": ...}
    # unit: dbm | dbm_x10 | dbm_x100 | uw_x10 | mw_x10000 | text | m | dm
    "onu_columns": {},
    # CLI pass, run after SNMP when transport is telnet or columns are missing.
    "cli": {"prelude": [], "optics_command": "", "status_command": ""},
}


def _text(v):
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace").strip("\x00 ").strip()
    return "" if v is None else str(v).strip()


def _mac_from_bytes(v):
    if isinstance(v, bytes) and len(v) == 6:
        return ":".join(f"{b:02X}" for b in v)
    t = _text(v).upper().replace("-", ":").replace(".", "")
    if re.fullmatch(r"[0-9A-F]{12}", t):
        return ":".join(t[i:i + 2] for i in range(0, 12, 2))
    return t if re.fullmatch(r"([0-9A-F]{2}:){5}[0-9A-F]{2}", t) else ""


def convert(value, unit):
    """A vendor column's raw value in the unit the model stores, or None."""
    if value is None:
        return None
    if unit == "text":
        m = re.search(r"-?\d+(?:\.\d+)?", _text(value))
        return float(m.group()) if m else None
    try:
        n = float(_text(value)) if isinstance(value, bytes) else float(value)
    except (TypeError, ValueError):
        return None
    if unit == "dbm":
        return round(n, 2)
    if unit == "dbm_x10":
        return round(n / 10, 2)
    if unit == "dbm_x100":
        return round(n / 100, 2)
    if unit == "uw_x10":         # tenths of a microwatt
        return round(10 * math.log10(n / 10000), 2) if n > 0 else None
    if unit == "mw_x10000":      # ten-thousandths of a milliwatt
        return round(10 * math.log10(n / 10000), 2) if n > 0 else None
    if unit == "m":
        return int(n)
    if unit == "dm":
        return int(n / 10)
    if unit == "temp_x256":
        return round(n / 256, 1)
    if unit == "volt_x10000":
        return round(n / 10000, 2)
    return n


def mac_from_index(suffix):
    """The last six arcs of an FDB index are the MAC, one octet each."""
    arcs = suffix.split(".")[-6:]
    if len(arcs) != 6:
        return ""
    try:
        return ":".join(f"{int(a):02X}" for a in arcs)
    except ValueError:
        return ""


# ------------------------------------------------------------------ CLI tables

HEADER_ALIASES = {
    "onu": "id", "onuid": "id", "onu-id": "id", "id": "id", "port": "id", "interface": "id",
    "mac": "mac", "macaddress": "mac", "mac-address": "mac",
    "status": "status", "state": "status", "oper": "status",
    "rx": "rx", "rxpower": "rx", "rx(dbm)": "rx", "rxpower(dbm)": "rx", "onurx": "rx",
    "tx": "tx", "txpower": "tx", "tx(dbm)": "tx", "txpower(dbm)": "tx",
    "oltrx": "olt_rx", "oltrxpower": "olt_rx",
    "distance": "distance", "distance(m)": "distance", "rtt(m)": "distance",
    "temperature": "temp", "temp": "temp", "voltage": "volt",
    "lastdownreason": "down", "downcause": "down", "lastdowncause": "down",
}
ID_RE = re.compile(r"(?i)(?:g?e?pon)?\s*(\d+)/(\d+)\s*[:/.]\s*(\d+)")


def parse_cli_table(text):
    """
    Rows of an ONU table printed by an OLT CLI, keyed by (port, onu).

    Generic on purpose: VSOL and HiOSO both print aligned tables with a header
    row, but the header words differ by firmware. The header is mapped through
    HEADER_ALIASES, rows are split on runs of two or more spaces (or single
    spaces when the column count matches), and the ONU id is found by pattern
    rather than position. A line that does not carry an ONU id is skipped.
    """
    rows = {}
    columns = None
    for line in text.splitlines():
        if not line.strip() or set(line.strip()) <= set("-=+ "):
            continue
        tokens = [t for t in re.split(r"\s{2,}|\t", line.strip()) if t]
        if len(tokens) < 2:
            tokens = line.split()
        id_match = ID_RE.search(line)
        if columns is None or not id_match:
            names = [HEADER_ALIASES.get(re.sub(r"[\s_]", "", t).lower()) for t in tokens]
            if sum(1 for n in names if n) >= 2:
                columns = names
            continue
        if len(tokens) != len(columns):
            tokens = line.split()
            if len(tokens) != len(columns):
                continue
        rec = {}
        for name, tok in zip(columns, tokens):
            if name:
                rec[name] = tok
        rows[(int(id_match.group(2)), int(id_match.group(3)))] = rec
    return rows


DOWN_WORDS = {
    "dying": "dying_gasp", "gasp": "dying_gasp", "power": "dying_gasp",
    "los": "los", "lof": "los", "fiber": "los", "signal": "los", "loss": "los",
}


def down_reason(text):
    t = (text or "").lower()
    for word, reason in DOWN_WORDS.items():
        if word in t:
            return reason
    return "unknown" if t else ""


# ------------------------------------------------------------------ driver

class EponSnmpDriver(OltDriver):
    def _snmp(self):
        return SnmpClient(self.olt.mgmt_ip, self.olt.snmp_community, port=self.olt.snmp_port)

    def poll(self):
        snmp = self._snmp()
        port_re = re.compile(self.profile["port_name_re"])
        onu_re = re.compile(self.profile["onu_name_re"])

        # The first request doubles as the reachability check: an OltError
        # here is the OLT being down, and the collector records it as such.
        sysv = snmp.get(SYS_DESCR, SYS_UPTIME, SYS_NAME)
        system = {"descr": _text(sysv.get(SYS_DESCR)), "uptime_ticks": sysv.get(SYS_UPTIME),
                  "name": _text(sysv.get(SYS_NAME))}

        names = snmp.table(IF_NAME) or {}
        descrs = snmp.table(IF_DESCR)
        labels = {i: _text(names.get(i)) or _text(descrs.get(i)) for i in set(names) | set(descrs)}
        oper = snmp.table(IF_OPER)
        rx = snmp.table(IF_HC_IN) or snmp.table(IF_IN)
        tx = snmp.table(IF_HC_OUT) or snmp.table(IF_OUT)
        alias = snmp.table(IF_ALIAS)
        phys = snmp.table(IF_PHYS)

        onus, ports, by_ifindex = {}, {}, {}
        for ifindex, label in labels.items():
            m = onu_re.match(label)
            if m:
                snap = OnuSnapshot(pon_port=int(m.group(2)), onu_index=int(m.group(3)))
                if ifindex in oper:
                    snap.online = oper[ifindex] == 1
                snap.rx_bytes, snap.tx_bytes = rx.get(ifindex), tx.get(ifindex)
                snap.description = _text(alias.get(ifindex))
                snap.mac_address = _mac_from_bytes(phys.get(ifindex))
                onus[snap.key] = snap
                by_ifindex[ifindex] = snap
                continue
            m = port_re.match(label)
            if m:
                p = int(m.group(2))
                ports[p] = PortSnapshot(
                    pon_port=p, online=(oper.get(ifindex) == 1) if ifindex in oper else None,
                    rx_bytes=rx.get(ifindex), tx_bytes=tx.get(ifindex))

        coverage = {"onu_list": bool(onus), "traffic": any(s.rx_bytes is not None for s in onus.values())}
        coverage.update(self._vendor_columns(snmp, onus, by_ifindex))
        coverage["fdb"] = self._fdb(snmp, by_ifindex)
        if self.profile["cli"].get("optics_command") and self.olt.cli_username:
            coverage["cli"] = self._cli_pass(onus)

        return PollResult(onus=list(onus.values()), ports=list(ports.values()),
                          system=system, coverage=coverage)

    def _vendor_columns(self, snmp, onus, by_ifindex):
        found = {}
        for field_name, spec in (self.profile.get("onu_columns") or {}).items():
            try:
                col = snmp.table(spec["oid"])
            except OltError:
                found[field_name] = False
                continue
            hits = 0
            for suffix, raw in col.items():
                if spec.get("index", "ifindex") == "ifindex":
                    snap = by_ifindex.get(suffix.split(".")[-1])
                else:
                    arcs = suffix.split(".")
                    snap = onus.get((int(arcs[-2]), int(arcs[-1]))) if len(arcs) >= 2 else None
                if snap is None:
                    continue
                if field_name == "last_down_reason":
                    value = down_reason(_text(raw)) if spec.get("unit") == "text" else \
                        spec.get("map", {}).get(str(raw), "unknown")
                elif field_name == "mac_address":
                    value = _mac_from_bytes(raw)
                else:
                    value = convert(raw, spec.get("unit", "dbm"))
                if value not in (None, ""):
                    setattr(snap, field_name, value)
                    hits += 1
            found[field_name] = hits > 0
        return found

    def _fdb(self, snmp, by_ifindex):
        bridge_to_if = {k: str(v) for k, v in snmp.table(BRIDGE_PORT_IFINDEX).items()}
        fdb = snmp.table(QFDB_PORT) or snmp.table(FDB_PORT)
        hits = 0
        for suffix, bridge_port in fdb.items():
            ifindex = bridge_to_if.get(str(bridge_port), str(bridge_port))
            snap = by_ifindex.get(ifindex)
            mac = mac_from_index(suffix)
            if snap is not None and mac:
                snap.client_macs.append(mac)
                hits += 1
        return hits > 0

    def _cli_pass(self, onus):
        cli = self.profile["cli"]
        try:
            with TelnetCli(self.olt.mgmt_ip, self.olt.cli_port,
                           self.olt.cli_username, self.olt.cli_password) as t:
                for line in cli.get("prelude", []):
                    t.run(line)
                rows = parse_cli_table(t.run(cli["optics_command"]))
        except OltError:
            return False
        for key, rec in rows.items():
            snap = onus.get(key)
            if snap is None:
                snap = onus[key] = OnuSnapshot(pon_port=key[0], onu_index=key[1])
            if "rx" in rec:
                snap.rx_power_dbm = convert(rec["rx"], "text")
            if "tx" in rec:
                snap.tx_power_dbm = convert(rec["tx"], "text")
            if "olt_rx" in rec:
                snap.olt_rx_power_dbm = convert(rec["olt_rx"], "text")
            if "distance" in rec:
                d = convert(rec["distance"], "text")
                snap.distance_m = int(d) if d is not None else None
            if "temp" in rec:
                snap.temperature_c = convert(rec["temp"], "text")
            if "mac" in rec and not snap.mac_address:
                snap.mac_address = _mac_from_bytes(rec["mac"])
            if "status" in rec and snap.online is None:
                snap.online = rec["status"].lower() in ("online", "up", "registered", "active", "working")
            if "down" in rec:
                snap.last_down_reason = down_reason(rec["down"])
        return bool(rows)

    def discover(self):
        snmp = self._snmp()
        out = {"system": {k: _text(v) for k, v in snmp.get(SYS_DESCR, SYS_NAME).items()}}
        out["interfaces"] = {i: _text(v) for i, v in (snmp.table(IF_NAME) or snmp.table(IF_DESCR)).items()}
        out["enterprise"] = [(oid, _text(v) if isinstance(v, bytes) else v)
                             for oid, v in snmp.walk(ENTERPRISES, limit=20000)]
        return out


def merged_profile(base, overrides):
    prof = {**DEFAULT_PROFILE, **base}
    prof["cli"] = {**DEFAULT_PROFILE["cli"], **base.get("cli", {}), **(overrides or {}).get("cli", {})}
    prof["onu_columns"] = {**base.get("onu_columns", {}), **(overrides or {}).get("onu_columns", {})}
    for k, v in (overrides or {}).items():
        if k not in ("cli", "onu_columns"):
            prof[k] = v
    return prof
