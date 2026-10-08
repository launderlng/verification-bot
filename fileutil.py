"""Helpers for storing and delivering big files (no Discord imports, easy to test)."""
import base64
import hashlib
import hmac
import ipaddress
import os
import re
import socket
import time
import urllib.parse
from typing import Optional

ATTACH_LIMIT = 25 * 1024 * 1024  # most Discord lets a bot attach to a message; bigger files get a download link instead
LINK_SECONDS = 30 * 60
BLOCKED_EXTENSIONS = {
    ".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".pif", ".lnk", ".hta", ".cpl", ".reg", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse",
    ".wsf", ".jar", ".apk", ".dll", ".sh", ".app", ".dmg", ".pkg", ".msp", ".gadget",
}


def human_size(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def max_file_bytes() -> int:
    return int(float(os.getenv("MAX_FILE_MB", "200")) * 1024 * 1024)


def storage_cap_bytes() -> int:
    return int(float(os.getenv("FILE_STORAGE_MB", "1000")) * 1024 * 1024)


def public_base_url() -> Optional[str]:
    """Where members can reach the bot's web address (needed for download links)."""
    explicit = (os.getenv("PUBLIC_URL") or "").strip().rstrip("/")
    if explicit.startswith("https://"):
        return explicit
    domain = (os.getenv("RAILWAY_PUBLIC_DOMAIN") or "").strip()
    return f"https://{domain}" if domain else None


def safe_filename(name: str) -> str:
    """Keep only the file's own name: no folders, no odd characters."""
    name = os.path.basename((name or "").replace("\\", "/")).strip().strip(".")
    name = re.sub(r"[^\w.\- ()\[\]]+", "_", name)
    return name[:120] or "file"


def blocked_extension(filename: str) -> Optional[str]:
    ext = os.path.splitext((filename or "").lower())[1]
    return ext if ext in BLOCKED_EXTENSIONS else None


# ------------------------------------------------------ signed download links ----

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def signing_key(secret_material: str) -> bytes:
    return hashlib.sha256(b"download-links:" + (secret_material or "").encode()).digest()


def make_token(file_id: int, user_id: int, key: bytes, ttl: int = LINK_SECONDS, now: Optional[float] = None) -> str:
    expires = int((now if now is not None else time.time()) + ttl)
    payload = f"{file_id}.{user_id}.{expires}".encode()
    return f"{_b64(payload)}.{_b64(hmac.new(key, payload, hashlib.sha256).digest())}"


def verify_token(token: str, key: bytes, now: Optional[float] = None) -> Optional[tuple[int, int]]:
    """Returns (file_id, user_id) if the link is genuine and hasn't expired, otherwise None."""
    try:
        payload_b64, sig_b64 = token.split(".")
        payload = _unb64(payload_b64)
        if not hmac.compare_digest(hmac.new(key, payload, hashlib.sha256).digest(), _unb64(sig_b64)):
            return None
        file_id, user_id, expires = (int(x) for x in payload.decode().split("."))
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
    if (now if now is not None else time.time()) > expires:
        return None
    return file_id, user_id


# ----------------------------------------------------------- URL safety ----

def is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_reserved or addr.is_unspecified)


def check_public_url(url: str) -> str:
    """Only https links to the public internet. Refuses anything that points at private or internal addresses."""
    url = (url or "").strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or len(url) > 1000:
        raise ValueError("Use a direct download link that starts with `https://`.")
    host = parsed.hostname
    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ValueError("I couldn't find that website.") from None
    if not infos or not all(is_public_ip(info[4][0]) for info in infos):
        raise ValueError("That link points at a private or internal address, so I won't download it.")
    return url


def filename_from_url(url: str, content_disposition: Optional[str] = None) -> str:
    if content_disposition:
        match = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", content_disposition, re.I)
        if match:
            return safe_filename(urllib.parse.unquote(match.group(1)))
    return safe_filename(urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(url).path)))
