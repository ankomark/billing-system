"""
"Is it you or me?" answered for the customer, on the captive portal.

Plain words only. A customer does not need -23.4 dBm; they need to know
whether to restart their phone, wait because the operator already knows, or
call. Nothing here reveals another customer, a location, or an address.
"""

from django.utils import timezone

from ..models import FiberOutage, Onu
from . import analytics, linking


def check(customer, now=None):
    now = now or timezone.now()
    onus = linking.onus_for_customer(customer, now)
    if not onus:
        return {"status": "unknown",
                "message": "We could not match you to a fiber link yet. If your internet is not working, contact support."}
    onu = max(onus, key=lambda o: o.last_seen)
    outage = FiberOutage.objects.filter(onus=onu, ended_at__isnull=True).exclude(
        cause=FiberOutage.CAUSE_CUSTOMER_POWER).order_by("-started_at").first()
    if outage and outage.scope != FiberOutage.SCOPE_ONU:
        since = timezone.localtime(outage.started_at)
        msg = (f"There is a network problem in your area since {since:%H:%M}. "
               f"Our team already knows and is working on it.")
        if outage.cause == FiberOutage.CAUSE_MAINTENANCE:
            msg = "Planned maintenance is under way in your area. Service will return when it ends."
        elif outage.cause == FiberOutage.CAUSE_POWER:
            msg = "There appears to be a power cut in your area. Service returns when power is back."
        return {"status": "outage", "message": msg, "since": outage.started_at}
    if onu.status == Onu.STATUS_OFFLINE:
        if onu.last_down_reason == Onu.DOWN_DYING_GASP:
            return {"status": "down", "message": "The fiber box that serves you has lost power. "
                    "If it is in your home, check it is plugged in and switched on."}
        return {"status": "down", "message": "The fiber link that serves you is down and we have been alerted. "
                "If the fiber box is in your home, check its cable is firmly plugged in and not bent."}
    band = analytics.signal_band(onu.rx_power_dbm)
    if band == analytics.CRITICAL:
        return {"status": "weak", "message": "The fiber link that serves you is connected but its signal is very weak. "
                "We have been alerted; a technician may need to visit."}
    if band in (analytics.WARNING, analytics.OVERLOAD):
        return {"status": "fair", "message": "The fiber link that serves you is working, with a slightly weak signal. "
                "If browsing is slow, restart your phone's Wi-Fi first."}
    return {"status": "ok", "message": "The fiber link that serves you is healthy. "
            "If something is slow, try turning your phone's Wi-Fi off and on."}
