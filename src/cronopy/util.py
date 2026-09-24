from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import struct
import time


def totp_code(secret: str, for_time: float | None = None) -> str:
    """Current RFC 6238 TOTP code (SHA-1, 30 s period, 6 digits) for a base32 ``secret``."""
    normalized = "".join(secret.split()).upper()
    normalized += "=" * (-len(normalized) % 8)
    key = base64.b32decode(normalized)
    counter = int((time.time() if for_time is None else for_time) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    return f"{code % 1_000_000:06d}"


def format_day(day: dt.date) -> str:
    """Format a date as Cronometer expects: non-zero-padded ``YYYY-M-D``."""
    return f"{day.year}-{day.month}-{day.day}"


def parse_day(value: str) -> dt.date:
    """Parse Cronometer's non-zero-padded ``YYYY-M-D``."""
    y, m, d = (int(part) for part in value.split("-"))
    return dt.date(y, m, d)


def parse_time(value: str) -> dt.time:
    """Parse Cronometer's non-zero-padded ``H:M:S`` (missing parts default to 0)."""
    parts = [int(p) for p in value.split(":")] + [0, 0]
    return dt.time(parts[0], parts[1], parts[2])
