"""
Billing the operators.

This is the platform's own revenue, entirely separate from the subscriber
billing every other task in this package deals with. Money here settles into
the platform's till, not an operator's.
"""

import logging
import secrets
from datetime import datetime

from celery import shared_task
from django.db import IntegrityError, transaction
from django.utils import timezone

from billing.models import Tenant, TenantInvoice, TenantSubscription
from billing.tenancy import tenant_context

logger = logging.getLogger(__name__)

# Days an operator has to pay before the invoice is overdue. Restriction itself
# is phase 6 — this only marks the invoice.
PAYMENT_TERMS_DAYS = 7


def generate_platform_invoice_number():
    """
    PINV- prefix, against the subscriber INV-.

    Both are globally unique and both are used to resolve an operator from an
    M-Pesa callback that carries no other context, so telling them apart at a
    glance matters — in a log line, a database, or a statement.
    """
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"PINV-{stamp}-{secrets.token_hex(2).upper()}"


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_kwargs={"max_retries": 3},
    retry_jitter=True,
)
def generate_tenant_invoices(self):
    """
    Issue invoices for operators whose billing period has ended.

    Idempotent: a unique constraint on (tenant, period_start) means re-running
    after a partial failure cannot double-bill anyone.
    """
    now = timezone.now()
    issued = 0

    due = (
        TenantSubscription.objects.all_tenants()
        .select_related("plan", "tenant")
        .filter(current_period_end__lte=now)
        .exclude(status="cancelled")
    )

    for subscription in due.iterator(chunk_size=100):
        # Trials are not billed until the trial actually ends.
        if subscription.trial_ends_at and subscription.trial_ends_at > now:
            continue

        try:
            with transaction.atomic():
                TenantInvoice.objects.create(
                    tenant=subscription.tenant,
                    subscription=subscription,
                    number=generate_platform_invoice_number(),
                    period_start=subscription.current_period_start,
                    period_end=subscription.current_period_end,
                    amount=subscription.plan.price,
                    due_date=now + timezone.timedelta(days=PAYMENT_TERMS_DAYS),
                )
        except IntegrityError:
            # Already invoiced for this period — the idempotency guard doing
            # its job, not an error.
            logger.info(
                "[platform-billing] %s already invoiced for period starting %s",
                subscription.tenant, subscription.current_period_start,
            )
            continue

        issued += 1
        logger.info("[platform-billing] Invoiced %s", subscription.tenant)

    logger.info("[platform-billing] Issued %s invoice(s)", issued)
    return issued


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_kwargs={"max_retries": 2},
)
def mark_overdue_tenants(self):
    """
    Flag operators who have missed their due date.

    Only moves them to `past_due` — it never restricts access. Restriction is a
    separate, deliberate step in phase 6, because cutting an operator off has
    consequences for subscribers who have done nothing wrong.
    """
    now = timezone.now()
    flagged = 0

    overdue = (
        TenantInvoice.objects.all_tenants()
        .select_related("tenant")
        .filter(status="unpaid", due_date__lt=now)
    )

    seen = set()
    for invoice in overdue.iterator(chunk_size=100):
        if invoice.tenant_id in seen:
            continue
        seen.add(invoice.tenant_id)

        tenant = invoice.tenant
        # Includes `trial`: an operator whose trial ended was invoiced like
        # anyone else, so an unpaid overdue bill is past due regardless of how
        # they started. Already past_due, restricted or cancelled operators are
        # left alone — this sweep never escalates.
        if tenant.status in ("trial", "active"):
            tenant.status = "past_due"
            tenant.save(update_fields=["status"])
            flagged += 1
            logger.warning("[platform-billing] %s is past due", tenant)

        subscription = invoice.subscription
        if subscription.status == "active":
            subscription.status = "past_due"
            subscription.save(update_fields=["status"])

    logger.info("[platform-billing] Flagged %s operator(s) past due", flagged)
    return flagged


# =====================================================
# ESCALATION
# =====================================================
# Restriction is never the first an operator hears of a problem. Reminders go
# out before the due date and repeatedly after it, and only once the grace
# period has fully elapsed does the account lock.
#
# Even then it is a lockout of the *operator*, not their subscribers. See
# Tenant.is_restricted.

# Days relative to the due date. Negative is before.
REMINDER_OFFSETS = (-3, 3, 7, 14)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_kwargs={"max_retries": 3},
)
def send_platform_billing_reminders(self):
    """Warn operators about invoices coming due, and ones already overdue."""
    from billing.notifications import notify_customer

    today = timezone.now().date()
    sent = 0

    unpaid = (
        TenantInvoice.objects.all_tenants()
        .select_related("tenant")
        .filter(status="unpaid")
    )

    for invoice in unpaid.iterator(chunk_size=100):
        offset = (today - invoice.due_date.date()).days
        if offset not in REMINDER_OFFSETS:
            continue

        tenant = invoice.tenant
        contact = tenant.contact_phone
        if not contact:
            logger.warning(
                "[platform-billing] No contact phone for %s — cannot remind", tenant
            )
            continue

        if offset < 0:
            body = (
                f"Invoice {invoice.number} for {invoice.amount} is due on "
                f"{invoice.due_date:%d %b %Y}."
            )
        else:
            remaining = Tenant.GRACE_DAYS - offset
            body = (
                f"Invoice {invoice.number} for {invoice.amount} is {offset} day(s) "
                f"overdue. Your dashboard will be locked in {max(remaining, 0)} day(s). "
                "Your customers are not affected."
            )

        try:
            # Platform-to-operator, so this deliberately uses the platform's own
            # messaging credentials — no tenant context is entered.
            notify_customer(contact, body)
            sent += 1
        except Exception:
            logger.exception("[platform-billing] Reminder failed for %s", tenant)

    logger.info("[platform-billing] Sent %s reminder(s)", sent)
    return sent


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_kwargs={"max_retries": 2},
)
def restrict_expired_grace_tenants(self):
    """
    Lock out operators whose grace period has fully elapsed.

    This is the only automatic escalation, and it stops at the operator's own
    dashboard. Their subscribers keep their internet, renewals keep working and
    every background task keeps running. Cutting subscribers off remains a
    deliberate manual decision, because they paid the operator in good faith
    and are not party to this dispute.
    """
    from billing.models import set_tenant_status

    cutoff = timezone.now() - timezone.timedelta(days=Tenant.GRACE_DAYS)
    restricted = 0

    overdue = (
        TenantInvoice.objects.all_tenants()
        .select_related("tenant")
        .filter(status="unpaid", due_date__lt=cutoff)
    )

    for tenant_id in set(overdue.values_list("tenant_id", flat=True)):
        tenant = Tenant.objects.get(id=tenant_id)
        if tenant.is_restricted:
            continue

        moved = set_tenant_status(
            tenant,
            "restricted",
            reason=(
                f"Invoice unpaid more than {Tenant.GRACE_DAYS} days past due. "
                "Operator dashboard locked; subscriber service unaffected."
            ),
            automatic=True,
        )
        if moved:
            restricted += 1
            logger.warning("[platform-billing] %s restricted", tenant)

    logger.info("[platform-billing] Restricted %s operator(s)", restricted)
    return restricted
