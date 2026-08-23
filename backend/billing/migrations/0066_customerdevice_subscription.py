from django.db import migrations, models
import django.db.models.deletion


def attach_to_a_subscription(apps, schema_editor):
    """
    Give every existing binding the package it was most plausibly bought under.

    The newest active subscription of that customer which already existed when
    the device was bound. Falling back to their newest active one, and leaving
    it null when they have none — a null binding counts against nothing, which
    for a customer with no live package is the truth.

    Getting an individual row wrong here costs very little: the binding is
    re-pointed at the right subscription the next time that device presents a
    code, and until then the only effect is which allowance it is counted in.
    """
    CustomerDevice = apps.get_model("billing", "CustomerDevice")
    Subscription = apps.get_model("billing", "Subscription")

    for device in CustomerDevice.objects.filter(subscription__isnull=True).iterator():
        subs = Subscription.objects.filter(
            customer_id=device.customer_id, status="active",
        ).order_by("-id")

        chosen = subs.filter(created_at__lte=device.first_seen).first()
        if chosen is None:
            chosen = subs.first()
        if chosen is None:
            continue

        device.subscription_id = chosen.id
        device.save(update_fields=["subscription"])


class Migration(migrations.Migration):
    """
    Count device places against the subscription that paid for them.

    Reversing drops the column; the bindings themselves are untouched, and the
    counting reverts to per-customer.
    """

    dependencies = [
        ('billing', '0065_customerdevice_auto_bound'),
    ]

    operations = [
        migrations.AddField(
            model_name='customerdevice',
            name='subscription',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='devices', to='billing.subscription'),
        ),
        migrations.RunPython(
            attach_to_a_subscription,
            migrations.RunPython.noop,
        ),
    ]
