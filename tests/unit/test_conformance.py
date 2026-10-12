"""The conformance suite: every case in tests/fixtures/conformance, one test each."""

from __future__ import annotations

import contextlib
import hashlib
import itertools
import json
import struct
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from entitler import (
    APIError,
    CacheEntry,
    Check,
    EntitlerClient,
    EntitlerConnectionError,
    EntitlerServer,
    EntitlerTimeoutError,
    ErrorCode,
    Feature,
    MemoryCache,
    MeteredCheck,
    PendingCancel,
    PendingMove,
    Sku,
    SnapshotError,
    SnapshotKey,
    SnapshotKeys,
    TokenError,
    UsageEventInput,
    UsageResult,
    VerifiedSnapshot,
    verify_snapshot,
)
from entitler.testing import answers

from .support import KEY, Clock, Transport, ok, respond, seconds_of

FIXTURES = Path(__file__).parent.parent / "fixtures" / "conformance"
BASE = "https://api.entitler.dev"
CLOSED = "This Entitler client is closed. Create a new one."


def load(name: str) -> list[dict[str, Any]]:
    return list(json.loads((FIXTURES / name).read_text(encoding="utf-8"))["cases"])


def cases(name: str) -> pytest.MarkDecorator:
    found = load(name)
    return pytest.mark.parametrize("case", found, ids=[case["name"] for case in found])


def python_message(message: str) -> str:
    """A specification message in this SDK's spelling (section 3.1)."""
    return (
        message.replace(
            "{ token }, { key, identityToken } or { key }",
            "(token=...), (key=..., identity_token=...) or (key=...)",
        )
        .replace(
            "{ token } or { key, identityToken }", "(token=...) or (key=..., identity_token=...)"
        )
        .replace("idempotencyKey", "idempotency_key")
        .replace("addOn", "add_on")
        .replace("asOf", "as_of")
        .replace("clockSkewSeconds", "clock_skew_seconds")
        .replace("snapshotKeys()", "snapshot_keys()")
    )


def utf16(units: list[int]) -> str:
    return struct.pack(f"<{len(units)}H", *units).decode("utf-16-le", "surrogatepass")


