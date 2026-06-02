"""
Thin compatibility shim — re-exports from billing.notifications.

Previously this module read AT_USERNAME/AT_API_KEY from Django settings
(environment variables only), so credential updates through the SystemSettings
UI had no effect here. All logic is now in billing.notifications which reads
from get_setting() (DB-backed, multi-worker-safe cache).

Kept to avoid breaking any management commands or third-party code that still
imports from this path. Import billing.notifications directly in new code.
"""
from billing.notifications import notify_customer, send_sms, send_whatsapp  # noqa: F401
