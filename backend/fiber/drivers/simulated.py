"""
A pretend OLT, for tests and for running the dashboard locally.

Deterministic: the same OLT id always yields the same ONUs, distances and
power levels, so a test can name "port 2, ONU 5" and know what it is. Faults
are switched on through the OLT's profile_overrides["sim"]:

    {"unreachable": true}                    the OLT does not answer
    {"port_down": [2], "cause": "los"}       every ONU on PON 2 drops
    {"down": ["1:3", "1:4"], "cause": "dying_gasp"}
    {"rx_offset": {"1:7": -6.5}}             one ONU's light falls 6.5 dB
    {"clients": {"1:3": ["AA:BB:CC:00:00:01"]}}
    {"onus_per_port": [8, 6, 0, 0]}
    {"traffic_bps": 2000000}                 per online ONU, both ways

Never registered against real hardware: the admin refuses a simulated OLT
outside DEBUG, because its ONUs and customers would be invented.
"""

import random
import time

from .base import OltDriver, OltError, OnuSnapshot, PollResult, PortSnapshot


class SimulatedDriver(OltDriver):
    def _sim(self):
        return (self.olt.profile_overrides or {}).get("sim", {})

    def poll(self):
        sim = self._sim()
        if sim.get("unreachable"):
            raise OltError("simulated: OLT not answering")
        rnd = random.Random(f"olt-{self.olt.pk}")
        per_port = sim.get("onus_per_port") or [12, 8, 6, 0][: self.olt.pon_ports]
        down = set(sim.get("down", []))
        port_down = set(sim.get("port_down", []))
        cause = sim.get("cause", "los")
        offsets = sim.get("rx_offset", {})
        clients = sim.get("clients", {})
        now = sim.get("clock", time.time())
        bps = int(sim.get("traffic_bps", 1_500_000))

        onus, ports = [], []
        for p, count in enumerate(per_port, start=1):
            port_rx = port_tx = 0
            for i in range(1, count + 1):
                key = f"{p}:{i}"
                # Every draw is made whether or not the ONU is up, so taking one
                # offline cannot shift the numbers of the ONUs after it.
                distance = rnd.randint(150, 4200)
                # ~0.35 dB/km of fibre plus splitter loss and connector scatter.
                rx = round(-14.0 - distance * 0.00035 - rnd.uniform(3.0, 9.5) + offsets.get(key, 0), 2)
                mac = "E0:67:%02X:%02X:%02X:%02X" % (self.olt.pk % 256, p, i, rnd.randint(0, 255))
                counter = int(now * bps / 8) + rnd.randint(0, 10**9)
                tx_dbm = round(rnd.uniform(1.5, 3.2), 2)
                olt_rx = round(rx - rnd.uniform(1.0, 2.5), 2)
                temp = round(rnd.uniform(38, 55), 1)
                online = not (key in down or p in port_down)
                snap = OnuSnapshot(
                    pon_port=p, onu_index=i, online=online, mac_address=mac,
                    description=f"SIM-{p}-{i}", model="SIM-ONU",
                    distance_m=distance if online else None,
                    rx_power_dbm=rx if online else None,
                    tx_power_dbm=tx_dbm if online else None,
                    olt_rx_power_dbm=olt_rx if online else None,
                    temperature_c=temp if online else None,
                    last_down_reason="" if online else cause,
                    rx_bytes=counter if online else None,
                    tx_bytes=counter // 4 if online else None,
                    client_macs=list(clients.get(key, [])) if online else [],
                )
                onus.append(snap)
                if online:
                    port_rx += counter
                    port_tx += counter // 4
            ports.append(PortSnapshot(pon_port=p, online=p not in port_down,
                                      rx_bytes=port_rx, tx_bytes=port_tx))
        coverage = {k: True for k in ("onu_list", "traffic", "fdb", "rx_power_dbm", "distance_m",
                                      "last_down_reason")}
        return PollResult(onus=onus, ports=ports,
                          system={"descr": "Simulated EPON OLT", "name": self.olt.name},
                          coverage=coverage)

    def discover(self):
        return {"system": {"descr": "Simulated EPON OLT"}, "interfaces": {}, "enterprise": []}
