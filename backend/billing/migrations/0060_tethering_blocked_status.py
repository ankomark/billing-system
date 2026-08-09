"""
The `blocked` case status, and a hop count that starts at zero.

No data changes and no column added. Existing cases keep the status they had,
and an operator who never sets TETHERING_POLICY to `block` will never see that
value.

The hops default moves from 1 to 0 so that "the hop counter never saw this
address" is expressible — it is what tells a case resting on connection count
alone apart from one the TTL rules caught, and the two answer to different
thresholds. Rows written before this all carry a real 1 or 2, set from the list
that caught them, so nothing existing changes meaning.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0059_invoice_checkout_request_id"),
    ]

    operations = [
        migrations.AlterField(
            model_name="tetheringcase",
            name="status",
            field=models.CharField(
                choices=[
                    ("watching", "Watching"),
                    ("warned", "Warned"),
                    ("throttled", "Throttled"),
                    ("kicked", "Session ended"),
                    ("blocked", "Blocked"),
                    ("cleared", "Cleared"),
                ],
                default="watching",
                max_length=12,
            ),
        ),
        migrations.AlterField(
            model_name="tetheringcase",
            name="hops",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
