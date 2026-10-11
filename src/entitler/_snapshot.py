"""Offline snapshots: verifying a signed copy of a customer's entitlements with no request."""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from . import _clock
from ._decode import DecodeError, Obj, claimed_entitlement, track
from ._errors import SnapshotError, SnapshotErrorCode
from ._models import Entitlement, Entitlements, EnvironmentRef, SnapshotKey, SnapshotKeys, TrackRef
from ._values import format_instant, trim, whole

DEFAULT_ISSUER = "https://api.entitler.dev/customers"
_SEGMENT = re.compile(r"[A-Za-z0-9_-]*")
_EARLIEST = int(datetime(1, 1, 1, tzinfo=UTC).timestamp())
_LATEST = int(datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC).timestamp())
_NOT_SNAPSHOT = "That is not an entitlements snapshot. Pass the token snapshot() returned."
_NO_KEY = "None of the keys passed signed this snapshot. Fetch them again with snapshot_keys()."
_CHANGED = "This snapshot was changed after Entitler signed it."


@dataclass(frozen=True, slots=True, kw_only=True)
class VerifiedSnapshot:
    """A snapshot that verified: the customer's entitlements as Entitler signed them.

    Meters in a snapshot are frozen when it is signed, and its entitlements carry no upgrades,
    since offline gating never offers a purchase.
    """

    customer: str
    """The customer's external id."""
    environment: EnvironmentRef
    """The environment it was signed in."""
    track: TrackRef
    """The customer's track."""
    release: int | None
    """The release, or `None`."""
    change: str | None
    """The change, or `None`."""
    testers: bool
    """True on a track for testers and in a test environment."""
    expires_at: datetime
    """When it expires."""
    entitlements: Entitlements[EnvironmentRef]
    """The entitlements, groups included, with `as_of` the instant it was signed."""


def _invalid(message: str = _NOT_SNAPSHOT) -> SnapshotError:
    return SnapshotError(SnapshotErrorCode.SNAPSHOT_INVALID, message)


def _segment(text: str) -> bytes:
    if not _SEGMENT.fullmatch(text) or len(text) % 4 == 1:
        raise _invalid()
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        raise _invalid() from None


def _json(data: bytes) -> object:
    try:
        return json.loads(data.decode("utf-8"), parse_constant=_refuse)
    except (ValueError, RecursionError):
        return None


def _refuse(name: str) -> object:
    raise ValueError(name)


def _key_list(keys: SnapshotKeys | Sequence[object]) -> list[Mapping[str, object]]:
    items: Sequence[object] = keys.keys if isinstance(keys, SnapshotKeys) else keys
    found: list[Mapping[str, object]] = []
    for item in items:
        if isinstance(item, SnapshotKey):
            found.append({"kid": item.kid, "x": item.x, "y": item.y, "crv": item.crv})
        elif isinstance(item, Mapping):
            found.append(cast("Mapping[str, object]", item))
    return found


