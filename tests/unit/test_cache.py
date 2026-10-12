"""The answer cache, stale answers and the outage window (sections 7.3 and 7.4)."""

from __future__ import annotations

import contextlib
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import anyio
import anyio.lowlevel
import httpx
import pytest

from entitler import (
    APIError,
    AsyncEntitlerServer,
    CacheEntry,
    EntitlerClient,
    EntitlerConnectionError,
    EntitlerServer,
    Feature,
    MemoryCache,
    TokenError,
    is_unreachable,
)
from entitler.testing import answers

from .support import KEY, Clock, Transport, ok

CHECK = answers.check()
FRESH = {"ETag": '"v1"', "Cache-Control": "private, max-age=60"}
METER = {"ETag": '"v1"', "Cache-Control": "private, no-cache"}


def server_with(http: httpx.Client) -> EntitlerServer:
    return EntitlerServer(KEY, http_client=http, max_retries=0)


def server(transport: Transport, **options: Any) -> EntitlerServer:
    options.setdefault("max_retries", 0)
    return EntitlerServer(KEY, http_client=transport.client(), **options)


class Store:
    """A custom store kept in a dict, which can be told to fail."""

    def __init__(self, failing: bool = False) -> None:
        self.entries: dict[str, CacheEntry] = {}
        self.ttls: list[float] = []
        self.failing = failing

    def get(self, key: str) -> CacheEntry | None:
        if self.failing:
            raise OSError("store down")
        return self.entries.get(key)

    def set(self, key: str, entry: CacheEntry, ttl: float) -> None:
        if self.failing:
            raise OSError("store down")
        self.entries[key] = entry
        self.ttls.append(ttl)


def test_a_fresh_answer_is_served_without_a_request(transport: Transport, clock: Clock) -> None:
    client = server(transport)
    transport.answers = [ok(CHECK, **FRESH)]
    first = client.customer("u").check("export_pdf")
    second = client.customer("u").check("export_pdf")
    assert first == second
    assert first is not second
    assert len(transport.requests) == 1
    clock.now += 61
    transport.answers = [{"status": 304, "headers": {"ETag": '"v1"'}}]
    assert client.customer("u").check("export_pdf").entitled
    assert transport.requests[-1].headers["If-None-Match"] == '"v1"'


def test_revalidate_skips_a_fresh_answer_and_sends_its_etag(
    transport: Transport, clock: Clock
) -> None:
    client = server(transport)
    transport.answers = [ok(CHECK, **FRESH), {"status": 304}]
    client.customer("u").check("export_pdf")
    client.customer("u").check("export_pdf", revalidate=True)
    assert len(transport.requests) == 2
    assert transport.requests[1].headers["If-None-Match"] == '"v1"'


def test_no_cache_answers_are_revalidated_every_time(transport: Transport, clock: Clock) -> None:
    client = server(transport)
    transport.answers = [ok(CHECK, **METER), {"status": 304}, {"status": 304}]
    for _ in range(3):
        client.customer("u").check("export_pdf")
    assert len(transport.requests) == 3
    no_cache_with_max_age = {"ETag": '"v"', "Cache-Control": "no-cache, max-age=600"}
    transport.answers = [ok(CHECK, **no_cache_with_max_age), {"status": 304}]
    client.customer("w").check("export_pdf")
    client.customer("w").check("export_pdf")
    assert len(transport.requests) == 5


def test_an_aged_answer_is_fresh_for_less_time(transport: Transport, clock: Clock) -> None:
    client = server(transport)
    transport.answers = [ok(CHECK, Age="50", **FRESH), {"status": 304}]
    client.customer("u").check("export_pdf")
    clock.now += 11
    client.customer("u").check("export_pdf")
    assert len(transport.requests) == 2


def test_a_304_renews_the_entry_and_keeps_headers_it_leaves_out(
    transport: Transport, clock: Clock
) -> None:
    store = Store()
    client = server(transport, cache=store)
    transport.answers = [ok(CHECK, **FRESH), {"status": 304, "headers": {"ETag": '"v2"'}}]
    client.customer("u").check("export_pdf")
    clock.now += 100
    client.customer("u").check("export_pdf")
    (entry,) = store.entries.values()
    assert entry.etag == '"v2"'
    assert entry.cache_control == "private, max-age=60"
    renewed = entry.received_at - datetime.fromtimestamp(clock.now, UTC)
    assert timedelta(0) <= renewed <= timedelta(microseconds=1)
    assert store.ttls == [86400 + 60, 86400 + 60]


