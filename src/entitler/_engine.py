"""The one request builder, retry policy and answer cache both kinds of client share.

Each call is a generator that yields the I/O it needs (an attempt, a wait, a credential, a cache
read or write) and receives the result, so the same logic runs under a blocking driver and an
async driver; only I/O and sleeping differ.
"""

from __future__ import annotations

import contextlib
import math
import platform
import re
import sys
import threading
from collections import OrderedDict
from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar, cast

from . import _clock
from ._cache import CacheEntry
from ._decode import DecodeError, Obj, answer
from ._errors import (
    APIError,
    EntitlerConnectionError,
    EntitlerTimeoutError,
    ErrorCode,
    Payment,
    PaymentStatus,
    error_code,
    stands_in,
)
from ._values import canonical_json, encode, sha256_hex
from ._version import __version__

T = TypeVar("T")
Gen = Generator[Any, Any, T]
"""A call: yields effects, receives their results, and returns its answer."""

CLOSED = "This Entitler client is closed. Create a new one."
USER_AGENT = f"entitler-python/{__version__} python/{platform.python_version()}"
INVALID = "Entitler sent an answer this SDK cannot read."
RETRIED = frozenset({408, 429, 500, 502, 503, 504})
TRACKED_CUSTOMERS = 1000
OUTAGE_WINDOW = 30.0
LONGEST = sys.float_info.max
LONGEST_HEADER = 2**53 - 1
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_IMF = re.compile(
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), ([0-9]{2}) ([A-Z][a-z]{2}) ([0-9]{4}) "
    r"([0-9]{2}):([0-9]{2}):([0-9]{2}) GMT"
)
_RFC850 = re.compile(
    r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), ([0-9]{2})-([A-Z][a-z]{2})-"
    r"([0-9]{2}) ([0-9]{2}):([0-9]{2}):([0-9]{2}) GMT"
)
_ASCTIME = re.compile(
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) ([A-Z][a-z]{2}) ([ 0-9][0-9]) "
    r"([0-9]{2}):([0-9]{2}):([0-9]{2}) ([0-9]{4})"
)


def closed_error() -> RuntimeError:
    """The error a closed client's calls raise."""
    return RuntimeError(CLOSED)


@dataclass(frozen=True, slots=True)
class Authorised:
    """A credential's request headers, and the credential that keys its kept answers."""

    headers: Mapping[str, str] = field(repr=False)
    kind: str
    credential: str = field(repr=False)
    token: str | None = field(default=None, repr=False)


OPEN = Authorised({}, "key", "")


@dataclass(frozen=True, slots=True)
class Received:
    """One answer: its status, its headers by lower-case name, and its body as text."""

    status: int
    headers: Mapping[str, str]
    text: str


@dataclass(frozen=True, slots=True)
class Attempt:
    """Effect: send one request, answering `Received` or raising a connection or timeout error."""

    method: str
    url: str
    headers: Mapping[str, str] = field(repr=False)
    body: str | None
    timeout: float
    idempotency_key: str | None


@dataclass(frozen=True, slots=True)
class Sleep:
    """Effect: wait between attempts."""

    seconds: float


@dataclass(frozen=True, slots=True)
class Authorise:
    """Effect: answer the client's current `Authorised`, asking a token provider when needed."""


@dataclass(frozen=True, slots=True)
class Refresh:
    """Effect: after a `401`, answer a newer `Authorised`, or `None` when it cannot refresh."""

    used: Authorised


@dataclass(frozen=True, slots=True)
class CacheGet:
    """Effect: read a kept answer from the store."""

    key: str


@dataclass(frozen=True, slots=True)
class CacheSet:
    """Effect: keep an answer in the store."""

    key: str
    entry: CacheEntry
    ttl: float


ABSENT: Any = object()
"""A request with no body."""


@dataclass(slots=True)
class Call(Generic[T]):
    """One request, and how to decode its answer.

    `decode` receives the answer and one flag: `stale` for a read through the cache, `replayed`
    for any other request.
    """

    method: str
    path: str
    decode: Callable[[Obj, bool], T]
    timeout: float | None = None
    query: Mapping[str, str | None] = field(default_factory=dict[str, str | None])
    body: Any = ABSENT
    headers: Mapping[str, str | None] = field(default_factory=dict[str, str | None])
    idempotency_key: str | None = None
    cached: bool = False
    revalidate: bool = False
    customer: str | None = None
    changes: tuple[str, ...] = ()
    open: bool = False
    empty: bool = False


