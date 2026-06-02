"""
Performance indexes for 10k+ customer scale.

Without these, every expiry check, dashboard count, revenue report, and
usage alert query does a full table scan on 100k+ row tables.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0022_encrypt_password_fields"),
    ]

    operations = [
        # ─── Customer ────────────────────────────────────────────────────────
        migrations.AddIndex(
            model_name="customer",
            index=models.Index(fields=["status"], name="customer_status_idx"),
        ),
        migrations.AddIndex(
            model_name="customer",
            index=models.Index(fields=["pppoe_username"], name="customer_pppoe_username_idx"),
        ),
        migrations.AddIndex(
            model_name="customer",
            index=models.Index(fields=["connection_type"], name="customer_connection_type_idx"),
        ),

        # ─── Subscription ────────────────────────────────────────────────────
        migrations.AddIndex(
            model_name="subscription",
            index=models.Index(fields=["status"], name="subscription_status_idx"),
        ),
        migrations.AddIndex(
            model_name="subscription",
            index=models.Index(fields=["expiry_date"], name="subscription_expiry_date_idx"),
        ),
        # Composite index — enforce_subscription_expiry queries both columns together
        migrations.AddIndex(
            model_name="subscription",
            index=models.Index(
                fields=["status", "expiry_date"],
                name="subscription_status_expiry_idx",
            ),
        ),

        # ─── Invoice ─────────────────────────────────────────────────────────
        migrations.AddIndex(
            model_name="invoice",
            index=models.Index(fields=["payment_status"], name="invoice_payment_status_idx"),
        ),
        migrations.AddIndex(
            model_name="invoice",
            index=models.Index(fields=["created_at"], name="invoice_created_at_idx"),
        ),

        # ─── Payment ─────────────────────────────────────────────────────────
        migrations.AddIndex(
            model_name="payment",
            index=models.Index(fields=["paid_at"], name="payment_paid_at_idx"),
        ),
        migrations.AddIndex(
            model_name="payment",
            index=models.Index(fields=["method"], name="payment_method_idx"),
        ),
        # Composite — revenue_summary() always filters paid_at + aggregates
        migrations.AddIndex(
            model_name="payment",
            index=models.Index(fields=["paid_at", "method"], name="payment_paid_at_method_idx"),
        ),

        # ─── MpesaTransaction ────────────────────────────────────────────────
        migrations.AddIndex(
            model_name="mpesatransaction",
            index=models.Index(fields=["status"], name="mpesa_status_idx"),
        ),
        migrations.AddIndex(
            model_name="mpesatransaction",
            index=models.Index(fields=["processed"], name="mpesa_processed_idx"),
        ),
        # Composite — failed_mpesa_transactions() filters both
        migrations.AddIndex(
            model_name="mpesatransaction",
            index=models.Index(
                fields=["status", "processed"],
                name="mpesa_status_processed_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="mpesatransaction",
            index=models.Index(fields=["created_at"], name="mpesa_created_at_idx"),
        ),
    ]
