"""
Row-Level Security for billing_station.

Same reasoning as every other tenant-scoped table: without a policy this is the
one table with no database-level isolation, and nothing would say so. A station
names where an operator's hardware physically is, which is not something a
competitor on the same platform should be able to read.

Uses 0037's NULLIF/COALESCE expression rather than 0030's. 0030 casts
current_setting to integer inside a branch guarded by an earlier OR, and
Postgres does not guarantee OR short-circuits, so an empty setting raises
`invalid input syntax for type integer: ""`.

FORCE as well: Django connects as the role owning these tables, and an owner
bypasses RLS without it.

No-op on SQLite, which has no RLS.
"""

from django.db import migrations

TABLES = ["billing_station"]
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
        ("billing", "0041_station_routerdevice_station_and_more"),
    ]

    operations = [
        migrations.RunPython(apply_policies, remove_policies),
    ]