def test_with_a_clock_that_never_moves_a_read_answered_after_a_write_stays_fresh(
    transport: Transport, clock: Clock
) -> None:
    client = server(transport)
    transport.answers = [ok(answers.usage_result()), ok(CHECK, **FRESH)]
    customer = client.customer("u")
    customer.record_usage(Feature.metered("ai_credits"), 1, idempotency_key="k")
    customer.check("export_pdf")
    customer.check("export_pdf")
    assert len(transport.requests) == 2


def test_an_answer_exactly_max_age_old_is_not_fresh(transport: Transport, clock: Clock) -> None:
    store = Store()
    client = server(transport, cache=store)
    transport.answers = [ok(CHECK, **FRESH), {"status": 304}]
    client.customer("u").check("export_pdf")
    (entry,) = store.entries.values()
    clock.now = entry.received_at.timestamp() + 60
    client.customer("u").check("export_pdf")
    assert len(transport.requests) == 2


def test_a_receipt_ahead_of_the_clock_never_counts_as_negative_age(
    transport: Transport, clock: Clock
) -> None:
    client = server(transport)
    stale_on_arrival = {"ETag": '"v1"', "Cache-Control": "max-age=60", "Age": "60"}
    transport.answers = [ok(CHECK, **stale_on_arrival), {"status": 304}]
    client.customer("u").check("export_pdf")
    client.customer("u").check("export_pdf")
    assert len(transport.requests) == 2


def test_a_304_with_nothing_kept_fails(transport: Transport) -> None:
    transport.answers = [{"status": 304}]
    with pytest.raises(APIError) as raised:
        server(transport).customer("u").check("export_pdf")
    assert (raised.value.status, raised.value.code) == (304, "http_error")
    transport.answers = [{"status": 304}]
    with pytest.raises(APIError):
        server(transport, cache=None).customer("u").check("export_pdf")


def test_no_store_and_unmarked_answers_are_never_kept(transport: Transport) -> None:
    store = Store()
    client = server(transport, cache=store)
    transport.answers = [ok(CHECK, ETag='"v"', **{"Cache-Control": "no-store"}), ok(CHECK)]
    client.customer("u").check("export_pdf")
    client.customer("v").check("export_pdf")
    assert store.entries == {}


def test_a_write_makes_the_customers_kept_answers_revalidate(
    transport: Transport, clock: Clock
) -> None:
    client = server(transport)
    transport.fallback = lambda _r: httpx.Response(200, json=answers.usage_result())
    transport.answers = [ok(CHECK, **FRESH)]
    client.customer("u").check("export_pdf")
    clock.now += 1
    client.customer("u").record_usage(Feature.metered("ai_credits"), 1, idempotency_key="k")
    transport.answers = [{"status": 304}]
    client.customer("u").check("export_pdf")
    assert transport.requests[-1].headers["If-None-Match"] == '"v1"'
    transport.answers = [ok(CHECK, **FRESH)]
    client.customer("other").check("export_pdf")
    count = len(transport.requests)
    client.customer("other").check("export_pdf")
    assert len(transport.requests) == count


def test_a_read_that_started_before_a_write_is_not_kept_as_fresh(
    transport: Transport, clock: Clock
) -> None:
    client = server(transport)
    customer = client.customer("u")

    def answer_after_a_write(_request: httpx.Request) -> httpx.Response:
        customer._ops.engine._bump(customer._ops.path)
        clock.now += 1
        return httpx.Response(200, json=CHECK, headers=FRESH)

    transport.answers = [answer_after_a_write, {"status": 304}]
    customer.check("export_pdf")
    customer.check("export_pdf")
    assert len(transport.requests) == 2


def test_write_tracking_is_off_without_a_cache(transport: Transport) -> None:
    transport.fallback = lambda _r: httpx.Response(200, json=answers.usage_result())
    client = server(transport, cache=None)
    client.customer("u").record_usage(Feature.metered("ai_credits"), 1, idempotency_key="k")
    assert client._driver.engine._generations == {}


def test_a_forgotten_customer_still_revalidates_an_entry_from_before_its_write(
    transport: Transport, clock: Clock
) -> None:
    client = server(transport)
    engine = client._driver.engine
    transport.answers = [ok(CHECK, **FRESH)]
    client.customer("first").check("export_pdf")
    clock.now += 1
    engine._bump("/customers/first")
    for index in range(1100):
        engine._bump(f"/customers/c{index}")
    assert "/customers/first" not in engine._generations
    transport.answers = [{"status": 304}]
    client.customer("first").check("export_pdf")
    assert len(transport.requests) == 2


