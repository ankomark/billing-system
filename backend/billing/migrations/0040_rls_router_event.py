"""
Row-Level Security for billing_routerevent.

Every tenant-scoped table carries a policy; a new one added without it is the
single table with no database-level isolation, and nothing would say so. Router
events name an operator's hardware and the errors it returned, which is exactly
the operational detail one operator should not be able to read about another.

The expression is 0037's, not 0030's. 0030's original form casts
current_setting(...) to integer in a branch guarded by an earlier OR, and
Postgres does not guarantee OR short-circuits — so an empty setting raises
`invalid input syntax for type integer: ""`. That is not hypothetical here: it
is what this migration did on its first run, because it was written by copying
0030 rather than the fix that superseded it. NULLIF/COALESCE never casts an
empty string.

FORCE is required as well: Django connects as the role that owns these tables,
and an owner bypasses RLS without it.

No-op on SQLite, which has no RLS.
"""

from django.db import migrations

TABLES = ["billing_routerevent"]
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
        ("billing", "0039_routerevent"),
    ]

    operations = [
        migrations.RunPython(apply_policies, remove_policies),
    ]
