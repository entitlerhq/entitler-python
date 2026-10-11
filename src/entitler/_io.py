"""The blocking and async drivers: the only code that sends requests, sleeps or asks providers."""

from __future__ import annotations

import base64
import binascii
import inspect
import json
import re
import threading
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast

import anyio
import httpx

from . import _clock
from ._cache import AsyncCacheStore, CacheStore
from ._engine import (
    Attempt,
    Authorise,
    Authorised,
    CacheGet,
    CacheSet,
    Engine,
    Gen,
    Received,
    Refresh,
    Sleep,
    closed_error,
)
from ._errors import EntitlerConnectionError, EntitlerTimeoutError, TokenError
from ._values import trim

T = TypeVar("T")

TokenProvider = Callable[[], str]
"""A function that answers a fresh token, such as one that asks your server for a customer token."""

AsyncTokenProvider = Callable[[], Awaitable[str]]
"""An async function that answers a fresh token."""

REFRESH_BEFORE = 60.0
_SEGMENT = re.compile(r"[A-Za-z0-9_-]*")


class _Unavailable(Exception):
    """A sanitised summary of an HTTP library failure, kept in place of the error itself."""


def claims(token: str) -> dict[str, object] | None:
    """Reads a JWT's claims without verifying it, or answers `None` when it is not one."""
    parts = token.split(".")
    if len(parts) != 3 or not parts[1] or not _SEGMENT.fullmatch(parts[1]):
        return None
    try:
        decoded = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        found = json.loads(decoded.decode("utf-8"))
    except (ValueError, binascii.Error, RecursionError):
        return None
    return cast("dict[str, object]", found) if isinstance(found, dict) else None


