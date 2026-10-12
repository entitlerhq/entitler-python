"""Strict decoding (section 6.2), checked against the frozen OpenAPI documents."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from entitler import APIError, EntitlerServer, ErrorCode
from entitler import _decode as d
from entitler._decode import DecodeError, Obj, parse_instant
from entitler.testing import answers

from .support import KEY, Transport

OPENAPI = Path(__file__).parent.parent / "fixtures" / "openapi"
CUSTOMERS = json.loads((OPENAPI / "customers.json").read_text(encoding="utf-8"))
KEYS = json.loads((OPENAPI / "keys.json").read_text(encoding="utf-8"))

PRICE = {
    "amount": 900,
    "currency": "aud",
    "interval": "month",
    "intervalCount": 1,
    "tax": "inclusive",
}
SKU = {
    "period": {"key": "monthly", "label": "Monthly"},
    "connector": "apple",
    "ids": {"productId": "p"},
    "price": PRICE,
}
PURCHASE = {
    "money": "real",
    "channel": {"provider": "stripe", "connectionId": "c"},
    "release": 1,
    "change": None,
    "arm": None,
}
SUBSCRIPTION = {
    "product": {"key": "app", "name": "App"},
    "plan": {"id": "p", "key": "pro", "name": "Pro"},
    "version": 1,
    "cohort": None,
    "period": {"key": "monthly", "label": "Monthly"},
    "startedAt": answers.instant(),
    "renewsAt": answers.instant(),
    "billing": {
        "provider": "stripe",
        "startsAt": answers.instant(),
        "endsAt": answers.instant(),
        "trialEndsAt": None,
        "cancelsAt": None,
    },
    "until": None,
    "addOns": [
        {
            "plan": {"id": "a", "key": "seats", "name": "Seats"},
            "version": 1,
            "quantity": 2,
            "countable": True,
            "addedAt": answers.instant(),
            "movingTo": None,
            "purchase": PURCHASE,
        }
    ],
    "pending": {"type": "cancel", "movingTo": None},
    "purchase": PURCHASE,
    "override": {
        "from": {"id": "f", "key": "free", "name": "Free"},
        "period": None,
        "billedBy": {"provider": "stripe", "connectionId": "c"},
        "since": answers.instant(),
        "by": "agent",
    },
}
SOURCES = [
    {"type": "plan", "plan": "pro", "name": "Pro", "version": 1, "byDefault": False, "value": True}
]
UPGRADES = [{"plan": "pro", "name": "Pro", "move": "upgrade", "action": "buy", "reason": None}]


def detail() -> dict[str, Any]:
    body = answers.customer_detail(
        entitlements=[answers.entitlement(sources=SOURCES, upgrades=UPGRADES)],
        grants=[answers.grant()],
        usage=[answers.usage_event()],
    )
    body["subscription"] = copy.deepcopy(SUBSCRIPTION)
    body["products"] = [
        {
            "product": {"key": "app", "name": "App"},
            "defaultPlan": None,
            "subscription": copy.deepcopy(SUBSCRIPTION),
        }
    ]
    body["moveOptions"] = [
        answers.move_option(skus=[SKU], impact=[{"kind": "gains", "text": "SSO"}])
    ]
    body["activity"] = [
        {"text": "Joined", "at": answers.instant(), "by": None, "actor": None, "reason": None}
    ]
    body["banked"] = {"ai_credits": 5}
    body["customer"] = answers.customer_summary(
        plan={"id": "p", "key": "pro", "name": "Pro", "version": 1}
    )
    body["customer"]["plans"] = [
        {"id": "p", "key": "pro", "name": "Pro", "version": 1, "product": "app"}
    ]
    return body


def billing() -> dict[str, Any]:
    drift = {"billedPlan": "pro", "heldPlan": None, "observedAt": answers.instant()}
    body = answers.customer_billing()
    body.update({"provider": "stripe", "status": "active", "sku": SKU, "items": 1, "drift": drift})
    body["products"] = [
        {"product": "app", "status": "active", "sku": SKU, "items": 1, "drift": drift}
    ]
    return body


def pricing() -> dict[str, Any]:
    listing = {
        "period": {"key": "monthly", "label": "Monthly"},
        "channels": [
            {
                "channel": {"provider": "stripe", "connectionId": "c"},
                "name": "Stripe",
                "mode": "test",
                "purchasable": True,
                "ids": {"priceId": "price_1"},
                "price": PRICE,
            }
        ],
    }
    plan = answers.pricing_plan(features={"sso": True, "seats": 5}, listings=[listing])
    body = answers.pricing(plans=[plan])
    body["products"] = [{"key": "app", "name": "App", "defaultPlan": "free"}]
    return body


ANSWERS: dict[
    str, tuple[str, Callable[[], dict[str, Any]], Callable[[Obj], Any], dict[str, Any]]
] = {
    "check": (
        "TrackedCheck",
        lambda: answers.check(sources=SOURCES, upgrades=UPGRADES),
        d.check,
        CUSTOMERS,
    ),
    "metered-check": (
        "TrackedCheck",
        lambda: answers.check(
            type="metered", value=10, used=1, held=1, resets_at=answers.instant()
        ),
        d.check,
        CUSTOMERS,
    ),
    "entitlements": (
        "TrackedEntitlementList",
        lambda: answers.entitlements(
            items=[answers.entitlement(sources=SOURCES, upgrades=UPGRADES)]
        ),
        d.entitlements,
        CUSTOMERS,
    ),
    "plans": (
        "CustomerPlans",
        lambda: answers.customer_plans(
            held=[
                answers.held_plan(
                    product={"key": "app", "name": "App"},
                    period={"key": "monthly", "label": "Monthly"},
                    renews_at=answers.instant(),
                    pending={"type": "move", "plan": {"id": "p", "key": "pro", "name": "Pro"}},
                    billed_by="stripe",
                )
            ],
            options=[
                answers.move_option(
                    skus=[SKU],
                    impact=[{"kind": "gains", "text": "SSO"}],
                    from_={"id": "f", "key": "free", "name": "Free"},
                    product={"key": "app", "name": "App"},
                )
            ],
        ),
        d.customer_plans,
        CUSTOMERS,
    ),
    "usage-result": (
        "TrackedUsageResult",
        lambda: answers.usage_result(sources=SOURCES),
        d.usage_result,
        CUSTOMERS,
    ),
    "customer-usage": (
        "TrackedCustomerUsage",
        lambda: answers.customer_usage(
            features=[answers.feature_usage()], log=[answers.usage_event()]
        ),
        lambda o: d.customer_usage(o, lambda page: page),
        CUSTOMERS,
    ),
    "registered": ("RegisteredCustomer", answers.registered_customer, d.registered, CUSTOMERS),
    "summary": ("TrackedCustomerSummary", answers.customer_summary, d.customer_summary, CUSTOMERS),
    "detail": (
        "TrackedCustomerDetail",
        detail,
        lambda o: d.customer_detail(o, lambda page: page),
        CUSTOMERS,
    ),
    "page": (
        "TrackedCustomerList",
        lambda: answers.customer_page(items=[answers.customer_summary()]),
        d.customer_page,
        CUSTOMERS,
    ),
    "plan-change": (
        "PlanChange",
        lambda: answers.plan_change(product={"key": "app", "name": "App"}),
        d.plan_change,
        CUSTOMERS,
    ),
    "step-done": ("SubscribeStep", answers.subscribe_step, d.subscribe_step, CUSTOMERS),
    "step-pay": (
        "SubscribeStep",
        lambda: answers.subscribe_step(next="pay"),
        d.subscribe_step,
        CUSTOMERS,
    ),
    "step-manage": (
        "SubscribeStep",
        lambda: answers.subscribe_step(next="manage"),
        d.subscribe_step,
        CUSTOMERS,
    ),
    "grant-change": ("GrantChange", answers.grant_change, d.grant_change, CUSTOMERS),
    "track": ("CustomerTrack", answers.customer_track, d.customer_track, CUSTOMERS),
    "pricing": ("Pricing", pricing, d.pricing, CUSTOMERS),
    "features": (
        "FeatureList",
        lambda: answers.feature_list(
            features=[
                answers.feature_listing(type="metered"),
                answers.feature_listing(type="group", includes=["a"]),
            ]
        ),
        d.feature_list,
        CUSTOMERS,
    ),
    "snapshot-keys": (
        "Jwks",
        lambda: answers.snapshot_keys(
            keys=[
                {
                    "kty": "EC",
                    "crv": "P-256",
                    "x": "x",
                    "y": "y",
                    "kid": "k",
                    "alg": "ES256",
                    "use": "sig",
                }
            ]
        ),
        d.snapshot_keys,
        CUSTOMERS,
    ),
    "issued-snapshot": ("IssuedSnapshot", answers.issued_snapshot, d.issued_snapshot, CUSTOMERS),
    "issued-token": ("IssuedCustomerToken", answers.issued_token, d.issued_token, CUSTOMERS),
    "billing": ("CustomerBilling", billing, d.customer_billing, CUSTOMERS),
    "billing-portal": ("BillingPortal", answers.billing_portal, d.billing_portal, CUSTOMERS),
    "billing-sync": ("BillingSync", answers.billing_sync, d.billing_sync, CUSTOMERS),
    "batch": (
        "UsageEventsResult",
        lambda: answers.usage_events_result(
            results=[
                {
                    "index": 0,
                    "outcome": "error",
                    "id": None,
                    "late": False,
                    "error": {"code": "x", "message": "y"},
                }
            ]
        ),
        d.usage_events_result,
        CUSTOMERS,
    ),
    "key-self": (
        "CredentialSelf",
        lambda: answers.credential_self(registration=True),
        d.credential_self,
        KEYS,
    ),
    "token-self": (
        "CredentialSelf",
        lambda: {"customer": "u", "scopes": ["entitlements:read"], "expiresAt": answers.instant()},
        d.credential_self,
        KEYS,
    ),
}


def resolve(schema: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        schema = document["components"]["schemas"][schema["$ref"].split("/")[-1]]
    return schema


def matches(value: Any, schema: dict[str, Any], document: dict[str, Any]) -> bool:
    """A loose match, to pick the `anyOf` or `oneOf` branch a value belongs to."""
    schema = resolve(schema, document)
    for combinator in ("anyOf", "oneOf"):
        if combinator in schema:
            return any(matches(value, branch, document) for branch in schema[combinator])
    if "const" in schema:
        return bool(value == schema["const"])
    kind = schema.get("type")
    if isinstance(kind, list):
        kinds = cast("list[str]", kind)
        return any(matches(value, {**schema, "type": k}, document) for k in kinds)
    if kind == "null":
        return value is None
    if kind == "object" or "properties" in schema:
        if not isinstance(value, dict):
            return False
        found = cast("dict[str, Any]", value)
        properties: dict[str, Any] = schema.get("properties", {})
        required: list[str] = schema.get("required", [])
        if not set(required) <= set(found):
            return False
        return all(
            matches(found[name], properties[name], document) for name in found if name in properties
        )
    if kind == "array":
        return isinstance(value, list)
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind in ("integer", "number"):
        return isinstance(value, int | float) and not isinstance(value, bool)
    return True


def required_paths(
    value: Any, schema: dict[str, Any], document: dict[str, Any], path: tuple[Any, ...] = ()
) -> Iterator[tuple[Any, ...]]:
    """Every path of a field the schema requires, in a value that fits it."""
    schema = resolve(schema, document)
    for combinator in ("anyOf", "oneOf"):
        if combinator in schema:
            for branch in schema[combinator]:
                if matches(value, branch, document):
                    yield from required_paths(value, branch, document, path)
                    return
            return
    if isinstance(value, dict) and "properties" in schema:
        for name in schema.get("required", []):
            yield (*path, name)
        for name, item in cast("dict[str, Any]", value).items():
            if name in schema["properties"]:
                yield from required_paths(item, schema["properties"][name], document, (*path, name))
    elif isinstance(value, list) and value and "items" in schema:
        yield from required_paths(value[0], schema["items"], document, (*path, 0))


def without(body: Any, path: tuple[Any, ...]) -> Any:
    copied = copy.deepcopy(body)
    target = copied
    for part in path[:-1]:
        target = target[part]
    del target[path[-1]]
    return copied


@pytest.mark.parametrize("name", list(ANSWERS))
def test_every_builder_decodes_with_the_real_decoder(name: str) -> None:
    schema, build, decode, document = ANSWERS[name]
    body = build()
    assert matches(body, {"$ref": f"#/components/schemas/{schema}"}, document)
    decode(Obj(json.loads(json.dumps(body)), ""))


@pytest.mark.parametrize("name", list(ANSWERS))
def test_an_empty_object_does_not_decode(name: str) -> None:
    decode = ANSWERS[name][2]
    with pytest.raises(DecodeError):
        decode(Obj({}, ""))


def removals() -> Iterator[Any]:
    for name, (schema, build, _decode, document) in ANSWERS.items():
        body = build()
        paths = sorted(
            set(required_paths(body, {"$ref": f"#/components/schemas/{schema}"}, document)), key=str
        )
        for path in paths:
            yield pytest.param(name, path, id=f"{name}:{'.'.join(map(str, path))}")


@pytest.mark.parametrize(("name", "path"), list(removals()))
def test_each_required_field_missing_does_not_decode(name: str, path: tuple[Any, ...]) -> None:
    _schema, build, decode, _document = ANSWERS[name]
    with pytest.raises(DecodeError):
        decode(Obj(without(build(), path), ""))


@pytest.mark.parametrize(
    "text",
    [
        "2026-02-31T00:00:00Z",
        "2025-02-29T00:00:00Z",
        "2026-07-01T24:00:00Z",
        "2026-07-01T09:60:00Z",
        "2026-07-01T09:30:60Z",
        "2026-07-01T09:30:00+25:00",
        "2026-07-01T09:30:00+05:60",
        "2026-07-01T09:30:00",
        "2026-07-01 09:30:00Z",
        "20260701T093000Z",
        "2026-W27-3T09:30:00Z",
        "0000-01-01T00:00:00Z",
        "١٢٣٤-01-01T00:00:00Z",
    ],
)
def test_impossible_instants_never_roll_over(text: str) -> None:
    assert parse_instant(text) is None


def test_instants_keep_their_offset_and_fraction() -> None:
    found = parse_instant("2024-02-29T23:59:59.1234567-05:30")
    assert found is not None
    assert found.microsecond == 123456
    assert found.utcoffset() is not None


def test_metadata_strings_stay_exactly_as_sent() -> None:
    body = answers.customer_summary(metadata={"at": "2026-07-01T09:30:00Z", "until": "never"})
    summary = d.customer_summary(Obj(body, ""))
    assert summary.metadata == {"at": "2026-07-01T09:30:00Z", "until": "never"}


def test_the_invalid_response_cause_holds_no_body_text(transport: Transport) -> None:
    secret = "eyJsecret-token-in-a-body"
    transport.answers = [
        {"status": 200, "body": f'{{"token": "{secret}", broken'},
        {"status": 200, "body": {"token": secret}},
    ]
    client = EntitlerServer(KEY, http_client=transport.client(), max_retries=0)
    for _ in range(2):
        with pytest.raises(APIError) as raised:
            client.customer("u").token()
        error = raised.value
        chain: list[BaseException] = []
        current: BaseException | None = error
        while current is not None:
            chain.append(current)
            current = current.__cause__ or current.__context__
        assert error.code == "invalid_response"
        assert error.message == "Entitler sent an answer this SDK cannot read."
        for link in chain:
            assert secret not in str(link)
            assert secret not in repr(link)
            assert not hasattr(link, "doc")


def test_nan_and_infinity_are_not_json() -> None:
    with pytest.raises(DecodeError):
        d.parse_json('{"value": NaN}')


def test_fields_of_another_type_do_not_decode() -> None:
    body = answers.usage_result()
    body["amount"] = 1.0
    with pytest.raises(DecodeError):
        d.usage_result(Obj(body, ""))
    body = answers.check()
    body["value"] = False
    with pytest.raises(DecodeError):
        d.check(Obj(body, ""))


def test_every_documented_error_code_is_known() -> None:
    known = {code.value for code in ErrorCode}
    for document in (CUSTOMERS, KEYS):
        enum = document["components"]["schemas"]["ErrorBody"]["properties"]["error"]["properties"][
            "code"
        ]["enum"]
        assert set(enum) <= known
    assert {
        "http_error",
        "invalid_response",
        "connection_failed",
        "timed_out",
        "internal",
        "snapshot_key_unavailable",
    } <= known


def test_unknown_codes_stay_strings(transport: Transport) -> None:
    transport.answers = [{"status": 418, "body": answers.error("teapot", "Short and stout.")}]
    client = EntitlerServer(KEY, http_client=transport.client(), max_retries=0)
    with pytest.raises(APIError) as raised:
        client.customer("u").check("sso")
    assert raised.value.code == "teapot"
    assert not isinstance(raised.value.code, ErrorCode)


def test_decoding_never_depends_on_content_type(transport: Transport) -> None:
    def html(_request: httpx.Request) -> httpx.Response:
        body = json.dumps(answers.check()).encode()
        return httpx.Response(200, content=body, headers={"Content-Type": "text/html"})

    transport.answers = [html]
    client = EntitlerServer(KEY, http_client=transport.client(), max_retries=0)
    assert client.customer("u").check("sso").entitled


def test_instants_at_both_ends_of_the_calendar_convert_on_every_platform() -> None:
    from entitler._values import utc

    first = datetime(1, 1, 1, tzinfo=UTC)
    last = datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)
    assert utc(first.timestamp()) == first
    assert utc(last.timestamp()) == last
    assert utc(1_782_898_200.25) == datetime(2026, 7, 1, 9, 30, 0, 250000, tzinfo=UTC)
