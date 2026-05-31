from django.db import migrations
import billing.fields

ENCRYPTED_PREFIX = "enc:"


def encrypt_existing_passwords(apps, schema_editor):
    """
    One-time data migration: encrypt all plaintext passwords already in the DB.
    Safe to run multiple times — skips values that are already encrypted.
    Skipped silently if FIELD_ENCRYPTION_KEY is not configured (dev environments).
    """
    from django.conf import settings
    key = getattr(settings, "FIELD_ENCRYPTION_KEY", None)
    if not key:
        return  # no key configured — skip silently

    from cryptography.fernet import Fernet
    if isinstance(key, str):
        key = key.encode()
    cipher = Fernet(key)

    Customer = apps.get_model("billing", "Customer")
    updated = 0
    for c in Customer.objects.exclude(pppoe_password="").filter(pppoe_password__isnull=False):
        if not c.pppoe_password.startswith(ENCRYPTED_PREFIX):
            enc = cipher.encrypt(c.pppoe_password.encode()).decode()
            Customer.objects.filter(pk=c.pk).update(pppoe_password=f"{ENCRYPTED_PREFIX}{enc}")
            updated += 1

    RouterDevice = apps.get_model("billing", "RouterDevice")
    for r in RouterDevice.objects.exclude(password="").filter(password__isnull=False):
        if not r.password.startswith(ENCRYPTED_PREFIX):
            enc = cipher.encrypt(r.password.encode()).decode()
            RouterDevice.objects.filter(pk=r.pk).update(password=f"{ENCRYPTED_PREFIX}{enc}")
            updated += 1


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0021_accessauditlog"),
    ]

    operations = [
        migrations.AlterField(
            model_name="customer",
            name="pppoe_password",
            field=billing.fields.EncryptedCharField(blank=True),
        ),
        migrations.AlterField(
            model_name="routerdevice",
            name="password",
            field=billing.fields.EncryptedCharField(),
        ),
        migrations.RunPython(
            encrypt_existing_passwords,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