def retry_after(headers: Mapping[str, str], now: float) -> float | None:
    """Reads `Retry-After` as seconds: digits, or an HTTP date in any of RFC 9110's three forms."""
    value = headers.get("retry-after", "").strip(" \t")
    if not value:
        return None
    if re.fullmatch(r"[0-9]+", value):
        digits = value.lstrip("0") or "0"
        if len(digits) > len(str(int(LONGEST))):
            return LONGEST
        return float(min(int(digits), int(LONGEST)))
    date = _http_date(value)
    return None if date is None else max(0.0, date - now)


def _http_date(value: str) -> float | None:
    if found := _IMF.fullmatch(value):
        day, month, year, hour, minute, second = found.groups()
    elif found := _RFC850.fullmatch(value):
        day, month, short, hour, minute, second = found.groups()
        this_year = datetime.now(UTC).year
        whole = this_year // 100 * 100 + int(short)
        year = str(whole - 100 if whole > this_year + 50 else whole)
    elif found := _ASCTIME.fullmatch(value):
        month, day, hour, minute, second, year = found.groups()
    else:
        return None
    if month not in _MONTHS:
        return None
    try:
        parsed = datetime(
            int(year),
            _MONTHS.index(month) + 1,
            int(day),
            int(hour),
            int(minute),
            int(second),
            tzinfo=UTC,
        )
    except ValueError:
        return None
    return parsed.timestamp()


def _directive(control: str | None, name: str) -> bool:
    return (
        bool(control)
        and re.search(rf"(?:^|[\s,]){name}(?:$|[\s,=])", control or "", re.I) is not None
    )


def _seconds(digits: str) -> int:
    """A header's run of digits as whole seconds, saturating at `LONGEST_HEADER` however long."""
    significant = digits.lstrip("0") or "0"
    if len(significant) > len(str(LONGEST_HEADER)):
        return LONGEST_HEADER
    return min(int(significant), LONGEST_HEADER)


def max_age(control: str | None) -> int | None:
    """The `max-age` directive of a `Cache-Control` value, in seconds."""
    found = re.search(r"(?:^|[\s,])max-age=([0-9]+)", control or "", re.I)
    return None if found is None else _seconds(found.group(1))


def _age(headers: Mapping[str, str]) -> int | None:
    value = headers.get("age", "").strip()
    return _seconds(value) if re.fullmatch(r"[0-9]+", value) else None


def api_error(received: Received, idempotency_key: str | None, now: float) -> APIError:
    """The `APIError` for a non-2xx answer, reading the error body leniently."""
    redirect = 300 <= received.status < 400 and received.status != 304
    error: Mapping[str, object] = {}
    if not redirect:
        try:
            body: object = answer(received.text).data
        except DecodeError:
            body = None
        found = body.get("error") if isinstance(body, dict) else None
        if isinstance(found, dict):
            error = cast("dict[str, object]", found)
    code = error.get("code")
    message = error.get("message")
    payment = error.get("payment") if received.status == 402 else None
    return APIError(
        status=received.status,
        code=error_code(code) if isinstance(code, str) and code else ErrorCode.HTTP_ERROR,
        message=message
        if isinstance(message, str) and message
        else f"Entitler answered with HTTP {received.status}.",
        request_id=received.headers.get("x-request-id"),
        retry_after=retry_after(received.headers, now),
        idempotency_key=idempotency_key,
        payment=_payment(payment),
    )


def _payment(value: object) -> Payment | None:
    if not isinstance(value, dict):
        return None
    found = cast("dict[str, object]", value)
    status, url = found.get("status"), found.get("url")
    if status not in {item.value for item in PaymentStatus}:
        return None
    return Payment(status=PaymentStatus(str(status)), url=url if isinstance(url, str) else None)


def invalid_response(
    received: Received | None, idempotency_key: str | None, cause: DecodeError
) -> APIError:
    """The `APIError` for a 2xx answer that does not decode, chained to where it failed."""
    error = APIError(
        status=received.status if received else 200,
        code=ErrorCode.INVALID_RESPONSE,
        message=INVALID,
        request_id=received.headers.get("x-request-id") if received else None,
        idempotency_key=idempotency_key if received else None,
    )
    error.__cause__ = cause
    return error


@dataclass(slots=True)
class Answer(Generic[T]):
    """A decoded answer, and whether it was replayed."""

    data: T
    replayed: bool


