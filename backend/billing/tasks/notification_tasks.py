import logging
from celery import shared_task

from billing.notifications import send_sms_result, send_whatsapp
from billing.tenancy import tenant_context

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=10,
    retry_kwargs={"max_retries": 5},
    retry_jitter=True,
)
def send_sms_task(self, phone: str, message: str, tenant_id: int | None = None) -> bool:
    """
    One SMS, retried only where retrying could help.

    This raised on any falsy result, so every refusal was sent six times. The
    day an operator's sender ID was rejected, every message in the queue asked
    the provider the same question six times and got the same answer — load on
    them, load on us, and five later attempts hiding the first failure in the
    log behind four identical ones.

    So the provider having answered ends it. A status code is a verdict, and no
    amount of backoff turns an invalid sender ID or an unroutable number into a
    delivery; _flag_if_serious has already told the operator about the ones only
    they can fix. No code means we never got an answer — a timeout, a 502, a
    broken connection — and that is exactly what retrying is for.
    """
    # The operator travels with the task: the send resolves BlessedTexts
    # credentials via get_setting(), and a worker has no request context to
    # infer the operator from. Without this a message could be sent — and
    # billed — through another operator's account.
    with tenant_context(tenant_id):
        ok, code = send_sms_result(phone, message)

    if ok:
        logger.info(f"[sms] Sent to {phone}")
        return True

    if code is not None:
        logger.error(
            "[sms] Not delivered to %s and not retried — the provider answered "
            "%s, which will not change on a second attempt", phone, code)
        return False

    raise Exception(f"SMS delivery failed to {phone}")


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=15,
    retry_kwargs={"max_retries": 5},
    retry_jitter=True,
)
def send_whatsapp_task(self, phone: str, message: str, tenant_id: int | None = None) -> bool:
    with tenant_context(tenant_id):
        result = send_whatsapp(phone, message)
    if not result:
        raise Exception(f"WhatsApp delivery failed to {phone}")
    logger.info(f"[whatsapp] Sent to {phone}")
    return True


@shared_task
def notify_customer_task(phone: str, message: str, tenant_id: int | None = None) -> None:
    """Fan-out: each channel handles its own retries."""
    send_sms_task.delay(phone, message, tenant_id=tenant_id)
    send_whatsapp_task.delay(phone, message, tenant_id=tenant_id)
    logger.info(f"[notify] Queued SMS+WhatsApp for {phone}")


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_kwargs={"max_retries": 2},
)
def dispatch_broadcast_task(
    self,
    audience: str,
    channel: str,
    message: str,
    customer_ids: list,
    tenant_id: int | None = None,
) -> dict:
    """
    Fan-out broadcast to a filtered customer set.
    Uses .iterator() so the entire customer table is never loaded into memory.
    Called from AdminBroadcastView as a single async task, avoiding blocking
    the web worker with an N-iteration loop.
    """
    from billing.models import Customer

    # Broadcast belongs to the operator who triggered it. tenant_id is passed
    # explicitly because a worker has no request context to infer it from.
    qs = Customer.objects.all_tenants().only("id", "phone", "tenant_id")
    if tenant_id is not None:
        qs = qs.filter(tenant_id=tenant_id)

    if audience == "active":
        qs = qs.filter(status="active")
    elif audience == "expired":
        qs = qs.filter(status="expired")
    elif audience == "custom":
        qs = qs.filter(id__in=customer_ids)
    # "all" → no additional filter

    if channel == "sms":
        return _broadcast_sms(qs, message, tenant_id, audience)

    # WhatsApp has no bulk endpoint, so it stays one message per customer.
    queued = failed = 0
    for customer in qs.iterator(chunk_size=200):
        try:
            send_whatsapp_task.delay(customer.phone, message, tenant_id=customer.tenant_id)
            queued += 1
        except Exception as exc:
            failed += 1
            logger.error(f"[broadcast] Failed to queue for {customer.phone}: {exc}")

    logger.info(f"[broadcast] Queued {queued} whatsapp message(s), {failed} failed — {audience}")
    # "queued", not "sent". Dispatching a task is all this knows.
    return {"queued": queued, "failed": failed, "channel": channel, "audience": audience}


# The provider takes many messages per request. Batched rather than sent whole
# because one request carrying ten thousand recipients is a single point of
# failure and a timeout waiting to happen.
BULK_BATCH = 100


def _broadcast_sms(qs, message, tenant_id, audience):
    """
    SMS broadcast through the provider's bulk endpoint.

    This queued one Celery task per customer, so a broadcast to four hundred
    subscribers was four hundred tasks and four hundred round trips, and the
    number it reported was how many it had queued rather than how many were
    accepted. Batched now, and the count is what the provider actually took.
    """
    from billing.notifications import send_bulk_sms

    sent = failed = 0
    errors = []
    batch = []

    def flush():
        nonlocal sent, failed
        if not batch:
            return
        result = send_bulk_sms(batch, tenant=tenant_id)
        sent += result["sent"]
        failed += result["failed"]
        errors.extend(result["errors"])
        batch.clear()

    for customer in qs.iterator(chunk_size=200):
        batch.append((customer.phone, message))
        if len(batch) >= BULK_BATCH:
            flush()
    flush()

    logger.info("[broadcast] SMS: %s sent, %s failed — %s", sent, failed, audience)
    return {
        "sent": sent,
        "failed": failed,
        "channel": "sms",
        "audience": audience,
        "errors": errors[:20],
    }
