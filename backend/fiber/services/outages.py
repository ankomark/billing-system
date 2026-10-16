"""
Turn "these ONUs went dark" into incidents an operator can act on.

Grouping. ONUs that drop on the same PON port within GROUP_WINDOW belong to
one incident. PORT_MIN of them, or half the port, makes it a port outage; fewer
are each their own single-ONU incident. An OLT that stops answering is one
OLT-wide incident covering every ONU it was serving.

Cause, from what the ONUs said as they went:
  - mostly LOS (light gone)            -> fiber_cut: ours to fix, a ticket, the
                                          customers told, compensated after;
  - mostly dying gasp, several at once -> power_cut: the area's mains, not us;
  - one dying gasp                     -> customer_power: their own socket;
  - OLT silent                         -> olt_down: ours (power, uplink);
  - inside a maintenance window        -> maintenance: already announced.

Customers are told only once an incident has lasted NOTIFY_AFTER, so a
thirty-second flap does not text a street. They are compensated, if the
operator has it on, once it is over and only if it lasted COMPENSATE_MIN.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from billing.models import Subscription

from ..models import FiberOutage, MaintenanceWindow, Onu, RepairTicket
from . import compensation, linking, notify

logger = logging.getLogger(__name__)

GROUP_WINDOW = timedelta(minutes=10)
PORT_MIN = 3
NOTIFY_AFTER = timedelta(minutes=5)
TICKET_SINGLE_AFTER = timedelta(minutes=30)

OURS = (FiberOutage.CAUSE_FIBER, FiberOutage.CAUSE_OLT, FiberOutage.CAUSE_UNKNOWN)


def _active_window(olt, onu, at):
    for w in MaintenanceWindow.objects.filter(olt=olt, starts_at__lte=at, ends_at__gte=at):
        if w.covers(onu, at):
            return w
    return None


def _cause(reasons, scope):
    los = sum(1 for r in reasons if r == Onu.DOWN_LOS)
    gasp = sum(1 for r in reasons if r == Onu.DOWN_DYING_GASP)
    if gasp > los and gasp * 2 >= len(reasons):
        return FiberOutage.CAUSE_POWER if scope != FiberOutage.SCOPE_ONU else FiberOutage.CAUSE_CUSTOMER_POWER
    if los and los * 2 >= len(reasons):
        return FiberOutage.CAUSE_FIBER
    return FiberOutage.CAUSE_UNKNOWN


def process(olt, went_down, came_up, now, olt_recovered=False):
    opened, closed = [], []

    if olt_recovered:
        for outage in FiberOutage.objects.filter(olt=olt, scope=FiberOutage.SCOPE_OLT, ended_at__isnull=True):
            _close(outage, now)
            closed.append(outage.pk)

    by_port = {}
    for onu, reason in went_down:
        by_port.setdefault(onu.pon_port, []).append((onu, reason))

    for port, group in by_port.items():
        present = Onu.objects.filter(olt=olt, pon_port=port, present=True).count() or len(group)
        recent = (FiberOutage.objects.filter(
            olt=olt, pon_port=port, ended_at__isnull=True, started_at__gte=now - GROUP_WINDOW)
            .exclude(scope=FiberOutage.SCOPE_OLT).order_by("-started_at").first())

        if recent and recent.scope == FiberOutage.SCOPE_PORT:
            recent.onus.add(*[o for o, _ in group])
            _refresh(recent, now)
            continue

        # Earlier single-ONU incidents on this port in the window join a new
        # port incident: three houses going dark two minutes apart is one cut.
        singles = list(FiberOutage.objects.filter(
            olt=olt, pon_port=port, scope=FiberOutage.SCOPE_ONU, ended_at__isnull=True,
            started_at__gte=now - GROUP_WINDOW))
        earlier = [o for s in singles for o in s.onus.all()]
        total = len(group) + len(earlier)

        if total >= PORT_MIN or total * 2 >= present and total >= 2:
            reasons = [r for _, r in group] + [o.last_down_reason for o in earlier]
            started = min([s.started_at for s in singles] + [now])
            outage = FiberOutage.objects.create(
                tenant_id=olt.tenant_id, olt=olt, pon_port=port, scope=FiberOutage.SCOPE_PORT,
                cause=_cause(reasons, FiberOutage.SCOPE_PORT), started_at=started)
            outage.onus.add(*[o for o, _ in group], *earlier)
            for s in singles:
                s.delete()
            _maybe_maintenance(outage, [o for o, _ in group][0], now)
            _refresh(outage, now)
            opened.append(outage.pk)
            if outage.cause in OURS:
                notify.admin(f"Fiber outage on {olt.name} PON {port}: {outage.onus_affected} ONUs down "
                             f"({outage.get_cause_display()}), {outage.customers_affected} customers.")
        else:
            for onu, reason in group:
                outage = FiberOutage.objects.create(
                    tenant_id=olt.tenant_id, olt=olt, pon_port=port, scope=FiberOutage.SCOPE_ONU,
                    cause=_cause([reason], FiberOutage.SCOPE_ONU), started_at=now)
                outage.onus.add(onu)
                _maybe_maintenance(outage, onu, now)
                _refresh(outage, now)
                opened.append(outage.pk)

    if came_up:
        up_ids = {o.pk for o in came_up}
        for outage in FiberOutage.objects.filter(olt=olt, ended_at__isnull=True, onus__in=up_ids).distinct():
            if not outage.onus.exclude(status=Onu.STATUS_ONLINE).exists():
                _close(outage, now)
                closed.append(outage.pk)

    _follow_up(olt, now)
    return {"opened": opened, "closed": closed}


def olt_unreachable(olt, now):
    if FiberOutage.objects.filter(olt=olt, scope=FiberOutage.SCOPE_OLT, ended_at__isnull=True).exists():
        return None
    since = olt.last_ok_at or now
    outage = FiberOutage.objects.create(
        tenant_id=olt.tenant_id, olt=olt, scope=FiberOutage.SCOPE_OLT,
        cause=FiberOutage.CAUSE_OLT, started_at=since)
    outage.onus.add(*Onu.objects.filter(olt=olt, present=True, status=Onu.STATUS_ONLINE))
    _refresh(outage, now)
    notify.admin(f"OLT {olt.name} is not answering: {outage.onus_affected} ONUs, "
                 f"{outage.customers_affected} customers behind it.")
    _follow_up(olt, now)
    return outage


def _maybe_maintenance(outage, onu, now):
    window = _active_window(outage.olt, onu, now)
    if window:
        outage.maintenance = window
        outage.cause = FiberOutage.CAUSE_MAINTENANCE
        outage.save(update_fields=["maintenance", "cause"])


def entitled_subscriptions(customers, at):
    """
    Paid packages that were live when the incident began.

    "expired" is included on purpose: a three-hour package can run out in the
    middle of a cut, and its owner lost the end of it to us. A package that a
    data cap ended (capped_at set) is not: the outage did not cost that one.
    """
    return (Subscription.objects.filter(
        customer__in=customers, invoice__payment_status="paid", expiry_date__gt=at,
        status__in=("active", "expired"), capped_at__isnull=True)
        .select_related("package", "customer"))


def daily_value(sub):
    length = sub.package.calculate_expiry(sub.start_date) - sub.start_date
    days = max(length.total_seconds() / 86400, 1 / 24)
    return Decimal(sub.package.price or 0) / Decimal(str(days))


def _refresh(outage, now):
    onus = list(outage.onus.all())
    customers = linking.customers_for(onus, now)
    subs = list(entitled_subscriptions(customers, outage.started_at))
    outage.onus_affected = len(onus)
    outage.customers_affected = customers.count()
    outage.revenue_at_risk_per_day = sum((daily_value(s) for s in subs), Decimal("0")).quantize(Decimal("0.01"))
    distances = [o.distance_m for o in onus if o.distance_m is not None]
    outage.nearest_m = min(distances) if distances else None
    outage.farthest_m = max(distances) if distances else None
    if outage.pon_port is not None:
        up = (Onu.objects.filter(olt=outage.olt, pon_port=outage.pon_port, status=Onu.STATUS_ONLINE,
                                 distance_m__isnull=False).exclude(pk__in=[o.pk for o in onus])
              .order_by("-distance_m").values_list("distance_m", flat=True).first())
        outage.farthest_unaffected_m = up
    outage.save()


def _close(outage, now):
    outage.ended_at = now
    outage.save(update_fields=["ended_at"])
    RepairTicket.objects.filter(outage=outage, status__in=(RepairTicket.STATUS_OPEN, RepairTicket.STATUS_ASSIGNED),
                                kind=RepairTicket.KIND_OUTAGE).update(
        status=RepairTicket.STATUS_RESOLVED, resolved_at=now, resolution="Service came back on its own")
    granted = 0
    # Ours to make good: a port or OLT incident we caused, or any cut fiber
    # (a single house's drop cable included). Not a power cut, not a
    # customer's own socket, not announced maintenance (that has its own).
    if (outage.cause in OURS and outage.scope != FiberOutage.SCOPE_ONU) or \
            outage.cause == FiberOutage.CAUSE_FIBER:
        granted = compensation.for_outage(outage, now)
    if outage.notified_at:
        notify.restored(outage, granted, now)


def _follow_up(olt, now):
    """Texts and tickets for incidents that have now lasted long enough to be real."""
    for outage in FiberOutage.objects.filter(olt=olt, ended_at__isnull=True):
        age = now - outage.started_at
        if outage.cause in OURS and outage.scope != FiberOutage.SCOPE_ONU:
            if not outage.notified_at and age >= NOTIFY_AFTER:
                notify.outage_started(outage, now)
            if not outage.tickets.exists():
                _ticket(outage)
        elif outage.cause == FiberOutage.CAUSE_FIBER and age >= TICKET_SINGLE_AFTER \
                and not outage.tickets.exists():
            _ticket(outage)


def _ticket(outage):
    where = f"{outage.olt.name}" + (f" PON {outage.pon_port}" if outage.pon_port is not None else "")
    zone = ""
    if outage.nearest_m is not None:
        zone = f" Dark ONUs from {outage.nearest_m} m to {outage.farthest_m} m along the fiber"
        if outage.farthest_unaffected_m is not None:
            zone += f"; the farthest ONU still up on this port is at {outage.farthest_unaffected_m} m"
        zone += "."
    single = outage.onus.first() if outage.scope == FiberOutage.SCOPE_ONU else None
    RepairTicket.objects.create(
        tenant_id=outage.tenant_id, kind=RepairTicket.KIND_OUTAGE, olt=outage.olt, onu=single,
        outage=outage,
        title=f"{outage.get_cause_display()}: {where}, {outage.onus_affected} ONU(s) down",
        detail=(f"Started {timezone.localtime(outage.started_at):%Y-%m-%d %H:%M}. "
                f"{outage.customers_affected} paying customers affected, "
                f"KES {outage.revenue_at_risk_per_day}/day at risk.{zone}"))
