"""Token providers (section 7.5) and closing clients (section 4.4)."""

from __future__ import annotations

import base64
import json
import re
import threading
from typing import Any

import anyio
import httpx
import pytest

from entitler import (
    APIError,
    AsyncEntitlerClient,
    AsyncEntitlerServer,
    EntitlerClient,
    EntitlerServer,
    Feature,
    TokenError,
)
from entitler.testing import answers

from .support import KEY, Clock, Transport, ok, wired

CLOSED = "This Entitler client is closed. Create a new one."
NOW = 1_782_898_200


def token(iat: int | None, exp: int, tag: str = "x") -> str:
    claims: dict[str, Any] = {"exp": exp}
    if iat is not None:
        claims["iat"] = iat
    middle = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJIUzI1NiJ9.{middle}.{tag}"


def test_the_provider_is_asked_once_then_again_near_expiry(
    transport: Transport, clock: Clock
) -> None:
    wired(transport)
    calls: list[int] = []

    def provider() -> str:
        calls.append(1)
        return token(NOW, NOW + 3600, str(len(calls)))

    client = EntitlerClient(token=provider, http_client=transport.client(), cache=None)
    client.me.check("sso")
    client.me.check("sso")
    assert len(calls) == 1
    clock.now = NOW + 3540
    client.me.check("sso")
    assert len(calls) == 2
    assert transport.requests[-1].headers["Authorization"].endswith(".2")


def test_a_sixty_second_token_is_asked_at_most_once_per_request(
    transport: Transport, clock: Clock
) -> None:
    wired(transport)
    calls: list[int] = []

    def provider() -> str:
        calls.append(1)
        return token(NOW, NOW + 60)

    clock.now = NOW + 55
    client = EntitlerClient(token=provider, http_client=transport.client(), cache=None)
    for _ in range(3):
        client.me.check("sso")
    assert len(calls) == 3


@pytest.mark.parametrize(
    "answer",
    ["", "   ", "not-a-jwt", "a.b.c", token(NOW, NOW), token(NOW + 10, NOW), 42],
)
def test_blank_and_unreadable_tokens_fail_with_token_error(
    answer: Any, transport: Transport
) -> None:
    wired(transport)
    client = EntitlerClient(token=lambda: answer, http_client=transport.client())
    with pytest.raises(TokenError):
        client.me.check("sso")
    assert transport.requests == []


def test_a_failing_provider_is_a_token_error_with_its_cause(transport: Transport) -> None:
    def provider() -> str:
        raise ConnectionError("no network")

    client = EntitlerClient(
        key="ent_pk_test_x", identity_token=provider, http_client=transport.client()
    )
    with pytest.raises(TokenError) as raised:
        client.me.check("sso")
    assert isinstance(raised.value.__cause__, ConnectionError)


def test_a_failed_refresh_is_not_kept(transport: Transport, clock: Clock) -> None:
    wired(transport)
    answers_given = [ValueError("first"), token(NOW, NOW + 3600)]

    def provider() -> str:
        answer = answers_given.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return str(answer)

    client = EntitlerClient(token=provider, http_client=transport.client())
    with pytest.raises(TokenError):
        client.me.check("sso")
    assert client.me.check("sso").entitled


def test_one_retry_after_401_with_a_new_token(transport: Transport, clock: Clock) -> None:
    tokens = [token(NOW, NOW + 3600, "two"), token(NOW, NOW + 3600, "one")]
    client = EntitlerClient(
        token=lambda: tokens.pop(), http_client=transport.client(), max_retries=0
    )
    transport.answers = [{"status": 401}, ok(answers.check())]
    client.me.check("sso")
    assert [r.headers["Authorization"][-3:] for r in transport.requests] == ["one", "two"]


def test_503_then_401_then_200(transport: Transport, clock: Clock) -> None:
    tokens = [token(NOW, NOW + 3600, "two"), token(NOW, NOW + 3600, "one")]
    client = EntitlerClient(
        token=lambda: tokens.pop(), http_client=transport.client(), max_retries=1
    )
    transport.answers = [{"status": 503}, {"status": 401}, ok(answers.check())]
    assert client.me.check("sso").entitled
    assert len(transport.requests) == 3


