"""
One poll of one OLT: read it, store what it said, and work out what changed.

The OLT is the only source of truth about ONUs, and it is polled rather than
trusted to push: EPON boxes at this price send SNMP traps inconsistently and
nothing here would be listening on a public port for them anyway.

What a poll writes:
  - the Onu rows, created the first time an ONU is seen and followed by MAC
    if it moves port;
  - one OnuReading per ONU (raw, 14 days);
  - OnuCustomer links from the OLT's MAC table (linking.py);
  - transitions, handed to outages.py, which decides what they mean.

Two rules carried over from billing's router health sweep:
  - a failed read is "unknown", never "offline". One missed poll does not
    take an OLT's ONUs down; FAIL_THRESHOLD in a row does, as one outage.
  - a value the OLT did not report is left as it was. In particular an ONU
    that goes dark keeps its last distance, which is exactly what the fault
    zone needs: where the dark ONUs are along the fibre.
"""

import logging

from django.db import transaction
from django.utils import timezone

from billing.tenancy import tenant_context
from billing.utils import normalize_mac

from ..drivers import OltError, get_driver
from ..models import Olt, Onu, OnuReading
from . import linking, outages

logger = logging.getLogger(__name__)

# Two polls five minutes apart. One timeout is a lost UDP packet; two is an OLT
# (or the path to it) that is down.
FAIL_THRESHOLD = 2

# A counter that went backwards by more than this is a reset (reboot, ONU
# re-registered), not a 32-bit wrap; the interval is dropped rather than
# reported as an absurd number.
WRAP_32 = 2 ** 32


def _delta(prev, cur):
    if prev is None or cur is None:
        return None
    if cur >= prev:
        return cur - prev
    if prev < WRAP_32 and prev - cur > WRAP_32 // 2:
        return cur + WRAP_32 - prev
    return None


def poll_olt(olt, now=None):
    """Poll one OLT. Returns a short summary dict; never raises for the OLT's sake."""
    now = now or timezone.now()
    with tenant_context(olt.tenant_id):
        try:
            result = get_driver(olt).poll()
        except OltError as exc:
            return _record_failure(olt, str(exc), now)
        except Exception as exc:  # a driver bug must not stop the sweep
            logger.exception("[fiber] %s: driver crashed", olt.name)
            return _record_failure(olt, f"driver error: {exc.__class__.__name__}", now)
        with transaction.atomic():
            return _apply(olt, result, now)


def _record_failure(olt, reason, now):
    olt.consecutive_failures += 1
    olt.last_error = reason[:255]
    olt.last_polled_at = now
    went_dark = olt.is_reachable and olt.consecutive_failures >= FAIL_THRESHOLD
    first_ever = olt.last_ok_at is None
    if olt.consecutive_failures >= FAIL_THRESHOLD:
        olt.is_reachable = False
    olt.save(update_fields=["consecutive_failures", "last_error", "last_polled_at", "is_reachable"])
    logger.warning("[fiber] %s unreachable (%s, %s in a row)", olt.name, reason, olt.consecutive_failures)
    if went_dark and not first_ever:
        outages.olt_unreachable(olt, now)
    return {"olt": olt.pk, "ok": False, "error": reason}


