"""
Smart Mapping's API. Reads only stored rows -- see analytics.py for why no
request ever talks to an OLT, with one exception: "poll now" and "test", which
an admin presses on purpose and which run in a worker, not in the request.
"""

from datetime import timedelta

from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from billing.models import Customer
from billing.permissions import IsTenantAdmin, IsTenantAdminOrReadOnlyMember, IsTenantMember
from billing.throttles import HotspotPollThrottle

from .models import FiberOutage, MaintenanceWindow, Olt, Onu, OnuCustomer, RepairTicket, Splitter
from .serializers import MaintenanceSerializer, OltSerializer, SplitterSerializer, TicketSerializer
from .services import analytics, line_check, linking, notify, splitters


def _int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


class OverviewView(APIView):
    permission_classes = [IsTenantMember]

    def get(self, request):
        return Response(analytics.overview())


# ------------------------------------------------------------------ OLTs

class OltListView(APIView):
    permission_classes = [IsTenantAdminOrReadOnlyMember]

    def get(self, request):
        return Response(OltSerializer(Olt.objects.select_related("router"), many=True).data)

    def post(self, request):
        ser = OltSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        olt = ser.save()
        return Response(OltSerializer(olt).data, status=status.HTTP_201_CREATED)


class OltDetailView(APIView):
    permission_classes = [IsTenantAdminOrReadOnlyMember]

    def get(self, request, pk):
        return Response(OltSerializer(get_object_or_404(Olt, pk=pk)).data)

    def patch(self, request, pk):
        ser = OltSerializer(get_object_or_404(Olt, pk=pk), data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        return Response(OltSerializer(ser.save()).data)

    def delete(self, request, pk):
        get_object_or_404(Olt, pk=pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class OltPollView(APIView):
    """Poll now, in a worker. The result lands on the next overview refresh."""

    permission_classes = [IsTenantAdmin]

    def post(self, request, pk):
        from .tasks import poll_single_olt_task

        olt = get_object_or_404(Olt, pk=pk)
        poll_single_olt_task.delay(olt.pk)
        return Response({"queued": True})


# ------------------------------------------------------------------ ONUs

class OnuListView(APIView):
    permission_classes = [IsTenantMember]

    def get(self, request):
        q = request.query_params
        now = timezone.now()
        qs = Onu.objects.select_related("olt", "splitter")
        if q.get("present", "1") != "all":
            qs = qs.filter(present=True)
        if _int(q.get("olt")):
            qs = qs.filter(olt_id=_int(q.get("olt")))
        if _int(q.get("port")):
            qs = qs.filter(pon_port=_int(q.get("port")))
        if q.get("status") in (Onu.STATUS_ONLINE, Onu.STATUS_OFFLINE, Onu.STATUS_UNKNOWN):
            qs = qs.filter(status=q["status"])
        band = q.get("band")
        if band == analytics.CRITICAL:
            qs = qs.filter(rx_power_dbm__lt=analytics.CRITICAL_AT)
        elif band == analytics.WARNING:
            qs = qs.filter(rx_power_dbm__lt=analytics.WARN_AT, rx_power_dbm__gte=analytics.CRITICAL_AT)
        elif band == analytics.GOOD:
            qs = qs.filter(rx_power_dbm__gte=analytics.WARN_AT, rx_power_dbm__lte=analytics.OVERLOAD_AT)
        if q.get("degrading") == "1":
            qs = qs.filter(degrading=True)
        if q.get("unlocated") == "1":
            qs = qs.filter(latitude__isnull=True)
        linked_ids = set(linking.fresh_links(now).values_list("onu_id", flat=True))
        if q.get("unlinked") == "1":
            qs = qs.exclude(pk__in=linked_ids)
        term = (q.get("q") or "").strip()
        if term:
            cust_onus = linking.fresh_links(now).filter(
                Q(customer__full_name__icontains=term) | Q(customer__phone__icontains=term)
            ).values_list("onu_id", flat=True)
            qs = qs.filter(Q(label__icontains=term) | Q(olt_description__icontains=term)
                           | Q(mac_address__icontains=term.upper()) | Q(location_note__icontains=term)
                           | Q(pk__in=cust_onus))
        order = {"signal": "rx_power_dbm", "distance": "-distance_m", "position": "pon_port"}.get(
            q.get("sort"), "pon_port")
        qs = qs.order_by(order, "pon_port", "onu_index")
        limit = min(_int(q.get("limit"), 500), 2000)
        return Response([analytics.onu_row(o, linked_ids) for o in qs[:limit]])


class OnuDetailView(APIView):
    permission_classes = [IsTenantMember]

    def get(self, request, pk):
        onu = get_object_or_404(Onu.objects.select_related("olt", "splitter"), pk=pk)
        now = timezone.now()
        links = linking.fresh_links(now).filter(onu=onu).select_related("customer")
        row = analytics.onu_row(onu, {onu.pk} if links.exists() else set())
        row.update({
            "olt_description": onu.olt_description, "label_raw": onu.label, "serial": onu.serial,
            "model": onu.model, "voltage_v": onu.voltage_v, "location_note": onu.location_note,
            "located_at": onu.located_at, "located_by": onu.located_by, "splitter_id": onu.splitter_id,
            "customers": [{"id": l.customer_id, "name": l.customer.full_name, "phone": l.customer.phone,
                           "status": l.customer.status, "device_mac": l.device_mac, "source": l.source,
                           "last_seen": l.last_seen} for l in links],
            "history": analytics.onu_history(onu, _int(request.query_params.get("days"), 14)),
            "outages": [_outage_row(o) for o in onu.outages.order_by("-started_at")[:10]],
            "tickets": TicketSerializer(onu.tickets.order_by("-created_at")[:10], many=True).data,
        })
        return Response(row)

    def patch(self, request, pk):
        onu = get_object_or_404(Onu, pk=pk)
        if "label" in request.data:
            onu.label = (request.data.get("label") or "")[:128]
        if "splitter" in request.data:
            sid = _int(request.data.get("splitter"))
            onu.splitter = get_object_or_404(Splitter, pk=sid, olt=onu.olt) if sid else None
        onu.save(update_fields=["label", "splitter"])
        return Response({"ok": True})


class OnuLocateView(APIView):
    """The technician's 'Save location' button. Staff may do it: it is field work."""

    permission_classes = [IsTenantMember]

    def post(self, request, pk):
        onu = get_object_or_404(Onu, pk=pk)
        try:
            lat = float(request.data.get("latitude"))
            lng = float(request.data.get("longitude"))
        except (TypeError, ValueError):
            return Response({"detail": "latitude and longitude are required."}, status=400)
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return Response({"detail": "That is not a position on Earth."}, status=400)
        onu.latitude, onu.longitude = lat, lng
        onu.location_note = (request.data.get("note") or onu.location_note)[:255]
        onu.located_at = timezone.now()
        onu.located_by = getattr(request.user, "username", "")[:150]
        sid = _int(request.data.get("splitter"))
        if sid:
            onu.splitter = get_object_or_404(Splitter, pk=sid, olt=onu.olt)
        if request.data.get("label"):
            onu.label = request.data["label"][:128]
        onu.save()
        return Response(analytics.onu_row(onu))


class OnuLinkView(APIView):
    """Tie a customer to an ONU by hand -- PPPoE households, whose MAC billing never sees."""

    permission_classes = [IsTenantAdmin]

    def post(self, request, pk):
        onu = get_object_or_404(Onu, pk=pk)
        customer = get_object_or_404(Customer, pk=_int(request.data.get("customer")))
        link, _ = OnuCustomer.objects.update_or_create(
            onu=onu, customer=customer,
            defaults={"source": OnuCustomer.SOURCE_MANUAL, "last_seen": timezone.now()})
        return Response({"ok": True, "link": link.pk})

    def delete(self, request, pk):
        OnuCustomer.objects.filter(onu_id=pk, customer_id=_int(request.query_params.get("customer"))).delete()
        return Response(status=204)


# ------------------------------------------------------------------ map & splitters

class MapView(APIView):
    permission_classes = [IsTenantMember]

    def get(self, request):
        now = timezone.now()
        linked = set(linking.fresh_links(now).values_list("onu_id", flat=True))
        open_onus = set(FiberOutage.objects.filter(ended_at__isnull=True).values_list("onus", flat=True))
        onus = Onu.objects.filter(present=True).select_related("olt", "splitter")
        return Response({
            "olts": [{"id": o.pk, "name": o.name, "lat": o.latitude, "lng": o.longitude,
                      "reachable": o.is_reachable} for o in Olt.objects.filter(is_active=True)],
            "splitters": [{"id": s.pk, "name": s.name, "olt_id": s.olt_id, "pon_port": s.pon_port,
                           "lat": s.latitude, "lng": s.longitude, "parent": s.parent_id, "ratio": s.ratio}
                          for s in Splitter.objects.all()],
            "onus": [{**analytics.onu_row(o, linked), "splitter_id": o.splitter_id,
                      "in_outage": o.pk in open_onus} for o in onus],
        })


class SplitterListView(APIView):
    permission_classes = [IsTenantAdminOrReadOnlyMember]

    def get(self, request):
        qs = Splitter.objects.all()
        if _int(request.query_params.get("olt")):
            qs = qs.filter(olt_id=_int(request.query_params.get("olt")))
        return Response(SplitterSerializer(qs, many=True).data)

    def post(self, request):
        ser = SplitterSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        splitter = ser.save()
        ids = request.data.get("onu_ids") or []
        if ids:
            Onu.objects.filter(pk__in=ids, olt=splitter.olt, pon_port=splitter.pon_port).update(splitter=splitter)
        return Response(SplitterSerializer(splitter).data, status=201)


class SplitterDetailView(APIView):
    permission_classes = [IsTenantAdminOrReadOnlyMember]

    def patch(self, request, pk):
        ser = SplitterSerializer(get_object_or_404(Splitter, pk=pk), data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        return Response(SplitterSerializer(ser.save()).data)

    def delete(self, request, pk):
        get_object_or_404(Splitter, pk=pk).delete()
        return Response(status=204)


class SplitterSuggestView(APIView):
    permission_classes = [IsTenantMember]

    def get(self, request):
        out = []
        olt_id = _int(request.query_params.get("olt"))
        for olt in Olt.objects.filter(is_active=True, **({"pk": olt_id} if olt_id else {})):
            out.extend(splitters.suggest(olt, _int(request.query_params.get("port"))))
        return Response(out)


# ------------------------------------------------------------------ outages, maintenance, tickets

def _outage_row(o):
    return {
        "id": o.pk, "olt": o.olt.name, "olt_id": o.olt_id, "pon_port": o.pon_port, "scope": o.scope,
        "cause": o.cause, "cause_label": o.get_cause_display(), "started_at": o.started_at,
        "ended_at": o.ended_at, "minutes": int(o.duration.total_seconds() // 60),
        "onus_affected": o.onus_affected, "customers_affected": o.customers_affected,
        "revenue_at_risk_per_day": float(o.revenue_at_risk_per_day), "nearest_m": o.nearest_m,
        "farthest_m": o.farthest_m, "farthest_unaffected_m": o.farthest_unaffected_m,
        "notified_count": o.notified_count, "notified_at": o.notified_at,
        "compensated_count": o.compensated_count, "compensated_at": o.compensated_at,
        "maintenance": o.maintenance_id,
    }


class OutageListView(APIView):
    permission_classes = [IsTenantMember]

    def get(self, request):
        days = min(_int(request.query_params.get("days"), 30), 366)
        since = timezone.now() - timedelta(days=days)
        qs = FiberOutage.objects.filter(Q(ended_at__isnull=True) | Q(started_at__gte=since)) \
            .select_related("olt").order_by("ended_at", "-started_at")
        if request.query_params.get("include_customer_power") != "1":
            qs = qs.exclude(cause=FiberOutage.CAUSE_CUSTOMER_POWER)
        return Response([_outage_row(o) for o in qs[:300]])


class OutageDetailView(APIView):
    permission_classes = [IsTenantMember]

    def get(self, request, pk):
        o = get_object_or_404(FiberOutage.objects.select_related("olt"), pk=pk)
        now = timezone.now()
        onus = list(o.onus.select_related("olt"))
        customers = linking.customers_for(onus, now)
        return Response({**_outage_row(o),
                         "onus": [analytics.onu_row(x) for x in onus],
                         "customers": [{"id": c.pk, "name": c.full_name, "phone": c.phone} for c in customers[:500]]})


class MaintenanceListView(APIView):
    permission_classes = [IsTenantAdminOrReadOnlyMember]

    def get(self, request):
        return Response(MaintenanceSerializer(MaintenanceWindow.objects.select_related("olt")[:100], many=True).data)

    def post(self, request):
        ser = MaintenanceSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        window = ser.save(created_by=getattr(request.user, "username", ""))
        if request.data.get("notify", True) not in (False, "false", "0", 0):
            notify.maintenance_announcement(window, timezone.now())
        return Response(MaintenanceSerializer(window).data, status=201)


class MaintenanceDetailView(APIView):
    permission_classes = [IsTenantAdmin]

    def delete(self, request, pk):
        get_object_or_404(MaintenanceWindow, pk=pk).delete()
        return Response(status=204)


class MaintenanceNotifyView(APIView):
    permission_classes = [IsTenantAdmin]

    def post(self, request, pk):
        window = get_object_or_404(MaintenanceWindow, pk=pk)
        sent = notify.maintenance_announcement(window, timezone.now())
        return Response({"sent": sent})


class TicketListView(APIView):
    permission_classes = [IsTenantMember]

    def get(self, request):
        qs = RepairTicket.objects.select_related("olt", "onu")
        st = request.query_params.get("status")
        if st == "open":
            qs = qs.exclude(status=RepairTicket.STATUS_RESOLVED)
        elif st:
            qs = qs.filter(status=st)
        return Response(TicketSerializer(qs[:300], many=True).data)

    def post(self, request):
        ser = TicketSerializer(data={**request.data, "kind": RepairTicket.KIND_MANUAL})
        ser.is_valid(raise_exception=True)
        return Response(TicketSerializer(ser.save()).data, status=201)


class TicketDetailView(APIView):
    permission_classes = [IsTenantMember]

    def patch(self, request, pk):
        ticket = get_object_or_404(RepairTicket, pk=pk)
        for field in ("status", "assigned_to", "resolution"):
            if field in request.data:
                setattr(ticket, field, request.data[field] or "")
        if ticket.status not in dict(RepairTicket.STATUS_CHOICES):
            return Response({"detail": "Unknown status."}, status=400)
        if ticket.status == RepairTicket.STATUS_RESOLVED and not ticket.resolved_at:
            ticket.resolved_at = timezone.now()
        elif ticket.status != RepairTicket.STATUS_RESOLVED:
            ticket.resolved_at = None
        ticket.save()
        return Response(TicketSerializer(ticket).data)


# ------------------------------------------------------------------ reports

class PortReportView(APIView):
    permission_classes = [IsTenantMember]

    def get(self, request):
        days = min(max(_int(request.query_params.get("days"), 30), 1), 366)
        now = timezone.now()
        return Response(analytics.port_report(now - timedelta(days=days), now, now))


class MonthlyReportView(APIView):
    permission_classes = [IsTenantMember]

    def get(self, request):
        raw = request.query_params.get("month") or timezone.localtime().strftime("%Y-%m")
        try:
            year, month = (int(x) for x in raw.split("-"))
            assert 1 <= month <= 12
        except (ValueError, AssertionError):
            return Response({"detail": "month must look like 2026-10"}, status=400)
        return Response(analytics.monthly_report(year, month))


# ------------------------------------------------------------------ captive portal

class LineCheckView(APIView):
    """'Check my line' on the captive portal. Identified like every portal call: MAC + ?t=."""

    permission_classes = [AllowAny]
    throttle_classes = [HotspotPollThrottle]

    def get(self, request):
        from billing.views import _hotspot_customer_for

        mac = request.query_params.get("mac")
        if not mac:
            return Response({"detail": "MAC address is required"}, status=400)
        customer = _hotspot_customer_for(request, mac)
        if customer is None:
            return Response({"status": "unknown",
                             "message": "We could not find your account. Buy a package or log in first."})
        from billing.tenancy import tenant_context

        with tenant_context(customer.tenant_id):
            return Response(line_check.check(customer))