def test_403_credential_not_allowed_is_never_refreshed(transport: Transport) -> None:
    calls: list[int] = []

    def provider() -> str:
        calls.append(1)
        return token(NOW, NOW + 3600)

    client = EntitlerClient(token=provider, http_client=transport.client(), max_retries=2)
    transport.answers = [{"status": 403, "body": answers.error("credential_not_allowed")}]
    with pytest.raises(APIError) as raised:
        client.me.check("sso")
    assert raised.value.code == "credential_not_allowed"
    assert len(calls) == 1
    assert len(transport.requests) == 1


def test_a_401_after_a_newer_token_uses_it_without_asking(
    transport: Transport, clock: Clock
) -> None:
    wired(transport)
    calls: list[int] = []

    def provider() -> str:
        calls.append(1)
        return token(NOW, NOW + 3600, str(len(calls)))

    client = EntitlerClient(token=provider, http_client=transport.client(), cache=None)
    client.me.check("sso")
    source = client._driver.credentials._token
    assert source is not None
    assert source.refresh("an older token") == token(NOW, NOW + 3600, "1")
    assert len(calls) == 1


def test_concurrent_threads_share_one_refresh(transport: Transport) -> None:
    wired(transport)
    calls: list[int] = []
    gate = threading.Event()

    def provider() -> str:
        calls.append(1)
        gate.wait(1)
        return token(NOW, 9_999_999_999)

    client = EntitlerClient(token=provider, http_client=transport.client(), cache=None)
    threads = [threading.Thread(target=client.me.check, args=("sso",)) for _ in range(8)]
    for thread in threads:
        thread.start()
    gate.set()
    for thread in threads:
        thread.join()
    assert len(calls) == 1
    assert len(transport.requests) == 8


@pytest.mark.anyio
async def test_async_waiters_share_one_refresh_and_one_may_cancel() -> None:
    transport = wired(Transport())
    calls: list[int] = []
    release = anyio.Event()

    async def provider() -> str:
        calls.append(1)
        await release.wait()
        return token(NOW, 9_999_999_999)

    client = AsyncEntitlerClient(token=provider, http_client=transport.async_client(), cache=None)
    results: list[bool] = []

    async def read() -> None:
        results.append((await client.me.check("sso")).entitled)

    async with anyio.create_task_group() as group:
        group.start_soon(read)
        with anyio.CancelScope() as cancelled:
            cancelled.cancel()
            await client.me.check("sso")
        group.start_soon(read)
        await anyio.sleep(0.01)
        release.set()
    assert len(calls) == 1
    assert results == [True, True]


@pytest.mark.anyio
async def test_the_caller_that_starts_a_refresh_can_be_cancelled_at_once() -> None:
    transport = wired(Transport())
    calls: list[int] = []
    started = anyio.Event()
    release = anyio.Event()

    async def provider() -> str:
        calls.append(1)
        started.set()
        await release.wait()
        return token(NOW, 9_999_999_999)

    client = AsyncEntitlerClient(token=provider, http_client=transport.async_client(), cache=None)
    results: list[bool] = []

    async def read() -> None:
        results.append((await client.me.check("sso")).entitled)

    with anyio.move_on_after(0.05) as first:
        await client.me.check("sso")
    assert first.cancelled_caught
    assert started.is_set()
    async with anyio.create_task_group() as group:
        group.start_soon(read)
        await anyio.sleep(0.01)
        release.set()
    assert calls == [1]
    assert results == [True]
    await client.aclose()


@pytest.mark.anyio
async def test_a_failed_async_refresh_is_not_kept() -> None:
    transport = wired(Transport())
    answers_given = iter(["", token(NOW, 9_999_999_999)])

    async def provider() -> str:
        return next(answers_given)

    client = AsyncEntitlerClient(token=provider, http_client=transport.async_client(), cache=None)
    with pytest.raises(TokenError, match="blank token"):
        await client.me.check("sso")
    assert (await client.me.check("sso")).entitled
    await client.aclose()


