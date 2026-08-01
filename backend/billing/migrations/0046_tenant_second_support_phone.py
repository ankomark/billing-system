from django.db import migrations, models


class Migration(migrations.Migration):
    """
    A second support number.

    One is a person, and people are sometimes unreachable — while the customer
    who needs them is standing at a hotspot with no internet and no other way
    to ask. The captive portal shows whichever are set.
    """

    dependencies = [
        ("billing", "0045_alter_adminactionlog_action"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="support_phone_2",
            field=models.CharField(blank=True, max_length=20),
        ),
    ]
