"""Test doubles for your app's own tests: a customer that answers without the network.

`fake_customer()` answers a real `ServerCustomer` whose requests go to an in-process fake of the
API, so every method decodes as it would from Entitler and no request leaves the process. The
package never imports this module itself.

Example:
    ```python
    from entitler import Feature
    from entitler.testing import fake_customer, metered

    def test_export_needs_the_feature() -> None:
        customer = fake_customer({"export_pdf": False, "ai_credits": metered(value=100, used=97)})
        assert not customer.is_entitled("export_pdf", default=True)
        customer.record_usage(Feature.metered("ai_credits"), 3, idempotency_key="job-1")
        assert customer.writes[0].method == "record_usage"
    ```
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from .. import _clock
from .._async_clients import AsyncEntitlerServer
from .._async_customer import AsyncServerCustomer
from .._clients import EntitlerServer
from .._customer import ServerCustomer
from .._errors import APIError
from .._ops import ServerOps
from . import answers

__all__ = [
    "FakeAsyncCustomer",
    "FakeCall",
    "FakeCustomer",
    "Meter",
    "answers",
    "fake_async_customer",
    "fake_customer",
    "metered",
]

Json = dict[str, Any]


@dataclass(frozen=True, slots=True)
class Meter:
    """A metered feature's allowance and the amount used so far, for `fake_customer`."""

    value: int | str
    """The allowance per period: a whole number or `"unlimited"`."""
    used: int = 0
    """The amount used so far."""


def metered(*, value: int | str, used: int = 0) -> Meter:
    """A metered feature for `fake_customer`: its allowance and the amount used so far."""
    return Meter(value, used)


@dataclass(frozen=True, slots=True)
class FakeCall:
    """One call the fake received: its method, and its arguments as the request carried them."""

    method: str
    """The customer method's name, such as `record_usage` or `set_plan`."""
    arguments: Mapping[str, Any]
    """The request's body, path ids and query, by the API's field names."""
    idempotency_key: str | None
    """The idempotency key the request sent, when it sent one."""


_ROUTES: list[tuple[str, re.Pattern[str], str]] = [
    (m, re.compile(p), name)
    for m, p, name in [
        ("GET", r"/entitlements/(?P<feature>[^/]+)", "check"),
        ("GET", r"/entitlements", "entitlements"),
        ("GET", r"/plans", "plans"),
        ("GET", r"/pricing", "pricing"),
        ("GET", r"/usage", "usage"),
        ("GET", r"", "details"),
        ("GET", r"/billing", "billing"),
        ("POST", r"/usage", "record_usage"),
        ("POST", r"/usage/holds", "hold_usage"),
        ("POST", r"/usage/holds/(?P<holdId>[^/]+)/settle", "settle_usage"),
        ("DELETE", r"/usage/holds/(?P<holdId>[^/]+)", "release_usage"),
        ("DELETE", r"/usage/(?P<usageId>[^/]+)", "cancel_usage"),
        ("POST", r"/snapshots", "snapshot"),
        ("POST", r"/subscription", "subscribe"),
        ("DELETE", r"/subscription", "cancel"),
        ("DELETE", r"/subscription/pending", "undo_pending_change"),
        ("DELETE", r"/subscription/add-ons/(?P<addOn>[^/]+)", "cancel"),
        ("DELETE", r"/subscription/add-ons/(?P<addOn>[^/]+)/pending", "undo_pending_change"),
        ("POST", r"/billing-portal", "billing_portal"),
        ("POST", r"/billing/sync", "sync_billing"),
        ("PUT", r"", "register"),
        ("PATCH", r"", "update"),
        ("DELETE", r"", "erase"),
        ("POST", r"/tokens", "token"),
        ("PUT", r"/track", "set_track"),
        ("PUT", r"/plan", "set_plan"),
        ("PUT", r"/add-ons/(?P<addOn>[^/]+)", "set_add_on"),
        ("POST", r"/grants", "grant"),
        ("DELETE", r"/grants/(?P<grantId>[^/]+)", "revoke_grant"),
        ("POST", r"/meters/(?P<feature>[^/]+)/adjustments", "adjust_meter"),
    ]
]