@pytest.mark.anyio
async def test_a_providers_fatal_error_is_never_wrapped() -> None:
    async def provider() -> str:
        raise MemoryError

    client = AsyncEntitlerClient(token=provider)
    with pytest.raises(MemoryError):
        await client.me.check("sso")
    await client.aclose()


def test_a_sync_providers_fatal_error_is_never_wrapped() -> None:
    def provider() -> str:
        raise RecursionError

    with pytest.raises(RecursionError):
        EntitlerClient(token=provider).me.check("sso")


def test_a_hanging_sync_provider_blocks_other_threads_for_the_timeout_at_most() -> None:
    entered = threading.Event()
    release = threading.Event()

    def provider() -> str:
        entered.set()
        release.wait(5)
        return token(NOW, 9_999_999_999)

    client = EntitlerClient(token=provider, timeout=0.1, cache=None)
    first = threading.Thread(target=lambda: client.me.is_entitled("sso", default=False))
    first.start()
    entered.wait(5)
    with pytest.raises(TokenError, match=r"did not answer within 0\.1 seconds"):
        client.me.check("sso")
    release.set()
    first.join(5)
    client.close()


@pytest.mark.anyio
async def test_a_hanging_provider_times_out_as_a_token_error() -> None:
    async def provider() -> str:
        await anyio.sleep_forever()
        raise AssertionError

    client = AsyncEntitlerClient(token=provider, timeout=0.05)
    with pytest.raises(TokenError, match=r"did not answer within 0\.05 seconds"):
        await client.me.check("sso")
    await client.aclose()


@pytest.mark.anyio
async def test_timeouts_are_refused_before_any_provider_runs() -> None:
    calls: list[int] = []

    async def provider() -> str:
        calls.append(1)
        return token(NOW, NOW + 60)

    client = AsyncEntitlerClient(token=provider)
    with pytest.raises(ValueError, match="Pass timeout"):
        await client.me.check("sso", timeout=-1)
    with pytest.raises(ValueError, match="Pass timeout"):
        await client.me.is_entitled("sso", default=True, timeout=0)
    assert calls == []
    await client.aclose()


def test_closing_refuses_later_calls_and_twice_is_safe(transport: Transport) -> None:
    client = EntitlerServer(KEY, http_client=transport.client())
    client.close()
    client.close()
    with pytest.raises(RuntimeError, match=CLOSED):
        client.customer("u").check("sso")
    with pytest.raises(RuntimeError, match=CLOSED):
        client.customer("u").is_entitled("sso", default=True)
    with pytest.raises(RuntimeError, match=CLOSED):
        client.record_usage_batch([])
    assert transport.requests == []


def test_an_injected_client_stays_open_and_an_own_client_closes(transport: Transport) -> None:
    http = transport.client()
    with EntitlerServer(KEY, http_client=http):
        pass
    assert not http.is_closed
    own = EntitlerServer(KEY)
    inner = own._driver._client
    own.close()
    assert inner.is_closed


def test_closing_stops_a_retry_wait_in_another_thread(transport: Transport) -> None:
    started = threading.Event()

    def busy(_request: httpx.Request) -> httpx.Response:
        started.set()
        return httpx.Response(503)

    transport.fallback = busy
    client = EntitlerServer(KEY, http_client=transport.client(), max_retries=5)
    errors: list[BaseException] = []

    def call() -> None:
        try:
            client.customer("u").check("sso")
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=call)
    thread.start()
    started.wait(1)
    client.close()
    thread.join(5)
    assert not thread.is_alive()
    assert isinstance(errors[0], RuntimeError)
    assert str(errors[0]) == CLOSED


