"""Answer builders: complete JSON bodies, as the API sends them, for tests that fake HTTP.

Each builder fills every field the schema requires, so a fixture never fails to decode for the
wrong reason. Pass any field to change it; the result is a plain `dict` ready for `respx` or
`httpx.MockTransport`.

Example:
    ```python
    respx.get(url).respond(json=answers.check(feature="export_pdf", entitled=False, value=0))
    ```
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from .._values import format_instant

Json = dict[str, Any]
"""A JSON object."""

__all__ = [
    "Json",
    "billing_portal",
    "billing_sync",
    "check",
    "context",
    "credential_self",
    "customer_billing",
    "customer_detail",
    "customer_page",
    "customer_plans",
    "customer_summary",
    "customer_track",
    "customer_usage",
    "entitlement",
    "entitlements",
    "error",
    "feature_list",
    "feature_listing",
    "feature_usage",
    "grant",
    "grant_change",
    "held_plan",
    "instant",
    "issued_snapshot",
    "issued_token",
    "move_option",
    "plan_change",
    "pricing",
    "pricing_plan",
    "registered_customer",
    "snapshot_keys",
    "subscribe_step",
    "usage_event",
    "usage_events_result",
    "usage_result",
]


def instant(value: datetime | None = None) -> str:
    """An instant as the API writes it, in UTC with milliseconds; now when none is given."""
    return format_instant((value or datetime.now(UTC)).astimezone(UTC))


def context(
    *,
    environment: Mapping[str, Any] | None = None,
    track: Mapping[str, Any] | None = None,
    release: int | None = 1,
    change: str | None = None,
    testers: bool = True,
    experiment: Mapping[str, Any] | None = None,
) -> Json:
    """The answer context: environment, track, release, change, testers and experiment."""
    return {
        "environment": dict(environment or {"id": "env_test", "name": "Test", "kind": "test"}),
        "track": dict(track or {"id": "trk_all_customers", "name": "All customers"}),
        "release": release,
        "change": change,
        "testers": testers,
        "experiment": None if experiment is None else dict(experiment),
    }


def _meter(
    type_: str, used: int | None, held: int | None, remaining: object, resets_at: object
) -> Json:
    if type_ != "metered" and used is None:
        return {}
    return {
        "used": used or 0,
        "held": held or 0,
        "remaining": remaining,
        "resetsAt": resets_at,
    }


def entitlement(
    *,
    key: str = "export_pdf",
    type: str = "boolean",  # noqa: A002 - the schema's own field name
    entitled: bool = True,
    value: object = True,
    sources: Sequence[Mapping[str, Any]] = (),
    used: int | None = None,
    held: int | None = None,
    remaining: object = None,
    resets_at: str | None = None,
    upgrades: Sequence[Mapping[str, Any]] = (),
) -> Json:
    """`CustomerEntitlement`: one feature of an entitlement list."""
    if type == "metered" and remaining is None and isinstance(value, int):
        remaining = max(0, value - (used or 0) - (held or 0))
    return {
        "key": key,
        "type": type,
        "value": value,
        "entitled": entitled,
        "sources": [dict(item) for item in sources],
        **_meter(type, used, held, remaining, resets_at),
        "upgrades": [dict(item) for item in upgrades],
    }


def check(
    *,
    customer: str = "user_1",
    feature: str = "export_pdf",
    type: str = "boolean",  # noqa: A002 - the schema's own field name
    entitled: bool = True,
    value: object = True,
    sources: Sequence[Mapping[str, Any]] = (),
    used: int | None = None,
    held: int | None = None,
    remaining: object = None,
    resets_at: str | None = None,
    upgrades: Sequence[Mapping[str, Any]] = (),
    as_of: str | None = None,
    **context_fields: Any,
) -> Json:
    """`TrackedCheck`: one feature's entitlement."""
    item = entitlement(
        key=feature,
        type=type,
        entitled=entitled,
        value=value,
        sources=sources,
        used=used,
        held=held,
        remaining=remaining,
        resets_at=resets_at,
        upgrades=upgrades,
    )
    item.pop("key")
    return {
        "customer": customer,
        "asOf": as_of or instant(),
        "feature": feature,
        **item,
        **context(**context_fields),
    }


