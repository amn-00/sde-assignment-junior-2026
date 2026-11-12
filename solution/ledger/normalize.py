"""Pure parsing/normalisation helpers. No I/O, no database: easy to unit test.

Each parser returns the normalised value, or None if the input is invalid.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

IST = timezone(timedelta(hours=5, minutes=30))

# ---------- headers ----------

_SEP_RUN = re.compile(r"[_\s]+")


def normalize_header(h: str) -> str:
    """lower-case, trim, collapse runs of '_' / whitespace into one space.

    We trim once more at the end so a stray trailing '_' ('phone_') still matches.
    """
    return _SEP_RUN.sub(" ", h.lower().strip()).strip()


# ---------- phone ----------

_NON_DIGIT = re.compile(r"\D")


def normalize_phone(raw) -> str | None:
    """Indian mobile -> '+91XXXXXXXXXX', or None if invalid."""
    if raw is None:
        return None
    digits = _NON_DIGIT.sub("", str(raw))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    return None


# ---------- date ----------

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_DMY = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$")
_DMONY = re.compile(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})$")


def parse_date(raw: str) -> str | None:
    """Accepts YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY, DD-Mon-YYYY. Returns ISO 'YYYY-MM-DD'."""
    s = raw.strip()
    try:
        if m := _ISO.match(s):
            y, mo, d = int(m[1]), int(m[2]), int(m[3])
        elif m := _DMY.match(s):
            d, mo, y = int(m[1]), int(m[2]), int(m[3])
        elif m := _DMONY.match(s):
            mo = _MONTHS.get(m[2].lower())
            if mo is None:
                return None
            d, y = int(m[1]), int(m[3])
        else:
            return None
        return date(y, mo, d).isoformat()  # raises on 31/02 etc.
    except ValueError:
        return None


# ---------- decimal ----------

_AMOUNT = re.compile(r"^(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?|\.\d+)$", re.IGNORECASE)


def parse_decimal(raw: str) -> str | None:
    """'₹27,554' / 'Rs. 500' / 'INR 12.5' -> '27554.00'. Must be >= 0. Stored as a string."""
    m = _AMOUNT.match(raw.strip())
    if not m:
        return None
    try:
        value = Decimal(m[1].replace(",", ""))
    except InvalidOperation:
        return None
    if value < 0:
        return None
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# ---------- enum ----------

def parse_enum(raw: str, allowed: list[str]) -> str | None:
    v = raw.strip().lower()
    return v if v in {a.lower() for a in allowed} else None


# ---------- event timestamps ----------

def parse_timestamp(raw) -> datetime | None:
    """'...Z' (UTC), '...+05:30' (offset) or naive (= IST). Returns an aware datetime."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    s = raw.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
ONE_US = timedelta(microseconds=1)


def to_epoch_us(dt: datetime) -> int:
    """Aware datetime -> integer microseconds since epoch (exact, sortable)."""
    return (dt - EPOCH) // ONE_US


def from_epoch_us(us: int) -> datetime:
    return EPOCH + us * ONE_US


def ist_iso(us: int) -> str:
    return from_epoch_us(us).astimezone(IST).isoformat(timespec="seconds")


def ist_date(us: int) -> str:
    return from_epoch_us(us).astimezone(IST).date().isoformat()
