from django.db.models.signals import post_save
from django.dispatch import receiver

from billing import message_templates as templates
from billing.models import Customer
from billing.notifications import notify_customer
from billing.tenancy import tenant_context


def _common(customer):
    """What every welcome message can refer to."""
    tenant = customer.tenant
    return {
        "name": customer.full_name,
        "brand": tenant.business_name or tenant.name,
        "support": tenant.support_phone or "",
    }


@receiver(post_save, sender=Customer)
def send_customer_welcome_message(sender, instance, created, **kwargs):
    """
    Sends onboarding SMS/WhatsApp when a NEW customer is created.
    Runs only ONCE.
    """

    if not created:
        return  # only on first creation

    customer = instance

    # --------------------------------------------------
    # HOTSPOT CUSTOMER
    # --------------------------------------------------
    if customer.connection_type == "hotspot":

        # Their messaging credentials, not another operator's — a customer
        # can be created from a worker or a shell where no context is set.
        with tenant_context(customer.tenant_id):
            notify_customer(customer.phone, templates.render(
                templates.WELCOME_HOTSPOT,
                tenant=customer.tenant_id,
                **_common(customer),
            ))

    # --------------------------------------------------
    # PPPoE CUSTOMER
    # --------------------------------------------------
    elif customer.connection_type == "pppoe":

        if not customer.pppoe_username or not customer.pppoe_password:
            # Credentials not ready yet → skip safely
            return

        # Their messaging credentials, not another operator's — a customer
        # can be created from a worker or a shell where no context is set.
        with tenant_context(customer.tenant_id):
            notify_customer(customer.phone, templates.render(
                templates.WELCOME_PPPOE,
                tenant=customer.tenant_id,
                username=customer.pppoe_username,
                password=customer.pppoe_password,
                **_common(customer),
            ))