class Engine:
    """The state one client keeps: settings, the cache's write generations and the outage window."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float,
        max_retries: int,
        max_retry_delay: float,
        stale_for: float,
        cached: bool,
        on_error: Callable[[Exception], None] | None,
        extra_headers: Callable[[], Mapping[str, str]] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_retry_delay = max_retry_delay
        self.stale_for = stale_for
        self.cached = cached
        self.on_error = on_error
        self.extra_headers = extra_headers
        self.closed = False
        self._lock = threading.Lock()
        self._generations: OrderedDict[str, tuple[int, float]] = OrderedDict()
        self._counter = 0
        self._forgotten_until = -math.inf
        self._down_until = 0.0
        self._probing = False

    def ensure_open(self) -> None:
        """Raises the closed error once the client is closed."""
        if self.closed:
            raise closed_error()

    def report(self, error: Exception) -> None:
        """Passes an absorbed error to `on_error`, which can never change a call's outcome."""
        if self.on_error is None:
            return
        with contextlib.suppress(Exception):
            self.on_error(error)

    def send(self, call: Call[T]) -> Gen[Answer[T]]:
        """Sends a call, through the cache when it reads through it."""
        self.ensure_open()
        if self.cached:
            for customer in call.changes:
                self._bump(customer)
        url = self._url(call)
        headers = self._headers(call)
        auth: Authorised = OPEN if call.open else (yield Authorise())
        if not (call.cached and self.cached):
            used = [auth]
            received = yield from self._exchange(call, url, headers, used)
            if received.status == 304:
                raise api_error(received, call.idempotency_key, _clock.now())
            replayed = received.headers.get("idempotent-replayed") == "true"
            if call.empty and received.text == "":
                data = call.decode(Obj({}, ""), replayed)
            else:
                data = self._decode(call, received.text, received, replayed)
            self._down_until = 0.0
            return Answer(data, replayed)
        return (yield from self._cached(call, url, headers, auth))

    def _cached(
        self, call: Call[T], url: str, headers: dict[str, str], auth: Authorised
    ) -> Gen[Answer[T]]:
        key = cache_key(call.method, url, auth, headers)
        started = _clock.now()
        generation = self._generation(call.customer)
        kept = yield from self._get(key)
        now = _clock.now()
        usable = kept is not None and now - kept.received_at.timestamp() < self.stale_for
        if kept is not None and not call.revalidate and self._fresh(kept, call.customer, now):
            return Answer(self._decode(call, kept.body, None, False), False)
        with self._lock:
            down = self._down_until > 0
            in_window = usable and (now < self._down_until or (down and self._probing))
            probe = down and not in_window
            if probe:
                self._probing = True
        if kept is not None and in_window:
            return Answer(self._decode(call, kept.body, None, True), False)
        if kept is not None and kept.etag is not None:
            headers["If-None-Match"] = kept.etag
        used = [auth]
        try:
            received = yield from self._exchange(call, url, headers, used)
            if received.status == 304 and kept is None:
                raise api_error(received, None, _clock.now())
            body = kept.body if kept is not None and received.status == 304 else received.text
            data = self._decode(call, body, received, False)
            self._down_until = 0.0
        except Exception as error:
            if stands_in(error):
                wait = error.retry_after if isinstance(error, APIError) else None
                window = min(max(OUTAGE_WINDOW, wait or 0.0), self.stale_for)
                self._down_until = _clock.now() + window
            if kept is not None and usable and stands_in(error) and used[0] == auth:
                self.report(error)
                return Answer(self._decode(call, kept.body, None, True), False)
            raise
        finally:
            if probe:
                self._probing = False
        unchanged = self._generation(call.customer) == generation
        received_at = _clock.now() if unchanged and self._forgotten_until < started else started
        entry = _entry(received, kept, received_at)
        if not _directive(entry.cache_control, "no-store") and (
            entry.etag is not None or max_age(entry.cache_control) is not None
        ):
            ttl = self.stale_for + (max_age(entry.cache_control) or 0)
            try:
                yield CacheSet(key, entry, ttl)
            except Exception as error:
                self.report(error)
        return Answer(data, False)

    def _get(self, key: str) -> Gen[CacheEntry | None]:
        try:
            entry = yield CacheGet(key)
        except Exception as error:
            self.report(error)
            return None
        if not isinstance(entry, CacheEntry) or entry.v != 1 or entry.received_at.tzinfo is None:
            return None
        return entry

    def _generation(self, customer: str | None) -> int:
        found = None if customer is None else self._generations.get(customer)
        return 0 if found is None else found[0]

    def _bump(self, customer: str) -> None:
        with self._lock:
            self._counter += 1
            self._generations[customer] = (self._counter, _clock.now())
            self._generations.move_to_end(customer)
            if len(self._generations) > TRACKED_CUSTOMERS:
                _, (_, at) = self._generations.popitem(last=False)
                self._forgotten_until = max(self._forgotten_until, at)

    def _fresh(self, entry: CacheEntry, customer: str | None, now: float) -> bool:
        seconds = max_age(entry.cache_control)
        if seconds is None or _directive(entry.cache_control, "no-cache"):
            return False
        received = entry.received_at.timestamp()
        if now - received + (entry.age or 0) >= seconds or received <= self._forgotten_until:
            return False
        found = None if customer is None else self._generations.get(customer)
        return found is None or found[1] < received

    def _decode(self, call: Call[T], text: str, received: Received | None, flag: bool) -> T:
        try:
            return call.decode(answer(text), flag)
        except DecodeError as cause:
            failure = cause
        raise invalid_response(received, call.idempotency_key, failure)

    def _url(self, call: Call[Any]) -> str:
        query = "&".join(
            f"{encode(name)}={encode(value)}"
            for name, value in call.query.items()
            if value is not None
        )
        return f"{self.base_url}{call.path}{'?' + query if query else ''}"

    def _headers(self, call: Call[Any]) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if not call.open and self.extra_headers is not None:
            headers.update(self.extra_headers())
        if call.body is not ABSENT:
            headers["Content-Type"] = "application/json"
        if call.idempotency_key is not None:
            headers["Idempotency-Key"] = call.idempotency_key
        headers.update({name: value for name, value in call.headers.items() if value is not None})
        return headers

    def _exchange(
        self, call: Call[Any], url: str, headers: dict[str, str], used: list[Authorised]
    ) -> Gen[Received]:
        timeout = self.timeout if call.timeout is None else call.timeout
        body = None if call.body is ABSENT else canonical_json(call.body)
        refreshed = False
        retry = 0
        while True:
            failure: Exception
            try:
                received: Received = yield Attempt(
                    call.method,
                    url,
                    {**headers, **used[0].headers},
                    body,
                    timeout,
                    call.idempotency_key,
                )
                redirect = 300 <= received.status < 400 and received.status != 304
                if not redirect and (received.status < 300 or received.status == 304):
                    return received
                failure = api_error(received, call.idempotency_key, _clock.now())
                if redirect:
                    raise failure
                if received.status == 401 and not refreshed and not call.open:
                    refreshed = True
                    newer: Authorised | None = yield Refresh(used[0])
                    if newer is not None:
                        used[0] = newer
                        continue
                if received.status not in RETRIED:
                    raise failure
            except (EntitlerConnectionError, EntitlerTimeoutError) as error:
                failure = error
            wait = failure.retry_after if isinstance(failure, APIError) else None
            if wait is not None and wait > self.max_retry_delay:
                raise failure
            if retry >= self.max_retries:
                raise failure
            backoff = min(8.0, 0.5 * 2 ** min(retry, 5))
            yield Sleep(wait if wait is not None else _clock.jitter() * backoff)
            retry += 1


def cache_key(method: str, url: str, auth: Authorised, headers: Mapping[str, str]) -> str:
    """The cache key: a hash of the request and the credential's own hash, never the credential."""
    return sha256_hex(
        canonical_json(
            [
                "entitler-cache-v1",
                method,
                url,
                auth.kind,
                sha256_hex(auth.credential),
                headers.get("Entitler-As-Of"),
                headers.get("Entitler-Visitor"),
            ]
        )
    )


def _entry(received: Received, kept: CacheEntry | None, received_at: float) -> CacheEntry:
    at = datetime.fromtimestamp(received_at, UTC)
    headers = received.headers
    if received.status == 304 and kept is not None:
        age = _age(headers)
        if age is None and "cache-control" not in headers:
            age = kept.age
        return CacheEntry(
            body=kept.body,
            etag=headers.get("etag", kept.etag),
            cache_control=headers.get("cache-control", kept.cache_control),
            age=age,
            received_at=at,
        )
    return CacheEntry(
        body=received.text,
        etag=headers.get("etag"),
        cache_control=headers.get("cache-control"),
        age=_age(headers),
        received_at=at,
    )
