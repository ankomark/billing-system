"""
Row-Level Security for every fiber table, as billing's 0057 does for its own.

These rows say where an operator's customers are plugged in, which ONU feeds
whom, and who an outage hit. None of it is another operator's business. Same
NULLIF/COALESCE expression as billing 0037/0057 (an empty setting must not be
cast), and FORCE because Django connects as the tables' owner. No-op on SQLite.
"""

from django.db import migrations

TABLES = [
    "fiber_olt", "fiber_splitter", "fiber_onu", "fiber_onucustomer", "fiber_onureading",
    "fiber_onuhourly", "fiber_porthourly", "fiber_maintenancewindow", "fiber_fiberoutage",
    "fiber_compensationgrant", "fiber_repairticket",
]
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
        for table in TABLES:
            cur.execute(APPLY.format(table=table, policy=POLICY, scope=SCOPE))


def remove_policies(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cur:
        for table in TABLES:
            cur.execute(REMOVE.format(table=table, policy=POLICY))


class Migration(migrations.Migration):

    dependencies = [
        ("fiber", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(apply_policies, remove_policies),
    ]
