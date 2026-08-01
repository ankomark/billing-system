from django.db import migrations, models


class Migration(migrations.Migration):
    """
    A payment method for what was given away.

    Comped access is recorded as a payment of zero rather than as no payment at
    all: it runs the same path as any other — voucher minted, access
    provisioned, invoice settled — while adding nothing to revenue, and it
    shows up in revenue_by_method as its own row with a count and no money.
    Free internet should be countable.
    """

    dependencies = [
        ("billing", "0046_tenant_second_support_phone"),
    ]

    operations = [
        migrations.AlterField(
            model_name="payment",
            name="method",
            field=models.CharField(
                choices=[
                    ("cash", "Cash"),
                    ("mpesa", "M-Pesa"),
                    ("bank", "Bank"),
                    ("comp", "Free (no charge)"),
                ],
                max_length=10,
            ),
        ),
    ]