class _Refused(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass
class _Hold:
    id: str
    feature: str
    amount: int
    key: str
    expires: float
    state: str = "open"
    settled: int | None = None


@dataclass
class _Report:
    id: str
    feature: str
    amount: int
    cancelled: bool = False


@dataclass
class _Api:
    customer: str
    values: dict[str, Any]
    plans: Json | None
    pricing: Json | None
    writes: list[FakeCall] = field(default_factory=list[FakeCall])
    overrides: dict[str, Callable[[FakeCall], Json]] = field(
        default_factory=dict[str, Callable[[FakeCall], Json]]
    )
    keys: dict[str, tuple[str, Any]] = field(default_factory=dict[str, tuple[str, Any]])
    holds: dict[str, _Hold] = field(default_factory=dict[str, _Hold])
    reports: dict[str, _Report] = field(default_factory=dict[str, _Report])
    log: list[Json] = field(default_factory=list[Json])
    registered: bool = False
    counter: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.raw_path.decode("ascii").split("?")[0]
        local = path.removeprefix(f"/customers/{_encoded(self.customer)}")
        name, params = self._route(request.method, local)
        body: Json = json.loads(request.content) if request.content else {}
        query = {k: v for k, v in request.url.params.items() if k != "erase"}
        arguments: Json = {**body, **params, **query}
        call = FakeCall(name, arguments, request.headers.get("Idempotency-Key"))
        with self.lock:
            if request.method != "GET":
                self.writes.append(call)
            try:
                if name in self.overrides:
                    answer: Json | None = self.overrides[name](call)
                    replayed = False
                else:
                    answer, replayed = self._answer(name, call)
            except APIError as error:
                return _error(error.status, str(error.code), error.message)
            except _Refused as refused:
                return _error(refused.status, refused.code, str(refused))
        if answer is None:
            return httpx.Response(204)
        headers = {"Idempotent-Replayed": "true"} if replayed else {}
        return httpx.Response(200, json=answer, headers=headers)

    def _route(self, method: str, local: str) -> tuple[str, dict[str, str]]:
        from urllib.parse import unquote

        for verb, pattern, name in _ROUTES:
            found = pattern.fullmatch(local)
            if verb == method and found:
                return name, {k: unquote(v) for k, v in found.groupdict().items()}
        raise _Refused(404, "not_found", f"The fake has no route for {method} {local}.")

    def _next_id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}_{self.counter}"

    def _feature(self, key: str) -> Json:
        value = self.values.get(key, _MISSING)
        if value is _MISSING:
            raise _Refused(
                404, "feature_not_found", f"No feature has the key {key} in this environment."
            )
        if isinstance(value, Meter):
            used = value.used
            held = sum(h.amount for h in self._open_holds(key))
            limit = value.value
            remaining: Any = (
                "unlimited" if limit == "unlimited" else max(0, int(limit) - used - held)
            )
            entitled = remaining == "unlimited" or remaining > 0
            return answers.entitlement(
                key=key,
                type="metered",
                entitled=entitled,
                value=limit,
                used=used,
                held=held,
                remaining=remaining,
            )
        if isinstance(value, bool):
            return answers.entitlement(
                key=key, type="boolean", entitled=value, value=True if value else 0
            )
        entitled = value == "unlimited" or (isinstance(value, int) and value > 0)
        return answers.entitlement(key=key, type="config", entitled=entitled, value=value)

    def _open_holds(self, key: str) -> list[_Hold]:
        now = _clock.now()
        return [
            h
            for h in self.holds.values()
            if h.feature == key and h.state == "open" and h.expires > now
        ]

    def _check(self, key: str) -> Json:
        item = self._feature(key)
        return {
            "customer": self.customer,
            "asOf": answers.instant(),
            "feature": item.pop("key"),
            **item,
            **answers.context(),
        }

    def _meter(self, key: str) -> Meter:
        value = self.values.get(key, _MISSING)
        if value is _MISSING:
            raise _Refused(
                404, "feature_not_found", f"No feature has the key {key} in this environment."
            )
        if not isinstance(value, Meter):
            raise _Refused(400, "not_metered", f"{key} is not a metered feature.")
        return value

    def _result(self, key: str, **fields: Any) -> Json:
        check = self._check(key)
        self._meter(key)
        return {
            **check,
            "outcome": fields.get("outcome", "recorded"),
            "refusal": fields.get("refusal"),
            "id": fields.get("id"),
            "holdId": fields.get("hold_id"),
            "mode": fields.get("mode", "gate"),
            "amount": fields.get("amount", 0),
            "meterChange": fields.get("meter_change", 0),
            "overBy": fields.get("over_by", 0),
            "late": False,
            "occurredAt": fields.get("occurred_at"),
            "expiresAt": fields.get("expires_at"),
            "reportedAs": "api",
        }

    def _replay(self, key: str | None, fingerprint: str) -> Any:
        if key is None or key not in self.keys:
            return None
        stored, ref = self.keys[key]
        if stored != fingerprint:
            raise _Refused(
                422, "idempotency_mismatch", "This idempotency key was used for another request."
            )
        return ref

    def _answer(self, name: str, call: FakeCall) -> tuple[Json | None, bool]:
        args = call.arguments
        fingerprint = json.dumps([name, args], sort_keys=True)
        if name == "check":
            return self._check(args["feature"]), False
        if name == "entitlements":
            items = [self._feature(k) for k in sorted(self.values)]
            return answers.entitlements(customer=self.customer, items=items), False
        if name == "plans":
            return self.plans or answers.customer_plans(customer=self.customer), False
        if name == "pricing":
            return self.pricing or answers.pricing(customer=self.customer), False
        if name == "usage":
            features = [
                answers.feature_usage(feature=k, value=v.value, used=v.used)
                for k, v in sorted(self.values.items())
                if isinstance(v, Meter)
            ]
            return answers.customer_usage(
                customer=self.customer, features=features, log=list(self.log)
            ), False
        if name == "details":
            items = [self._feature(k) for k in sorted(self.values)]
            summary = answers.customer_summary(external_id=self.customer)
            return answers.customer_detail(
                customer=summary, entitlements=items, usage=list(self.log)
            ), False
        if name == "record_usage":
            return self._record(call, fingerprint)
        if name == "hold_usage":
            return self._hold(call, fingerprint)
        if name == "settle_usage":
            return self._settle(args["holdId"], int(args["amount"])), False
        if name == "release_usage":
            return self._release(args["holdId"]), False
        if name == "cancel_usage":
            return self._cancel(call), False
        if name == "adjust_meter":
            return self._adjust(call), False
        return self._plain(name, call), False

    def _record(self, call: FakeCall, fingerprint: str) -> tuple[Json, bool]:
        args = call.arguments
        feature, amount = args["feature"], int(args["amount"])
        mode = args.get("mode", "gate")
        replay = self._replay(call.idempotency_key, fingerprint)
        if isinstance(replay, _Report):
            outcome = "cancelled" if replay.cancelled else "duplicate"
            return self._result(
                feature, outcome=outcome, id=replay.id, amount=amount, mode=mode
            ), True
        meter = self._meter(feature)
        held = sum(h.amount for h in self._open_holds(feature))
        limit = meter.value
        room = None if limit == "unlimited" else int(limit) - meter.used - held
        if mode == "gate" and room is not None and amount > room:
            refusal = "not_entitled" if int(limit) == 0 else "over_allowance"
            return self._result(
                feature, outcome="refused", refusal=refusal, amount=amount, mode=mode
            ), False
        report = _Report(self._next_id("use"), feature, amount)
        self.reports[report.id] = report
        self.values[feature] = Meter(limit, meter.used + amount)
        over = 0 if room is None else max(0, amount - max(room, 0))
        self.log.append(answers.usage_event(id=report.id, feature=feature, amount=amount))
        if call.idempotency_key is not None:
            self.keys[call.idempotency_key] = (fingerprint, report)
        return self._result(
            feature, id=report.id, amount=amount, meter_change=amount, over_by=over, mode=mode
        ), False

    def _hold(self, call: FakeCall, fingerprint: str) -> tuple[Json, bool]:
        args = call.arguments
        feature, amount = args["feature"], int(args["amount"])
        replay = self._replay(call.idempotency_key, fingerprint)
        if isinstance(replay, _Hold):
            self._expire(replay)
            outcome = {"open": "duplicate", "settled": "settled", "released": "released"}.get(
                replay.state, "duplicate"
            )
            return self._hold_result(replay, outcome), True
        meter = self._meter(feature)
        held = sum(h.amount for h in self._open_holds(feature))
        limit = meter.value
        if limit != "unlimited" and amount > int(limit) - meter.used - held:
            refusal = "not_entitled" if int(limit) == 0 else "over_allowance"
            return self._result(feature, outcome="refused", refusal=refusal, amount=amount), False
        ttl = int(args.get("ttlSeconds", 300))
        hold = _Hold(
            self._next_id("hld"), feature, amount, call.idempotency_key or "", _clock.now() + ttl
        )
        self.holds[hold.id] = hold
        if call.idempotency_key is not None:
            self.keys[call.idempotency_key] = (fingerprint, hold)
        return self._hold_result(hold, "held"), False

    def _expire(self, hold: _Hold) -> None:
        if hold.state == "open" and hold.expires <= _clock.now():
            hold.state = "expired"

    def _hold_result(self, hold: _Hold, outcome: str, meter_change: int = 0) -> Json:
        from datetime import UTC, datetime

        expires = answers.instant(datetime.fromtimestamp(hold.expires, UTC))
        return self._result(
            hold.feature,
            outcome=outcome,
            hold_id=hold.id,
            amount=hold.amount if hold.settled is None else hold.settled,
            meter_change=meter_change,
            expires_at=expires,
        )

    def _find_hold(self, hold_id: str) -> _Hold:
        hold = self.holds.get(hold_id)
        if hold is None:
            raise _Refused(404, "not_found", f"No hold has the id {hold_id}.")
        self._expire(hold)
        return hold

    def _settle(self, hold_id: str, amount: int) -> Json:
        hold = self._find_hold(hold_id)
        if hold.state == "expired":
            raise _Refused(409, "hold_expired", "This hold expired before it was settled.")
        if hold.state == "released":
            raise _Refused(409, "hold_released", "This hold was released.")
        if hold.state == "settled":
            if hold.settled == amount:
                return self._hold_result(hold, "duplicate")
            raise _Refused(
                409, "hold_settled", "This hold was already settled with another amount."
            )
        if amount > hold.amount:
            raise _Refused(400, "invalid_body", "Settle a hold with at most the amount held.")
        hold.state, hold.settled = "settled", amount
        meter = self._meter(hold.feature)
        self.values[hold.feature] = Meter(meter.value, meter.used + amount)
        if amount > 0:
            self.log.append(answers.usage_event(id=hold.id, feature=hold.feature, amount=amount))
        return self._hold_result(hold, "settled", meter_change=amount)

    def _release(self, hold_id: str) -> Json:
        hold = self._find_hold(hold_id)
        if hold.state == "settled":
            raise _Refused(409, "hold_settled", "This hold was already settled.")
        hold.state = "released"
        return self._hold_result(hold, "released")

    def _cancel(self, call: FakeCall) -> Json:
        usage_id = call.arguments["usageId"]
        report = self.reports.get(usage_id)
        if report is None:
            raise _Refused(404, "not_found", f"No usage report has the id {usage_id}.")
        change = 0
        if not report.cancelled:
            report.cancelled = True
            meter = self._meter(report.feature)
            self.values[report.feature] = Meter(meter.value, max(0, meter.used - report.amount))
            change = -report.amount
            for entry in self.log:
                if entry["id"] == usage_id:
                    entry["cancelledAt"] = answers.instant()
                    entry["actor"] = call.arguments.get("actor")
                    entry["reason"] = call.arguments.get("reason")
        return self._result(
            report.feature,
            outcome="cancelled",
            id=report.id,
            amount=report.amount,
            meter_change=change,
        )

    def _adjust(self, call: FakeCall) -> Json:
        args = call.arguments
        feature = args["feature"]
        meter = self._meter(feature)
        target = max(0, meter.used + int(args["by"])) if "by" in args else int(args["to"])
        change = target - meter.used
        self.values[feature] = Meter(meter.value, target)
        usage_id = None
        if change:
            usage_id = self._next_id("use")
            self.log.append(
                answers.usage_event(
                    id=usage_id,
                    feature=feature,
                    kind="adjust",
                    amount=change,
                    set_to=target,
                    actor=args.get("actor"),
                    reason=args.get("reason"),
                )
            )
        return self._result(
            feature, outcome="adjusted", id=usage_id, amount=abs(change), meter_change=change
        )

    def _plain(self, name: str, call: FakeCall) -> Json | None:
        args = call.arguments
        if name == "erase":
            return None
        if name == "register":
            created = not self.registered
            self.registered = True
            return answers.registered_customer(external_id=self.customer, created=created)
        if name == "update":
            return answers.customer_summary(
                external_id=self.customer, name=args.get("name", ""), email=args.get("email", "")
            )
        if name == "token":
            return answers.issued_token(
                customer=self.customer, scopes=args.get("scopes", ["entitlements:read"])
            )
        if name == "snapshot":
            return answers.issued_snapshot()
        if name == "set_track":
            track = args.get("track")
            ref = None if track is None else {"id": f"trk_{track}", "name": track}
            return answers.customer_track(customer=self.customer, track=ref)
        if name == "billing":
            return answers.customer_billing()
        if name == "billing_portal":
            return answers.billing_portal()
        if name == "sync_billing":
            return answers.billing_sync()
        if name == "grant":
            return answers.grant_change(
                grant_body=answers.grant(
                    id=self._next_id("grt"), feature=args["feature"], value=args.get("value", "")
                )
            )
        if name == "revoke_grant":
            return answers.grant_change(
                grant_body=answers.grant(id=args["grantId"], revoked_at=answers.instant())
            )
        if name == "subscribe":
            plan = {"id": f"pln_{args['plan']}", "key": args["plan"], "name": args["plan"]}
            return answers.subscribe_step(change=answers.plan_change(plan=plan))
        if name == "set_add_on":
            plan = {"id": f"pln_{args['addOn']}", "key": args["addOn"], "name": args["addOn"]}
            return answers.plan_change(plan=plan, quantity=int(args["quantity"]))
        if name == "set_plan" and isinstance(args.get("plan"), str):
            plan = {"id": f"pln_{args['plan']}", "key": args["plan"], "name": args["plan"]}
            return answers.plan_change(plan=plan)
        return answers.plan_change()