def test_a_customer_forgotten_while_its_read_is_in_flight_is_revalidated(
    transport: Transport, clock: Clock
) -> None:
    client = server(transport)
    engine = client._driver.engine

    def answer_after_a_write_and_many_more(_request: httpx.Request) -> httpx.Response:
        engine._bump("/customers/u")
        clock.now += 1
        for index in range(1000):
            engine._bump(f"/customers/c{index}")
        return httpx.Response(200, json=CHECK, headers=FRESH)

    transport.answers = [answer_after_a_write_and_many_more, {"status": 304}]
    client.customer("u").check("export_pdf")
    assert "/customers/u" not in engine._generations
    client.customer("u").check("export_pdf")
    assert len(transport.requests) == 2


def test_many_thousands_of_writes_stay_fast(transport: Transport) -> None:
    engine = server(transport)._driver.engine
    started = time.perf_counter()
    for index in range(50_000):
        engine._bump(f"/customers/c{index}")
    assert time.perf_counter() - started < 2.0
    assert len(engine._generations) == 1000


def test_the_memory_cache_drops_the_least_recently_used(transport: Transport) -> None:
    memory = MemoryCache(max_entries=2)
    client = server(transport, cache=memory)
    transport.fallback = lambda _r: httpx.Response(200, json=CHECK, headers=FRESH)
    for name in ("a", "b", "a", "c"):
        client.customer(name).check("export_pdf")
    assert len(memory) == 2
    count = len(transport.requests)
    client.customer("a").check("export_pdf")
    client.customer("b").check("export_pdf")
    assert len(transport.requests) == count + 1
    with pytest.raises(ValueError, match="max_entries"):
        MemoryCache(max_entries=0)
    assert repr(memory) == "MemoryCache(max_entries=2)"


def test_the_memory_cache_is_safe_across_threads(transport: Transport) -> None:
    client = server(transport, cache=MemoryCache(max_entries=50))
    transport.fallback = lambda _r: httpx.Response(200, json=CHECK, headers=FRESH)
    errors: list[BaseException] = []

    def work(index: int) -> None:
        try:
            for round_ in range(20):
                client.customer(f"c{(index + round_) % 80}").check("export_pdf")
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []


def test_a_failing_store_counts_as_a_miss_and_goes_to_on_error(transport: Transport) -> None:
    seen: list[Exception] = []
    client = server(transport, cache=Store(failing=True), on_error=seen.append)
    transport.fallback = lambda _r: httpx.Response(200, json=CHECK, headers=FRESH)
    assert client.customer("u").check("export_pdf").entitled
    assert [str(error) for error in seen] == ["store down", "store down"]


def test_a_store_holding_something_else_is_a_miss(transport: Transport) -> None:
    store = Store()
    client = server(transport, cache=store)
    transport.fallback = lambda _r: httpx.Response(200, json=CHECK, headers=FRESH)
    client.customer("u").check("export_pdf")
    for key in store.entries:
        store.entries[key] = "junk"  # type: ignore[assignment]  # a broken store answering junk
    client.customer("u").check("export_pdf")
    assert len(transport.requests) == 2


def test_an_unreachable_read_answers_the_kept_entry_as_stale(
    transport: Transport, clock: Clock
) -> None:
    seen: list[Exception] = []
    client = server(transport, on_error=seen.append)
    transport.answers = [ok(CHECK, **METER), {"failure": "connection"}]
    client.customer("u").check("export_pdf")
    stale = client.customer("u").check("export_pdf")
    assert stale.stale is True
    assert isinstance(seen[0], EntitlerConnectionError)
    assert is_unreachable(seen[0])


@pytest.mark.parametrize(
    "answer",
    [
        {"status": 503},
        {"status": 429},
        {"status": 500},
        {"failure": "timeout"},
        {"status": 200, "body": "<html>captive portal</html>"},
    ],
)
def test_each_unreachable_failure_stands_in(
    answer: dict[str, Any], transport: Transport, clock: Clock
) -> None:
    client = server(transport)
    transport.answers = [ok(CHECK, **METER), answer]
    client.customer("u").check("export_pdf")
    assert client.customer("u").check("export_pdf").stale


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409])
def test_other_failures_are_raised(status: int, transport: Transport, clock: Clock) -> None:
    client = server(transport)
    transport.answers = [ok(CHECK, **METER), {"status": status}]
    client.customer("u").check("export_pdf")
    with pytest.raises(APIError):
        client.customer("u").check("export_pdf")


