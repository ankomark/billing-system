"""
The delivery log, and Row-Level Security for it.

Both in one migration deliberately: a tenant-scoped table that exists for even
one deploy without its policy is a table any operator can read. These rows carry
subscribers' phone numbers and the text of messages sent to them, which is
exactly the kind of thing the policy is for.

The RLS half follows 0057 — see its notes on why the NULLIF/COALESCE form is
used rather than 0030's, and why FORCE is needed when Django connects as the
table's owner. No-op on SQLite, which has no RLS.
"""

import django.db.models.deletion
from django.db import migrations, models

import billing.models

TABLE = "billing_messagelog"
POLICY = "tenant_isolation"

SCOPE = (
    "tenant_id = COALESCE("
    "NULLIF(current_setting('app.current_tenant_id', true), '')::integer, "
    "tenant_id)"
)

APPLY = """
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS {policy} ON {table};
CREATE POLICY {policy} ON {table}
    USING ({scope})
    WITH CHECK ({scope});
"""

REMOVE = """
DROP POLICY IF EXISTS {policy} ON {table};
ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;
ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
"""


def apply_policies(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cur:
        cur.execute(APPLY.format(table=TABLE, policy=POLICY, scope=SCOPE))


def remove_policies(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cur:
        cur.execute(REMOVE.format(table=TABLE, policy=POLICY))


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0060_tethering_blocked_status"),
    ]

    operations = [
        migrations.CreateModel(
            name="MessageLog",
            fields=[
                ("id", models.BigAutoField(
                    auto_created=True, primary_key=True, serialize=False,
                    verbose_name="ID")),
                ("channel", models.CharField(
                    choices=[("sms", "SMS"), ("whatsapp", "WhatsApp")],
                    max_length=10)),
                ("phone", models.CharField(blank=True, max_length=32)),
                ("status", models.CharField(
                    choices=[("sent", "Sent"), ("refused", "Refused"),
                             ("failed", "Failed")],
                    max_length=10)),
                ("status_code", models.CharField(blank=True, max_length=20)),
                ("reason", models.TextField(blank=True)),
                ("message", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("tenant", models.ForeignKey(
                    blank=True,
                    default=billing.models.default_tenant,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="+",
                    to="billing.tenant")),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["tenant", "status", "-created_at"],
                        name="billing_msglog_tenant_status_idx"),
                    models.Index(
                        fields=["tenant", "-created_at"],
                        name="billing_msglog_tenant_time_idx"),
                ],
            },
        ),
        migrations.RunPython(apply_policies, remove_policies),
    ]
