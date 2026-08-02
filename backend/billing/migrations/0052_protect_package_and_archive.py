import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    A sold package stops being deletable, and gains somewhere to retire to.

    Subscription.package was CASCADE. Deleting a package took every
    subscription on it and, through those, the invoices, the payments and the
    vouchers — the record that money changed hands, destroyed because somebody
    tidied their price list. The confirm dialog said "existing subscriptions
    are not affected", which was the opposite of what happened. Measured
    against the development data before changing it: one package, one
    customer, five rows deleted.

    PROTECT makes the database refuse. is_archived gives the operator what
    they actually wanted, which is for the package to stop being offered.
    """

    dependencies = [
        ("billing", "0051_block_device_and_audit_actions"),
    ]

    operations = [
        migrations.AddField(
            model_name="package",
            name="is_archived",
            field=models.BooleanField(
                default=False,
                help_text="Retired from sale. Existing subscribers keep it; it "
                          "stops being offered to anyone new.",
            ),
        ),
        migrations.AlterField(
            model_name="subscription",
            name="package",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                to="billing.package",
            ),
        ),
    ]
