import django.db.models.deletion
from django.db import migrations, models

import billing.models


class Migration(migrations.Migration):
    """
    How many devices a package is good for, and which ones are using it.

    Access was bound to a single MAC held on the customer row, so a package
    could only ever cover one phone and a household paying for three had no way
    to say so. Counting the devices is what makes a limit above one possible —
    and what makes the limit of one actually enforceable, since the count is
    now a thing the redemption path checks rather than a field it overwrites.
    """

    dependencies = [
        ("billing", "0047_comp_payment_method"),
    ]

    operations = [
        migrations.AddField(
            model_name="package",
            name="max_devices",
            field=models.PositiveSmallIntegerField(
                default=1,
                help_text="How many devices one voucher may be used on. 1 means "
                          "the first phone to use it, and only that phone.",
            ),
        ),
        migrations.CreateModel(
            name="CustomerDevice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("mac_address", models.CharField(max_length=50)),
                ("first_seen", models.DateTimeField(auto_now_add=True)),
                ("last_seen", models.DateTimeField(auto_now=True)),
                ("customer", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="devices", to="billing.customer")),
                # Exactly as TenantScopedModel declares it: PROTECT so removing
                # an operator never silently destroys history, and blank=True so
                # DRF does not mark it required on "__all__" serializers.
                ("tenant", models.ForeignKey(
                    blank=True,
                    default=billing.models.default_tenant,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="+", to="billing.tenant")),
            ],
            options={"abstract": False},
        ),
        migrations.AddIndex(
            model_name="customerdevice",
            index=models.Index(fields=["tenant", "customer"],
                               name="device_tenant_customer_idx"),
        ),
        migrations.AddConstraint(
            model_name="customerdevice",
            constraint=models.UniqueConstraint(
                fields=("tenant", "mac_address"),
                name="customer_device_tenant_mac_uniq",
            ),
        ),
    ]
