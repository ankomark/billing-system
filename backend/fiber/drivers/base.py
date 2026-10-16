"""
What every OLT driver hands back, whatever the box speaks.

The collector never sees an OID or a CLI line. A driver turns its vendor's
dialect into these records, and None means "this OLT did not say" -- never a
guessed zero. An ONU with no optical reading is shown as unknown power, not as
-0.0 dBm, and a distance nobody reported is not 0 m from the OLT.
"""

from dataclasses import dataclass, field


class OltError(Exception):
    """The OLT could not be read. Carries a short reason for last_error."""


@dataclass
class OnuSnapshot:
    pon_port: int
    onu_index: int
    online: bool | None = None
    mac_address: str = ""
    serial: str = ""
    description: str = ""
    model: str = ""
    distance_m: int | None = None
    rx_power_dbm: float | None = None      # light arriving at the ONU
    tx_power_dbm: float | None = None      # light the ONU sends
    olt_rx_power_dbm: float | None = None  # that light as it reaches the OLT
    temperature_c: float | None = None
    voltage_v: float | None = None
    # "los" | "dying_gasp" | "unknown" | "" when the OLT keeps no reason.
    last_down_reason: str = ""
    rx_bytes: int | None = None
    tx_bytes: int | None = None
    # Device MACs the OLT has learned behind this ONU (its FDB).
    client_macs: list = field(default_factory=list)

    @property
    def key(self):
        return (self.pon_port, self.onu_index)


@dataclass
class PortSnapshot:
    pon_port: int
    online: bool | None = None
    rx_bytes: int | None = None
    tx_bytes: int | None = None


@dataclass
class PollResult:
    onus: list
    ports: list = field(default_factory=list)
    system: dict = field(default_factory=dict)
    # Which parts of the profile answered, for the OLT page: an OLT that lists
    # ONUs but returns no optics is a profile to finish, not a dark network.
    coverage: dict = field(default_factory=dict)


class OltDriver:
    """Read-only. No driver method changes anything on the OLT."""

    def __init__(self, olt, profile):
        self.olt = olt
        self.profile = profile

    def poll(self) -> PollResult:
        raise NotImplementedError

    def discover(self) -> dict:
        """Raw material for finishing a profile: see manage.py fiber_discover."""
        raise NotImplementedError
