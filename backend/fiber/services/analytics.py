"""
What the fiber plant looks like, now and over time.

Signal bands are for EPON ONU receive power (1000BASE-PX20+ optics):

    above -24 dBm     good
    -24 to -27 dBm    warning -- clean connectors, look for tight bends
    below -27 dBm     critical -- at the edge of the optic's sensitivity
    above -3 dBm      overload -- too much light, needs an attenuator

Everything reads stored rows. Nothing here talks to an OLT, for the same
reason the dashboard never asks a router: one slow box must not hang a page.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Max, Sum
from django.utils import timezone

from billing.models import Payment

from ..models import FiberOutage, Olt, Onu, OnuHourly, OnuReading, PortHourly, RepairTicket
from . import linking

GOOD, WARNING, CRITICAL, OVERLOAD, UNKNOWN = "good", "warning", "critical", "overload", "unknown"
WARN_AT, CRITICAL_AT, OVERLOAD_AT = -24.0, -27.0, -3.0


def signal_band(rx):
    if rx is None:
        return UNKNOWN
    if rx > OVERLOAD_AT:
        return OVERLOAD
    if rx >= WARN_AT:
        return GOOD
    if rx >= CRITICAL_AT:
        return WARNING
    return CRITICAL


# ------------------------------------------------------------------ Phase 1

def overview(now=None):
    now = now or timezone.now()
    hour_ago = now - timedelta(hours=1)
    linked_ids = set(linking.fresh_links(now).values_list("onu_id", flat=True))
    traffic = {
        (r["olt_id"], r["pon_port"]): r
        for r in PortHourly.objects.filter(hour__gte=hour_ago - timedelta(hours=1))
        .values("olt_id", "pon_port").annotate(rx=Sum("rx_bytes"), tx=Sum("tx_bytes"),
                                                peak_rx=Max("peak_rx_bps"), peak_tx=Max("peak_tx_bps"))
    }
    open_outages = defaultdict(int)
    for o in FiberOutage.objects.filter(ended_at__isnull=True).values("olt_id", "pon_port"):
        open_outages[(o["olt_id"], o["pon_port"])] += 1

    olts = []
    totals = defaultdict(int)
    for olt in Olt.objects.filter(is_active=True):
        ports = {p: defaultdict(int) for p in range(1, olt.pon_ports + 1)}
        for onu in Onu.objects.filter(olt=olt, present=True).only(
                "pk", "pon_port", "status", "rx_power_dbm", "first_seen"):
            port = ports.setdefault(onu.pon_port, defaultdict(int))
            port["total"] += 1
            port[onu.status] += 1
            if onu.status == Onu.STATUS_ONLINE:
                port[signal_band(onu.rx_power_dbm)] += 1
                if onu.pk not in linked_ids and onu.first_seen < now - timedelta(days=1):
                    port["unlinked"] += 1
            if onu.first_seen >= now - timedelta(days=1):
                port["new"] += 1
        port_rows = []
        for p, c in sorted(ports.items()):
            t = traffic.get((olt.pk, p), {})
            peak = max(t.get("peak_rx") or 0, t.get("peak_tx") or 0)
            row = {
                "pon_port": p, "total": c["total"], "online": c[Onu.STATUS_ONLINE],
                "offline": c[Onu.STATUS_OFFLINE], "unknown_status": c[Onu.STATUS_UNKNOWN],
                "good": c[GOOD], "warning": c[WARNING], "critical": c[CRITICAL],
                "overload": c[OVERLOAD], "power_unknown": c[UNKNOWN],
                "unlinked": c["unlinked"], "new": c["new"],
                "open_outages": open_outages.get((olt.pk, p), 0),
                "peak_mbps": round(peak / 1e6, 1) if peak else None,
                "utilisation_pct": round(100 * peak / (olt.pon_capacity_mbps * 1e6), 1) if peak else None,
            }
            port_rows.append(row)
            for k in ("total", "online", "offline", "warning", "critical", "unlinked"):
                totals[k] += row[k]
        olts.append({
            "id": olt.pk, "name": olt.name, "vendor": olt.get_vendor_display(), "model": olt.model,
            "mgmt_ip": olt.mgmt_ip, "reachable": olt.is_reachable, "last_polled_at": olt.last_polled_at,
            "last_ok_at": olt.last_ok_at, "last_error": olt.last_error,
            "uplink": olt.uplink_interface, "router": olt.router.name if olt.router_id else None,
            "ports": port_rows,
        })
    worst = [onu_row(o, linked_ids) for o in Onu.objects.filter(
        present=True, status=Onu.STATUS_ONLINE, rx_power_dbm__lt=WARN_AT)
        .select_related("olt").order_by("rx_power_dbm")[:15]]
    return {
        "as_of": now, "olts": olts, "totals": dict(totals),
        "open_outages": FiberOutage.objects.filter(ended_at__isnull=True).count(),
        "open_tickets": RepairTicket.objects.exclude(status=RepairTicket.STATUS_RESOLVED).count(),
        "degrading": Onu.objects.filter(present=True, degrading=True).count(),
        "worst_signal": worst,
    }


def onu_row(o, linked_ids=None):
    return {
        "id": o.pk, "olt": o.olt.name, "olt_id": o.olt_id, "pon_port": o.pon_port,
        "onu_index": o.onu_index, "position": o.position, "label": str(o), "mac": o.mac_address,
        "status": o.status, "distance_m": o.distance_m, "rx_power_dbm": o.rx_power_dbm,
        "tx_power_dbm": o.tx_power_dbm, "olt_rx_power_dbm": o.olt_rx_power_dbm,
        "temperature_c": o.temperature_c, "band": signal_band(o.rx_power_dbm),
        "last_down_reason": o.last_down_reason, "last_down_at": o.last_down_at,
        "last_up_at": o.last_up_at, "degrading": o.degrading, "trend_db_per_day": o.trend_db_per_day,
        "located": o.latitude is not None, "latitude": o.latitude, "longitude": o.longitude,
        "splitter": o.splitter.name if o.splitter_id else None,
        "linked": (o.pk in linked_ids) if linked_ids is not None else None,
        "present": o.present, "first_seen": o.first_seen,
    }


# ------------------------------------------------------------------ Phase 4

def _hour(dt):
    return dt.replace(minute=0, second=0, microsecond=0)


def rollup_hour(hour_start):
    """Fold one hour of raw readings into OnuHourly and PortHourly. Idempotent."""
    hour_end = hour_start + timedelta(hours=1)
    readings = (OnuReading.objects.filter(at__gte=hour_start, at__lt=hour_end)
                .select_related("onu").order_by("at"))
    per_onu = defaultdict(list)
    for r in readings:
        per_onu[r.onu].append(r)
    port_rates = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    port_online = defaultdict(lambda: defaultdict(int))
    port_bytes = defaultdict(lambda: [0, 0])

    for onu, rows in per_onu.items():
        rx_vals = [r.rx_power_dbm for r in rows if r.rx_power_dbm is not None]
        peak_rx = peak_tx = 0
        rx_bytes = tx_bytes = 0
        for r in rows:
            key = (onu.olt_id, onu.pon_port)
            if r.online:
                port_online[key][r.at] += 1
            if r.seconds:
                if r.rx_delta:
                    rate = r.rx_delta * 8 // r.seconds
                    peak_rx = max(peak_rx, rate)
                    port_rates[key][r.at][0] += rate
                if r.tx_delta:
                    rate = r.tx_delta * 8 // r.seconds
                    peak_tx = max(peak_tx, rate)
                    port_rates[key][r.at][1] += rate
            rx_bytes += r.rx_delta or 0
            tx_bytes += r.tx_delta or 0
        port_bytes[(onu.olt_id, onu.pon_port)][0] += rx_bytes
        port_bytes[(onu.olt_id, onu.pon_port)][1] += tx_bytes
        OnuHourly.objects.update_or_create(
            onu=onu, hour=hour_start,
            defaults={
                "tenant_id": onu.tenant_id, "samples": len(rows),
                "online_samples": sum(1 for r in rows if r.online),
                "rx_power_avg": round(sum(rx_vals) / len(rx_vals), 2) if rx_vals else None,
                "rx_power_min": min(rx_vals) if rx_vals else None,
                "rx_bytes": rx_bytes, "tx_bytes": tx_bytes,
                "peak_rx_bps": peak_rx, "peak_tx_bps": peak_tx,
            })

    tenants = {o.olt_id: o.tenant_id for o in per_onu}
    for key, (rxb, txb) in port_bytes.items():
        rates = port_rates.get(key, {})
        online = port_online.get(key, {})
        PortHourly.objects.update_or_create(
            olt_id=key[0], pon_port=key[1], hour=hour_start,
            defaults={
                "tenant_id": tenants[key[0]], "rx_bytes": rxb, "tx_bytes": txb,
                "peak_rx_bps": max((v[0] for v in rates.values()), default=0),
                "peak_tx_bps": max((v[1] for v in rates.values()), default=0),
                "onus_online_avg": round(sum(online.values()) / len(online), 1) if online else 0,
            })
    return len(per_onu)


def slope_per_day(points):
    """Least-squares slope of [(datetime, value)] in value units per day."""
    if len(points) < 2:
        return None
    t0 = points[0][0]
    xs = [(t - t0).total_seconds() / 86400 for t, _ in points]
    ys = [v for _, v in points]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


TREND_DAYS = 14
TREND_MIN_POINTS = 48
TREND_SLOPE = -0.15   # dB per day
TREND_DROP = 1.5      # dB, first three days against the last day


def trend_sweep(now=None):
    """Flag ONUs whose received light is sliding, before they drop."""
    now = now or timezone.now()
    since = now - timedelta(days=TREND_DAYS)
    flagged = cleared = 0
    for onu in Onu.objects.filter(present=True).select_related("olt"):
        pts = list(OnuHourly.objects.filter(onu=onu, hour__gte=since, rx_power_avg__isnull=False)
                   .order_by("hour").values_list("hour", "rx_power_avg"))
        if len(pts) < TREND_MIN_POINTS:
            continue
        slope = slope_per_day(pts)
        early = [v for t, v in pts if t < pts[0][0] + timedelta(days=3)]
        late = [v for t, v in pts if t >= now - timedelta(days=1)] or [pts[-1][1]]
        drop = sum(early) / len(early) - sum(late) / len(late)
        degrading = slope is not None and (
            (slope <= TREND_SLOPE and drop >= TREND_DROP)
            or (sum(late) / len(late) < CRITICAL_AT + 1 and drop >= 1.0))
        onu.trend_db_per_day = round(slope, 3) if slope is not None else None
        if degrading and not onu.degrading:
            onu.degrading, onu.degrading_since = True, now
            flagged += 1
            if not RepairTicket.objects.filter(onu=onu, kind=RepairTicket.KIND_DEGRADING) \
                    .exclude(status=RepairTicket.STATUS_RESOLVED).exists():
                RepairTicket.objects.create(
                    tenant_id=onu.tenant_id, kind=RepairTicket.KIND_DEGRADING, olt=onu.olt, onu=onu,
                    title=f"Signal sliding: {onu} ({onu.olt.name} {onu.position})",
                    detail=(f"Received power fell {drop:.1f} dB over {TREND_DAYS} days "
                            f"({slope:.2f} dB/day), now {sum(late) / len(late):.1f} dBm. "
                            f"Likely a dirty connector or a bending cable; it has not dropped yet."
                            + (f" Fiber distance {onu.distance_m} m." if onu.distance_m else "")))
        elif not degrading and onu.degrading:
            onu.degrading, onu.degrading_since = False, None
            cleared += 1
        onu.save(update_fields=["trend_db_per_day", "degrading", "degrading_since"])
    return {"flagged": flagged, "cleared": cleared}


def onu_history(onu, days=14):
    since = timezone.now() - timedelta(days=days)
    rows = OnuHourly.objects.filter(onu=onu, hour__gte=since).order_by("hour")
    return [{"hour": r.hour, "rx_power_avg": r.rx_power_avg, "rx_power_min": r.rx_power_min,
             "online_pct": round(100 * r.online_samples / r.samples, 1) if r.samples else None,
             "rx_mbps": round(r.peak_rx_bps / 1e6, 2), "tx_mbps": round(r.peak_tx_bps / 1e6, 2),
             "rx_bytes": r.rx_bytes, "tx_bytes": r.tx_bytes} for r in rows]


def uptime(qs_hourly):
    agg = qs_hourly.aggregate(s=Sum("samples"), o=Sum("online_samples"))
    return round(100 * agg["o"] / agg["s"], 2) if agg["s"] else None


def port_report(start, end, now=None):
    """Per PON port: customers, uptime, traffic, peak load, revenue, outages."""
    now = now or timezone.now()
    links = linking.fresh_links(now).select_related("onu")
    customers_by_port = defaultdict(set)
    for l in links:
        customers_by_port[(l.onu.olt_id, l.onu.pon_port)].add(l.customer_id)
    rows = []
    for olt in Olt.objects.filter(is_active=True):
        for p in range(1, olt.pon_ports + 1):
            key = (olt.pk, p)
            cust = customers_by_port.get(key, set())
            hourly = OnuHourly.objects.filter(onu__olt=olt, onu__pon_port=p, hour__gte=start, hour__lt=end)
            ph = PortHourly.objects.filter(olt=olt, pon_port=p, hour__gte=start, hour__lt=end).aggregate(
                rx=Sum("rx_bytes"), tx=Sum("tx_bytes"), peak_rx=Max("peak_rx_bps"), peak_tx=Max("peak_tx_bps"))
            revenue = Payment.objects.filter(customer_id__in=cust, paid_at__gte=start, paid_at__lt=end) \
                .aggregate(t=Sum("amount"))["t"] or Decimal("0")
            outs = FiberOutage.objects.filter(olt=olt, pon_port=p, started_at__gte=start, started_at__lt=end) \
                .exclude(cause__in=(FiberOutage.CAUSE_CUSTOMER_POWER,))
            down_minutes = sum(int(o.duration.total_seconds() // 60) for o in outs)
            peak = max(ph["peak_rx"] or 0, ph["peak_tx"] or 0)
            rows.append({
                "olt": olt.name, "olt_id": olt.pk, "pon_port": p,
                "onus": Onu.objects.filter(olt=olt, pon_port=p, present=True).count(),
                "customers": len(cust), "revenue": float(revenue),
                "uptime_pct": uptime(hourly),
                "rx_gb": round((ph["rx"] or 0) / 1e9, 2), "tx_gb": round((ph["tx"] or 0) / 1e9, 2),
                "peak_mbps": round(peak / 1e6, 1),
                "peak_utilisation_pct": round(100 * peak / (olt.pon_capacity_mbps * 1e6), 1),
                "outages": outs.count(), "outage_minutes": down_minutes,
            })
    return rows


def monthly_report(year, month):
    tz = timezone.get_current_timezone()
    from datetime import datetime

    start = timezone.make_aware(datetime(year, month, 1), tz)
    end = timezone.make_aware(datetime(year + (month == 12), month % 12 + 1, 1), tz)
    now = min(timezone.now(), end)
    ports = port_report(start, end, now)
    outs = FiberOutage.objects.filter(started_at__gte=start, started_at__lt=end)
    by_cause = {r["cause"]: r["n"] for r in outs.values("cause").annotate(n=Count("id"))}
    bands = defaultdict(int)
    for rx in Onu.objects.filter(present=True, status=Onu.STATUS_ONLINE).values_list("rx_power_dbm", flat=True):
        bands[signal_band(rx)] += 1
    tickets = RepairTicket.objects.filter(created_at__gte=start, created_at__lt=end)
    from ..models import CompensationGrant

    grants = CompensationGrant.objects.filter(granted_at__gte=start, granted_at__lt=end)
    return {
        "month": f"{year}-{month:02d}",
        "ports": ports,
        "totals": {
            "onus": sum(p["onus"] for p in ports), "customers": sum(p["customers"] for p in ports),
            "revenue": sum(p["revenue"] for p in ports),
            "uptime_pct": uptime(OnuHourly.objects.filter(hour__gte=start, hour__lt=end)),
            "outage_minutes": sum(p["outage_minutes"] for p in ports),
        },
        "outages_by_cause": by_cause,
        "signal_now": dict(bands),
        "degrading_now": Onu.objects.filter(present=True, degrading=True).count(),
        "tickets": {"opened": tickets.count(),
                    "resolved": tickets.filter(status=RepairTicket.STATUS_RESOLVED).count()},
        "compensation": {"grants": grants.count(),
                         "minutes": int((grants.aggregate(s=Sum("seconds"))["s"] or 0) // 60)},
    }


def prune(now=None):
    now = now or timezone.now()
    raw = OnuReading.objects.filter(at__lt=now - OnuReading.RETENTION).delete()[0]
    hourly = OnuHourly.objects.filter(hour__lt=now - OnuHourly.RETENTION).delete()[0]
    ports = PortHourly.objects.filter(hour__lt=now - OnuHourly.RETENTION).delete()[0]
    return {"readings": raw, "hourly": hourly, "port_hourly": ports}