def _apply(olt, result, now):
    was_unreachable = not olt.is_reachable and olt.last_ok_at is not None
    olt.is_reachable = True
    olt.consecutive_failures = 0
    olt.last_error = ""
    olt.last_polled_at = olt.last_ok_at = now
    descr = (result.system or {}).get("descr", "")
    if descr and not olt.firmware:
        olt.firmware = descr[:64]
    olt.save(update_fields=["is_reachable", "consecutive_failures", "last_error",
                            "last_polled_at", "last_ok_at", "firmware"])

    existing = {(o.pon_port, o.onu_index): o for o in Onu.objects.filter(olt=olt)}
    by_mac = {o.mac_address: o for o in existing.values() if o.mac_address}
    seen_ids = set()
    went_down, came_up, readings, fdb = [], [], [], {}

    for snap in result.onus:
        mac = normalize_mac(snap.mac_address) if snap.mac_address else ""
        onu = existing.get(snap.key)
        if onu is None and mac and mac in by_mac and by_mac[mac].pk not in seen_ids:
            # Same ONU, new position: re-plugged into another port. Keep its
            # history and customers rather than starting a stranger.
            onu = by_mac[mac]
            existing.pop((onu.pon_port, onu.onu_index), None)
            Onu.objects.filter(olt=olt, pon_port=snap.pon_port, onu_index=snap.onu_index) \
                .exclude(pk=onu.pk).update(onu_index=1_000_000 + onu.pk, present=False)  # free the slot
            onu.pon_port, onu.onu_index = snap.pon_port, snap.onu_index
        if onu is None:
            onu = Onu(tenant_id=olt.tenant_id, olt=olt, pon_port=snap.pon_port,
                      onu_index=snap.onu_index, first_seen=now)
        prev_status = onu.status
        prev_rx, prev_tx = onu.rx_bytes, onu.tx_bytes
        prev_at = onu.last_seen if onu.pk else None

        if snap.online is True:
            onu.status = Onu.STATUS_ONLINE
        elif snap.online is False:
            onu.status = Onu.STATUS_OFFLINE
        onu.present = True
        onu.last_seen = now
        if mac:
            onu.mac_address = mac
        for field in ("serial", "model"):
            value = getattr(snap, field)
            if value:
                setattr(onu, field, value[:64])
        if snap.description:
            onu.olt_description = snap.description[:128]
        for field in ("distance_m", "rx_power_dbm", "tx_power_dbm", "olt_rx_power_dbm",
                      "temperature_c", "voltage_v"):
            value = getattr(snap, field)
            if value is not None:
                setattr(onu, field, value)
        if onu.status == Onu.STATUS_OFFLINE and prev_status != Onu.STATUS_OFFLINE:
            onu.last_down_at = now
            onu.last_down_reason = snap.last_down_reason or Onu.DOWN_UNKNOWN
        elif onu.status == Onu.STATUS_ONLINE and prev_status != Onu.STATUS_ONLINE:
            onu.last_up_at = now
        elif onu.status == Onu.STATUS_OFFLINE and snap.last_down_reason:
            onu.last_down_reason = snap.last_down_reason

        rx_d = _delta(prev_rx, snap.rx_bytes)
        tx_d = _delta(prev_tx, snap.tx_bytes)
        if snap.rx_bytes is not None:
            onu.rx_bytes = snap.rx_bytes
        if snap.tx_bytes is not None:
            onu.tx_bytes = snap.tx_bytes
        onu.save()
        seen_ids.add(onu.pk)

        if onu.status == Onu.STATUS_OFFLINE and prev_status == Onu.STATUS_ONLINE:
            went_down.append((onu, onu.last_down_reason))
        elif onu.status == Onu.STATUS_ONLINE and prev_status in (Onu.STATUS_OFFLINE, Onu.STATUS_UNKNOWN):
            came_up.append(onu)

        readings.append(OnuReading(
            tenant_id=olt.tenant_id, onu=onu, at=now, online=onu.status == Onu.STATUS_ONLINE,
            rx_power_dbm=snap.rx_power_dbm, tx_power_dbm=snap.tx_power_dbm,
            distance_m=snap.distance_m, rx_delta=rx_d, tx_delta=tx_d,
            seconds=int((now - prev_at).total_seconds()) if prev_at else None))
        if snap.client_macs:
            fdb[onu] = snap.client_macs

    # Gone from the OLT's table. Some firmware drops an ONU from the list the
    # moment it deregisters, which is to say the moment its light goes: that
    # is a down, not a deletion.
    for onu in Onu.objects.filter(olt=olt, present=True).exclude(pk__in=seen_ids):
        if onu.status == Onu.STATUS_ONLINE:
            went_down.append((onu, Onu.DOWN_UNKNOWN))
            onu.last_down_at = now
            onu.last_down_reason = Onu.DOWN_UNKNOWN
        onu.status = Onu.STATUS_OFFLINE
        onu.present = False
        onu.save(update_fields=["status", "present", "last_down_at", "last_down_reason"])

    OnuReading.objects.bulk_create(readings, batch_size=500)
    linked = linking.record_fdb(olt, fdb, now)
    outcome = outages.process(olt, went_down, came_up, now, olt_recovered=was_unreachable)

    return {"olt": olt.pk, "ok": True, "onus": len(result.onus),
            "online": sum(1 for s in result.onus if s.online), "went_down": len(went_down),
            "came_up": len(came_up), "linked": linked, "coverage": result.coverage, **outcome}


def poll_by_id(olt_id):
    olt = Olt.objects.all_tenants().filter(pk=olt_id, is_active=True).first()
    if olt is None:
        return None
    return poll_olt(olt)