def entitlements(
    *,
    customer: str = "user_1",
    items: Sequence[Mapping[str, Any]] = (),
    as_of: str | None = None,
    **context_fields: Any,
) -> Json:
    """`TrackedEntitlementList`: built from `entitlement()` items."""
    return {
        "customer": customer,
        "asOf": as_of or instant(),
        "entitlements": [dict(item) for item in items],
        **context(**context_fields),
    }


def held_plan(
    *,
    plan: Mapping[str, Any] | None = None,
    product: Mapping[str, Any] | None = None,
    version: int | None = 1,
    by_default: bool = False,
    period: Mapping[str, Any] | None = None,
    renews_at: str | None = None,
    pending: Mapping[str, Any] | None = None,
    billed_by: str | None = None,
) -> Json:
    """`HeldPlan`: a plan the customer holds."""
    return {
        "plan": dict(plan or {"id": "pln_free", "key": "free", "name": "Free", "kind": "plan"}),
        "product": None if product is None else dict(product),
        "version": version,
        "byDefault": by_default,
        "period": None if period is None else dict(period),
        "renewsAt": renews_at,
        "pending": None if pending is None else dict(pending),
        "billedBy": billed_by,
    }


def move_option(
    *,
    plan: Mapping[str, Any] | None = None,
    product: Mapping[str, Any] | None = None,
    from_: Mapping[str, Any] | None = None,
    move: str = "upgrade",
    action: str = "buy",
    reason: str | None = None,
    when: str = "now",
    periods: Sequence[Mapping[str, Any]] = ({"key": "monthly", "label": "Monthly"},),
    impact: Sequence[Mapping[str, Any]] = (),
    skus: Sequence[Mapping[str, Any]] = (),
) -> Json:
    """`MoveOption`: a move open to the customer."""
    return {
        "plan": dict(
            plan
            or {
                "id": "pln_pro",
                "key": "pro",
                "name": "Pro",
                "kind": "plan",
                "description": "",
                "default": False,
            }
        ),
        "product": None if product is None else dict(product),
        "from": None if from_ is None else dict(from_),
        "move": move,
        "action": action,
        "reason": reason,
        "when": when,
        "periods": [dict(item) for item in periods],
        "impact": [dict(item) for item in impact],
        "skus": [dict(item) for item in skus],
    }


def customer_plans(
    *,
    customer: str = "user_1",
    held: Sequence[Mapping[str, Any]] | None = None,
    options: Sequence[Mapping[str, Any]] = (),
    as_of: str | None = None,
    **context_fields: Any,
) -> Json:
    """`CustomerPlans`: the plans held and the moves open."""
    return {
        "customer": customer,
        "asOf": as_of or instant(),
        "held": [
            dict(item) for item in (held if held is not None else [held_plan(by_default=True)])
        ],
        "options": [dict(item) for item in options],
        **context(**context_fields),
    }


def usage_result(
    *,
    outcome: str = "recorded",
    refusal: str | None = None,
    id: str | None = "use_1",  # noqa: A002 - the schema's own field name
    hold_id: str | None = None,
    mode: str = "gate",
    amount: int = 1,
    meter_change: int | None = None,
    over_by: int = 0,
    late: bool = False,
    occurred_at: str | None = None,
    expires_at: str | None = None,
    reported_as: str = "api",
    feature: str = "ai_credits",
    value: object = 100,
    used: int = 1,
    held: int = 0,
    **check_fields: Any,
) -> Json:
    """`TrackedUsageResult`: what a usage write did, with the meter after it."""
    body = check(
        feature=feature,
        type="metered",
        value=value,
        used=used,
        held=held,
        entitled=check_fields.pop("entitled", True),
        **check_fields,
    )
    return {
        **body,
        "outcome": outcome,
        "refusal": refusal,
        "id": id,
        "holdId": hold_id,
        "mode": mode,
        "amount": amount,
        "meterChange": amount if meter_change is None else meter_change,
        "overBy": over_by,
        "late": late,
        "occurredAt": occurred_at,
        "expiresAt": expires_at,
        "reportedAs": reported_as,
    }


