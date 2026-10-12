"""The async clients mirror the blocking ones; no client follows a redirect (section 3.5)."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from entitler import (
    APIError,
    AsyncEntitlerClient,
    AsyncEntitlerServer,
    EntitlerClient,
    EntitlerServer,
    Feature,
    PlanBilling,
    Sku,
    When,
)
from entitler.testing import answers

from .support import KEY, Transport, ok, wired
from .test_requests import CALLS

CREDITS = Feature.metered("ai_credits")
LATER = datetime(2026, 7, 1, 9, 30, tzinfo=UTC)

ASYNC: list[tuple[str, Callable[[AsyncEntitlerServer], Awaitable[Any]]]] = [
    ("check", lambda s: s.customer("u").check("export_pdf", as_of=LATER)),
    (
        "is_entitled",
        lambda s: s.customer("u").is_entitled("export_pdf", default=False, as_of=LATER),
    ),
    ("entitlements", lambda s: s.customer("u").entitlements(as_of=LATER)),
    ("plans", lambda s: s.customer("u").plans(as_of=LATER)),
    ("pricing", lambda s: s.customer("u").pricing()),
    ("usage", lambda s: s.customer("u").usage(as_of=LATER)),
    ("details", lambda s: s.customer("u").details()),
    ("billing", lambda s: s.customer("u").billing()),
    ("record_usage", lambda s: s.customer("u").record_usage(CREDITS, 1, idempotency_key="k")),
    ("hold_usage", lambda s: s.customer("u").hold_usage(CREDITS, 1, idempotency_key="k")),
    ("settle_usage", lambda s: s.customer("u").settle_usage("h", 1)),
    ("release_usage", lambda s: s.customer("u").release_usage("h")),
    ("snapshot", lambda s: s.customer("u").snapshot()),
    ("subscribe", lambda s: s.customer("u").subscribe("pro")),
    ("cancel", lambda s: s.customer("u").cancel()),
    ("undo", lambda s: s.customer("u").undo_pending_change()),
    (
        "billing_portal",
        lambda s: s.customer("u").billing_portal(return_url="https://a.example.com"),
    ),
    ("sync_billing", lambda s: s.customer("u").sync_billing()),
    ("register", lambda s: s.customer("u").register(name="Ada")),
    ("update", lambda s: s.customer("u").update(name="Ada")),
    ("erase", lambda s: s.customer("u").erase()),
    ("token", lambda s: s.customer("u").token()),
    ("set_track", lambda s: s.customer("u").set_track("Beta")),
    (
        "set_plan",
        lambda s: s.customer("u").set_plan("pro", when=When.NOW, billing=PlanBilling.PROVIDER),
    ),
    (
        "set_plan_sku",
        lambda s: s.customer("u").set_plan(Sku(connector="apple", ids={"p": "1"}), until=LATER),
    ),
    ("set_add_on", lambda s: s.customer("u").set_add_on("seats", quantity=1)),
    ("grant", lambda s: s.customer("u").grant("sso")),
    ("revoke_grant", lambda s: s.customer("u").revoke_grant("g")),
    ("adjust_meter", lambda s: s.customer("u").adjust_meter(CREDITS, by=1, idempotency_key="k")),
    ("cancel_usage", lambda s: s.customer("u").cancel_usage("x")),
]


@pytest.mark.anyio
@pytest.mark.parametrize(("name", "call"), ASYNC, ids=[a[0] for a in ASYNC])
async def test_every_async_method_sends_its_request(
    name: str, call: Callable[[AsyncEntitlerServer], Awaitable[Any]]
) -> None:
    transport = wired(Transport())
    async with AsyncEntitlerServer(KEY, http_client=transport.async_client(), cache=None) as server:
        await call(server)
    assert len(transport.requests) == 1
    request = transport.requests[0]
    if request.method == "GET" and name in {
        "check",
        "is_entitled",
        "entitlements",
        "plans",
        "usage",
    }:
        assert request.headers["Entitler-As-Of"] == "2026-07-01T09:30:00.000Z"


def test_the_sync_table_covers_every_customer_method() -> None:
    names = {name.split("_minimal")[0] for name, *_ in CALLS}
    assert {"check", "record_usage", "set_plan", "adjust_by", "cancel_usage"} <= names


@pytest.mark.anyio
async def test_async_in_app_clients_sign_requests() -> None:
    transport = wired(Transport())
    calls: list[int] = []

    async def provider() -> str:
        calls.append(1)
        return "a.eyJleHAiOjk5OTk5OTk5OTl9.c"

    async with AsyncEntitlerClient(token=provider, http_client=transport.async_client()) as client:
        await client.me.check("sso")
        assert client.me.id == "user_1"
        await client.scopes()
        assert (await client.snapshot_keys()).keys == ()
        with pytest.raises(TypeError, match=r"me\.pricing"):
            await client.pricing()
        assert client.kind == "token"
        assert len(client.visitor) == 32
    identity = AsyncEntitlerClient(
        key="ent_pk_test_x", identity_token="a.b.c", http_client=transport.async_client()
    )
    await identity.register()
    assert transport.requests[-1].headers["Entitler-Identity-Token"] == "a.b.c"
    assert (await identity.scopes()).registration is None
    await identity.aclose()
    assert calls == [1]


def three_oh_seven(seen: list[str]) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        status = 308 if request.method != "GET" else 302
        return httpx.Response(
            status,
            headers={"Location": "https://elsewhere.example.com/"},
            json={"error": {"code": "moved", "message": "x"}},
        )

    return handle


BUILDERS: list[Callable[[httpx.Client], Any]] = [
    lambda http: EntitlerServer(KEY, http_client=http).customer("u"),
    lambda http: EntitlerClient(token="a.b.c", http_client=http).me,
    lambda http: EntitlerClient(key="ent_pk_test_x", identity_token="a.b.c", http_client=http).me,
]


@pytest.mark.parametrize("build", BUILDERS, ids=["server", "customer-token", "identity"])
def test_redirects_reach_no_second_origin_for_any_credential(
    build: Callable[[httpx.Client], Any],
) -> None:
    seen: list[str] = []
    http = httpx.Client(transport=httpx.MockTransport(three_oh_seven(seen)), follow_redirects=True)
    customer = build(http)
    calls: list[Callable[[], Any]] = [
        lambda: customer.check("sso"),
        lambda: customer.record_usage(CREDITS, 1, idempotency_key="k"),
    ]
    for call in calls:
        with pytest.raises(APIError) as raised:
            call()
        assert raised.value.code == "http_error"
        assert raised.value.message.startswith("Entitler answered with HTTP 30")
    assert all(url.startswith("https://api.entitler.dev/") for url in seen)
    assert len(seen) == 2


def test_the_key_only_client_does_not_follow_redirects_either() -> None:
    seen: list[str] = []
    http = httpx.Client(transport=httpx.MockTransport(three_oh_seven(seen)), follow_redirects=True)
    with pytest.raises(APIError):
        EntitlerClient(key="ent_pk_test_x", http_client=http).pricing()
    assert len(seen) == 1


@pytest.mark.anyio
async def test_async_redirects_are_not_followed() -> None:
    seen: list[str] = []
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(three_oh_seven(seen)), follow_redirects=True
    )
    server = AsyncEntitlerServer(KEY, http_client=http)
    with pytest.raises(APIError):
        await server.customer("u").record_usage(CREDITS, 1, idempotency_key="k")
    assert len(seen) == 1


@pytest.mark.anyio
async def test_async_batches_and_stores() -> None:
    from entitler import CacheEntry, UsageEventInput

    class Store:
        def __init__(self) -> None:
            self.entries: dict[str, CacheEntry] = {}

        async def get(self, key: str) -> CacheEntry | None:
            return self.entries.get(key)

        async def set(self, key: str, entry: CacheEntry, ttl: float) -> None:
            self.entries[key] = entry

    transport = wired(Transport())
    store = Store()
    server = AsyncEntitlerServer(
        KEY, http_client=transport.async_client(), cache=store, max_retries=0
    )
    transport.answers = [ok(answers.check(), ETag='"e"', **{"Cache-Control": "max-age=60"})]
    await server.customer("u").check("sso")
    await server.customer("u").check("sso")
    assert len(store.entries) == 1
    assert len(transport.requests) == 1
    events = [UsageEventInput(customer="u", feature=CREDITS, amount=1, idempotency_key="e1")]
    result = await server.record_usage_batch(events)
    assert result.recorded == 1
    assert json.loads(transport.requests[-1].content)["events"][0]["idempotencyKey"] == "e1"
    await server.aclose()