def test_an_entry_older_than_stale_for_never_stands_in(transport: Transport, clock: Clock) -> None:
    client = server(transport, stale_for=60)
    transport.answers = [ok(CHECK, **METER), {"failure": "connection"}]
    client.customer("u").check("export_pdf")
    clock.now += 61
    with pytest.raises(EntitlerConnectionError):
        client.customer("u").check("export_pdf")


def test_is_entitled_answers_its_default_and_reports(transport: Transport) -> None:
    seen: list[Exception] = []
    client = server(transport, on_error=seen.append)
    transport.answers = [
        {"status": 500},
        {"status": 404, "body": answers.error("feature_not_found")},
    ]
    assert client.customer("u").is_entitled("sso", default=True) is True
    assert client.customer("u").is_entitled("sso", default=False) is False
    assert [getattr(e, "status", None) for e in seen] == [500, 404]
    with pytest.raises(ValueError, match="Name the feature"):
        client.customer("u").is_entitled("", default=False)


def test_on_error_raising_changes_nothing(transport: Transport, clock: Clock) -> None:
    def broken(_error: Exception) -> None:
        raise RuntimeError("on_error broke")

    client = server(transport, on_error=broken, cache=Store(failing=True))
    transport.answers = [{"status": 500}]
    assert client.customer("u").is_entitled("sso", default=True) is True
    good = server(transport, on_error=broken)
    transport.answers = [ok(CHECK, **METER), {"status": 503}]
    good.customer("u").check("export_pdf")
    assert good.customer("u").check("export_pdf").stale


def test_the_outage_window_answers_kept_entries_without_a_request(
    transport: Transport, clock: Clock
) -> None:
    client = server(transport)
    transport.answers = [
        ok(CHECK, **METER),
        ok(answers.check(customer="v"), **METER),
        {"status": 503},
    ]
    client.customer("u").check("export_pdf")
    client.customer("v").check("export_pdf")
    assert client.customer("u").check("export_pdf").stale
    count = len(transport.requests)
    assert client.customer("v").check("export_pdf").stale
    assert len(transport.requests) == count
    transport.answers = [ok(answers.check(customer="w"), **METER)]
    client.customer("w").check("export_pdf")
    assert len(transport.requests) == count + 1
    clock.now += 31
    transport.answers = [{"status": 304}]
    assert not client.customer("v").check("export_pdf").stale
    transport.answers = [{"status": 304}]
    assert not client.customer("u").check("export_pdf").stale


def test_a_huge_retry_after_never_outlasts_stale_for(transport: Transport, clock: Clock) -> None:
    client = server(transport, stale_for=120)
    huge = {"Retry-After": "9" * 400}
    transport.answers = [ok(CHECK, **METER), {"status": 503, "headers": huge}]
    client.customer("u").check("export_pdf")
    client.customer("u").check("export_pdf")
    engine = client._driver.engine
    assert engine._down_until <= clock.now + 120
    clock.now += 121
    transport.answers = [{"status": 304}]
    client.customer("u").check("export_pdf")


def test_a_retry_after_too_long_to_parse_saturates_and_fails_at_once(
    transport: Transport, clock: Clock
) -> None:
    client = server(transport, max_retries=2)
    transport.answers = [{"status": 503, "headers": {"Retry-After": "7" * 5000}}]
    with pytest.raises(APIError) as raised:
        client.customer("u").check("export_pdf")
    assert raised.value.retry_after == sys.float_info.max
    assert len(transport.requests) == 1
    assert client._driver.engine._down_until <= clock.now + client._driver.engine.stale_for


def test_a_successful_write_ends_the_window(transport: Transport, clock: Clock) -> None:
    client = server(transport)
    transport.answers = [ok(CHECK, **METER), {"status": 503}]
    client.customer("u").check("export_pdf")
    client.customer("u").check("export_pdf")
    assert client._driver.engine._down_until > 0
    transport.answers = [ok(answers.usage_result())]
    client.customer("u").record_usage(Feature.metered("ai_credits"), 1, idempotency_key="k")
    assert client._driver.engine._down_until == 0


def test_a_success_ends_the_window(transport: Transport, clock: Clock) -> None:
    client = server(transport)
    transport.answers = [ok(CHECK, **METER), {"status": 503}, ok(answers.check(customer="w"))]
    client.customer("u").check("export_pdf")
    client.customer("u").check("export_pdf")
    client.customer("w").check("export_pdf")
    assert client._driver.engine._down_until == 0