def feature_usage(
    *,
    feature: str = "ai_credits",
    entitled: bool = True,
    value: object = 100,
    used: int = 0,
    held: int = 0,
    remaining: object = None,
    resets_at: str | None = None,
    sources: Sequence[Mapping[str, Any]] = (),
) -> Json:
    """`CustomerFeatureUsage`: one metered feature's meter."""
    if remaining is None and isinstance(value, int) and not isinstance(value, bool):
        remaining = max(0, value - used - held)
    return {
        "feature": feature,
        "type": "metered",
        "entitled": entitled,
        "value": value,
        "sources": [dict(item) for item in sources],
        "used": used,
        "held": held,
        "remaining": remaining,
        "resetsAt": resets_at,
    }


def usage_event(
    *,
    id: str = "use_1",  # noqa: A002 - the schema's own field name
    feature: str = "ai_credits",
    kind: str = "use",
    amount: int = 1,
    set_to: int | None = None,
    source: str = "api",
    by: str | None = None,
    actor: str | None = None,
    reason: str | None = None,
    at: str | None = None,
    cancelled_at: str | None = None,
) -> Json:
    """`UsageEvent`: one usage log entry."""
    return {
        "id": id,
        "feature": feature,
        "source": source,
        "by": by,
        "actor": actor,
        "reason": reason,
        "at": at or instant(),
        "cancelledAt": cancelled_at,
        "kind": kind,
        "amount": amount,
        "setTo": set_to,
    }


def customer_usage(
    *,
    customer: str = "user_1",
    features: Sequence[Mapping[str, Any]] = (),
    log: Sequence[Mapping[str, Any]] = (),
    next: str | None = None,  # noqa: A002 - the schema's own field name
    meters_start_again_at: str | None = None,
    as_of: str | None = None,
    **context_fields: Any,
) -> Json:
    """`TrackedCustomerUsage`: the meters and the usage log's page."""
    return {
        "customer": customer,
        "asOf": as_of or instant(),
        "metersStartAgainAt": meters_start_again_at,
        "features": [dict(item) for item in features],
        "log": {"items": [dict(item) for item in log], "next": next},
        **context(**context_fields),
    }


def registered_customer(
    *, external_id: str = "user_1", created: bool = True, environment_id: str = "env_test"
) -> Json:
    """`RegisteredCustomer`."""
    return {
        "id": f"cus_{external_id}",
        "externalId": external_id,
        "environmentId": environment_id,
        "createdAt": instant(),
        "created": created,
    }


def customer_summary(
    *,
    external_id: str = "user_1",
    name: str = "",
    email: str = "",
    metadata: Mapping[str, str] | None = None,
    plan: Mapping[str, Any] | None = None,
    track: Mapping[str, Any] | None = None,
) -> Json:
    """`TrackedCustomerSummary`."""
    return {
        "id": f"cus_{external_id}",
        "externalId": external_id,
        "name": name,
        "email": email,
        "environmentId": "env_test",
        "sample": False,
        "createdAt": instant(),
        "plan": None if plan is None else dict(plan),
        "plans": [],
        "defaultPlan": None,
        "status": "active",
        "kind": "default",
        "metadata": dict(metadata or {}),
        "track": dict(track or {"id": "trk_all_customers", "name": "All customers"}),
        "testCustomer": True,
    }


def customer_page(
    *,
    items: Sequence[Mapping[str, Any]] = (),
    next: str | None = None,  # noqa: A002 - the schema's own field name
    used: int = 0,
    limit: object = "unlimited",
) -> Json:
    """`TrackedCustomerList`: one page of the customer list."""
    return {"items": [dict(item) for item in items], "next": next, "used": used, "limit": limit}