def verify_snapshot(
    token: str,
    *,
    keys: SnapshotKeys | Sequence[object],
    customer: str,
    environment: str,
    issuer: str = DEFAULT_ISSUER,
    now: datetime | None = None,
    clock_skew_seconds: int = 60,
) -> VerifiedSnapshot:
    """Verifies a snapshot offline, with no request, and answers its entitlements.

    Ship the keys with the app, written by `entitler snapshot-keys` at build time, and replace
    them only with keys fetched from `snapshot_keys()` over HTTPS; never store them beside the
    snapshot. The expected customer is the signed-in person's external id from the app's own
    session, never from the stored snapshot, and the environment is the one the build ships for.

    Args:
        token: The snapshot `snapshot()` returned.
        keys: The `SnapshotKeys` from `snapshot_keys()`, or its list of keys.
        customer: The external id the snapshot must be for.
        environment: The environment id it must come from.
        issuer: The issuer it must name.
        now: The instant to check expiry against, an aware `datetime`; the system clock, read at
            each verification, when left out.
        clock_skew_seconds: How far ahead of `now` its signing instant may be, 0 to 300.

    Raises:
        SnapshotError: the snapshot does not verify; `code` is `snapshot_expired` once it has
            expired, else `snapshot_invalid`.
        ValueError: an argument is out of range.
        ImportError: the `cryptography` package is missing; install `entitler[snapshots]`.
    """
    if not isinstance(customer, str) or not isinstance(environment, str):
        raise TypeError("Provide the customer and environment the snapshot must be for.")
    if not trim(customer) or not trim(environment):
        raise ValueError("Provide the customer and environment the snapshot must be for.")
    skew = whole(
        clock_skew_seconds, "Pass clock_skew_seconds as a whole number from 0 to 300.", 0, 300
    )
    if now is not None and (not isinstance(now, datetime) or now.utcoffset() is None):
        raise ValueError("Pass now as a valid instant.")
    instant = _clock.now() if now is None else now.timestamp()
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    except ImportError as missing:
        raise ImportError(
            "Verifying snapshots needs the cryptography package: install entitler[snapshots]."
        ) from missing

    parts = token.split(".") if isinstance(token, str) else []
    if len(parts) != 3:
        raise _invalid()
    header_bytes, _, signature = (_segment(part) for part in parts)
    found = _json(header_bytes)
    if not isinstance(found, dict):
        raise _invalid()
    header = cast("dict[str, object]", found)
    if header.get("typ") != "entitlements+jwt" or header.get("alg") != "ES256" or "crit" in header:
        raise _invalid()
    kid = header.get("kid")
    key = next(
        (item for item in _key_list(keys) if isinstance(kid, str) and item.get("kid") == kid),
        None,
    )
    if key is None:
        raise _invalid(_NO_KEY)
    try:
        x, y = (int.from_bytes(_segment(str(key[name])), "big") for name in ("x", "y"))
        public = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
        if len(signature) != 64:
            raise _invalid(_CHANGED)
        encoded = encode_dss_signature(
            int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")
        )
        public.verify(encoded, f"{parts[0]}.{parts[1]}".encode("ascii"), ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError, KeyError, SnapshotError):
        raise _invalid(_CHANGED) from None

    claims = _claims(_segment(parts[1]))
    issued, expires = claims.iat, claims.exp
    if claims.iss != issuer:
        raise _invalid(f"This snapshot was not issued by {issuer}.")
    if issued > instant + skew:
        raise _invalid(
            f"This snapshot was signed for {_text(issued)}, which is still to come. "
            "Fetch a new one while online."
        )
    if instant >= expires:
        raise SnapshotError(
            SnapshotErrorCode.SNAPSHOT_EXPIRED,
            f"This snapshot expired at {_text(expires)}. Fetch a new one while online.",
        )
    if claims.sub != customer:
        raise _invalid(
            f"This snapshot is for another customer, not {customer}. "
            "Fetch one for the signed-in customer while online."
        )
    if claims.environment.id != environment:
        raise _invalid(
            f"This snapshot is from another environment, not {environment}. "
            "Fetch one from your app's environment while online."
        )
    signed = claims.environment
    entitlements = Entitlements(
        customer=claims.sub,
        as_of=datetime.fromtimestamp(issued, UTC),
        entitlements=claims.entitlements,
        environment=signed,
        track=claims.track,
        release=claims.release,
        change=claims.change,
        testers=claims.testers,
        experiment=None,
        stale=False,
    )
    return VerifiedSnapshot(
        customer=claims.sub,
        environment=signed,
        track=claims.track,
        release=claims.release,
        change=claims.change,
        testers=claims.testers,
        expires_at=datetime.fromtimestamp(expires, UTC),
        entitlements=entitlements,
    )


def _text(seconds: int) -> str:
    return format_instant(datetime.fromtimestamp(seconds, UTC))


@dataclass(frozen=True, slots=True)
class _Claims:
    iss: str
    sub: str
    environment: EnvironmentRef
    track: TrackRef
    release: int | None
    change: str | None
    testers: bool
    entitlements: tuple[Entitlement, ...]
    iat: int
    exp: int


def _claims(payload: bytes) -> _Claims:
    data = _json(payload)
    try:
        o = Obj(data, "")
        found = _Claims(
            iss=o.string("iss"),
            sub=o.string("sub"),
            environment=EnvironmentRef(id=o.obj("environment").string("id")),
            track=o.one("track", track),
            release=o.integer_or_none("release"),
            change=o.string_or_none("change"),
            testers=o.boolean("testers"),
            entitlements=o.array("entitlements", claimed_entitlement),
            iat=o.integer("iat", _EARLIEST),
            exp=o.integer("exp", _EARLIEST),
        )
    except DecodeError:
        raise _invalid() from None
    if found.iat > _LATEST or found.exp > _LATEST:
        raise _invalid()
    return found
