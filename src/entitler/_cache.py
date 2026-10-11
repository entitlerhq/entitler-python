"""The answer cache's entries and stores."""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True, kw_only=True)
class CacheEntry:
    """One kept answer: plain strings, numbers and an aware `datetime`, so pickle or JSON keeps it.

    Entries are private to this SDK: sharing a store with an SDK in another language is not
    supported.
    """

    body: str = field(repr=False)
    """The answer body, as the JSON text Entitler sent."""
    etag: str | None
    """The answer's `ETag`, for revalidating it."""
    cache_control: str | None
    """The answer's `Cache-Control` directives, such as `private, max-age=60`."""
    age: int | None
    """The answer's `Age` header in seconds: how old it already was when it arrived."""
    received_at: datetime
    """When the answer was received."""
    v: int = 1
    """The entry format's version, `1`."""


@runtime_checkable
class CacheStore(Protocol):
    """Where a server client keeps answers: the default `MemoryCache`, or your own store.

    A store such as Redis shares answers between processes. Keys are SHA-256 hashes and never
    contain a credential. A `get` that raises counts as a miss and a `set` that raises is skipped;
    both go to `on_error` and neither fails the call.

    Example:
        ```python
        class RedisCache:
            def get(self, key: str) -> CacheEntry | None:
                kept = redis.get(f"entitler:{key}")
                if kept is None:
                    return None
                fields = json.loads(kept)
                fields["received_at"] = datetime.fromisoformat(fields["received_at"])
                return CacheEntry(**fields)

            def set(self, key: str, entry: CacheEntry, ttl: float) -> None:
                fields = {**dataclasses.asdict(entry), "received_at": entry.received_at.isoformat()}
                redis.set(f"entitler:{key}", json.dumps(fields), ex=int(ttl) + 1)
        ```
    """

    def get(self, key: str) -> CacheEntry | None:
        """Answers the entry kept under `key`, or `None`."""
        ...

    def set(self, key: str, entry: CacheEntry, ttl: float) -> None:
        """Keeps `entry` under `key`, replacing any entry there.

        `ttl` is how long the entry stays useful, in seconds: the client's `stale_for` plus the
        entry's `max-age`. A store that cannot expire entries should drop them after that time.
        """
        ...


@runtime_checkable
class AsyncCacheStore(Protocol):
    """An asynchronous `CacheStore`, for `AsyncEntitlerServer`."""

    async def get(self, key: str) -> CacheEntry | None:
        """Answers the entry kept under `key`, or `None`."""
        ...

    async def set(self, key: str, entry: CacheEntry, ttl: float) -> None:
        """Keeps `entry` under `key` for `ttl` seconds, replacing any entry there."""
        ...


class MemoryCache:
    """The in-memory store: the least recently used answer goes first once it is full.

    It is safe to share between threads.
    """

    __slots__ = ("_entries", "_lock", "max_entries")

    def __init__(self, max_entries: int = 1000) -> None:
        """Creates a store holding at most `max_entries` answers.

        Raises:
            ValueError: `max_entries` is not a whole number of 1 or more.
        """
        if isinstance(max_entries, bool) or not isinstance(max_entries, int) or max_entries < 1:
            raise ValueError("Pass max_entries as a whole number of 1 or more.")
        self.max_entries = max_entries
        """How many answers it keeps."""
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> CacheEntry | None:
        """Answers the entry kept under `key`, and marks it as recently used."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
            return entry

    def set(self, key: str, entry: CacheEntry, ttl: float) -> None:
        """Keeps `entry` under `key`, dropping the least recently used entry when full."""
        with self._lock:
            self._entries[key] = entry
            self._entries.move_to_end(key)
            if len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        """Drops every entry."""
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:
        return f"MemoryCache(max_entries={self.max_entries})"
