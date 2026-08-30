from datetime import datetime
import re
import secrets


# Everything that is not a hex digit, once a separator has done its job.
_NOT_HEX = re.compile(r"[^0-9A-F]")


def normalize_mac(value):
    """
    One spelling for one device.

    A device address reaches us from places that disagree about how to write
    one. RouterOS substitutes $(mac) upper-cased with colons, but reports the
    same address in its own case on /ip/hotspot/active; a portal recovering
    from a reload carries whatever the query string kept; and an operator
    typing one into the admin writes what they read off a label, dashes and
    all.

    Compared as raw strings those are different devices. That is not academic:
    one phone bound as "3e:5e:.." and again as "3E:5E:.." fills both places on
    a two-device package, and on a one-device package its owner is told their
    code is in use on another device — about the phone in their hand.

    Anything that is not twelve hex digits comes back stripped and upper-cased
    rather than rejected. This runs on lookup paths as well as writes, and a
    value we cannot parse must at least still match itself.
    """
    raw = (value or "").strip().upper()
    if not raw:
        return ""

    hex_only = _NOT_HEX.sub("", raw)
    if len(hex_only) != 12:
        return raw

    return ":".join(hex_only[i:i + 2] for i in range(0, 12, 2))


def mac_variants(value):
    """
    The spellings a stored address might already be in, for a lookup.

    New rows are written canonical, so the first entry finds almost all of
    them. The rest are for what is already in the database: rows written
    before this existed keep the case and separators they were bound with, and
    a customer refused because of one is exactly the complaint this is here to
    end.

    A list rather than `iexact`, so the lookup still uses the index.
    """
    canonical = normalize_mac(value)
    raw = (value or "").strip()
    ordered = [canonical, canonical.lower(), raw, raw.upper(), raw.lower()]

    # Separator-free too: an operator typing a MAC off a label into the admin
    # writes AABBCCDDEEFF as often as they write the colons. Only when the
    # value really is one — stripping the non-hex out of a legacy username
    # would otherwise leave a two-letter fragment to match strangers with.
    bare = _NOT_HEX.sub("", canonical)
    if len(bare) == 12:
        ordered += [bare, bare.lower()]

    seen = []
    for candidate in ordered:
        if candidate and candidate not in seen:
            seen.append(candidate)
    return seen


def generate_invoice_number():
    """
    Generates unique invoice numbers like:
    INV-20250912153045-9A3FB7C210

    The timestamp only resolves to the second, so the random tail is the whole
    of the uniqueness guarantee between two invoices raised in the same second.
    It used to be two bytes — 65,536 values — and `Invoice.invoice_number` is
    `unique=True`, so this is a birthday problem, not a "will never happen":
    fifty invoices inside one second collided about 1.9% of the time, and a
    hundred about 7.3%.

    A collision is not a cosmetic failure. Subscription.save() creates the
    invoice inside its own `transaction.atomic()`, and nothing retries, so the
    IntegrityError rolls the subscription back with it — a customer whose
    payment has just cleared ends up with no subscription at all. It surfaced
    as an intermittent failure in the test suite, which raises invoices in
    bursts and therefore reaches those odds far sooner than production does.

    Five bytes takes the space to about 1.1 x 10^12, which puts a hundred
    invoices in one second at roughly one chance in 200 million. The shape is
    unchanged and the result is 29 characters, well inside max_length=50.
    """
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    random_part = secrets.token_hex(5).upper()
    return f"INV-{timestamp}-{random_part}"

