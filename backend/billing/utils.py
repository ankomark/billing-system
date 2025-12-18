from datetime import datetime
import secrets

def generate_invoice_number():
    """
    Generates unique invoice numbers like:
    INV-20250912153045-A9F3
    """
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    random_part = secrets.token_hex(2).upper()
    return f"INV-{timestamp}-{random_part}"

