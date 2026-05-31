import logging
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models

logger = logging.getLogger(__name__)

ENCRYPTED_PREFIX = "enc:"


def _get_cipher():
    """
    Returns a Fernet cipher when FIELD_ENCRYPTION_KEY is configured.
    Returns None when the key is absent — callers treat None as plaintext passthrough.
    This allows the field to work transparently in dev/test environments without
    a key while enforcing encryption in production where the key is always set.
    """
    key = getattr(settings, "FIELD_ENCRYPTION_KEY", None)
    if not key:
        return None  # no key → plaintext passthrough (dev / uninitialized)
    if isinstance(key, str):
        key = key.encode()
    return Fernet(key)


class EncryptedCharField(models.TextField):
    """
    Transparent symmetric encryption using Fernet (AES-128-CBC + HMAC-SHA256).

    Behaviour matrix:
    - FIELD_ENCRYPTION_KEY set   → encrypts on write, decrypts on read
    - FIELD_ENCRYPTION_KEY unset → stores/reads plaintext (dev/test safe)
    - Legacy plaintext in DB     → returned as-is regardless of key state
    - Already-encrypted value    → never double-encrypted

    Generate a key with:
        python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    """

    def from_db_value(self, value, expression, connection):
        if not value or not isinstance(value, str):
            return value
        if not value.startswith(ENCRYPTED_PREFIX):
            return value  # legacy plaintext — safe passthrough
        cipher = _get_cipher()
        if cipher is None:
            return value  # key removed after encryption — return blob as-is
        try:
            return cipher.decrypt(value[len(ENCRYPTED_PREFIX):].encode()).decode()
        except (InvalidToken, Exception):
            logger.error(
                "EncryptedCharField: decryption failed — returning raw value. "
                "Ensure FIELD_ENCRYPTION_KEY matches the key used when encrypting."
            )
            return value

    def get_prep_value(self, value):
        if not value or not isinstance(value, str):
            return value
        if value.startswith(ENCRYPTED_PREFIX):
            return value  # already encrypted — skip
        cipher = _get_cipher()
        if cipher is None:
            return value  # no key configured — store plaintext
        return f"{ENCRYPTED_PREFIX}{cipher.encrypt(value.encode()).decode()}"