@pytest.mark.anyio
async def test_twenty_concurrent_reads_send_one_test_request(clock: Clock) -> None:
    transport = Transport()
    started = anyio.Event()
    release = anyio.Event()
    calls = 0

    async def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json=CHECK, headers=METER)
        if calls == 2:
            return httpx.Response(503)
        started.set()
        await release.wait()
        return httpx.Response(304)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    client = AsyncEntitlerServer(KEY, http_client=http, max_retries=0)
    customer = client.customer("u")
    await customer.check("export_pdf")
    assert (await customer.check("export_pdf")).stale
    clock.now += 31
    results: list[bool] = []

    async def read() -> None:
        results.append((await customer.check("export_pdf")).stale)

    async with anyio.create_task_group() as group:
        group.start_soon(read)
        await started.wait()
        for _ in range(19):
            group.start_soon(read)
        await anyio.lowlevel.checkpoint()
        release.set()
    assert calls == 3
    assert sorted(results) == [False] + [True] * 19
    del transport


@pytest.mark.anyio
async def test_the_test_request_ends_on_cancellation(clock: Clock) -> None:
    calls = 0
    hang = anyio.Event()

    async def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json=CHECK, headers=METER)
        if calls == 2:
            return httpx.Response(503)
        if calls == 3:
            await hang.wait()
        return httpx.Response(304)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    client = AsyncEntitlerServer(KEY, http_client=http, max_retries=0)
    customer = client.customer("u")
    await customer.check("export_pdf")
    await customer.check("export_pdf")
    clock.now += 31
    with anyio.move_on_after(0.05):
        await customer.check("export_pdf")
    assert client._driver.engine._probing is False
    assert not (await customer.check("export_pdf")).stale
    assert calls == 4


def test_a_failing_provider_never_answers_entries_kept_under_its_old_token(
    transport: Transport, clock: Clock
) -> None:
    tokens = ["a.eyJleHAiOjE3ODI4OTg4MDAsImlhdCI6MTc4Mjg5ODIwMH0.one"]

    def provider() -> str:
        if not tokens:
            raise OSError("offline")
        return tokens.pop()

    client = EntitlerClient(token=provider, http_client=transport.client(), max_retries=0)
    transport.answers = [ok(CHECK, **METER)]
    client.me.check("export_pdf")
    clock.now += 600
    seen: list[bool] = []
    with pytest.raises(TokenError) as raised:
        client.me.check("export_pdf")
    assert isinstance(raised.value.__cause__, OSError)
    seen.append(client.me.is_entitled("export_pdf", default=False))
    assert seen == [False]


def test_a_refreshed_token_starts_new_entries(transport: Transport, clock: Clock) -> None:
    tokens = [
        "a.eyJleHAiOjE3ODI5MDE4MDAsImlhdCI6MTc4Mjg5ODIwMH0.two",
        "a.eyJleHAiOjE3ODI5MDE4MDAsImlhdCI6MTc4Mjg5ODIwMH0.one",
    ]
    client = EntitlerClient(
        token=lambda: tokens.pop(), http_client=transport.client(), max_retries=0
    )
    transport.answers = [ok(CHECK, **FRESH), {"status": 401}, ok(CHECK, **FRESH)]
    client.me.check("export_pdf")
    client.me.check("export_pdf", revalidate=True)
    assert transport.requests[-1].headers["Authorization"].endswith(".two")
    assert "If-None-Match" in transport.requests[1].headers
    transport.answers = [{"failure": "connection"}]
    with pytest.raises(EntitlerConnectionError):
        client.me.check("pricing_other")


def test_the_server_close_drops_its_own_memory_cache(transport: Transport) -> None:
    memory = MemoryCache()
    mine = server(transport, cache=memory)
    transport.fallback = lambda _r: httpx.Response(200, json=CHECK, headers=FRESH)
    mine.customer("u").check("export_pdf")
    mine.close()
    assert len(memory) == 1
    own = server(transport)
    own.customer("u").check("export_pdf")
    store = own._driver.store
    own.close()
    assert isinstance(store, MemoryCache)
    assert len(store) == 0


