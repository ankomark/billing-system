import secrets
import string

from billing.models import Customer


# -----------------------------------------------------
# RANDOM PASSWORD GENERATOR
# -----------------------------------------------------

def _random_password(length=10):
    chars = string.ascii_letters + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


# -----------------------------------------------------
# GENERATE PPPoE USERNAME + PASSWORD
# -----------------------------------------------------

def generate_pppoe_credentials(customer):
    """
    Generates a PPPoE username + password.

    The prefix comes from the operator, so their subscribers get credentials
    branded as theirs rather than as another operator's. Uniqueness is checked
    within that operator only, matching the (tenant, pppoe_username) constraint
    — PPPoE usernames must be unique on a router, and routers belong to one
    operator.
    """
    prefix = (getattr(customer.tenant, "pppoe_prefix", "") or "NET").strip("-")

    # Use last 4 digits of phone OR fallback
    base = customer.phone[-4:] if customer.phone else secrets.token_hex(2)

    while True:
        # PREFIX-1234-XYZ format
        suffix = "".join(secrets.choice(string.ascii_uppercase) for _ in range(3))
        username = f"{prefix}-{base}-{suffix}"

        if not Customer.objects.all_tenants().filter(
            tenant_id=customer.tenant_id, pppoe_username=username
        ).exists():
            break

    # Secure random password
    password = _random_password(12)

    return username, password


# -----------------------------------------------------
# PROVISION NEW PPPoE USER ON MIKROTIK
# -----------------------------------------------------

def provision_pppoe_on_router(subscription):
    """
    This is called right after new subscription creation.
    Ensures PPPoE credentials are added to the router's PPP secret list.
    """

    customer = subscription.customer
    router = customer.router

    if not router:
        return  # no router linked

    if customer.connection_type != "pppoe":
        return  # customer is hotspot-based

    if not customer.pppoe_username or not customer.pppoe_password:
        return  # no credentials to provision yet

    from billing.router_service import safe_connect_router, create_pppoe_secret

    api = safe_connect_router(router)
    if not api:
        return  # router unreachable — access will be enabled when router comes back online

    create_pppoe_secret(api, router, customer, subscription.package)


# -----------------------------------------------------
# CHANGING A PASSWORD, EVERYWHERE IT IS HONOURED
# -----------------------------------------------------

# What a subscriber may type. Letters and digits only, matching what
# _random_password generates.
#
# Not timidity about RouterOS quoting — a PPP secret goes over the API as a
# value, not as a shell word. It is about the other end: the subscriber has to
# retype this into their own router's web page, often from a phone, and a
# password with a character their CPE's form mangles is a support call that
# looks exactly like "the internet is broken".
PASSWORD_MIN = 8
PASSWORD_MAX = 32
_ALLOWED = set(string.ascii_letters + string.digits)


class PasswordRejected(ValueError):
    """The password cannot be used, with a reason fit to show a subscriber."""


def validate_pppoe_password(value):
    value = (value or "").strip()
    if len(value) < PASSWORD_MIN:
        raise PasswordRejected(
            f"Use at least {PASSWORD_MIN} characters.")
    if len(value) > PASSWORD_MAX:
        raise PasswordRejected(
            f"Use at most {PASSWORD_MAX} characters.")
    if not set(value) <= _ALLOWED:
        raise PasswordRejected(
            "Use letters and numbers only — no spaces or symbols, because "
            "you will need to retype this into your own router.")
    return value


def suggest_pppoe_password():
    return _random_password(12)


def change_pppoe_password(customer, new_password):
    """
    Set a new PPPoE password and make the old one stop working.

    Returns (routers_updated, routers_failed).

    Every one of the operator's routers is rewritten, not just the one the
    subscriber is assigned to. A secret is left behind on the old router when
    somebody is re-homed — migrate_customer_router disconnects the session but
    has never deleted the account, and on 2026-09-09 two subscribers were found
    with live secrets on both boxes at once. If this only rewrote the assigned
    router, whoever the subscriber is trying to lock out could keep dialling
    the other one with the old password, and the subscriber would be told their
    password had changed. That is worse than refusing.

    The session is dropped last. Until it is, the person already online stays
    online: a PPP session is authenticated once, at dial time, and nothing
    re-checks the secret for the life of it. Changing the password alone would
    leave the freeloader connected until they happened to disconnect, which on
    a router that redials automatically is never.
    """
    from billing.router_service import (
        _tenant_routers, safe_connect_router, create_pppoe_secret,
        disconnect_pppoe_session,
    )

    if customer.connection_type != "pppoe":
        raise PasswordRejected("This is not a PPPoE account.")
    if not customer.pppoe_username:
        raise PasswordRejected("This account has no PPPoE username yet.")

    subscription = (
        customer.subscriptions.filter(status="active")
        .order_by("-expiry_date").first()
    )

    customer.pppoe_password = new_password
    customer.save(update_fields=["pppoe_password"])

    updated, failed = [], []
    for router in _tenant_routers(customer.tenant_id):
        api = safe_connect_router(router)
        if not api:
            # Named, not swallowed. A router that could not be reached is one
            # where the old password still works, and the subscriber is
            # entitled to know their change is not everywhere yet.
            failed.append(router.name)
            continue
        try:
            # Rebuilds the secret from customer.pppoe_password, which was
            # saved above. create_pppoe_secret removes and re-adds rather than
            # updating, so the new password is the only one on the row.
            create_pppoe_secret(
                api, router, customer,
                subscription.package if subscription else None,
                subscription.expiry_date if subscription else None,
            )
            updated.append(router.name)
        except Exception:
            failed.append(router.name)
            continue

        try:
            disconnect_pppoe_session(api, customer.pppoe_username)
        except Exception:
            # The secret is what matters and it is already written. A session
            # that outlives this call dies at the next reconnect, and the old
            # password will not get it back.
            pass

    return updated, failed
