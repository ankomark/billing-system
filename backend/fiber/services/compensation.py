"""
Give customers back the time an outage took from them.

Billing clamps a subscription's expiry to what its package sells, measured
from start_date (Subscription.save). Pushing expiry_date alone would be undone
on the next save, so a grant moves the whole window: start_date and
expiry_date both shift by the outage's length, in one UPDATE, and the package
still sells exactly what it sold. The router is then re-provisioned so the
hotspot account's limit-uptime matches the new expiry.

Opt-in per operator (SystemSetting FIBER_COMPENSATE, default on) and only for
outages that lasted COMPENSATE_MIN_MINUTES (default 10). Each grant is a
CompensationGrant row with a unique constraint, so a retry after a crash can
never give the same customer the same outage twice.
"""

import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import F

from billing.config import get_setting
from billing.models import Subscription

from ..models import CompensationGrant
from . import linking

logger = logging.getLogger(__name__)


def enabled(tenant_id):
    return (get_setting("FIBER_COMPENSATE", "true", tenant=tenant_id) or "").lower() in ("1", "true", "yes", "on")


def min_seconds(tenant_id):
    try:
        return int(get_setting("FIBER_COMPENSATE_MIN_MINUTES", "10", tenant=tenant_id)) * 60
    except (TypeError, ValueError):
        return 600


def _grant(subs, seconds, now, *, outage=None, maintenance=None):
    from billing.tasks.provisioning import ensure_customer_access_task

    granted = 0
    delta = timedelta(seconds=seconds)
    for sub in subs:
        try:
            with transaction.atomic():
                CompensationGrant.objects.create(
                    tenant_id=sub.tenant_id, subscription=sub, outage=outage,
                    maintenance=maintenance, seconds=seconds)
                Subscription.objects.filter(pk=sub.pk).update(
                    start_date=F("start_date") + delta, expiry_date=F("expiry_date") + delta)
                sub.refresh_from_db(fields=["expiry_date", "status"])
                # Ran out during the outage: the time we add is time they had
                # left, so the package is live again.
                if sub.status == "expired" and sub.expiry_date > now:
                    Subscription.objects.filter(pk=sub.pk).update(status="active")
        except IntegrityError:
            continue  # already granted for this incident
        granted += 1
        try:
            ensure_customer_access_task.delay(
                sub.customer_id, reason="fiber_compensation", subscription_id=sub.pk)
        except Exception:
            logger.warning("[fiber] could not queue re-provisioning for customer %s", sub.customer_id,
                           exc_info=True)
    return granted


def for_outage(outage, now):
    from .outages import entitled_subscriptions

    if not enabled(outage.tenant_id) or outage.compensated_at:
        return 0
    seconds = int(((outage.ended_at or now) - outage.started_at).total_seconds())
    if seconds < min_seconds(outage.tenant_id):
        return 0
    customers = linking.customers_for(list(outage.onus.all()), now)
    subs = list(entitled_subscriptions(customers, outage.started_at))
    granted = _grant(subs, seconds, now, outage=outage)
    outage.compensated_at = now
    outage.compensated_count = granted
    outage.save(update_fields=["compensated_at", "compensated_count"])
    return granted


def for_maintenance(window, now):
    from .outages import entitled_subscriptions
    from ..models import Onu

    if not window.compensate or window.compensated_at:
        return 0
    onus = Onu.objects.filter(olt=window.olt)
    if window.pon_port is not None:
        onus = onus.filter(pon_port=window.pon_port)
    customers = linking.customers_for(list(onus), now)
    seconds = int((window.ends_at - window.starts_at).total_seconds())
    subs = list(entitled_subscriptions(customers, window.starts_at))
    granted = _grant(subs, seconds, now, maintenance=window)
    window.compensated_at = now
    window.save(update_fields=["compensated_at"])
    return granted