_MISSING = object()


def _encoded(customer: str) -> str:
    from .._values import encode

    return encode(customer)


def _error(status: int, code: str, message: str) -> httpx.Response:
    return httpx.Response(status, json=answers.error(code, message))


class _Recording:
    _api: _Api

    @property
    def writes(self) -> list[FakeCall]:
        """Every write the fake received, in order."""
        return list(self._api.writes)

    def answer(self, method: str, fn: Callable[[FakeCall], Json]) -> None:
        """Replaces one method's answer: `fn` answers its JSON body, or raises an `APIError`.

        Build the body with the builders in `entitler.testing.answers`.
        """
        self._api.overrides[method] = fn


class FakeCustomer(_Recording, ServerCustomer):
    """A `ServerCustomer` answered by an in-process fake of the API: it makes no request."""

    def __init__(self, api: _Api, server: EntitlerServer) -> None:
        self._api = api
        self._server = server
        super().__init__(ServerOps(server._driver.engine, api.customer), server._driver)


class FakeAsyncCustomer(_Recording, AsyncServerCustomer):
    """An `AsyncServerCustomer` answered by an in-process fake of the API."""

    def __init__(self, api: _Api, server: AsyncEntitlerServer) -> None:
        self._api = api
        self._server = server
        super().__init__(ServerOps(server._driver.engine, api.customer), server._driver)