def grant(
    *,
    id: str = "grt_1",  # noqa: A002 - the schema's own field name
    feature: str = "sso",
    value: str = "",
    until: str | None = None,
    revoked_at: str | None = None,
    reason: str = "",
    by: str = "key_test",
    actor: str | None = None,
) -> Json:
    """`Grant`: `value` is the API's string, empty for on."""
    return {
        "id": id,
        "feature": feature,
        "value": value,
        "from": instant(),
        "until": until,
        "revokedAt": revoked_at,
        "reason": reason,
        "by": by,
        "actor": actor,
    }


def customer_detail(
    *,
    customer: Mapping[str, Any] | None = None,
    entitlements: Sequence[Mapping[str, Any]] = (),
    grants: Sequence[Mapping[str, Any]] = (),
    usage: Sequence[Mapping[str, Any]] = (),
    next: str | None = None,  # noqa: A002 - the schema's own field name
    as_of: str | None = None,
) -> Json:
    """`TrackedCustomerDetail`."""
    return {
        "customer": dict(customer or customer_summary()),
        "asOf": as_of or instant(),
        "subscription": None,
        "defaultPlan": None,
        "products": [],
        "addOns": [],
        "entitlements": [dict(item) for item in entitlements],
        "banked": {},
        "moveOptions": [],
        "grants": [dict(item) for item in grants],
        "usage": {"items": [dict(item) for item in usage], "next": next},
        "activity": [],
        "environment": context()["environment"],
    }


def plan_change(
    *,
    product: Mapping[str, Any] | None = None,
    plan: Mapping[str, Any] | None = None,
    quantity: int | None = None,
    effective: str = "now",
    at: str | None = None,
    until: str | None = None,
    changed: bool = True,
) -> Json:
    """`PlanChange`."""
    return {
        "product": None if product is None else dict(product),
        "plan": dict(plan or {"id": "pln_pro", "key": "pro", "name": "Pro"}),
        "quantity": quantity,
        "effective": effective,
        "at": at or instant(),
        "until": until,
        "changed": changed,
    }


def subscribe_step(
    *,
    next: str = "done",  # noqa: A002 - the schema's own field name
    url: str = "https://checkout.example.com/pay",
    billed_by: str = "apple",
    change: Mapping[str, Any] | None = None,
) -> Json:
    """`SubscribeStep`: `done` with a `PlanChange`, `pay` with `url`, `confirming` or `manage`."""
    if next == "done":
        return {**dict(change or plan_change()), "next": "done"}
    if next == "pay":
        return {"next": "pay", "url": url}
    if next == "manage":
        return {"next": "manage", "billedBy": billed_by}
    return {"next": next}


def grant_change(*, grant_body: Mapping[str, Any] | None = None) -> Json:
    """`GrantChange`."""
    return {"grant": dict(grant_body or grant())}


def customer_track(*, customer: str = "user_1", track: Mapping[str, Any] | None = None) -> Json:
    """`CustomerTrack`."""
    return {
        "customer": customer,
        "environmentId": "env_test",
        "track": dict(track or {"id": "trk_all_customers", "name": "All customers"}),
        "source": "server",
        "previousTrackId": None,
    }


def pricing_plan(
    *,
    key: str = "pro",
    name: str = "Pro",
    kind: str = "plan",
    default: bool = False,
    features: Mapping[str, object] | None = None,
    periods: Sequence[Mapping[str, Any]] = (
        {"key": "monthly", "label": "Monthly", "count": 1, "unit": "months"},
    ),
    listings: Sequence[Mapping[str, Any]] = (),
) -> Json:
    """`PricingPlan`: a plan on sale."""
    return {
        "id": f"pln_{key}",
        "key": key,
        "name": name,
        "description": "",
        "kind": kind,
        "product": None,
        "salesLed": False,
        "status": "active",
        "version": 1,
        "default": default,
        "periods": [dict(item) for item in periods],
        "attachesTo": [],
        "features": dict(features or {}),
        "listings": [dict(item) for item in listings],
    }


