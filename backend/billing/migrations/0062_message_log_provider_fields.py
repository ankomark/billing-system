"""
What the provider calls a message, and what it charged for it.

Both come back in the send response and were being discarded. message_id is
the only identifier the two systems share, so without it, matching a row here
against the provider's outbox means reading timestamps and counting characters.

The index renames are unrelated housekeeping, folded in because they would
otherwise reappear on every makemigrations: 0061 named these indexes in the
migration, where the length is not checked, while leaving them unnamed on the
model. Django derived hash names, saw different ones in the database, and kept
offering to rename them. The model now names them, which the system check caps
at thirty characters — the originals were thirty-two.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0061_message_log'),
    ]

    operations = [
        migrations.RenameIndex(
            model_name='messagelog',
            new_name='msglog_tenant_status_idx',
            old_name='billing_msglog_tenant_status_idx',
        ),
        migrations.RenameIndex(
            model_name='messagelog',
            new_name='msglog_tenant_time_idx',
            old_name='billing_msglog_tenant_time_idx',
        ),
        migrations.AddField(
            model_name='messagelog',
            name='message_cost',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=8, null=True),
        ),
        migrations.AddField(
            model_name='messagelog',
            name='message_id',
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
