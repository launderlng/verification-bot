"""Small, dependency-free helpers for the Stripe integration (easy to test)."""
import hashlib
import hmac
import os
import re
import time
import urllib.parse
from typing import Optional

ZERO_DECIMAL = {"bif", "clp", "djf", "gnf", "jpy", "kmf", "krw", "mga", "pyg", "rwf", "ugx", "vnd", "vuv", "xaf", "xof", "xpf"}
THREE_DECIMAL = {"bhd", "iqd", "jod", "kwd", "lyd", "omr", "tnd"}


def webhook_secrets() -> list[str]:
    """STRIPE_WEBHOOK_SECRET may hold several signing secrets separated by commas (e.g. test + live)."""
    return [s.strip() for s in os.getenv("STRIPE_WEBHOOK_SECRET", "").split(",") if s.strip()]


def verify_signature(payload: bytes, header: str, secrets: list[str], tolerance: int = 300, now: Optional[float] = None) -> bool:
    """Check a Stripe-Signature header ('t=...,v1=...') against the raw request body."""
    timestamp, signatures = None, []
    for item in (header or "").split(","):
        key, _, value = item.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            signatures.append(value)
    if not timestamp or not signatures or not secrets:
        return False
    try:
        sent_at = int(timestamp)
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - sent_at) > tolerance:
        return False  # too old (or from the future): possible replay
    signed = f"{timestamp}.".encode() + payload
    for secret in secrets:
        expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        if any(hmac.compare_digest(expected, sig) for sig in signatures):
            return True
    return False


# ---- signed client_reference_id: "<guild>-<user>-<product>[_<option>]-<signature>" -------
# The optional _<option> says which payment option (e.g. 1 Week) the buyer picked. Stripe only
# allows letters, digits, dashes and underscores here, hence the underscore.

def _sign(body: str, secret: str) -> str:
    return hmac.new(secret.encode(), f"ref:{body}".encode(), hashlib.sha256).hexdigest()[:12]


def make_ref(guild_id: int, user_id: int, product_id: int, secret: str, option_id: Optional[int] = None) -> str:
    body = f"{guild_id}-{user_id}-{product_id}" + (f"_{option_id}" if option_id else "")
    return f"{body}-{_sign(body, secret)}"


def parse_ref_full(ref: Optional[str], secrets: list[str]) -> Optional[tuple[int, int, int, Optional[int]]]:
    """Returns (guild_id, user_id, product_id, option_id or None) if the reference is genuine, otherwise None."""
    match = re.fullmatch(r"(\d{1,20})-(\d{1,20})-(\d{1,10})(?:_(\d{1,10}))?-([0-9a-f]{12})", ref or "")
    if not match:
        return None
    body = f"{match[1]}-{match[2]}-{match[3]}" + (f"_{match[4]}" if match[4] else "")
    if any(hmac.compare_digest(_sign(body, s), match[5]) for s in secrets):
        return int(match[1]), int(match[2]), int(match[3]), (int(match[4]) if match[4] else None)
    return None


def parse_ref(ref: Optional[str], secrets: list[str]) -> Optional[tuple[int, int, int]]:
    """Returns (guild_id, user_id, product_id) if the reference is genuine, otherwise None."""
    full = parse_ref_full(ref, secrets)
    return full[:3] if full else None


def tracked_url(url: str, ref: str) -> str:
    """Add (or replace) client_reference_id on a Stripe Payment Link."""
    parsed = urllib.parse.urlparse(url)
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True) if k != "client_reference_id"]
    query.append(("client_reference_id", ref))
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))


def format_amount(amount_minor: Optional[int], currency: Optional[str]) -> str:
    if amount_minor is None:
        return "—"
    code = (currency or "").lower()
    if code in ZERO_DECIMAL:
        value = float(amount_minor)
        return f"{value:,.0f} {code.upper()}"
    divisor = 1000 if code in THREE_DECIMAL else 100
    decimals = 3 if code in THREE_DECIMAL else 2
    return f"{amount_minor / divisor:,.{decimals}f} {code.upper()}".strip()