def _api(
    values: Mapping[str, Any], customer_id: str, plans: Json | None, pricing: Json | None
) -> _Api:
    return _Api(customer_id, dict(values), plans, pricing)


def fake_customer(
    values: Mapping[str, bool | int | str | Meter],
    *,
    id: str = "user_1",  # noqa: A002 - the customer's id, as the specification names it
    plans: Json | None = None,
    pricing: Json | None = None,
) -> FakeCustomer:
    """A `ServerCustomer` that makes no request, for your app's own tests.

    Args:
        values: Each feature's value by key: `True` or `False` for an on/off feature, an amount
            or `"unlimited"` for a config feature, or `metered(value=..., used=...)` for a meter.
            A key it does not hold answers `404 feature_not_found`.
        id: The customer's external id.
        plans: The `plans()` answer, built with `answers.customer_plans`.
        pricing: The `pricing()` answer, built with `answers.pricing`.
    """
    api = _api(values, id, plans, pricing)
    client = httpx.Client(transport=httpx.MockTransport(api.handle))
    server = EntitlerServer("ent_test_fake", cache=None, max_retries=0, http_client=client)
    return FakeCustomer(api, server)


def fake_async_customer(
    values: Mapping[str, bool | int | str | Meter],
    *,
    id: str = "user_1",  # noqa: A002 - the customer's id, as the specification names it
    plans: Json | None = None,
    pricing: Json | None = None,
) -> FakeAsyncCustomer:
    """An `AsyncServerCustomer` that makes no request. See `fake_customer`."""
    api = _api(values, id, plans, pricing)
    client = httpx.AsyncClient(transport=httpx.MockTransport(api.handle))
    server = AsyncEntitlerServer("ent_test_fake", cache=None, max_retries=0, http_client=client)
    return FakeAsyncCustomer(api, server)