@pytest.mark.anyio
async def test_closing_cancels_async_calls_in_flight() -> None:
    hang = anyio.Event()

    async def handle(_request: httpx.Request) -> httpx.Response:
        hang.set()
        await anyio.sleep_forever()
        raise AssertionError

    http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    client = AsyncEntitlerServer(KEY, http_client=http)
    errors: list[BaseException] = []

    async def call() -> None:
        try:
            await client.customer("u").check("sso")
        except BaseException as error:
            errors.append(error)

    async with anyio.create_task_group() as group:
        group.start_soon(call)
        await hang.wait()
        await client.aclose()
        await client.aclose()
    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert str(errors[0]) == CLOSED
    assert not http.is_closed
    with pytest.raises(RuntimeError, match=CLOSED):
        await client.customer("u").check("sso")


@pytest.mark.anyio
async def test_closing_cancels_a_pending_refresh() -> None:
    started = anyio.Event()

    async def provider() -> str:
        started.set()
        await anyio.sleep_forever()
        raise AssertionError

    client = AsyncEntitlerClient(token=provider)
    errors: list[BaseException] = []

    async def call() -> None:
        try:
            await client.me.check("sso")
        except BaseException as error:
            errors.append(error)

    async with anyio.create_task_group() as group:
        group.start_soon(call)
        await started.wait()
        await client.aclose()
    assert isinstance(errors[0], RuntimeError)


@pytest.mark.anyio
async def test_cancellation_ends_a_retry_wait_at_once() -> None:
    transport = Transport()
    transport.fallback = lambda _r: httpx.Response(503, headers={"Retry-After": "5"})
    client = AsyncEntitlerServer(KEY, http_client=transport.async_client(), max_retries=3)
    with anyio.move_on_after(0.1) as scope:
        await client.customer("u").check("sso")
    assert scope.cancelled_caught
    assert len(transport.requests) == 1


@pytest.mark.anyio
async def test_async_clients_work_as_context_managers() -> None:
    transport = wired(Transport())
    async with AsyncEntitlerServer(KEY, http_client=transport.async_client()) as server:
        customer = server.customer("u")
        assert (await customer.check("sso")).entitled
        assert await customer.is_entitled("sso", default=False)
        await customer.record_usage(Feature.metered("ai_credits"), 1, idempotency_key="k")
        assert (await server.pricing()).plans == ()
        assert (await server.features()).features == ()
        assert (await server.scopes()).scopes
        assert (await server.snapshot_keys()).keys == ()
        assert [c async for c in server.customers.list()] == []
        assert [p.used async for p in server.customers.list().pages()] == [0]
        details = await customer.details()
        assert [e async for e in details.usage] == []
        usage = await customer.usage()
        assert [e async for e in usage.log] == []
        created = await server.customers.create("u", name="Ada")
        assert created.customer.external_id == "user_1"
        batch = await server.record_usage_batch([])
        assert batch.results == ()
    async with AsyncEntitlerClient(
        key="ent_pk_test_x", http_client=transport.async_client()
    ) as client:
        assert (await client.pricing()).plans == ()
        with pytest.raises(TypeError, match="Sign the customer in first"):
            await client.scopes()
    assert "kind='publishable'" in repr(client)


def test_key_only_clients_refuse_signed_in_members() -> None:
    client = EntitlerClient(key="ent_pk_test_x")
    message = (
        "Sign the customer in first: create the client with (token=...) or "
        "(key=..., identity_token=...)."
    )
    with pytest.raises(TypeError) as raised:
        client.me  # noqa: B018 - reading the member raises
    assert str(raised.value) == message
    with pytest.raises(TypeError, match="Sign the customer in first"):
        client.register()
    with pytest.raises(TypeError, match="Sign the customer in first"):
        client.scopes()
    signed_in = EntitlerClient(token="a.b.c")
    with pytest.raises(
        TypeError, match=r"Read the signed-in customer's pricing with me.pricing\(\)."
    ):
        signed_in.pricing()
    assert client.kind == "publishable"
    assert signed_in.kind == "token"
    assert EntitlerClient(key="ent_pk_test_x", identity_token="a.b.c").kind == "identity"


