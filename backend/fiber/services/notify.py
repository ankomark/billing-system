"""
Telling people. Customers about incidents that affect them; the operator about
incidents worth getting up for.

Customer texts are on by default and switched off per operator with
SystemSetting FIBER_SMS=false. They go as one bulk send per incident (one
request, not one per household) through billing's own SMS path, so they are
logged in Message Delivery and billed to the operator's own SMS account like
every other message.
"""

import logging

from django.utils import timezone

from billing.config import get_setting

from . import linking

logger = logging.getLogger(__name__)


def sms_enabled(tenant_id):
    return (get_setting("FIBER_SMS", "true", tenant=tenant_id) or "").lower() in ("1", "true", "yes", "on")


def _brand(tenant_id):
    return get_setting("BUSINESS_NAME", "", tenant=tenant_id) or "Your internet provider"


def admin(message):
    try:
        from billing.tasks.alert_tasks import notify_admin_task

        notify_admin_task.delay(message)
    except Exception:
        logger.warning("[fiber] admin alert not queued: %s", message)


def _send(tenant_id, customers, text):
    phones = sorted({c.phone for c in customers if c.phone})
    if not phones or not sms_enabled(tenant_id):
        return 0
    from billing.notifications import send_bulk_sms
    from billing.tenancy import tenant_context

    with tenant_context(tenant_id):
        result = send_bulk_sms([(p, text) for p in phones], tenant=tenant_id)
    return result.get("sent", 0) if isinstance(result, dict) else len(phones)


def outage_started(outage, now):
    from .compensation import enabled as compensating

    customers = list(linking.customers_for(list(outage.onus.all()), now))
    # Only promised when the operator actually compensates.
    promise = " The time you lose will be added back to your package." \
        if compensating(outage.tenant_id) else ""
    text = (f"{_brand(outage.tenant_id)}: we know about an internet outage in your area "
            f"and our team is on it.{promise} No need to call.")
    sent = _send(outage.tenant_id, customers, text)
    outage.notified_at = now
    outage.notified_count = sent
    outage.save(update_fields=["notified_at", "notified_count"])
    return sent


def restored(outage, granted, now):
    customers = list(linking.customers_for(list(outage.onus.all()), now))
    minutes = int(outage.duration.total_seconds() // 60)
    extra = f" We have added {minutes} minutes to your package." if granted else ""
    text = f"{_brand(outage.tenant_id)}: internet in your area is back on.{extra} Sorry for the trouble."
    sent = _send(outage.tenant_id, customers, text)
    outage.restored_notified_at = now
    outage.save(update_fields=["restored_notified_at"])
    return sent


def maintenance_announcement(window, now):
    from ..models import Onu

    onus = Onu.objects.filter(olt=window.olt)
    if window.pon_port is not None:
        onus = onus.filter(pon_port=window.pon_port)
    customers = list(linking.customers_for(list(onus), now))
    start = timezone.localtime(window.starts_at)
    end = timezone.localtime(window.ends_at)
    when = f"{start:%a %d %b %H:%M}-{end:%H:%M}" if start.date() == end.date() \
        else f"{start:%a %d %b %H:%M} to {end:%a %d %b %H:%M}"
    extra = " The time will be added back to your package." if window.compensate else ""
    text = (f"{_brand(window.tenant_id)}: planned network work in your area {when} "
            f"({window.reason}). Internet may be off during this time.{extra}")
    sent = _send(window.tenant_id, customers, text)
    window.notified_at = now
    window.notified_count = sent
    window.save(update_fields=["notified_at", "notified_count"])
    return sent
