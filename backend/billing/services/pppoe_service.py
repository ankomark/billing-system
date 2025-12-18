import secrets
import string

from billing.models import Customer
from billing.router_service import create_pppoe_secret


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
    Ensures the username is unique in the database.
    """

    # Use last 4 digits of phone OR fallback
    base = customer.phone[-4:] if customer.phone else secrets.token_hex(2)

    while True:
        # SKY-1234-XYZ format
        suffix = "".join(secrets.choice(string.ascii_uppercase) for _ in range(3))
        username = f"SKY-{base}-{suffix}"

        if not Customer.objects.filter(pppoe_username=username).exists():
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

    # Create PPPoE secret if not existing
    create_pppoe_secret(router, customer, subscription.package)