def _whole(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def readable(token: str) -> bool:
    """True for a token whose claims hold an integer `exp`, and any `iat` an integer before it."""
    found = claims(token)
    if found is None or not _whole(found.get("exp")):
        return False
    issued = found.get("iat")
    return issued is None or (_whole(issued) and cast("int", issued) < cast("int", found["exp"]))


def expires_soon(token: str, received_at: float, now: float) -> bool:
    """True when the token expires within min(60 seconds, half its lifetime), the edge included."""
    found = claims(token) or {}
    expires = found.get("exp")
    if not _whole(expires):
        return False
    issued = found.get("iat")
    start = float(cast("int", issued)) if _whole(issued) else received_at
    end = float(cast("int", expires))
    return end - now <= min(REFRESH_BEFORE, (end - start) / 2)


class _TokenState:
    """A token kept from a fixed string or a provider."""

    def __init__(self, given: object, blank: str) -> None:
        self.refreshable = callable(given)
        self.current: str | None = None if self.refreshable else trim(cast("str", given))
        self.received_at = 0.0
        self.blank = blank

    def kept(self) -> str | None:
        current = self.current
        if current is None or not self.refreshable:
            return current
        return None if expires_soon(current, self.received_at, _clock.now()) else current

    def accept(self, answer: object) -> str:
        token = trim(answer) if isinstance(answer, str) else ""
        if not token:
            raise TokenError("The token provider answered a blank token.")
        if not readable(token):
            raise TokenError("The token provider answered a token that is not a readable JWT.")
        self.current = token
        self.received_at = _clock.now()
        return token


class SyncToken:
    """A token for the blocking client: one refresh at a time, behind a lock."""

    def __init__(self, given: str | TokenProvider, blank: str) -> None:
        self._state = _TokenState(given, blank)
        self._provider = given if callable(given) else None
        self._lock = threading.Lock()

    @property
    def refreshable(self) -> bool:
        """True for a provider."""
        return self._provider is not None

    def get(self) -> str:
        """The kept token, or a new one from the provider."""
        kept = self._state.kept()
        if kept is not None:
            return kept
        with self._lock:
            kept = self._state.kept()
            return kept if kept is not None else self._ask()

    def refresh(self, used: str) -> str:
        """A newer token after a `401`: the one kept when it differs from `used`, else a new one."""
        with self._lock:
            current = self._state.current
            return current if current is not None and current != used else self._ask()

    def _ask(self) -> str:
        provider = cast("TokenProvider", self._provider)
        try:
            answer = provider()
        except Exception as error:
            raise TokenError("The token provider failed.") from error
        return self._state.accept(answer)


class AsyncToken:
    """An async client's token: one refresh at a time, which a cancelled waiter never stops."""

    def __init__(self, given: str | AsyncTokenProvider, blank: str, driver: AsyncDriver) -> None:
        self._state = _TokenState(given, blank)
        self._provider = given if callable(given) else None
        self._driver = driver
        self._lock: anyio.Lock | None = None

    @property
    def refreshable(self) -> bool:
        """True for a provider."""
        return self._provider is not None

    def _guard(self) -> anyio.Lock:
        if self._lock is None:
            self._lock = anyio.Lock()
        return self._lock

    async def get(self) -> str:
        """The kept token, or a new one from the provider."""
        kept = self._state.kept()
        if kept is not None:
            return kept
        async with self._guard():
            kept = self._state.kept()
            return kept if kept is not None else await self._ask()

    async def refresh(self, used: str) -> str:
        """A newer token after a `401`: the one kept when it differs from `used`, else a new one."""
        async with self._guard():
            current = self._state.current
            return current if current is not None and current != used else await self._ask()

    async def _ask(self) -> str:
        provider = cast("AsyncTokenProvider", self._provider)
        timeout = self._driver.engine.timeout
        failure: TokenError | None = None
        answer: object = None
        deadline = anyio.CancelScope()
        with anyio.CancelScope(shield=True) as scope:
            self._driver.scopes.add(scope)
            try:
                with anyio.move_on_after(min(timeout, _clock.MAX_WAIT)) as deadline:
                    try:
                        answer = provider()
                        if inspect.isawaitable(answer):
                            answer = await answer
                    except Exception as error:
                        failure = TokenError("The token provider failed.")
                        failure.__cause__ = error
            finally:
                self._driver.scopes.discard(scope)
        if scope.cancel_called:
            raise closed_error()
        if deadline.cancelled_caught:
            raise TokenError(f"The token provider did not answer within {timeout:g} seconds.")
        if failure is not None:
            raise failure
        return self._state.accept(answer)


class SyncCredentials:
    """How a blocking client signs its requests."""

    def __init__(self, kind: str, key: str | None = None, token: SyncToken | None = None) -> None:
        self.kind = kind
        self._key = key
        self._token = token

    def authorise(self) -> Authorised:
        """The headers for the next request."""
        return _signed(self.kind, self._key, None if self._token is None else self._token.get())

    def refresh(self, used: Authorised) -> Authorised | None:
        """Newer headers after a `401`, or `None` when the credential cannot refresh."""
        if self._token is None or not self._token.refreshable:
            return None
        return _signed(self.kind, self._key, self._token.refresh(cast("str", used.token)))


class AsyncCredentials:
    """How an async client signs its requests."""

    def __init__(self, kind: str, key: str | None = None, token: AsyncToken | None = None) -> None:
        self.kind = kind
        self._key = key
        self.token = token

    async def authorise(self) -> Authorised:
        """The headers for the next request."""
        token = None if self.token is None else await self.token.get()
        return _signed(self.kind, self._key, token)

    async def refresh(self, used: Authorised) -> Authorised | None:
        """Newer headers after a `401`, or `None` when the credential cannot refresh."""
        if self.token is None or not self.token.refreshable:
            return None
        return _signed(self.kind, self._key, await self.token.refresh(cast("str", used.token)))


def _signed(kind: str, key: str | None, token: str | None) -> Authorised:
    if kind == "token":
        value = cast("str", token)
        return Authorised({"Authorization": f"Bearer {value}"}, "customer-token", value, value)
    if kind == "identity":
        value = cast("str", token)
        headers = {"Authorization": f"Bearer {key}", "Entitler-Identity-Token": value}
        return Authorised(headers, "identity", f"{key}\n{value}", value)
    return Authorised({"Authorization": f"Bearer {key}"}, "key", cast("str", key))


def _received(response: httpx.Response) -> Received:
    try:
        text = response.content.decode("utf-8")
    except UnicodeDecodeError:
        text = "�"
    headers = {name.lower(): value for name, value in response.headers.items()}
    return Received(response.status_code, headers, text)


def _request(client: httpx.Client | httpx.AsyncClient, attempt: Attempt) -> httpx.Request:
    seconds = min(attempt.timeout, _clock.MAX_WAIT)
    return client.build_request(
        attempt.method,
        attempt.url,
        headers=dict(attempt.headers),
        content=None if attempt.body is None else attempt.body.encode("utf-8"),
        timeout=httpx.Timeout(seconds),
    )


def _failure(error: Exception, attempt: Attempt) -> Exception:
    if isinstance(error, httpx.TimeoutException | TimeoutError):
        return EntitlerTimeoutError(
            f"Entitler did not answer within {attempt.timeout:g} seconds.",
            idempotency_key=attempt.idempotency_key,
        )
    failure = EntitlerConnectionError(
        "Entitler could not be reached.", idempotency_key=attempt.idempotency_key
    )
    failure.__cause__ = _Unavailable(f"{type(error).__name__}: {error}")
    return failure


class SyncDriver:
    """Runs calls with blocking I/O, from any thread."""

    def __init__(
        self,
        engine: Engine,
        credentials: SyncCredentials,
        store: CacheStore | None,
        http_client: httpx.Client | None,
    ) -> None:
        self.engine = engine
        self.credentials = credentials
        self.store = store
        self._client = http_client or httpx.Client(follow_redirects=False)
        self._owns_client = http_client is None
        self._stop = threading.Event()

    def run(self, call: Gen[T]) -> T:
        """Runs a call to its answer."""
        value: Any = None
        error: Exception | None = None
        while True:
            try:
                effect = call.send(value) if error is None else call.throw(error)
            except StopIteration as stop:
                return cast("T", stop.value)
            value, error = None, None
            try:
                value = self._perform(effect)
            except Exception as failure:
                error = failure
            except BaseException:
                call.close()
                raise

    def _perform(self, effect: object) -> object:
        if isinstance(effect, Attempt):
            return self._attempt(effect)
        if isinstance(effect, Sleep):
            if _clock.wait(self._stop, effect.seconds) or self.engine.closed:
                raise closed_error()
            return None
        if isinstance(effect, Authorise):
            return self.credentials.authorise()
        if isinstance(effect, Refresh):
            return self.credentials.refresh(effect.used)
        if isinstance(effect, CacheGet):
            return None if self.store is None else self.store.get(effect.key)
        if isinstance(effect, CacheSet) and self.store is not None:
            self.store.set(effect.key, effect.entry, effect.ttl)
        return None

    def _attempt(self, attempt: Attempt) -> Received:
        self.engine.ensure_open()
        failure: Exception
        try:
            response = self._client.send(_request(self._client, attempt), follow_redirects=False)
            return _received(response)
        except (httpx.TransportError, httpx.StreamError, OSError) as error:
            failure = _failure(error, attempt)
        if self.engine.closed:
            raise closed_error()
        raise failure

    def close(self) -> None:
        """Stops waits in flight and releases an HTTP client the driver built itself."""
        self.engine.closed = True
        self._stop.set()
        if self._owns_client:
            self._client.close()


class AsyncDriver:
    """Runs calls with async I/O, on one event loop (asyncio or trio)."""

    def __init__(
        self,
        engine: Engine,
        store: CacheStore | AsyncCacheStore | None,
        http_client: httpx.AsyncClient | None,
    ) -> None:
        self.engine = engine
        self.store = store
        self.credentials = AsyncCredentials("server")
        self.scopes: set[anyio.CancelScope] = set()
        self._client = http_client or httpx.AsyncClient(follow_redirects=False)
        self._owns_client = http_client is None

    async def run(self, call: Gen[T]) -> T:
        """Runs a call to its answer; closing the client cancels it."""
        self.engine.ensure_open()
        with anyio.CancelScope() as scope:
            self.scopes.add(scope)
            try:
                return await self._drive(call)
            finally:
                self.scopes.discard(scope)
        raise closed_error()

    async def _drive(self, call: Gen[T]) -> T:
        value: Any = None
        error: Exception | None = None
        while True:
            try:
                effect = call.send(value) if error is None else call.throw(error)
            except StopIteration as stop:
                return cast("T", stop.value)
            value, error = None, None
            try:
                value = await self._perform(effect)
            except Exception as failure:
                error = failure
            except BaseException:
                call.close()
                raise

    async def _perform(self, effect: object) -> object:
        if isinstance(effect, Attempt):
            return await self._attempt(effect)
        if isinstance(effect, Sleep):
            await _clock.sleep(effect.seconds)
            return None
        if isinstance(effect, Authorise):
            return await self.credentials.authorise()
        if isinstance(effect, Refresh):
            return await self.credentials.refresh(effect.used)
        if isinstance(effect, CacheGet):
            return None if self.store is None else await _maybe(self.store.get(effect.key))
        if isinstance(effect, CacheSet) and self.store is not None:
            await _maybe(self.store.set(effect.key, effect.entry, effect.ttl))
        return None

    async def _attempt(self, attempt: Attempt) -> Received:
        self.engine.ensure_open()
        failure: Exception
        try:
            with anyio.fail_after(min(attempt.timeout, _clock.MAX_WAIT)):
                response = await self._client.send(
                    _request(self._client, attempt), follow_redirects=False
                )
            return _received(response)
        except (httpx.TransportError, httpx.StreamError, OSError) as error:
            failure = _failure(error, attempt)
        raise failure

    async def aclose(self) -> None:
        """Cancels the calls in flight and the pending refresh, and releases its own HTTP client."""
        self.engine.closed = True
        for scope in list(self.scopes):
            scope.cancel()
        if self._owns_client:
            await self._client.aclose()


async def _maybe(value: object) -> object:
    return await value if inspect.isawaitable(value) else value