def instant(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def millis(value: datetime | None) -> int | None:
    return None if value is None else round(value.timestamp() * 1000)


def open_value(value: Any) -> Any:
    """An enum or code as the conformance cases write it: a string, or `{"unknown": raw}`."""
    if value is None:
        return None
    from enum import Enum

    return value.value if isinstance(value, Enum) else {"unknown": value}


def feature_value(value: object) -> dict[str, Any] | None:
    if value is True:
        return {"kind": "on"}
    if value == "unlimited":
        return {"kind": "unlimited"}
    if isinstance(value, int):
        return {"kind": "amount", "amount": value}
    return None


def server(transport: Transport, **options: Any) -> EntitlerServer:
    options.setdefault("max_retries", 0)
    options.setdefault("cache", None)
    return EntitlerServer(KEY, http_client=transport.client(), **options)


def test_manifest_hashes_and_counts() -> None:
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        data = (FIXTURES / entry["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"], entry["path"]
        assert len(json.loads(data)["cases"]) == entry["cases"], entry["path"]


class RecordingStore:
    """A custom store that records the keys it is asked for."""

    def __init__(self) -> None:
        self.keys: list[str] = []

    def get(self, key: str) -> CacheEntry | None:
        self.keys.append(key)
        return None

    def set(self, key: str, entry: CacheEntry, ttl: float) -> None:
        return None


class RecordingMemory(MemoryCache):
    """The in-memory store, recording the keys it is asked for: the unit suite's own hook."""

    def __init__(self) -> None:
        super().__init__()
        self.keys: list[str] = []

    def get(self, key: str) -> CacheEntry | None:
        self.keys.append(key)
        return super().get(key)


ROUTE_BODIES: dict[str, Callable[[], dict[str, Any]]] = {
    "/customers/{id}/entitlements/{feature}": answers.check,
    "/customers/{id}/entitlements": answers.entitlements,
    "/customers/{id}/plans": answers.customer_plans,
    "/customers/{id}/pricing": answers.pricing,
    "/pricing": answers.pricing,
    "/pricing/features": answers.feature_list,
}


@cases("cache-keys.json")
def test_cache_keys(case: dict[str, Any], transport: Transport) -> None:
    if case.get("appliesWhere"):
        pytest.skip(f"Does not apply in Python: {case['appliesWhere']}.")
    outcome = case["outcome"]
    assert hashlib.sha256(outcome["serialised"].encode()).hexdigest() == outcome["cacheKey"]
    route = case["built"]["route"]
    params = case["built"]["params"]
    body = ROUTE_BODIES[route]()
    transport.fallback = lambda _request: httpx.Response(200, json=body, headers={"ETag": '"e"'})
    base_url = case["built"]["baseUrl"]
    visitor = case["visitor"]
    kind = case["principalKind"]
    if kind == "key" and not case["credential"].strip().startswith("ent_pk_"):
        store = RecordingStore()
        client: Any = EntitlerServer(
            case["credential"], base_url=base_url, cache=store, http_client=transport.client()
        )
        customer = client.customer(params.get("id", "x"))
        as_of = case.get("asOfInput") or case["asOf"]
        given = {} if visitor is None else {"visitor": visitor}
        dated = {} if as_of is None else {"as_of": instant(as_of)}
        reads = {
            "/customers/{id}/entitlements/{feature}": lambda: customer.check(
                params["feature"], **dated
            ),
            "/customers/{id}/entitlements": lambda: customer.entitlements(**dated),
            "/customers/{id}/plans": lambda: customer.plans(**dated),
            "/customers/{id}/pricing": lambda: customer.pricing(**given),
            "/pricing": lambda: client.pricing(**given),
            "/pricing/features": lambda: client.features(),
        }
        reads[route]()
        keys = store.keys
    else:
        credentials: dict[str, Any] = (
            {"token": case["credential"]}
            if kind == "customer-token"
            else {"key": case["credential"]}
            if kind == "key"
            else {"key": case["key"], "identity_token": case["identityToken"]}
        )
        own = case.get("clientVisitor", visitor)
        client = EntitlerClient(
            base_url=base_url, visitor=own, http_client=transport.client(), **credentials
        )
        memory = RecordingMemory()
        client._driver.store = memory
        given = {"visitor": visitor} if "clientVisitor" in case else {}
        reads = {
            "/customers/{id}/entitlements/{feature}": lambda: client.me.check(params["feature"]),
            "/customers/{id}/entitlements": lambda: client.me.entitlements(),
            "/customers/{id}/plans": lambda: client.me.plans(),
            "/customers/{id}/pricing": lambda: client.me.pricing(**given),
            "/pricing": lambda: client.pricing(**given),
        }
        reads[route]()
        keys = memory.keys
    assert keys == [outcome["cacheKey"]]
    request = transport.requests[0]
    assert str(request.url) == case["url"]
    for name, value in outcome["requestHeaders"].items():
        assert request.headers.get(name) == value, name
    client.close()


def _segment_call(
    case: dict[str, Any], customer_id: str, value: str
) -> Callable[[EntitlerServer], Any]:
    request = case["request"]
    route = request["route"]
    parameter = case["parameter"]
    options: dict[str, Any] = request.get("options") or {}
    params: dict[str, Any] = request.get("params") or {}

    def target(name: str) -> str:
        return value if parameter == name else str(params[name])

    def run(client: EntitlerServer) -> Any:
        customer = client.customer(customer_id)
        method = request["method"]
        if route == "/customers/{id}/entitlements/{feature}":
            return customer.check(target("feature"))
        if route == "/customers/{id}?erase=true":
            return customer.erase()
        if route == "/customers/{id}/subscription/add-ons/{addOn}":
            return customer.cancel(add_on=target("addOn"))
        if route == "/customers/{id}/subscription/add-ons/{addOn}/pending":
            return customer.undo_pending_change(add_on=target("addOn"))
        if route == "/customers/{id}/add-ons/{addOn}":
            return customer.set_add_on(target("addOn"), quantity=options.get("quantity", 1))
        if route == "/customers/{id}/meters/{feature}/adjustments":
            return customer.adjust_meter(
                Feature.metered(target("feature")),
                by=options.get("by", 1),
                idempotency_key=options.get("idempotencyKey", "adjust-1"),
            )
        if route == "/customers/{id}/usage/holds/{holdId}/settle":
            return customer.settle_usage(target("holdId"), options.get("amount", 1))
        if route == "/customers/{id}/usage/holds/{holdId}":
            return customer.release_usage(target("holdId"))
        if route == "/customers/{id}/grants/{grantId}":
            return customer.revoke_grant(target("grantId"))
        if route == "/customers/{id}/usage/{usageId}":
            return customer.cancel_usage(target("usageId"))
        raise AssertionError(f"Unknown route {method} {route}")

    return run


@cases("path-encoding.json")
def test_path_encoding(case: dict[str, Any], transport: Transport) -> None:
    transport.fallback = lambda _request: httpx.Response(200, json={})
    kind = case["kind"]
    outcome = case["outcome"]
    client = server(transport, base_url=case.get("baseUrl") or BASE)
    if kind == "segment":
        value = utf16(case["idUtf16"]) if "idUtf16" in case else case["id"]
        params: dict[str, Any] = case["request"].get("params") or {}
        customer_id = value if case["parameter"] == "customer" else str(params["id"])
        call = _segment_call(case, customer_id, str(value))
    elif kind == "baseUrl":

        def base_url_call(c: EntitlerServer) -> Any:
            return c.customer("user_42").check("export_pdf")

        call = base_url_call

    elif kind == "query":
        value = utf16(case["valueUtf16"]) if "valueUtf16" in case else case["value"]
        parameter = case["parameter"]
        route = case["request"]["route"]

        def query_call(c: EntitlerServer) -> Any:
            if parameter == "q":
                return next(iter(c.customers.list(q=value).pages()))
            if parameter == "cursor":
                return next(iter(c.customers.list(cursor=value).pages()))
            customer = c.customer(case["request"]["params"]["id"])
            if route.endswith("/pending"):
                return customer.undo_pending_change(product=value)
            return customer.cancel(product=value)

        call = query_call

    else:
        options = case["call"]["options"]

        def arguments_call(c: EntitlerServer) -> Any:
            customer = c.customer(case["call"]["customer"])
            method = (
                customer.cancel
                if case["call"]["method"] == "cancel"
                else customer.undo_pending_change
            )
            return method(add_on=options["addOn"], product=options["product"])

        call = arguments_call

    if outcome.get("kind") == "ArgumentError":
        with pytest.raises((ValueError, TypeError)) as raised:
            call(client)
        assert str(raised.value) == python_message(outcome["message"])
        assert transport.requests == []
        return
    with contextlib.suppress(APIError):
        call(client)
    assert str(transport.requests[0].url) == outcome["url"]


HELD: dict[str, Any] = {
    "hold_id": "hld_1",
    "expires_at": "2026-07-01T09:35:00.000Z",
    "as_of": "2026-07-01T09:30:00.000Z",
}


def _key_call(case: dict[str, Any], client: EntitlerServer) -> Callable[[], Any]:
    customer = client.customer("user_42")
    key = case.get("key")
    method = case["method"]
    metered = Feature.metered("ai_credits")
    if method == "recordUsage":
        return lambda: customer.record_usage(metered, 1, idempotency_key=key)  # type: ignore[arg-type]  # the case may give no key, which the SDK must refuse
    if method == "holdUsage":
        return lambda: customer.hold_usage(metered, 1, idempotency_key=key)  # type: ignore[arg-type]  # the case may give no key, which the SDK must refuse
    if method == "startHold":

        def start() -> Any:
            hold = customer.start_hold(metered, 1, idempotency_key=key)  # type: ignore[arg-type]  # the case may give no key, which the SDK must refuse
            hold.use(2)
            return hold.finish()

        return start
    if method == "withHold":

        def within() -> Any:
            with customer.with_hold(metered, 1, idempotency_key=key) as hold:  # type: ignore[arg-type]  # the case may give no key, which the SDK must refuse
                hold.use(2)

        return within
    if method == "adjustMeter":
        return lambda: customer.adjust_meter(metered, by=1, idempotency_key=key)  # type: ignore[arg-type]  # the case may give no key, which the SDK must refuse
    if method == "cancel":
        return lambda: customer.cancel(idempotency_key=key)
    if method == "token":
        return lambda: customer.token()
    if method == "snapshot":
        return lambda: customer.snapshot()
    if method == "billingPortal":
        return lambda: customer.billing_portal(return_url="https://app.example.com/billing")
    if method == "syncBilling":
        return lambda: customer.sync_billing()
    raise AssertionError(method)


def _key_answers(method: str) -> Callable[[httpx.Request], httpx.Response]:
    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/usage/holds"):
            body = answers.usage_result(outcome="held", amount=1, **HELD)
        elif path.endswith("/settle"):
            body = answers.usage_result(outcome="settled", amount=1, hold_id="hld_1")
        elif "/usage" in path or "/meters/" in path:
            body = answers.usage_result(outcome="recorded", amount=1)
        elif path.endswith("/tokens"):
            body = answers.issued_token()
        elif path.endswith("/snapshots"):
            body = answers.issued_snapshot()
        elif path.endswith("/billing-portal"):
            body = answers.billing_portal()
        elif path.endswith("/billing/sync"):
            body = answers.billing_sync()
        else:
            body = answers.plan_change()
        return httpx.Response(200, json=body)

    del method
    return answer


def _batch_events(case: dict[str, Any]) -> list[Any]:
    if "eventPattern" in case:
        pattern = case["eventPattern"]
        events: list[dict[str, Any]] = [
            {
                "customer": f"{pattern['customerPrefix']}{i % pattern['customers']}",
                "feature": pattern["feature"],
                "amount": i + 1,
                "idempotencyKey": f"{pattern['keyPrefix']}{i}",
            }
            for i in range(pattern["count"])
        ]
        for override in case.get("overrides", []):
            events[override["index"]] = override["event"]
    else:
        events = case["events"]
    return [
        UsageEventInput(
            customer=event["customer"],
            feature=Feature.metered(event["feature"]),
            amount=event["amount"],
            idempotency_key=event.get("idempotencyKey"),  # type: ignore[arg-type]  # the case may give no key, which the SDK must refuse
            occurred_at=instant(event["occurredAt"]) if "occurredAt" in event else None,
        )
        for event in events
    ]


@cases("idempotency-keys.json")
def test_idempotency_keys(case: dict[str, Any], transport: Transport, clock: Clock) -> None:
    clock.at("2026-07-01T09:30:00.000Z")
    outcome = case["outcome"]
    client = server(transport)
    if case["method"] == "recordUsageBatch":

        def answer(request: httpx.Request) -> httpx.Response:
            sent = json.loads(request.content)["events"]
            results = [
                {"index": i, "outcome": "recorded", "id": f"use_{i}", "late": False, "error": None}
                for i in range(len(sent))
            ]
            return httpx.Response(200, json=answers.usage_events_result(results=results))

        transport.fallback = answer
        events = _batch_events(case)
        register = case.get("register")
        for _ in range(case.get("calls", 1)):
            result = client.record_usage_batch(events, register=register)
            refused = [
                {"index": r.index, "code": r.error.code, "message": r.error.message}
                for r in result.results
                if r.error is not None
            ]
            assert refused == [
                {**item, "message": python_message(item["message"])} for item in outcome["refused"]
            ]
            assert len(result.results) == len(events)
        expected = outcome["requests"] * case.get("calls", 1)
        assert len(transport.requests) == len(expected)
        for request, wanted in zip(transport.requests, expected, strict=True):
            assert len(json.loads(request.content)["events"]) == wanted["events"]
            assert request.headers["Idempotency-Key"] == wanted["idempotencyKey"]
            assert (
                wanted["idempotencyKey"]
                == "batch:" + hashlib.sha256(wanted["serialised"].encode()).hexdigest()
            )
            sent_register = json.loads(request.content).get("register")
            assert sent_register == (None if register is None else register)
        return
    transport.fallback = _key_answers(case["method"])
    call = _key_call(case, client)
    if "valid" not in outcome:
        with pytest.raises((TypeError, ValueError)) as raised:
            call()
        assert str(raised.value) == python_message(outcome["message"])
        assert transport.requests == []
        return
    call()
    first = transport.requests[0].headers["Idempotency-Key"]
    if case["key"] is None:
        parsed = uuid.UUID(first)
        assert parsed.version == 4
        assert parsed.variant == uuid.RFC_4122
        transport.requests.clear()
        call()
        assert transport.requests[0].headers["Idempotency-Key"] != first
    else:
        assert first == case["key"]
    if "excessKey" in outcome:
        assert transport.requests[-1].headers["Idempotency-Key"] == outcome["excessKey"]
        assert json.loads(transport.requests[-1].content)["mode"] == "observe"


def _client_for(case: dict[str, Any], transport: Transport, calls: list[int]) -> tuple[Any, Any]:
    credential = case["credential"]
    options: dict[str, Any] = {
        "max_retries": case["maxRetries"],
        "max_retry_delay": case["maxRetryDelay"],
        "cache": None,
        "http_client": transport.client(),
    }
    if credential["kind"] == "key":
        client: Any = EntitlerServer(credential["key"], **options)
        return client, client.customer(case["request"]["path"].split("/")[2])
    if credential["kind"] == "customerToken":
        client = EntitlerClient(token=credential["token"], **options)
        return client, client.me
    tokens = list(credential["tokens"])

    def provider() -> str:
        calls.append(1)
        return str(tokens.pop(0))

    client = EntitlerClient(token=provider, **options)
    return client, client.me


@cases("retry-timing.json")
def test_retry_timing(
    case: dict[str, Any], transport: Transport, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from entitler import _clock

    kind = case["kind"]
    if kind == "retryAfter":
        clock.at(case["now"])
        headers = {} if case["retryAfter"] is None else {"Retry-After": case["retryAfter"]}
        transport.answers = [{"status": 503, "headers": headers}, ok(answers.check())]
        client = server(transport, max_retries=2, max_retry_delay=case["maxRetryDelay"])
        action = case["outcome"]["action"]
        if action == "fail":
            with pytest.raises(APIError) as raised:
                client.customer("user_42").check("export_pdf")
            assert raised.value.status == 503
            assert clock.waits == []
        else:
            client.customer("user_42").check("export_pdf")
            assert len(clock.waits) == 1
            if action == "wait":
                assert clock.waits[0] == case["outcome"]["seconds"]
            else:
                assert 0 <= clock.waits[0] <= 0.5
        return
    if kind == "backoff":
        retry = case["retry"]
        for jitter in (0.0, 0.999999):
            clock.waits.clear()
            monkeypatch.setattr(_clock, "jitter", lambda value=jitter: value)
            transport.answers = [{"status": 503}] * (retry + 1) + [ok(answers.check())]
            server(transport, max_retries=retry + 1).customer("user_42").check("export_pdf")
            wait = clock.waits[int(retry)]
            assert case["outcome"]["minSeconds"] <= wait <= case["outcome"]["maxSeconds"]
            if jitter == 0.0:
                assert wait == case["outcome"]["minSeconds"]
            else:
                assert wait == pytest.approx(case["outcome"]["maxSeconds"], rel=1e-5)
        return
    if kind == "limits":
        retries = case["maxRetries"]
        transport.answers = [{"status": 503}] * (retries + 5)
        with pytest.raises(APIError):
            server(transport, max_retries=retries).customer("user_42").check("export_pdf")
        assert len(transport.requests) == case["outcome"]["maxAttemptsWithoutUnauthorised"]
        transport.requests.clear()
        token = "eyJhbGciOiJIUzI1NiJ9.eyJpYXQiOjE3ODI4OTgyMDAsImV4cCI6MTc4MjkwMTgwMH0.sig"
        transport.answers = [{"status": 401}] + [{"status": 503}] * (retries + 5)
        in_app = EntitlerClient(
            token=lambda: token, max_retries=retries, cache=None, http_client=transport.client()
        )
        with pytest.raises(APIError):
            in_app.me.check("export_pdf")
        assert len(transport.requests) == case["outcome"]["maxAttempts"]
        return
    clock.at(case["now"])
    calls: list[int] = []
    times: list[float] = []

    def timed(answer: dict[str, Any]) -> Callable[[httpx.Request], httpx.Response]:
        def handle(request: httpx.Request) -> httpx.Response:
            times.append(clock.now)
            return respond(answer, request)

        return handle

    transport.answers = [timed(answer) for answer in case["answers"]]
    client, customer = _client_for(case, transport, calls)
    request = case["request"]
    outcome = case["outcome"]
    error: BaseException | None = None
    try:
        if request["method"] == "GET":
            customer.check(request["path"].rsplit("/", 1)[1])
        else:
            customer.record_usage(
                Feature.metered(request["body"]["feature"]),
                request["body"]["amount"],
                idempotency_key=request["idempotencyKey"],
            )
    except Exception as raised:
        error = raised
    assert len(transport.requests) == outcome["attempts"]
    if "providerCalls" in outcome:
        assert len(calls) == outcome["providerCalls"]
    gaps = [later - earlier for earlier, later in itertools.pairwise(times)]
    assert len(gaps) == len(outcome["waits"])
    for wait, bounds in zip(gaps, outcome["waits"], strict=True):
        assert bounds["minSeconds"] <= wait <= bounds["maxSeconds"]
    result = outcome["result"]
    if result.get("status") == 200 and "kind" not in result:
        assert error is None
    else:
        expected = {
            "ApiError": APIError,
            "TimeoutError": EntitlerTimeoutError,
            "ConnectionError": EntitlerConnectionError,
        }[result["kind"]]
        assert isinstance(error, expected)
        if isinstance(error, APIError):
            assert error.status == result["status"]
            assert error.code == result["code"]
    if outcome.get("sameIdempotencyKeyOnEveryAttempt"):
        keys = {r.headers["Idempotency-Key"] for r in transport.requests}
        assert keys == {request["idempotencyKey"]}
    if outcome.get("errorCarriesIdempotencyKey"):
        assert getattr(error, "idempotency_key", None) == request["idempotencyKey"]
    client.close()


@cases("token-refresh.json")
def test_token_refresh(case: dict[str, Any], transport: Transport, clock: Clock) -> None:
    clock.at(case["receivedAt"])
    transport.fallback = lambda _request: httpx.Response(200, json=answers.check())
    calls: list[int] = []

    def provider() -> str:
        calls.append(1)
        return str(case["token"])

    options: dict[str, Any] = {"cache": None, "max_retries": 0, "http_client": transport.client()}
    if "key" in case:
        client = EntitlerClient(key=case["key"], identity_token=provider, **options)
    else:
        client = EntitlerClient(token=provider, **options)
    outcome = case["outcome"]
    if outcome.get("kind") == "TokenError":
        with pytest.raises(TokenError):
            client.me.check("export_pdf")
        assert transport.requests == []
        return
    client.me.check("export_pdf")
    for name, value in outcome["requestHeaders"].items():
        assert transport.requests[0].headers.get(name) == value
    for _ in range(case.get("close", 0)):
        client.close()
    clock.at(case["now"])
    if "later" in outcome:
        with pytest.raises(RuntimeError) as raised:
            client.me.check("export_pdf")
        assert str(raised.value) == outcome["later"]["message"]
        assert len(calls) == 1
        return
    client.me.check("export_pdf")
    assert (len(calls) == 2) == outcome["asksProvider"]
    client.close()


@cases("construction.json")
def test_construction(case: dict[str, Any]) -> None:
    given = case["credentials"]
    credentials: dict[str, Any] = {}
    for name, value in given.items():
        python = "identity_token" if name == "identityToken" else name
        credentials[python] = (lambda: "unused") if isinstance(value, dict) else value
    options: dict[str, Any] = {}
    if case.get("cache") == "customStore":
        options["cache"] = RecordingStore()
    outcome = case["outcome"]

    def build() -> Any:
        if case["client"] == "EntitlerServer":
            return EntitlerServer(credentials.pop("key"), **credentials, **options)
        return EntitlerClient(**credentials, **options)

    if outcome.get("valid"):
        client = build()
        text = repr(client) + str(client)
        for secret in outcome.get("stringFormOmits", []):
            assert secret not in text
        client.close()
        return
    with pytest.raises((TypeError, ValueError)) as raised:
        build()
    assert str(raised.value) == python_message(outcome["message"])


def _set(data: dict[str, Any], path: str, value: Any, missing: bool) -> None:
    *parents, last = path.split(".")
    for part in parents:
        data = data[part]
    if missing:
        data.pop(last, None)
    else:
        data[last] = value


@cases("values.json")
def test_values(case: dict[str, Any], transport: Transport) -> None:
    answer = json.loads(json.dumps(case["answer"]))
    if case["kind"] == "field":
        _set(answer, case["field"], case.get("json"), bool(case.get("missing")))
    elif case["kind"] == "instant":
        _set(answer, case["field"], case["text"], False)
    transport.answers = [{"status": 200, "headers": case["headers"], "body": answer}]
    customer = server(transport).customer(case["request"]["path"].split("/")[2])
    feature = case["request"]["path"].rsplit("/", 1)[1]
    outcome = case["outcome"]
    failure = outcome.get("answer", outcome)
    if failure.get("kind") == "ApiError" or ("value" in outcome and outcome["value"] is None):
        with pytest.raises(APIError) as raised:
            customer.check(feature)
        expected = failure if failure.get("kind") == "ApiError" else outcome["answer"]
        assert raised.value.status == expected["status"]
        assert raised.value.code == expected["code"]
        assert raised.value.message == expected["message"]
        return
    check: Check = customer.check(str(feature))
    if case["kind"] == "value":
        assert feature_value(check.value) == outcome["value"]
        assert check.entitled == outcome["answer"]["entitled"]
    elif case["kind"] == "instant":
        found = check.as_of if case["field"] == "asOf" else check.resets_at
        assert millis(found) == outcome["instant"]["epochMillis"]
    else:
        _compare_check(check, outcome)


def _compare_check(check: Check, outcome: dict[str, Any]) -> None:
    assert check.customer == outcome["customer"]
    assert check.feature == outcome["feature"]
    assert check.type == (
        outcome["type"] if isinstance(outcome["type"], str) else outcome["type"]["unknown"]
    )
    assert open_value(check.type) == outcome["type"]
    assert check.entitled == outcome["entitled"]
    assert feature_value(check.value) == outcome["value"]
    environment = outcome["environment"]
    assert (check.environment.id, check.environment.name) == (
        environment["id"],
        environment["name"],
    )
    assert open_value(check.environment.kind) == environment["kind"]
    assert (check.track.id, check.track.name) == (outcome["track"]["id"], outcome["track"]["name"])
    assert check.release == outcome["release"]
    assert check.change == outcome["change"]
    assert check.testers == outcome["testers"]
    if outcome["experiment"] is None:
        assert check.experiment is None
    else:
        assert check.experiment is not None
        assert check.experiment.id == outcome["experiment"]["id"]
        assert open_value(check.experiment.arm) == outcome["experiment"]["arm"]
    assert millis(check.as_of) == outcome["asOf"]["epochMillis"]
    assert check.stale == outcome["stale"]
    for name in outcome.get("absent", []):
        python = "resets_at" if name == "resetsAt" else str(name)
        assert getattr(check, python) is None
    if "used" in outcome:
        assert isinstance(check, MeteredCheck)
        assert check.used == outcome["used"]
        assert check.held == outcome["held"]
        assert feature_value(check.remaining) == outcome["remaining"]
        expected = outcome["resetsAt"]
        assert millis(check.resets_at) == (None if expected is None else expected["epochMillis"])


def _instant_outcome(value: datetime | None) -> Any:
    return None if value is None else {"epochMillis": millis(value)}


def _expected_instant(value: Any) -> Any:
    return None if value is None else {"epochMillis": value["epochMillis"]}


def _summary(value: Any, *names: str) -> Any:
    if value is None:
        return None
    return {name: getattr(value, name) for name in names}


def _error_fields(error: APIError) -> dict[str, Any]:
    code = error.code
    return {
        "kind": "ApiError",
        "status": error.status,
        "code": code.value if isinstance(code, ErrorCode) else {"unknown": code},
        "message": error.message,
        "requestId": error.request_id,
        "retryAfterSeconds": None if error.retry_after is None else int(error.retry_after),
        "idempotencyKey": error.idempotency_key,
        "payment": None
        if error.payment is None
        else {"status": error.payment.status.value, "url": error.payment.url},
    }


def _plan_change(change: Any) -> dict[str, Any]:
    return {
        "product": _summary(change.product, "key", "name"),
        "plan": _summary(change.plan, "id", "key", "name"),
        "quantity": change.quantity,
        "effective": open_value(change.effective),
        "at": _instant_outcome(change.at),
        "until": _instant_outcome(change.until),
        "changed": change.changed,
        "replayed": change.replayed,
    }


def _expected_plan_change(outcome: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "product": outcome["product"],
        "plan": outcome["plan"],
        "quantity": outcome["quantity"],
        "effective": outcome["effective"],
        "at": _expected_instant(outcome["at"]),
        "until": _expected_instant(outcome["until"]),
        "changed": outcome["changed"],
        "replayed": outcome["replayed"],
    }


def _upgrades(items: Any) -> list[dict[str, Any]]:
    return [
        {
            "plan": u.plan,
            "name": u.name,
            "move": open_value(u.move),
            "action": open_value(u.action),
            "reason": u.reason,
        }
        for u in items
    ]


def _period(value: Any) -> Any:
    return None if value is None else {"key": value.key, "label": value.label}


def _plans(plans: Any) -> dict[str, Any]:
    def pending(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, PendingMove):
            return {"type": "move", "plan": _summary(value.plan, "id", "key", "name")}
        if isinstance(value, PendingCancel):
            return {"type": "cancel", "movingTo": _summary(value.moving_to, "id", "key", "name")}
        return {"type": value.type}

    def price(value: Any) -> Any:
        if value is None:
            return None
        return {
            "amount": value.amount,
            "currency": value.currency,
            "interval": None if value.interval is None else open_value(value.interval),
            "intervalCount": value.interval_count,
            "tax": open_value(value.tax),
        }

    return {
        "held": [
            {
                "plan": {
                    "id": h.plan.id,
                    "key": h.plan.key,
                    "name": h.plan.name,
                    "kind": open_value(h.plan.kind),
                },
                "product": _summary(h.product, "key", "name"),
                "version": h.version,
                "byDefault": h.by_default,
                "period": _period(h.period),
                "renewsAt": _instant_outcome(h.renews_at),
                "pending": pending(h.pending),
                "billedBy": None if h.billed_by is None else open_value(h.billed_by),
            }
            for h in plans.held
        ],
        "options": [
            {
                "plan": {
                    "id": o.plan.id,
                    "key": o.plan.key,
                    "name": o.plan.name,
                    "kind": open_value(o.plan.kind),
                    "description": o.plan.description,
                    "default": o.plan.default,
                },
                "product": _summary(o.product, "key", "name"),
                "from": _summary(o.from_, "id", "key", "name"),
                "move": open_value(o.move),
                "action": open_value(o.action),
                "reason": o.reason,
                "when": open_value(o.when),
                "periods": [_period(p) for p in o.periods],
                "impact": [{"kind": open_value(i.kind), "text": i.text} for i in o.impact],
                "skus": [
                    {
                        "period": _period(s.period),
                        "connector": s.connector,
                        "ids": dict(s.ids),
                        "price": price(s.price),
                    }
                    for s in o.skus
                ],
            }
            for o in plans.options
        ],
    }


def _expected_plans(outcome: Mapping[str, Any]) -> dict[str, Any]:
    def fix(held: dict[str, Any]) -> dict[str, Any]:
        return {**held, "renewsAt": _expected_instant(held["renewsAt"])}

    return {"held": [fix(dict(h)) for h in outcome["held"]], "options": outcome["options"]}


def _usage(result: UsageResult) -> dict[str, Any]:
    return {
        "outcome": open_value(result.outcome),
        "refusal": None if result.refusal is None else open_value(result.refusal),
        "id": result.id,
        "holdId": result.hold_id,
        "mode": open_value(result.mode),
        "amount": result.amount,
        "meterChange": result.meter_change,
        "overBy": result.over_by,
        "late": result.late,
        "occurredAt": _instant_outcome(result.occurred_at),
        "expiresAt": _instant_outcome(result.expires_at),
        "reportedAs": open_value(result.reported_as),
        "entitled": result.entitled,
        "used": result.used,
        "remaining": feature_value(result.remaining),
        "held": result.held,
        "upgrades": _upgrades(result.upgrades),
        "replayed": result.replayed,
    }


def _expected_usage(outcome: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **outcome,
        "occurredAt": _expected_instant(outcome["occurredAt"]),
        "expiresAt": _expected_instant(outcome["expiresAt"]),
    }


def _answers_call(call: Mapping[str, Any], client: EntitlerServer) -> Any:
    customer = client.customer(call["customer"])
    method = call["method"]
    key = call.get("idempotencyKey")
    if method == "check":
        found: Check = customer.check(str(call["feature"]))
        return found
    if method == "plans":
        return customer.plans()
    if method == "recordUsage":
        return customer.record_usage(
            Feature.metered(call["feature"]), call["amount"], idempotency_key=str(key)
        )
    if method == "subscribe":
        return customer.subscribe(
            call["plan"],
            period=call.get("period"),
            return_url=call.get("returnUrl"),
            idempotency_key=key,
        )
    if method == "cancel":
        return customer.cancel(add_on=call.get("addOn"), idempotency_key=key)
    if method == "undoPendingChange":
        return customer.undo_pending_change(idempotency_key=key)
    if method == "setPlan":
        until = call.get("until")
        return customer.set_plan(
            call["plan"],
            period=call.get("period"),
            until=None if until is None else instant(until),
            reason=call.get("reason"),
            actor=call.get("actor"),
            idempotency_key=key,
        )
    if method == "setAddOn":
        return customer.set_add_on(call["addOn"], quantity=call["quantity"], idempotency_key=key)
    if method == "grant":
        return customer.grant(
            call["feature"],
            value=call.get("value"),
            days=call.get("days"),
            reason=call.get("reason"),
            actor=call.get("actor"),
            idempotency_key=key,
        )
    if method == "revokeGrant":
        return customer.revoke_grant(call["grantId"], idempotency_key=key)
    if method == "billingPortal":
        return customer.billing_portal(return_url=call["returnUrl"])
    if method == "syncBilling":
        return customer.sync_billing()
    raise AssertionError(method)


@cases("answers.json")
def test_answers(case: dict[str, Any], transport: Transport) -> None:
    transport.answers = [
        {"status": case["status"], "headers": case["headers"], "body": case["body"]}
    ]
    client = server(transport)
    outcome = case["outcome"]
    call = case["call"]
    if outcome.get("kind") == "ApiError":
        with pytest.raises(APIError) as raised:
            _answers_call(call, client)
        assert _error_fields(raised.value) == outcome
        return
    found = _answers_call(call, client)
    method = call["method"]
    if method == "check":
        assert found.entitled == outcome["entitled"]
        assert _upgrades(found.upgrades) == outcome["upgrades"]
    elif method == "plans":
        assert _plans(found) == _expected_plans(outcome)
    elif method == "recordUsage":
        assert _usage(found) == _expected_usage(outcome)
    elif method == "subscribe":
        assert open_value(found.next) == outcome["next"]
        if outcome["next"] == "pay":
            assert found.url == outcome["url"]
        elif outcome["next"] == "manage":
            assert open_value(found.billed_by) == outcome["billedBy"]
        elif outcome["next"] == "done":
            expected = {k: v for k, v in outcome.items() if k != "next"}
            assert _plan_change(found.change) == _expected_plan_change(expected)
        else:
            assert found.change is None
            assert found.url is None
    elif method in {"cancel", "undoPendingChange", "setPlan", "setAddOn"}:
        assert _plan_change(found) == _expected_plan_change(outcome)
    elif method in {"grant", "revokeGrant"}:
        grant = found.grant
        assert {
            "id": grant.id,
            "feature": grant.feature,
            "until": _instant_outcome(grant.until),
            "revokedAt": _instant_outcome(grant.revoked_at),
        } == {
            **outcome["grant"],
            "until": _expected_instant(outcome["grant"]["until"]),
            "revokedAt": _expected_instant(outcome["grant"]["revokedAt"]),
        }
        assert found.replayed == outcome["replayed"]
    elif method == "billingPortal":
        assert found.url == outcome["url"]
    else:
        assert found.changed == outcome["changed"]


def _errors_call(call: Mapping[str, Any], transport: Transport) -> None:
    method = call["method"]
    key = call.get("idempotencyKey")
    if "client" in call:
        client = EntitlerClient(
            key=call["client"]["key"],
            identity_token=call["client"]["identityToken"],
            max_retries=0,
            cache=None,
            http_client=transport.client(),
        )
        client.register(idempotency_key=key)
    else:
        customer = server(transport).customer(call["customer"])
        if method == "check":
            as_of = call.get("asOf")
            if as_of is None:
                customer.check(call["feature"])
            else:
                customer.check(call["feature"], as_of=instant(as_of))
        elif method == "recordUsage":
            customer.record_usage(
                Feature.metered(call["feature"]), call["amount"], idempotency_key=str(key)
            )
        elif method == "subscribe":
            customer.subscribe(call["plan"], period=call.get("period"), idempotency_key=key)
        elif "sku" in call:
            sku = Sku(connector=call["sku"]["connector"], ids=call["sku"]["ids"])
            until = call.get("until")
            customer.set_plan(
                sku, until=None if until is None else instant(until), idempotency_key=key
            )
        else:
            customer.set_plan(call["plan"], period=call.get("period"), idempotency_key=key)


@cases("errors.json")
def test_errors(case: dict[str, Any], transport: Transport, clock: Clock) -> None:
    if "receivedAt" in case:
        clock.at(case["receivedAt"])
    transport.answers = [
        {"status": case["status"], "headers": case["headers"], "body": case["body"]}
    ]
    with pytest.raises(APIError) as raised:
        _errors_call(case["call"], transport)
    assert _error_fields(raised.value) == case["outcome"]


def _keys_of(given: Any) -> SnapshotKeys | list[Any]:
    if isinstance(given, dict):
        return SnapshotKeys(
            keys=tuple(
                SnapshotKey(
                    kty=k.get("kty", ""),
                    crv=k.get("crv", ""),
                    x=k.get("x", ""),
                    y=k.get("y", ""),
                    kid=k.get("kid", ""),
                    alg=k.get("alg", ""),
                    use=k.get("use", ""),
                )
                for k in cast("list[dict[str, str]]", given["keys"])
            )
        )
    return list(given)


METER = ("used", "held", "remaining", "resetsAt")


def _snapshot(found: VerifiedSnapshot, has: list[str], get: list[str]) -> dict[str, Any]:
    def item(entry: Any) -> dict[str, Any]:
        return {
            "key": entry.key,
            "type": open_value(entry.type),
            "value": feature_value(entry.value),
            "entitled": entry.entitled,
            "upgrades": list(entry.upgrades),
            "used": entry.used,
            "held": entry.held,
            "remaining": feature_value(entry.remaining),
            "resetsAt": millis(entry.resets_at),
        }

    entitlements = found.entitlements
    return {
        "customer": found.customer,
        "environment": {"id": found.environment.id},
        "track": {"id": found.track.id, "name": found.track.name},
        "release": found.release,
        "change": found.change,
        "testers": found.testers,
        "expiresAt": millis(found.expires_at),
        "asOf": millis(entitlements.as_of),
        "experiment": entitlements.experiment,
        "items": [item(e) for e in entitlements.entitlements],
        "has": {k: entitlements.has(k) for k in has},
        "get": {k: _key_or_none(entitlements.get(k)) for k in get},
    }


def _key_or_none(entry: Any) -> Any:
    return None if entry is None else entry.key


def _expected_snapshot(outcome: Mapping[str, Any]) -> dict[str, Any]:
    entitlements = outcome["entitlements"]

    def item(entry: Mapping[str, Any]) -> dict[str, Any]:
        fields = {k: v for k, v in entry.items() if k not in ("sources", "absent")}
        for name in METER:
            fields.setdefault(name, None)
        reset = fields["resetsAt"]
        fields["resetsAt"] = None if reset is None else reset["epochMillis"]
        return fields

    return {
        "customer": outcome["customer"],
        "environment": outcome["environment"],
        "track": outcome["track"],
        "release": outcome["release"],
        "change": outcome["change"],
        "testers": outcome["testers"],
        "expiresAt": outcome["expiresAt"]["epochMillis"],
        "asOf": entitlements["asOf"]["epochMillis"],
        "experiment": entitlements["experiment"],
        "items": [item(i) for i in entitlements["items"]],
        "has": entitlements["has"],
        "get": {k: _key_or_none_json(v) for k, v in entitlements["get"].items()},
    }


def _key_or_none_json(entry: Any) -> Any:
    return None if entry is None else entry["key"]


@cases("snapshots.json")
def test_snapshots(case: dict[str, Any]) -> None:
    expected = case["expected"]
    outcome = case["outcome"]

    def verify() -> VerifiedSnapshot:
        return verify_snapshot(
            case["token"],
            keys=_keys_of(expected["keys"]),
            customer=expected["customer"],
            environment=expected["environment"],
            issuer=expected.get("issuer", "https://api.entitler.dev/customers"),
            now=instant(expected["now"]),
            clock_skew_seconds=expected["clockSkewSeconds"],
        )

    if "snapshot" in outcome:
        found = verify()
        snapshot = outcome["snapshot"]
        listed = snapshot["entitlements"]
        got = _snapshot(found, list(listed["has"]), list(listed["get"]))
        assert got == _expected_snapshot(snapshot)
        return
    if outcome["kind"] == "ArgumentError":
        with pytest.raises((TypeError, ValueError)) as raised:
            verify()
        assert str(raised.value) == python_message(outcome["message"])
        return
    with pytest.raises(SnapshotError) as error:
        verify()
    assert error.value.code == outcome["code"]
    assert error.value.message == python_message(outcome["message"])


def test_seconds_of_matches_the_clock() -> None:
    assert seconds_of("2026-07-01T09:30:00.000Z") == 1_782_898_200.0
