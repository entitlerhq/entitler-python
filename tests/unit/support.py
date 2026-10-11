"""A fake HTTP transport and a fake clock for the unit suite."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
import pytest

from entitler import _clock

Handler = Callable[[httpx.Request], httpx.Response]
KEY = "ent_test_conformance_server_key"


def seconds_of(text: str) -> float:
    """An RFC 3339 instant as seconds since 1970."""
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


@dataclass
class Clock:
    """A fake clock: waits advance it instead of sleeping."""

    now: float = 1_782_898_200.0
    waits: list[float] = field(default_factory=list[float])

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def wait(stop: Any, seconds: float) -> bool:
            self.waits.append(seconds)
            self.now += seconds
            return bool(stop.is_set())

        async def sleep(seconds: float) -> None:
            self.waits.append(seconds)
            self.now += seconds

        monkeypatch.setattr(_clock, "now", lambda: self.now)
        monkeypatch.setattr(_clock, "wait", wait)
        monkeypatch.setattr(_clock, "sleep", sleep)

    def at(self, text: str) -> None:
        self.now = seconds_of(text)


class _CutOff(httpx.SyncByteStream, httpx.AsyncByteStream):
    def __iter__(self) -> Iterator[bytes]:
        yield b'{"customer": "user_'
        raise httpx.ReadError("The connection was reset.")

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b'{"customer": "user_'
        raise httpx.ReadError("The connection was reset.")


def respond(answer: Mapping[str, Any], request: httpx.Request) -> httpx.Response:
    """Turns a conformance answer into a response, or raises its failure."""
    failure = answer.get("failure")
    if failure == "connection":
        raise httpx.ConnectError("Connection refused.", request=request)
    if failure == "timeout":
        raise httpx.ReadTimeout("Timed out.", request=request)
    if failure == "cut-off":
        return httpx.Response(200, headers={"Content-Type": "application/json"}, stream=_CutOff())
    body = answer.get("body")
    if body is None:
        content = b""
    elif isinstance(body, str):
        content = body.encode()
    else:
        content = json.dumps(body).encode()
    return httpx.Response(
        answer.get("status", 200), headers=answer.get("headers") or {}, content=content
    )


@dataclass
class Transport:
    """Answers requests in order, and keeps every request it received."""

    answers: list[Any] = field(default_factory=list[Any])
    requests: list[httpx.Request] = field(default_factory=list[httpx.Request])
    fallback: Handler | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.answers:
            answer = self.answers.pop(0)
            return answer(request) if callable(answer) else respond(answer, request)
        if self.fallback is not None:
            return self.fallback(request)
        raise AssertionError(f"No answer queued for {request.method} {request.url}")

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def async_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    def body(self, index: int = -1) -> Any:
        content = self.requests[index].content
        return json.loads(content) if content else None


def ok(body: Mapping[str, Any] | None, **headers: str) -> dict[str, Any]:
    """A `200` answer."""
    return {"status": 200, "headers": headers, "body": body}


ROUTES: list[tuple[str, str, Callable[[], dict[str, Any]]]] = []


def _routes() -> list[tuple[str, str, Callable[[], dict[str, Any]]]]:
    from entitler.testing import answers

    held = {"hold_id": "hld_1", "expires_at": "2026-07-01T09:35:00.000Z"}
    return [
        ("GET", r"/customers/[^/]+/entitlements/[^/]+", answers.check),
        ("GET", r"/customers/[^/]+/entitlements", answers.entitlements),
        ("GET", r"/customers/[^/]+/plans", answers.customer_plans),
        ("GET", r"/customers/[^/]+/pricing", answers.pricing),
        ("GET", r"/customers/[^/]+/usage", answers.customer_usage),
        ("GET", r"/customers/[^/]+/billing", answers.customer_billing),
        ("GET", r"/customers/snapshot-keys", answers.snapshot_keys),
        ("GET", r"/customers", answers.customer_page),
        ("GET", r"/customers/[^/]+", answers.customer_detail),
        ("GET", r"/pricing", answers.pricing),
        ("GET", r"/pricing/features", answers.feature_list),
        ("GET", r"/keys/self", answers.credential_self),
        ("POST", r"/customers", answers.customer_detail),
        ("POST", r"/customers/[^/]+/usage/holds", lambda: answers.usage_result(outcome="held", **held)),
        ("POST", r"/customers/[^/]+/usage/holds/[^/]+/settle", lambda: answers.usage_result(outcome="settled")),
        ("DELETE", r"/customers/[^/]+/usage/holds/[^/]+", lambda: answers.usage_result(outcome="released")),
        ("POST", r"/customers/[^/]+/usage", answers.usage_result),
        ("DELETE", r"/customers/[^/]+/usage/[^/]+", lambda: answers.usage_result(outcome="cancelled")),
        ("POST", r"/customers/[^/]+/meters/[^/]+/adjustments", lambda: answers.usage_result(outcome="adjusted")),
        ("POST", r"/customers/[^/]+/snapshots", answers.issued_snapshot),
        ("POST", r"/customers/[^/]+/tokens", answers.issued_token),
        ("POST", r"/customers/[^/]+/subscription", answers.subscribe_step),
        ("POST", r"/customers/[^/]+/billing-portal", answers.billing_portal),
        ("POST", r"/customers/[^/]+/billing/sync", answers.billing_sync),
        ("PUT", r"/customers/[^/]+/track", answers.customer_track),
        ("POST", r"/customers/[^/]+/grants", answers.grant_change),
        ("DELETE", r"/customers/[^/]+/grants/[^/]+", answers.grant_change),
        ("PUT", r"/customers/[^/]+", answers.registered_customer),
        ("PATCH", r"/customers/[^/]+", answers.customer_summary),
        ("DELETE", r"/customers/[^/]+", lambda: {}),
        ("POST", r"/usage/events", answers.usage_events_result),
    ]


def api(request: httpx.Request) -> httpx.Response:
    """Answers any request with a complete body for its route; other writes answer a plan change."""
    import re

    from entitler.testing import answers

    if not ROUTES:
        ROUTES.extend(_routes())
    path = request.url.path
    for method, pattern, build in ROUTES:
        if method == request.method and re.fullmatch(pattern, path):
            if method == "DELETE" and pattern == r"/customers/[^/]+":
                return httpx.Response(204)
            if pattern == r"/usage/events":
                sent = json.loads(request.content)["events"]
                results = [
                    {"index": i, "outcome": "recorded", "id": f"use_{i}", "late": False, "error": None}
                    for i in range(len(sent))
                ]
                return httpx.Response(200, json=answers.usage_events_result(results=results))
            return httpx.Response(200, json=build())
    return httpx.Response(200, json=answers.plan_change())


def wired(transport: Transport) -> Transport:
    """The transport, answering every request with `api` once its queue is empty."""
    transport.fallback = api
    return transport
