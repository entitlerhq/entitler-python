# Reliability

The clients are built so that Entitler being slow or down never takes your app down with it.

## Timeouts

Each attempt has a deadline: the client's `timeout` (10 seconds by default), overridable per call
with `timeout=`. Timeouts are float seconds, finite and above 0: `None`, `inf`, `nan`, 0 and
negatives raise `ValueError` (`Pass timeout as a number of seconds above 0.`) before a request or a
token provider runs. Leave a call's `timeout` out to use the client's. Every wait and timeout is
clamped to what `time.sleep`, anyio and httpx can represent.

- The blocking client hands the deadline to httpx, which applies it to each phase of the attempt
  (connecting, writing, each read), so a very slow trickle of bytes can take longer in total.
- The async client also bounds the whole attempt with `anyio.fail_after`.
- An attempt past its deadline raises `EntitlerTimeoutError` once its retries are spent.

Blocking calls have no cancellation token in Python, so `timeout=` is how a caller bounds them. In
async code, task cancellation (asyncio or trio) propagates unwrapped and ends a retry wait at once.

## Retries

Connection failures, timeouts, and the answers `408`, `429`, `500`, `502`, `503` and `504` are
retried, for every request: reads are safe, and every write carries an idempotency key, so a
retried write never applies twice. Nothing else is retried.

- `max_retries` (2 by default) retries after the first attempt, waiting a random time up to
  `min(8, 0.5 × 2^n)` seconds before retry `n`.
- A `Retry-After` answer header is honoured. When it asks for longer than `max_retry_delay` (10
  seconds by default), the client fails at once with that answer's `APIError`, whose `retry_after`
  (float seconds) says when to try again; a value too large to represent saturates at
  `sys.float_info.max`.
- After a `401`, an in-app client with a token provider refreshes the token and retries once, on
  top of the budget.

Every attempt of one call sends the same `Idempotency-Key`, and a failed write's error carries it
(`error.idempotency_key`), so you can repeat the call later with that key. A write you repeat
answers `replayed` true when Entitler already applied it.

## The answer cache

Checks, entitlement lists, `plans()`, pricing and `features()` go through a cache, on by default
with 1,000 answers in memory. Entitler says how long each answer stays fresh (`max-age`); a fresh
answer is served without a request, and an older one is revalidated with its `ETag`, so a `304`
costs no body. Answers that hold a meter are revalidated every time. A write through the client
marks that customer's kept answers for revalidation, so a read after a write never answers from
before it.

```python
from entitler import EntitlerServer, MemoryCache

large = EntitlerServer("ent_test_...", cache=10_000)  # a bigger in-memory cache
shared = EntitlerServer("ent_test_...", cache=MemoryCache(max_entries=500))
uncached = EntitlerServer("ent_test_...", cache=None)
```

The client's cache is the only cache: httpx keeps no HTTP cache, and an injected `http_client`
must not use a caching transport (such as hishel's), or revalidation after a write could miss
Entitler.

### A shared store

A server client also takes your own store, so processes share answers through Redis or similar. A
store has `get(key)` and `set(key, entry, ttl)` (both `async` for `AsyncEntitlerServer`). Keys are
SHA-256 hashes and never hold a credential. `ttl` is `stale_for` plus the entry's `max-age`, in
seconds; a store that cannot expire entries should drop them after that time.

```python
import dataclasses
import json
from datetime import datetime
from typing import Protocol

from entitler import CacheEntry


class Redis(Protocol):
    def get(self, name: str) -> bytes | None: ...
    def set(self, name: str, value: str, ex: int) -> object: ...


class RedisCache:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    def get(self, key: str) -> CacheEntry | None:
        kept = self.redis.get(f"entitler:{key}")
        if kept is None:
            return None
        fields = json.loads(kept)
        fields["received_at"] = datetime.fromisoformat(fields["received_at"])
        return CacheEntry(**fields)

    def set(self, key: str, entry: CacheEntry, ttl: float) -> None:
        fields = {**dataclasses.asdict(entry), "received_at": entry.received_at.isoformat()}
        self.redis.set(f"entitler:{key}", json.dumps(fields), ex=int(ttl) + 1)
```

A `get` that raises counts as a miss and a `set` that raises is skipped; both go to `on_error`,
and neither fails the call. Prefer JSON to pickle for a shared store, since unpickling runs
whatever the store holds.

## Stale answers while Entitler is unreachable

When a cached read fails because Entitler is unreachable (a connection failure, a timeout, `429` or
a `5xx` after retries, or an answer that does not decode, as a captive portal sends), and the cache
holds an answer received less than `stale_for` ago (24 hours by default), the read answers it with
`stale` true and passes the error to `on_error`.

For the next 30 seconds (or the failed answer's `Retry-After`, if longer), reads with a kept answer
are answered from it without a request; then one read tests Entitler again. So an outage costs one
slow read, not one per call. Any successful answer ends that window.

A kept answer stands in only for the credential that kept it: a new token's reads never see the
last token's answers. Apps that must work offline after a relaunch use
[snapshots](offline-snapshots.md).

## `on_error`

```python
import logging

from entitler import EntitlerServer

log = logging.getLogger("entitler")


def report(error: Exception) -> None:
    log.warning("Entitler fallback: %s", error)


observed = EntitlerServer("ent_test_...", on_error=report)
```

`on_error` receives each error a fallback absorbed: stale answers, `is_entitled` answering its
default, a custom store's failures, and a hold's settle or release that failed during cleanup. It
never changes a call's answer, and an `on_error` that raises is ignored.

## Threads and event loops

The blocking clients are thread-safe: share one per process (Django and Celery run threads). The
async clients belong to one event loop; create them in the app's lifespan.
