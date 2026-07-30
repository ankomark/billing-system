import secrets

from django.db import migrations


# Every model that carries a tenant FK. Kept as an explicit list so a model
# added later without being considered here shows up as a test failure rather
# than as silently unclaimed rows.
SCOPED_MODELS = [
    "RouterDevice",
    "Customer",
    "RouterFailoverLog",
    "Package",
    "Subscription",
    "Invoice",
    "Voucher",
    "Payment",
    "ExpiryReminderLog",
    "AccessAuditLog",
    "SystemSetting",
    "MpesaTransaction",
    "PPPoEUsageSnapshot",
    "PPPoEUsageState",
    "PPPoEUsageRecord",
    "HotspotUsageState",
    "HotspotUsageRecord",
    "UsageRecord",
]

DEFAULT_SLUG = "skylink"


def create_and_claim(apps, schema_editor):
    """
    Create the first tenant and assign every existing row to it.

    The system this migration runs against has always been a single operator's,
    so every row belongs to that one tenant by definition.
    """
    Tenant = apps.get_model("billing", "Tenant")

    tenant, created = Tenant.objects.get_or_create(
        slug=DEFAULT_SLUG,
        defaults={
            "name": "Skylink",
            "status": "active",
            "business_name": "Skylink WiFi",
            # The current code hardcodes "Support: 0700 XXX XXX", which is a
            # placeholder rather than a real number, so it is not carried over.
            "support_phone": "",
            # Matches the "SKY-" prefix in generate_pppoe_credentials(), so
            # existing usernames stay consistent with newly generated ones.
            "pppoe_prefix": "SKY",
            "public_token": secrets.token_urlsafe(24)[:32],
        },
    )

    claimed = {}
    for model_name in SCOPED_MODELS:
        Model = apps.get_model("billing", model_name)
        n = Model.objects.filter(tenant__isnull=True).update(tenant=tenant)
        if n:
            claimed[model_name] = n

    # Users are claimed too rather than being left NULL. NULL means platform
    # staff, and an account left NULL by accident would gain platform-wide
    # visibility once scoping lands in phase 2. Failing closed is the safer
    # default; designating real platform staff is a phase 4 task.
    User = apps.get_model("billing", "User")
    users = User.objects.filter(tenant__isnull=True).update(tenant=tenant)

    total = sum(claimed.values()) + users
    if total:
        detail = ", ".join(f"{k}={v}" for k, v in sorted(claimed.items()))
        print(
            f"\n  Assigned {total} row(s) to tenant '{tenant.name}'"
            f" (users={users}{', ' + detail if detail else ''})."
        )
    else:
        print(f"\n  Created tenant '{tenant.name}' on an empty database.")


def release_and_delete(apps, schema_editor):
    """
    Reverse: unclaim every row, then remove the tenant.

    Order matters — the FK is PROTECT, so the tenant cannot be deleted while
    anything still points at it.
    """
    Tenant = apps.get_model("billing", "Tenant")

    for model_name in SCOPED_MODELS:
        Model = apps.get_model("billing", model_name)
        Model.objects.filter(tenant__slug=DEFAULT_SLUG).update(tenant=None)

    User = apps.get_model("billing", "User")
    User.objects.filter(tenant__slug=DEFAULT_SLUG).update(tenant=None)

    Tenant.objects.filter(slug=DEFAULT_SLUG).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0025_tenant_model_and_nullable_fks"),
    ]

    operations = [
        migrations.RunPython(create_and_claim, release_and_delete),
    ]
