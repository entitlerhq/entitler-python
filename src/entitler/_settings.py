"""Construction: credentials and options, checked in the order the specification sets."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from typing import Any, TypeAlias, cast

from ._cache import AsyncCacheStore, CacheStore, MemoryCache
from ._engine import Engine
from ._values import require_text, seconds, timeout, trim, whole
from ._visitor import new_visitor_id, visitor_of

OnError: TypeAlias = Callable[[Exception], None]
"""Called with each error a fallback absorbed; a custom store's failures reach it too."""

WAYS_IN = "Create the client with (token=...), (key=..., identity_token=...) or (key=...)."
SIGN_IN = (
    "Sign the customer in first: create the client with (token=...) or "
    "(key=..., identity_token=...)."
)
SERVER_KEY_IN_APP = (
    "A secret key belongs on your server, in EntitlerServer. "
    "Use a publishable key (ent_pk_…) in an app."
)
PUBLISHABLE_KEY = (
    "A publishable key belongs in EntitlerClient. "
    "Use a secret key from the dashboard on your server."
)
BLANK_KEY = "Provide an Entitler API key from the dashboard."
BLANK_IDENTITY = "Provide the identity token your sign-in provider issued."
BLANK_CUSTOMER_CREDENTIAL = "Provide a customer token minted by your server."
CUSTOM_STORE = "In-app clients keep answers in memory: pass a cache size, or turn the cache off."


def server_key(key: object) -> str:
    """Checks a server client's key: not blank, and not publishable."""
    secret = require_text(key, BLANK_KEY)
    if secret.startswith("ent_pk_"):
        raise ValueError(PUBLISHABLE_KEY)
    return secret


def _credential(value: object, blank: str) -> object:
    return value if callable(value) else require_text(value, blank)


def in_app_credentials(
    token: object, key: object, identity_token: object, cache: object
) -> tuple[str, str | None, Any]:
    """Checks an in-app client's credentials, answering its kind, its key and its token."""
    if token is not None:
        if key is not None or identity_token is not None:
            raise TypeError(WAYS_IN)
    elif key is None:
        raise TypeError(WAYS_IN)
    if token is not None:
        kind, publishable, given = "token", None, _credential(token, BLANK_CUSTOMER_CREDENTIAL)
    else:
        publishable = require_text(key, BLANK_KEY)
        given = None if identity_token is None else _credential(identity_token, BLANK_IDENTITY)
        if not publishable.startswith("ent_pk_"):
            raise ValueError(SERVER_KEY_IN_APP)
        kind = "publishable" if given is None else "identity"
    if (cache is not None and not isinstance(cache, int | MemoryCache)) or isinstance(cache, bool):
        raise TypeError(CUSTOM_STORE)
    return kind, publishable, given


class Settings:
    """The options both clients take, checked."""

    def __init__(
        self,
        base_url: object,
        given_timeout: object,
        max_retries: object,
        max_retry_delay: object,
        stale_for: object,
        on_error: OnError | None,
    ) -> None:
        url = require_text(base_url, "Pass base_url as the API's URL.")
        self.base_url = trim(url)
        self.timeout = timeout(given_timeout)
        self.max_retries = whole(max_retries, "Pass max_retries as a whole number of 0 or more.", 0)
        self.max_retry_delay = seconds(
            max_retry_delay, "Pass max_retry_delay as a number of seconds of 0 or more."
        )
        self.stale_for = seconds(stale_for, "Pass stale_for as a number of seconds of 0 or more.")
        if on_error is not None and not callable(on_error):
            raise TypeError("Pass on_error as a function that takes the error.")
        self.on_error = on_error

    def store(self, cache: object, *, custom: bool) -> CacheStore | None:
        """A blocking client's store: a new in-memory store of the size given, a store, or none."""
        found = self.async_store(cache, custom=custom)
        if found is not None and inspect.iscoroutinefunction(found.get):
            raise TypeError("Pass an asynchronous cache store to AsyncEntitlerServer.")
        return cast("CacheStore | None", found)

    def async_store(self, cache: object, *, custom: bool) -> CacheStore | AsyncCacheStore | None:
        """The answer cache's store: a new in-memory store of the size given, a store, or none."""
        if cache is None:
            return None
        if isinstance(cache, int) and not isinstance(cache, bool):
            return MemoryCache(cache)
        if isinstance(cache, MemoryCache):
            return cache
        if custom and isinstance(cache, CacheStore | AsyncCacheStore):
            return cache
        raise TypeError("Pass cache as a size, a cache store or None." if custom else CUSTOM_STORE)

    def visitor(self, given: object) -> str:
        """The in-app client's visitor: the one given, else a new one."""
        return visitor_of(given) or new_visitor_id()

    def engine(
        self, *, cached: bool, extra_headers: Callable[[], Mapping[str, str]] | None = None
    ) -> Engine:
        """The client's engine."""
        return Engine(
            base_url=self.base_url,
            timeout=self.timeout,
            max_retries=self.max_retries,
            max_retry_delay=self.max_retry_delay,
            stale_for=self.stale_for,
            cached=cached,
            on_error=self.on_error,
            extra_headers=extra_headers,
        )
