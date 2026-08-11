"""
The words an operator sends, and what they cost to send.

Every message here was a formatted string buried at its call site, which meant
an operator could not change a word of it and nobody could see what any of it
cost. Both turned out to matter.

**On cost.** An SMS is 160 characters per part, but only while every character
is in the GSM 03.38 alphabet. One character outside it switches the whole
message to UCS-2 and the part size drops to 70. The voucher message cost three
parts instead of one for exactly that reason: an em dash in "Just stay
connected — auto-login will work". Not the length — the dash. The welcome
messages carried three emoji each and cost five parts to say hello.

So `sms_parts` is here, `check_template` refuses to save a message that would
silently triple in cost, and the defaults below are plain ASCII on purpose.

**On editing.** The defaults are what an operator gets without doing anything,
and every one of them is one part. An operator who wants their own wording
saves a template into SystemSetting and it is used instead — but a voucher
message with no {voucher} in it is a message that fails at the only job it
has, so the required placeholders are enforced rather than trusted.
"""

import logging
import re

logger = logging.getLogger(__name__)

# ─── GSM 03.38 ───────────────────────────────────────────────────────────────
# Frozen by the standard, so this table does not drift. The frontend has its
# own copy for the live counter on the settings page; this one is what decides
# whether a template may be saved.

GSM_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅå"
    "Δ_ΦΓΛΩΠΨΣΘΞÆæßÉ"
    " !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§"
    "¿abcdefghijklmnopqrstuvwxyzäöñüà"
)

# These cost two characters each: they are sent as an escape plus the symbol.
GSM_EXTENDED = set("^{}\\[~]|€")

GSM_SINGLE, GSM_MULTI = 160, 153
UCS2_SINGLE, UCS2_MULTI = 70, 67


def non_gsm_characters(text):
    """The characters that would force this message to UCS-2, in order."""
    seen, out = set(), []
    for char in text or "":
        if char in GSM_BASIC or char in GSM_EXTENDED or char in seen:
            continue
        seen.add(char)
        out.append(char)
    return out


