"""Visitor ids, which keep a person's experiment arm the same before and after sign-up."""

from __future__ import annotations

import base64
import re
import secrets

VISITOR_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{16,64}")
"""What a visitor id looks like: 16 to 64 letters, numbers, hyphens or underscores.

Match it with `VISITOR_ID_PATTERN.fullmatch(value)`, so a trailing line break is refused.
"""

_INVALID = "Pass visitor as an id of 16 to 64 letters, numbers, hyphens or underscores."


def new_visitor_id() -> str:
    """Mints a new visitor id: 24 cryptographically random bytes, base64url without padding.

    On a server, keep it for the visitor, for example in a first-party cookie, and pass it to
    `pricing(visitor=...)` and `register(visitor=...)`, so the same person sees the same
    experiment arm on every page and after signing up.
    """
    return base64.urlsafe_b64encode(secrets.token_bytes(24)).decode("ascii").rstrip("=")


def visitor_of(value: object) -> str | None:
    """Checks a given visitor id, answering `None` when none was given."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(_INVALID)
    if not VISITOR_ID_PATTERN.fullmatch(value):
        raise ValueError(_INVALID)
    return value