def pricing(
    *,
    customer: str | None = None,
    plans: Sequence[Mapping[str, Any]] = (),
    default_plan: str | None = None,
    **context_fields: Any,
) -> Json:
    """`Pricing`: the plans on sale."""
    return {
        **context(**context_fields),
        "customer": customer,
        "defaultPlan": default_plan,
        "products": [],
        "plans": [dict(item) for item in plans],
    }


def feature_listing(
    *,
    key: str = "export_pdf",
    name: str = "Export to PDF",
    type: str = "boolean",  # noqa: A002 - the schema's own field name
    description: str = "",
    unit: str = "",
    archived: bool = False,
    includes: Sequence[str] = (),
) -> Json:
    """`FeatureListing`: a feature in the catalogue."""
    return {
        "id": f"fea_{key}",
        "key": key,
        "name": name,
        "type": type,
        "description": description,
        "unit": unit,
        "resetEvery": {"count": 1, "unit": "months"} if type == "metered" else None,
        "archived": archived,
        "includes": list(includes),
    }


def feature_list(
    *,
    features: Sequence[Mapping[str, Any]] = (),
    release: int | None = 1,
    change: str | None = None,
    environment: Mapping[str, Any] | None = None,
    track: Mapping[str, Any] | None = None,
) -> Json:
    """`FeatureList`: the catalogue's features."""
    found = context(environment=environment, track=track, release=release, change=change)
    return {
        "environment": found["environment"],
        "track": found["track"],
        "release": release,
        "change": change,
        "features": [dict(item) for item in features],
    }


def snapshot_keys(*, keys: Sequence[Mapping[str, Any]] = ()) -> Json:
    """`Jwks`: the snapshot keys."""
    return {"keys": [dict(item) for item in keys]}


def issued_snapshot(*, token: str = "header.claims.signature", key_id: str = "key_1") -> Json:
    """`IssuedSnapshot`."""
    return {"token": token, "expiresAt": instant(), "keyId": key_id}


def issued_token(
    *,
    customer: str = "user_1",
    token: str = "header.claims.signature",
    scopes: Sequence[str] = ("entitlements:read",),
) -> Json:
    """`IssuedCustomerToken`."""
    return {"token": token, "customer": customer, "scopes": list(scopes), "expiresAt": instant()}


def customer_billing() -> Json:
    """`CustomerBilling` for a customer no provider bills."""
    return {
        "provider": None,
        "status": None,
        "sku": None,
        "items": 0,
        "drift": None,
        "products": [],
    }


def billing_portal(*, url: str = "https://billing.example.com/session") -> Json:
    """`BillingPortal`."""
    return {"provider": "stripe", "url": url}


def billing_sync(*, changed: bool = False) -> Json:
    """`BillingSync`."""
    return {"changed": changed}


def credential_self(
    *, scopes: Sequence[str] = ("entitlements:read",), registration: bool | None = None
) -> Json:
    """`CredentialSelf`: the scopes a credential holds."""
    body: Json = {
        "id": "key_test",
        "name": "Test",
        "kind": "secret",
        "orgId": "org_test",
        "projectId": None,
        "environmentId": None,
        "scopes": list(scopes),
        "allowedOrigins": [],
        "createdAt": instant(),
        "revokedAt": None,
    }
    if registration is not None:
        body["registration"] = registration
    return body


def usage_events_result(*, results: Sequence[Mapping[str, Any]] = ()) -> Json:
    """`UsageEventsResult`, with totals counted from `results`."""
    items = [dict(item) for item in results]
    return {
        "results": items,
        "recorded": sum(1 for item in items if item.get("outcome") == "recorded"),
        "duplicates": sum(1 for item in items if item.get("outcome") == "duplicate"),
        "errors": sum(1 for item in items if item.get("outcome") == "error"),
    }


def error(code: str, message: str = "Refused.", **extra: Any) -> Json:
    """An error body: `{"error": {"code", "message"}}`."""
    return {"error": {"code": code, "message": message, **extra}}
