from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Mark which device bindings the purchase flow made on the customer's behalf.

    Existing rows default to False — deliberate. That is the safe reading: an
    already-bound device keeps the protection it has today, and nothing a
    customer is currently connected on can be displaced by this change until
    it is rebound through the purchase flow.
    """

    dependencies = [
        ('billing', '0064_swap_usage_direction'),
    ]

    operations = [
        migrations.AddField(
            model_name='customerdevice',
            name='auto_bound',
            field=models.BooleanField(default=False),
        ),
    ]
