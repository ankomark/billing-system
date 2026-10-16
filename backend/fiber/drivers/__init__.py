"""
Pick the driver for an OLT.

VSOL and HiOSO both get the EPON SNMP driver with their own profile. The
profiles carry only what is known to hold for the model family; the vendor
columns (optics, distance, down reason) are left empty until confirmed against
the real box with `manage.py fiber_discover`, then set per OLT in
profile_overrides. An empty column reads as "not reported" on the dashboard,
which is the truth, rather than a guessed OID quietly returning a wrong number.
"""

from .base import OltError, OnuSnapshot, PollResult, PortSnapshot  # noqa: F401
from .epon import EponSnmpDriver, merged_profile
from .simulated import SimulatedDriver

PROFILES = {
    # V1600D-MINI (GEPON). Web on 192.168.8.100; SNMP and telnet are off from
    # the factory and must be enabled before this driver can read it.
    "vsol": {
        "name": "VSOL V1600D series (EPON)",
        "cli": {"prelude": ["enable", "terminal length 0"]},
    },
    # HA7304 (4-port EPON). Out-of-band MGMT port at 192.168.0.88 from the
    # factory; `mgmt outband` moves it.
    "hioso": {
        "name": "HiOSO HA73xx (EPON)",
        "cli": {"prelude": ["enable"]},
    },
    "simulated": {"name": "Simulated OLT"},
}


def get_driver(olt):
    profile = merged_profile(PROFILES.get(olt.vendor, {}), olt.profile_overrides)
    if olt.vendor == "simulated":
        return SimulatedDriver(olt, profile)
    return EponSnmpDriver(olt, profile)
