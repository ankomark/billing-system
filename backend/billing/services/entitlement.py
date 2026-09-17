"""
What entitles somebody to be on the network. One definition, in one place.

This rule was written out longhand in seven places and remembered correctly in
four of them. Each site asked some subset of the same three questions, and the
ones that asked fewer were not obviously wrong when read on their own -- the
query says `status="active"`, and a row that says active certainly looks like
an entitlement.

It is not, and the reason is structural: Subscription.save creates the row and
its unpaid invoice together, the moment somebody taps a package and before any
money moves. `active` therefore means "somebody started a checkout", not
"somebody paid". A subscription is born entitled-looking and only becomes real
when the M-Pesa callback records a Payment.

The three questions, and what each one is actually protecting against:

  status == "active"
      Not `suspended`, which is what enforce_usage_caps writes when a data
      allowance runs out -- and it leaves expiry_date alone, so a capped
      subscriber still has a future expiry. Not `expired`, which the
      abandoned-checkout sweep writes while also leaving expiry_date in the
      future. Anything checking only the date readmits both.

  invoice.payment_status == "paid"
      The money question. Only Payment.save sets this, and only the M-Pesa
      callback creates a Payment -- after checking ResultCode is 0, that a
      receipt is present, and that the amount matches the invoice. So this one
      flag is the end of a chain that starts at money actually landing.

  expiry_date > now
      The obvious one, and the only one every site remembered.

Written as "is paid" rather than "is not pending" on purpose. A payment status
added later must not quietly become grounds for service.

Call `granting_subscription(customer)` to find what to provision against, and
`is_entitled(subscription)` to judge one you already hold. Nothing that grants
network access should ask these questions itself.
"""

from django.utils import timezone

# The statuses a subscription can hold, and what each means for access.
#
# Spelled out because the previous version of this rule lived in
# voucher_service as BLOCKED_SUB_STATUSES = {"revoked", "cancelled"} -- two
# statuses this system has never had. It read as a deny-list and blocked
# nothing, so a subscription suspended for exhausting its data allowance
# passed validation and the portal told the customer their code was good.
GRANTING_STATUS = "active"


def granting_subscription(customer):
    """
    The subscription this customer should be served on, or None.

    Ordered by expiry among *paid* subscriptions, so a top-up wins over the
    package it replaces. Ordering before filtering is the bug this exists to
    prevent: an abandoned 250/- three-week prompt outranks a paid 10/-
    three-hour one purely by running longer, and provisioning against it hands
    out the package nobody paid for.
    """
    return (
        customer.subscriptions.filter(
            status=GRANTING_STATUS,
            invoice__payment_status="paid",
            expiry_date__gt=timezone.now(),
        )
        .select_related("package", "invoice")
        .order_by("-expiry_date")
        .first()
    )


def is_entitled(subscription):
    """
    Whether this particular subscription entitles anyone to anything.

    For callers that already hold a subscription -- a voucher points at one,
    so the question is not "what should this customer get" but "is the thing
    this code unlocks still good".
    """
    if subscription is None:
        return False

    if getattr(subscription, "status", None) != GRANTING_STATUS:
        return False

    expiry = getattr(subscription, "expiry_date", None)
    if not expiry or expiry <= timezone.now():
        return False

    invoice = getattr(subscription, "invoice", None)
    if invoice is None:
        # No invoice at all is not a free pass. Subscription.save creates one
        # on every row it writes, so a subscription without one was made by
        # something that bypassed the model -- which is not a reason to trust
        # it.
        return False

    return getattr(invoice, "payment_status", None) == "paid"


def entitlement_reason(subscription):
    """
    Why a subscription is not granting, for logs and operator-facing messages.

    Never shown to the person at the portal -- "your data ran out" and "your
    time ran out" are different sentences to them, and the portal composes
    those itself. This is for the operator reading a log asking why somebody
    was refused.
    """
    if subscription is None:
        return "no subscription"
    status = getattr(subscription, "status", None)
    if status == "suspended":
        return "suspended (data allowance spent)"
    if status != GRANTING_STATUS:
        return f"status is {status!r}"
    expiry = getattr(subscription, "expiry_date", None)
    if not expiry or expiry <= timezone.now():
        return "expired"
    invoice = getattr(subscription, "invoice", None)
    if invoice is None:
        return "no invoice"
    if getattr(invoice, "payment_status", None) != "paid":
        return f"invoice is {invoice.payment_status!r}, not paid"
    return "entitled"


def subscription_for_device(customer, mac_address, devices=None):
    """
    The package one of this customer's devices is on, or None.

    Each package covers the devices it was redeemed on, and nothing else. A
    customer holding a three-week package on one phone who buys three hours for
    a second phone has two packages, and each phone runs on its own -- the
    second purchase is not locked out because the first is still live, and it
    does not stretch the first phone's three weeks onto the second one either.

    Asking granting_subscription(customer) instead answered "the longest
    package" for every device. On 2026-09-15 that is what refused customer 32:
    they paid 10/- for three hours on a new phone at 16:06, the grant went to
    the three-week package on their other phone, and the new one was told
    "invalid username or password" five times and then again on a refund. The
    same answer read by the uptime sweep had already given five of that
    customer's retired handsets the three-week window.

    A device whose own package is over, or that was never bound to one, is
    served by a live package that has not claimed a device of its own yet --
    somebody who paid for their next package before typing its code, which is
    the renewal the grant has always had to carry. A package already in use on
    another device is that device's, and is not borrowed.

    `devices` is this customer's device rows when the caller already holds
    them. The uptime sweep asks this for every account on every router every
    five minutes, and reading one customer's devices back per account is the
    difference between one query and several hundred.
    """
    from billing.models import CustomerDevice
    from billing.utils import normalize_mac

    wanted = normalize_mac(mac_address)
    if not wanted:
        return None

    if devices is None:
        devices = list(
            CustomerDevice.objects.all_tenants()
            .filter(tenant_id=customer.tenant_id, customer=customer)
            .select_related("subscription__invoice", "subscription__package")
        )
    own = [d for d in devices if normalize_mac(d.mac_address) == wanted]
    if any(d.blocked for d in own):
        return None

    bound = [d.subscription for d in own
             if d.subscription_id and is_entitled(d.subscription)]
    if bound:
        return max(bound, key=lambda s: s.expiry_date)

    claimed = {d.subscription_id for d in devices
               if d.subscription_id and not d.blocked}
    return (
        customer.subscriptions.filter(
            status=GRANTING_STATUS,
            invoice__payment_status="paid",
            expiry_date__gt=timezone.now(),
        )
        .exclude(pk__in=claimed)
        .select_related("package", "invoice")
        .order_by("-expiry_date")
        .first()
    )