def test_header_numbers_saturate_however_long(transport: Transport) -> None:
    client = server(transport)
    huge = "9" * 5000
    forever = {"Cache-Control": f"max-age={huge}"}
    transport.answers = [ok(CHECK, ETag='"v1"', Age="0", **forever)]
    assert client.customer("u").check("export_pdf").entitled
    assert client.customer("u").check("export_pdf").entitled
    assert len(transport.requests) == 1
    transport.answers = [ok(CHECK, ETag='"v2"', Age=huge, **{"Cache-Control": "max-age=60"})]
    client.customer("w").check("export_pdf")
    transport.answers = [{"status": 304}]
    assert client.customer("w").check("export_pdf").entitled
    assert transport.requests[-1].headers["If-None-Match"] == '"v2"'


class _WriteInFlight:
    """A transport whose write waits until released, and whose read can wait for the write."""

    def __init__(self, outcome: str, read_after_settle: bool) -> None:
        self.outcome = outcome
        self.read_after_settle = read_after_settle
        self.write_entered = anyio.Event()
        self.release = anyio.Event()
        self.settled = anyio.Event()
        self.read_arrived = anyio.Event()
        self.reads = 0

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            self.reads += 1
            self.read_arrived.set()
            if self.read_after_settle and self.reads == 1:
                await self.settled.wait()
            if self.reads > 1:
                return httpx.Response(304, headers=FRESH, request=request)
            return httpx.Response(200, json=CHECK, headers=FRESH, request=request)
        self.write_entered.set()
        await self.release.wait()
        if self.outcome == "fails":
            return httpx.Response(500, json=answers.error("internal"), request=request)
        return httpx.Response(200, json=answers.usage_result(), request=request)


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["succeeds", "fails", "is cancelled"])
@pytest.mark.parametrize("read_after_settle", [False, True], ids=["before", "after"])
async def test_a_read_during_a_write_is_never_kept_fresh(
    outcome: str, read_after_settle: bool
) -> None:
    flight = _WriteInFlight(outcome, read_after_settle)
    http = httpx.AsyncClient(transport=httpx.MockTransport(flight.handle))
    client = AsyncEntitlerServer(KEY, http_client=http, max_retries=0)
    customer = client.customer("u")
    write_scope = anyio.CancelScope()
    written = anyio.Event()

    async def write() -> None:
        try:
            with write_scope, contextlib.suppress(APIError):
                await customer.record_usage(Feature.metered("ai_credits"), 1, idempotency_key="k")
        finally:
            written.set()

    async def settle() -> None:
        if outcome == "is cancelled":
            write_scope.cancel()
        else:
            flight.release.set()

    async with anyio.create_task_group() as group:
        group.start_soon(write)
        await flight.write_entered.wait()
        if read_after_settle:
            group.start_soon(customer.check, "export_pdf")
            await flight.read_arrived.wait()
            await settle()
            await written.wait()
            flight.settled.set()
        else:
            await customer.check("export_pdf")
            await settle()
    await customer.check("export_pdf")
    assert flight.reads == 2
    await client.aclose()


@pytest.mark.parametrize("outcome", ["succeeds", "fails"])
@pytest.mark.parametrize("read_after_settle", [False, True], ids=["before", "after"])
def test_a_blocking_read_during_a_write_is_never_kept_fresh(
    outcome: str, read_after_settle: bool
) -> None:
    entered, release, settled = threading.Event(), threading.Event(), threading.Event()
    read_arrived = threading.Event()
    reads: list[int] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            reads.append(1)
            read_arrived.set()
            if read_after_settle and len(reads) == 1:
                settled.wait(5)
            if len(reads) > 1:
                return httpx.Response(304, headers=FRESH, request=request)
            return httpx.Response(200, json=CHECK, headers=FRESH, request=request)
        entered.set()
        release.wait(5)
        if outcome == "fails":
            return httpx.Response(500, json=answers.error("internal"), request=request)
        return httpx.Response(200, json=answers.usage_result(), request=request)

    client = server_with(httpx.Client(transport=httpx.MockTransport(handle)))
    customer = client.customer("u")

    def write() -> None:
        with contextlib.suppress(APIError):
            customer.record_usage(Feature.metered("ai_credits"), 1, idempotency_key="k")

    writer = threading.Thread(target=write)
    writer.start()
    entered.wait(5)
    if read_after_settle:
        reader = threading.Thread(target=customer.check, args=("export_pdf",))
        reader.start()
        read_arrived.wait(5)
        release.set()
        writer.join(5)
        settled.set()
        reader.join(5)
    else:
        customer.check("export_pdf")
        release.set()
        writer.join(5)
    customer.check("export_pdf")
    assert len(reads) == 2
