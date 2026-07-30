"""
Extend Row-Level Security to the platform billing tables.

Migration 0030 applied RLS to a fixed list of tables. The three tenant-scoped
tables added in 0033 are not on it, so without this they would be the only
scoped tables in the schema with no database-level isolation — protected by the
application manager alone, which is exactly the single point of failure RLS
exists to remove.

PlatformPlan and PlatformSetting are deliberately excluded: neither is
tenant-scoped. The plan catalogue is shared, and PlatformSetting holds the
platform's own credentials, which no operator may read at all — that is
enforced by permissions rather than by a tenant predicate.
"""

from django.db import migrations


TABLES = [
    "billing_tenantsubscription",
    "billing_tenantinvoice",
    "billing_tenantpayment",
]

POLICY = "tenant_isolation"

ENABLE_SQL = """
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS {policy} ON {table};
CREATE POLICY {policy} ON {table}
    USING (
        current_setting('app.current_tenant_id', true) IS NULL
        OR current_setting('app.current_tenant_id', true) = ''
        OR tenant_id = current_setting('app.current_tenant_id', true)::integer
    )
    WITH CHECK (
        current_setting('app.current_tenant_id', true) IS NULL
        OR current_setting('app.current_tenant_id', true) = ''
        OR tenant_id = current_setting('app.current_tenant_id', true)::integer
    );
"""

DISABLE_SQL = """
DROP POLICY IF EXISTS {policy} ON {table};
ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;
ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
"""


def apply_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cur:
        for table in TABLES:
            cur.execute(ENABLE_SQL.format(table=table, policy=POLICY))


def remove_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cur:
        for table in TABLES:
            cur.execute(DISABLE_SQL.format(table=table, policy=POLICY))


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0033_platform_billing"),
    ]

    operations = [
        migrations.RunPython(apply_rls, remove_rls),
    ]
