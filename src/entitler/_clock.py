"""Time and randomness, in one place so the SDK's own tests can replace them."""

from __future__ import annotations

import random as _random
import threading
import time

import anyio

MAX_WAIT = min(threading.TIMEOUT_MAX, 1e9)


def now() -> float:
    """Seconds since 1970."""
    return time.time()


def jitter() -> float:
    """A random number from 0 to 1."""
    return _random.random()  # noqa: S311 - backoff jitter, not security


def wait(stop: threading.Event, seconds: float) -> bool:
    """Waits up to `seconds`, answering early with `True` once `stop` is set."""
    return stop.wait(min(seconds, MAX_WAIT))


async def sleep(seconds: float) -> None:
    """Sleeps without blocking the event loop."""
    await anyio.sleep(min(seconds, MAX_WAIT))