def test_options_are_checked() -> None:
    with pytest.raises(ValueError, match="Pass max_retries"):
        EntitlerServer(KEY, max_retries=-1)
    with pytest.raises(ValueError, match="Pass stale_for"):
        EntitlerServer(KEY, stale_for=-1)
    with pytest.raises(ValueError, match="Pass max_retry_delay"):
        EntitlerServer(KEY, max_retry_delay=float("nan"))
    with pytest.raises(ValueError, match="Pass timeout"):
        EntitlerServer(KEY, timeout=float("inf"))
    with pytest.raises(ValueError, match=r"Pass timeout as a number of seconds above 0\."):
        EntitlerServer(KEY, timeout=None)  # type: ignore[arg-type]  # an invalid option, which the client must refuse
    with pytest.raises(TypeError, match="Pass on_error"):
        EntitlerServer(KEY, on_error="print")  # type: ignore[arg-type]  # an invalid option, which the client must refuse
    with pytest.raises(TypeError, match="Pass cache"):
        EntitlerServer(KEY, cache="redis")  # type: ignore[arg-type]  # an invalid option, which the client must refuse
    with pytest.raises(ValueError, match="Pass base_url"):
        EntitlerServer(KEY, base_url=" ")

    class AsyncStore:
        async def get(self, key: str) -> None:
            return None

        async def set(self, key: str, entry: object, ttl: float) -> None:
            return None

    with pytest.raises(TypeError, match="AsyncEntitlerServer"):
        EntitlerServer(KEY, cache=AsyncStore())  # type: ignore[arg-type]  # an invalid option, which the client must refuse
    AsyncEntitlerServer(KEY, cache=AsyncStore())
    assert repr(EntitlerServer(KEY, base_url="http://localhost:8787/")) == (
        "EntitlerServer(base_url='http://localhost:8787', kind='server')"
    )


UNSENDABLE = ["a b", "a\tb", "aéb", "a\x01b"]
UNSENDABLE_IDS = ["space", "tab", "non-ascii", "control"]


def exactly(message: str) -> str:
    return f"^{re.escape(message)}$"


@pytest.mark.parametrize("inside", UNSENDABLE, ids=UNSENDABLE_IDS)
def test_a_credential_that_cannot_be_sent_fails_as_a_blank_one(inside: str) -> None:
    with pytest.raises(
        ValueError, match=exactly("Provide an Entitler API key from the dashboard.")
    ):
        EntitlerServer(f"ent_test_{inside}")
    with pytest.raises(
        ValueError, match=exactly("Provide an Entitler API key from the dashboard.")
    ):
        EntitlerClient(key=f"ent_pk_test_{inside}")
    with pytest.raises(
        ValueError, match=exactly("Provide a customer token minted by your server.")
    ):
        EntitlerClient(token=f"x.y.{inside}")
    with pytest.raises(
        ValueError, match=exactly("Provide the identity token your sign-in provider issued.")
    ):
        EntitlerClient(key="ent_pk_test_x", identity_token=f"x.y.{inside}")
    with pytest.raises(
        ValueError, match=exactly("Provide a customer token minted by your server.")
    ):
        AsyncEntitlerClient(token=f"x.y.{inside}")


@pytest.mark.parametrize("inside", UNSENDABLE, ids=UNSENDABLE_IDS)
def test_a_providers_token_that_cannot_be_sent_is_unreadable(inside: str) -> None:
    transport = wired(Transport())
    client = EntitlerClient(
        token=lambda: token(NOW, 9_999_999_999, f"sig{inside}"), http_client=transport.client()
    )
    with pytest.raises(TokenError, match="not a readable JWT") as raised:
        client.me.check("sso")
    assert inside not in str(raised.value)
    assert transport.requests == []


@pytest.mark.anyio
@pytest.mark.parametrize("inside", UNSENDABLE, ids=UNSENDABLE_IDS)
async def test_an_async_providers_token_that_cannot_be_sent_is_unreadable(inside: str) -> None:
    transport = wired(Transport())

    async def provider() -> str:
        return token(NOW, 9_999_999_999, f"sig{inside}")

    client = AsyncEntitlerClient(token=provider, http_client=transport.async_client())
    with pytest.raises(TokenError, match="not a readable JWT"):
        await client.me.check("sso")
    assert transport.requests == []
    await client.aclose()
