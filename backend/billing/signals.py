from django.db.models.signals import post_save
from django.dispatch import receiver

from billing.models import Customer
from billing.notifications import notify_customer


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

        message = (
            f"Welcome {customer.full_name} 🎉\n\n"
            f"You have been registered for WiFi Hotspot access.\n\n"
            f"📶 To connect:\n"
            f"1. Turn ON WiFi\n"
            f"2. Connect to the hotspot\n"
            f"3. You will be redirected to the login page\n\n"
            f"💡 After payment, you will receive a voucher code.\n"
            f"Thank you for choosing us!"
        )

        notify_customer(customer.phone, message)

    # --------------------------------------------------
    # PPPoE CUSTOMER
    # --------------------------------------------------
    elif customer.connection_type == "pppoe":

        if not customer.pppoe_username or not customer.pppoe_password:
            # Credentials not ready yet → skip safely
            return

        message = (
            f"Welcome {customer.full_name} 🎉\n\n"
            f"Your PPPoE internet account is ready.\n\n"
            f"🔐 Login Details:\n"
            f"Username: {customer.pppoe_username}\n"
            f"Password: {customer.pppoe_password}\n\n"
            f"📡 Configure your router to PPPoE mode.\n"
            f"Need help? Contact support.\n\n"
            f"Thank you for choosing us!"
        )

        notify_customer(customer.phone, message)
