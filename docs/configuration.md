# Configuration

Both clients take the same keyword-only options:

| Option | Default | Meaning |
| --- | --- | --- |
| `base_url` | `"https://api.entitler.dev"` | Entitler's API; trailing slashes are removed |
| `timeout` | `10.0` | each attempt's deadline in seconds ([reliability](reliability.md)) |
| `max_retries` | `2` | retries after the first attempt |
| `max_retry_delay` | `10.0` | the longest `Retry-After`, in seconds, the client waits for |
| `cache` | `1000` | the in-memory cache's size, `None` for no cache, or a `MemoryCache`; the server client also takes your own `CacheStore` |
| `stale_for` | `86400.0` | how long, in seconds, a kept answer may stand in while Entitler is unreachable |
| `on_error` | `None` | called with each error a fallback absorbed |
| `http_client` | a client of its own | your `httpx.Client` (`httpx.AsyncClient` for the async clients) |

The in-app client also takes `visitor=` ([visitors](pricing-and-visitors.md)). Every method takes
`timeout=` for that call.

```python
import os

import httpx

from entitler import EntitlerServer

configured = EntitlerServer(
    os.environ["ENTITLER_KEY"],
    timeout=5.0,
    max_retries=3,
    cache=5_000,
    http_client=httpx.Client(proxy="http://proxy.internal:3128"),
)
```

## Your own HTTP client

Pass an `httpx.Client` for a proxy, custom certificates or instrumentation. The SDK never closes a
client you pass; close it yourself. The SDK never follows redirects, whatever the client's
`follow_redirects` says, so a credential never reaches a second origin. Do not use a caching
transport (such as hishel's): the SDK's own cache is the only cache.

## Creating the server client on first use

Create one server client per process and share it. Where something imports your module without
the key in the environment (a build step, a type checker, a test collector), create the client on
first use instead of at import:

```python
import functools


@functools.cache
def entitler() -> EntitlerServer:
    return EntitlerServer(os.environ["ENTITLER_KEY"])


def can_export(user_id: str) -> bool:
    return entitler().customer(user_id).is_entitled(features.EXPORT_PDF, default=False)
```

Async apps create the async client in the app's lifespan, as the
[FastAPI guide](fastapi.md) shows.

## Closing

Clients are context managers: `with EntitlerServer(...) as server:`, or `async with` for the async
clients, which close them on exit. `close()` (`aclose()` on the async clients) does the same;
later calls raise `RuntimeError`. A long-running server usually keeps its client for the life of
the process.
