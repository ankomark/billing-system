from django.utils import timezone

def hotspot_welcome_message(customer, subscription):
    return (
        f"Welcome to Skylink WiFi!\n\n"
        f"Your payment was successful.\n"
        f"Package: {subscription.package.name}\n"
        f"Valid Until: {subscription.expiry_date.strftime('%d %b %Y %I:%M %p')}\n\n"
        f"Simply stay connected to WiFi — you’ll auto-connect.\n"
        f"Support: 0700 XXX XXX"
    )


def pppoe_welcome_message(customer, subscription):
    return (
        f"Welcome to Skylink Internet!\n\n"
        f"Your PPPoE account is ready.\n\n"
        f"Username: {customer.pppoe_username}\n"
        f"Password: {customer.pppoe_password}\n\n"
        f"Package: {subscription.package.name}\n"
        f"Valid Until: {subscription.expiry_date.strftime('%d %b %Y %I:%M %p')}\n\n"
        f"Use these details to connect your router.\n"
        f"Support: 0700 XXX XXX"
    )
