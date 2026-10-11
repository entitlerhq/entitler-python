"""Checks and formats the arguments the SDK sends."""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from datetime import UTC, datetime
from urllib.parse import quote

MAX_SAFE = 2**53 - 1
SPACE = " \t\r\n"
CUSTOMER_ID = "Provide the id your app uses for the customer."
FEATURE_KEY = "Name the feature by its key."
PLAN_KEY = "Name the plan by its id or its key."
PRODUCT_KEY = "Name the product by its key."
USAGE_AMOUNT = f"Pass amount as a whole number from 1 to {MAX_SAFE}."
REQUIRED_KEY = (
    "Pass idempotency_key: a key from your own unit of work, such as a message or job id."
)
TIMEOUT = "Pass timeout as a number of seconds above 0."
_KEY = re.compile(r"[\x21-\x7e](?:[\x20-\x7e]*[\x21-\x7e])?")
_DOTS = re.compile(r"\.+")


def trim(value: str) -> str:
    """Trims a credential of spaces, tabs, carriage returns and line feeds."""
    return value.strip(SPACE)


def require_text(value: object, message: str) -> str:
    """Answers a credential or other text trimmed, refusing one that is blank."""
    if not isinstance(value, str):
        raise TypeError(message)
    trimmed = trim(value)
    if not trimmed:
        raise ValueError(message)
    return trimmed


def sendable(credential: str) -> bool:
    """True when every character is printable ASCII other than a space (`!` to `~`)."""
    return all("!" <= character <= "~" for character in credential)


def require_credential(value: object, message: str) -> str:
    """Answers a key or token trimmed, refusing one that is blank or holds any other character.

    A credential travels in a header, and an HTTP library's error for a bad header value can
    quote it, so one that could not be sent fails here exactly as a blank one does.
    """
    credential = require_text(value, message)
    if not sendable(credential):
        raise ValueError(message)
    return credential


def require_id(value: object, message: str) -> str:
    """Answers an id exactly as given, refusing one that is blank."""
    if not isinstance(value, str):
        raise TypeError(message)
    if not trim(value):
        raise ValueError(message)
    return value


def encode(value: str) -> str:
    """Percent-encodes a path segment or query value as JavaScript's `encodeURIComponent` does."""
    try:
        return quote(value, safe="-_.!~*'()", encoding="utf-8", errors="strict")
    except UnicodeEncodeError:
        raise ValueError("Pass an id that is valid Unicode.") from None


def segment(value: str) -> str:
    """Percent-encodes one path segment, refusing ids URL handling would turn into another route."""
    encoded = encode(value)
    if _DOTS.fullmatch(value):
        raise ValueError("Pass an id that is not made only of dots.")
    return encoded


def id_segment(value: object, message: str) -> str:
    """Checks an id is not blank and encodes it as one path segment."""
    return segment(require_id(value, message))


def whole(value: object, message: str, low: int, high: int = MAX_SAFE) -> int:
    """Answers a whole number from `low` to `high`, refusing anything else with `message`."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(message)
    if not low <= value <= high:
        raise ValueError(message)
    return value


def usage_amount(value: object) -> int:
    """Checks a usage amount."""
    return whole(value, USAGE_AMOUNT, 1)


def idempotency_key(value: object, longest: int = 200) -> str:
    """Checks a caller's idempotency key: 1 to `longest` printable ASCII characters."""
    if not isinstance(value, str) or len(value) > longest or not _KEY.fullmatch(value):
        message = f"Pass idempotency_key as 1 to {longest} printable ASCII characters."
        raise (ValueError if isinstance(value, str) else TypeError)(message)
    return value


def required_key(value: object, longest: int = 200) -> str:
    """Checks a caller's required idempotency key."""
    if value is None:
        raise TypeError(REQUIRED_KEY)
    return idempotency_key(value, longest)


def key_or_new(value: object) -> str:
    """Checks a caller's idempotency key, or generates a UUID v4 when none was given."""
    return str(uuid.uuid4()) if value is None else idempotency_key(value)


def timeout(value: object) -> float:
    """Checks a timeout: a finite number of seconds above 0."""
    if value is None:
        raise ValueError(TIMEOUT)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(TIMEOUT)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(TIMEOUT)
    return float(value)


def seconds(value: object, message: str) -> float:
    """Checks a duration of 0 seconds or more."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(message)
    if math.isnan(value) or value < 0:
        raise ValueError(message)
    return float(value)


def instant(value: object, message: str) -> str:
    """Formats an aware `datetime` as RFC 3339 in UTC with milliseconds."""
    if not isinstance(value, datetime):
        raise TypeError(message)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(message)
    try:
        utc = value.astimezone(UTC)
    except OverflowError:
        raise ValueError(message) from None
    return format_instant(utc)


def format_instant(value: datetime) -> str:
    """Writes a UTC instant as `2026-07-01T09:30:00.000Z`."""
    return f"{value:%Y-%m-%dT%H:%M:%S}.{value.microsecond // 1000:03d}Z"


def canonical_json(value: object) -> str:
    """Serialises as JavaScript's `JSON.stringify` does: no whitespace, `/` and non-ASCII kept."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def sha256_hex(text: str) -> str:
    """The lowercase hex SHA-256 of `text`'s UTF-8 bytes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