def sms_parts(text):
    """
    (billed_length, parts, encoding) for one message.

    Length is not len(): an extended character costs two under GSM-7, and the
    part size itself depends on the encoding. Concatenated parts are smaller
    than a lone one because each carries a header saying which part it is.
    """
    text = text or ""
    if non_gsm_characters(text):
        length, single, multi, encoding = len(text), UCS2_SINGLE, UCS2_MULTI, "UCS-2"
    else:
        length = sum(2 if c in GSM_EXTENDED else 1 for c in text)
        single, multi, encoding = GSM_SINGLE, GSM_MULTI, "GSM-7"

    if length == 0:
        parts = 0
    elif length <= single:
        parts = 1
    else:
        parts = -(-length // multi)

    return length, parts, encoding


# ─── The templates ───────────────────────────────────────────────────────────

VOUCHER = "SMS_TEMPLATE_VOUCHER"
PPPOE = "SMS_TEMPLATE_PPPOE"
WELCOME_HOTSPOT = "SMS_TEMPLATE_WELCOME_HOTSPOT"
WELCOME_PPPOE = "SMS_TEMPLATE_WELCOME_PPPOE"

# Plain ASCII, and short. Each is one part with realistic values in it — see
# the test that holds them to that.
DEFAULTS = {
    VOUCHER: (
        "{brand}\n"
        "Voucher: {voucher}\n"
        "{package}, valid to {expiry}\n"
        "Help: {support}"
    ),
    PPPOE: (
        "{brand}\n"
        "PPPoE user: {username}\n"
        "Pass: {password}\n"
        "{package}, valid to {expiry}\n"
        "Help: {support}"
    ),
    WELCOME_HOTSPOT: (
        "Welcome to {brand}, {name}.\n"
        "Connect to the WiFi and the login page opens by itself.\n"
        "Help: {support}"
    ),
    WELCOME_PPPOE: (
        "Welcome to {brand}, {name}.\n"
        "PPPoE user: {username}\n"
        "Pass: {password}\n"
        "Set your router to PPPoE mode.\n"
        "Help: {support}"
    ),
}

# What each may refer to, so the settings page can list them and a typo like
# {vouchr} can be named rather than sent to a customer as-is.
PLACEHOLDERS = {
    VOUCHER: {"brand", "voucher", "package", "expiry", "support"},
    PPPOE: {"brand", "username", "password", "package", "expiry", "support"},
    WELCOME_HOTSPOT: {"brand", "name", "support"},
    WELCOME_PPPOE: {"brand", "name", "username", "password", "support"},
}

# Without these the message cannot do its job, so they are not optional. A
# voucher SMS with no code in it still sends, still costs, and still leaves the
# customer who paid for the code without it.
REQUIRED = {
    VOUCHER: {"voucher"},
    PPPOE: {"username", "password"},
    WELCOME_HOTSPOT: set(),
    WELCOME_PPPOE: set(),
}

# Stand-ins for counting, and for the preview on the settings page.
#
# The count has to be taken from a rendered message, not from the template:
# {expiry} is eight characters and becomes twenty, so counting the template
# understates every message by twelve and does it worst near the boundary
# where being wrong actually changes the bill. Long-ish on purpose — a
# template that fits with these fits with most real values.
SAMPLE = {
    "brand": "Skylink Fiber",
    "name": "John Mwangi",
    "voucher": "6EAQHDX",
    "package": "1 Hour Unlimited",
    "expiry": "11 Aug 2026 03:45 PM",
    "username": "john_m41",
    "password": "8kdmz2",
    "support": "0712345678",
}

LABELS = {
    VOUCHER: "Voucher SMS",
    PPPOE: "PPPoE details SMS",
    WELCOME_HOTSPOT: "Hotspot welcome SMS",
    WELCOME_PPPOE: "PPPoE welcome SMS",
}

_FIELD = re.compile(r"\{(\w+)\}")


def when(value, fmt="%d %b %Y %I:%M %p"):
    """
    A time the customer reading it will recognise.

    Datetimes are stored in UTC, because USE_TZ is on, and TIME_ZONE is only
    what they are *displayed* in. Formatting one straight into a message
    therefore prints UTC — three hours behind Nairobi — so a voucher bought at
    6:45pm told the customer it expired at 3:45pm, which is to say three hours
    before they bought it.

    Dates are not exempt: a due date at 22:00 UTC is already the next day here,
    so "%d %b" on the raw value names the wrong day.
    """
    if value is None:
        return ""
    from django.utils import timezone

    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return f"{value:{fmt}}"


def check_template(key, text):
    """
    Why this template may not be saved, or None.

    Returns a sentence to show the operator. Refusing is deliberate for the two
    cases that cost real money or break the message outright; wording is
    otherwise entirely theirs.
    """
    if key not in DEFAULTS:
        return f"Unknown template {key}."

    used = set(_FIELD.findall(text or ""))

    # Before the missing-required check, because a mistyped {vouchr} is both at
    # once and the typo is the more useful half to be told about. Reporting
    # only "must include {voucher}" to someone who thinks they just did is how
    # a person stares at a form.
    unknown = used - PLACEHOLDERS[key]
    if unknown:
        names = ", ".join("{%s}" % u for u in sorted(unknown))
        known = ", ".join("{%s}" % p for p in sorted(PLACEHOLDERS[key]))
        return (f"{names} is not something this message knows about, so it "
                f"would be sent to the customer exactly as written. "
                f"Available: {known}.")

    missing = REQUIRED[key] - used
    if missing:
        names = ", ".join("{%s}" % m for m in sorted(missing))
        return (f"{LABELS[key]} must still include {names} — without it the "
                f"message goes out and the customer does not get what they "
                f"paid for.")

    bad = non_gsm_characters(text)
    if bad:
        shown = " ".join(bad[:5])
        # Counted on a rendered message rather than the template, and stated as
        # a comparison. A bare figure is worse than none here: a short template
        # with an emoji in it still costs one part, and "this would cost 1
        # part" reads as an argument for keeping the emoji.
        now = sms_parts(_fill(text, SAMPLE))[1]
        clean = sms_parts(_fill("".join(c for c in text if c not in bad), SAMPLE))[1]
        cost = (f" A message like this would cost {now} SMS instead of {clean}."
                if now > clean else "")
        return (f"Remove {shown}: characters outside the standard SMS alphabet "
                f"cut a message part from 160 characters to 70.{cost}")

    return None


def get_template(key, tenant=None):
    """The operator's wording, or ours where they have not set any."""
    from billing.config import get_setting

    saved = get_setting(key, default="", tenant=tenant)
    if isinstance(saved, str) and saved.strip():
        return saved
    return DEFAULTS[key]


def render(key, tenant=None, **values):
    """
    One message, ready to send.

    A placeholder with nothing behind it takes its whole line with it, rather
    than leaving "Help:" above a blank. Support numbers are optional and most
    of the lines here are label-and-value, so a dangling label is the common
    case and not worth making every template carry a conditional for.

    Never raises, and never returns a message missing the thing it exists to
    carry. This runs on the path that tells a paying customer their voucher
    code; a template that has lost {voucher} must not be the reason they never
    hear it, so the default is used instead.

    check_template already refuses to save such a template, but it is not the
    only way one can arrive — a row set before that check existed, or written
    straight into the database, reaches here just the same.
    """
    template = get_template(key, tenant=tenant)

    try:
        text = _fill(template, values)
    except Exception:
        logger.exception("[sms] %s could not be rendered", key)
        text = ""

    if not _carries_what_it_must(key, text, values):
        logger.error(
            "[sms] the saved %s does not carry %s — sending the default "
            "instead, so the customer still gets it",
            key, ", ".join(sorted(REQUIRED[key])) or "its content")
        text = _fill(DEFAULTS[key], values)

    return text


def _carries_what_it_must(key, text, values):
    """Whether a rendered message still has the values that are the point of it."""
    if not text or not text.strip():
        return False
    for name in REQUIRED.get(key, ()):
        needed = str(values.get(name, "")).strip()
        if needed and needed not in text:
            return False
    return True


def _fill(template, values):
    lines = []
    for line in template.splitlines():
        used = _FIELD.findall(line)
        # A line that was only ever a label for something this operator does
        # not have. Dropped whole, so "Help: {support}" with no support number
        # leaves nothing behind rather than "Help:".
        if used and all(not str(values.get(name, "")).strip() for name in used):
            continue
        lines.append(_FIELD.sub(
            lambda m: str(values.get(m.group(1), m.group(0))), line))
    return "\n".join(lines).strip()
