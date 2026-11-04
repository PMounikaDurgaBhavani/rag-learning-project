"""PII masking for everything that leaves the request: traces, logs, MCP.

Rules (from the brief): never log a customer email, never log an order
address, mask sensitive customer information, keep it out of traces the
whole squad can read. The authorised UI reads customer records through its
own route; the trace and log paths go through scrub() first.
"""

import re

EMAIL = re.compile(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
# A phone number has at least 9 digits; an ISO date (2026-10-15) has 8 and is
# left alone, so order dates survive into the facts the model reads.
PHONE = re.compile(r"(?<![\w-])(\+?\d(?:[\d\s().-]*\d){8,})(?![\w-])")
CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
STREET = re.compile(r"\b\d{1,5}[A-Za-z]?\s+(?:[A-Z][a-z]+\s+){1,3}"
                    r"(?:Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|Lane|Ln\.?|Drive|Dr\.?|Way|Boulevard|Blvd\.?|Court|Ct\.?|Place|Pl\.?)\b"
                    r"(?:,?\s*(?:Apt|Suite|Unit|Flat)\.?\s*\w+)?")

SENSITIVE_KEYS = {
    "email": "email", "customer_email": "email", "contact_email": "email",
    "address": "address", "shipping_address": "address", "billing_address": "address",
    "street": "address", "address_line1": "address", "address_line2": "address",
    "phone": "phone", "card_last4": "drop", "payment_token": "drop", "password": "drop",
}

MASK_ADDRESS = "[address redacted]"


def mask_email(value):
    if not isinstance(value, str):
        return value
    def repl(match):
        local, domain = match.group(1), match.group(2)
        return f"{local[0]}***@{domain}"
    return EMAIL.sub(repl, value)


def mask_text(text):
    """Free text: emails, street addresses, phone numbers and card-like numbers are masked."""
    if not isinstance(text, str) or not text:
        return text
    text = mask_email(text)
    text = STREET.sub("[address redacted]", text)
    text = CARD.sub("[card redacted]", text)
    text = PHONE.sub(lambda m: "[phone redacted]" if sum(ch.isdigit() for ch in m.group(0)) >= 9 else m.group(0), text)
    return text


def mask_name(name):
    """'Jane Doe' -> 'Jane D.' for logs and traces."""
    if not isinstance(name, str) or not name.strip():
        return name
    parts = name.split()
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[-1][0]}."


def scrub(value, _key=None):
    """Recursively mask a JSON-like structure. Never mutates the input."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            kind = SENSITIVE_KEYS.get(str(key).lower())
            if kind == "drop":
                continue
            if kind == "email":
                out[key] = mask_email(item) if isinstance(item, str) else None
            elif kind == "address":
                out[key] = MASK_ADDRESS if item not in (None, "") else item
            elif kind == "phone":
                out[key] = "[phone redacted]" if item else item
            elif str(key).lower() in ("customer_name", "name") and isinstance(item, str):
                out[key] = mask_name(item)
            else:
                out[key] = scrub(item, key)
        return out
    if isinstance(value, list):
        return [scrub(item, _key) for item in value]
    if isinstance(value, str):
        return mask_text(value)
    return value


def contains_email(text):
    return bool(EMAIL.search(text or ""))
