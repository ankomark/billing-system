from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Blocking a device, and saying so afterwards.

    Blocked is not the same as removed. Removing frees the place, which is what
    a lost phone needs so the replacement can take its slot. Blocking refuses
    that particular device even with a valid code — a stolen handset, or a
    connection being abused — and deliberately does not hold a place, because
    blocking one device should not cost the customer something they paid for.

    The audit actions are the record of it. Access changes were only ever
    "activate" or "deactivate", which cannot distinguish a subscription being
    ended from one phone being refused.
    """

    dependencies = [
        ("billing", "0050_backfill_customer_devices"),
    ]

    operations = [
        migrations.AddField(
            model_name="customerdevice",
            name="blocked",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="customerdevice",
            name="blocked_reason",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="customerdevice",
            name="blocked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="accessauditlog",
            name="action",
            field=models.CharField(
                choices=[
                    ("deactivate", "Deactivate"),
                    ("activate", "Activate"),
                    ("device_blocked", "Device blocked"),
                    ("device_unblocked", "Device unblocked"),
                    ("device_removed", "Device removed"),
                    ("voucher_deactivated", "Voucher deactivated"),
                ],
                max_length=20,
            ),
        ),
    ]
