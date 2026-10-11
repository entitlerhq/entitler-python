"""Every customer and client operation, once, as calls both kinds of client run."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, TypeVar

from . import _decode as d
from ._decode import Obj
from ._engine import ABSENT, Call, Engine, Gen, invalid_response
from ._errors import (
    APIError,
    EntitlerError,
    EntitlerTimeoutError,
    ErrorCode,
    UsageRefusedError,
    UsageReplayedError,
    UsageSettlementError,
)
from ._features import Feature
from ._models import (
    BatchOutcome,
    BillingPortal,
    BillingSync,
    Check,
    CustomerBilling,
    CustomerDetail,
    CustomerPage,
    CustomerPlans,
    CustomerSummary,
    CustomerTrack,
    CustomerUsage,
    Entitlements,
    Environment,
    FeatureList,
    GrantChange,
    IssuedCustomerToken,
    IssuedSnapshot,
    Page,
    PlanBilling,
    PlanChange,
    Pricing,
    RegisteredCustomer,
    Scope,
    Scopes,
    Sku,
    SnapshotKeys,
    SubscribeStep,
    UsageEvent,
    UsageEventError,
    UsageEventInput,
    UsageEventResult,
    UsageEventsResult,
    UsageMode,
    UsageOutcome,
    UsageResult,
)
from ._values import (
    CUSTOMER_ID,
    FEATURE_KEY,
    MAX_SAFE,
    PLAN_KEY,
    PRODUCT_KEY,
    canonical_json,
    id_segment,
    instant,
    key_or_new,
    require_id,
    require_text,
    required_key,
    segment,
    sha256_hex,
    timeout,
    usage_amount,
    whole,
)
from ._visitor import visitor_of

T = TypeVar("T")
L = TypeVar("L")

SETTLED_AMOUNT = "Pass amount as a whole number from 0 to the held amount."
AMOUNT_USED = "Pass the amount used as a whole number of 0 or more."
ADJUSTMENT = "Pass either by, a whole number other than 0, or to, a whole number of 0 or more."
BATCH_SIZE = 500
SCOPE_ORDER = tuple(Scope)


def feature_key(feature: object) -> str:
    """The key of a feature constant or plain key, refusing a blank one."""
    return require_id(feature.key if isinstance(feature, Feature) else feature, FEATURE_KEY)


def call_timeout(value: float | None) -> float | None:
    """Checks a per-call timeout."""
    return None if value is None else timeout(value)


def as_of_header(value: datetime | None) -> dict[str, str | None]:
    """The `Entitler-As-Of` header for a server read at another instant."""
    return {
        "Entitler-As-Of": None if value is None else instant(value, "Pass as_of as a valid date.")
    }


def compact(**fields: object) -> dict[str, object]:
    """The fields given, leaving out `None`."""
    return {name: value for name, value in fields.items() if value is not None}


def with_flag(decode: Callable[[Obj], T]) -> Callable[[Obj, bool], T]:
    """A decoder that ignores the stale or replayed flag."""
    return lambda o, _flag: decode(o)


def known_scopes(scopes: tuple[str, ...]) -> tuple[Scope, ...]:
    """Keeps the scopes the SDK knows, in its fixed order."""
    held = set(scopes)
    return tuple(scope for scope in SCOPE_ORDER if scope.value in held)


def hold_is_open(result: UsageResult) -> bool:
    """True for an open hold: held, or a duplicate expiring after the answer's own instant."""
    if result.hold_id is None or result.expires_at is None:
        return False
    if result.outcome == UsageOutcome.HELD:
        return True
    return result.outcome == UsageOutcome.DUPLICATE and result.expires_at > result.as_of


class CustomerOps:
    """The calls one customer's methods make, on `/customers/{id}` or `/customers/me`."""

    def __init__(self, engine: Engine, customer_id: str | None) -> None:
        self.engine = engine
        self.id = customer_id
        self.path = "/customers/me" if customer_id is None else f"/customers/{segment(customer_id)}"
        self.named: str | None = customer_id

    def send(self, call: Call[T]) -> Gen[T]:
        """Sends a call, noting the customer an in-app answer names."""
        call.path = self.path + call.path
        call.customer = self.path
        answer = yield from self.engine.send(call)
        named = getattr(answer.data, "customer", None)
        if self.id is None and isinstance(named, str):
            self.named = named
        return answer.data

    def read(
        self,
        path: str,
        decode: Callable[[Obj, bool], T],
        *,
        revalidate: bool,
        call_seconds: float | None,
        headers: Mapping[str, str | None] | None = None,
    ) -> Gen[T]:
        """A read through the cache."""
        return self.send(
            Call(
                "GET",
                path,
                decode,
                timeout=call_timeout(call_seconds),
                cached=True,
                revalidate=revalidate is True,
                headers=headers or {},
            )
        )

    def write(
        self,
        method: str,
        path: str,
        decode: Callable[[Obj, bool], T],
        *,
        key: str,
        call_seconds: float | None,
        body: object = ABSENT,
        query: Mapping[str, str | None] | None = None,
        headers: Mapping[str, str | None] | None = None,
        changes: bool = True,
    ) -> Gen[T]:
        """A write, under its idempotency key."""
        return self.send(
            Call(
                method,
                path,
                decode,
                timeout=call_timeout(call_seconds),
                body=body,
                query=query or {},
                headers=headers or {},
                idempotency_key=key,
                changes=(self.path,) if changes else (),
            )
        )

    def check(
        self,
        feature: object,
        revalidate: bool,
        seconds: float | None,
        as_of: datetime | None = None,
    ) -> Gen[Check]:
        """`GET /customers/{id}/entitlements/{feature}`."""
        path = f"/entitlements/{segment(feature_key(feature))}"
        headers = as_of_header(as_of)
        return self.read(
            path, d.check, revalidate=revalidate, call_seconds=seconds, headers=headers
        )

    def is_entitled(
        self,
        feature: object,
        default: bool,
        revalidate: bool,
        seconds: float | None,
        as_of: datetime | None = None,
    ) -> Gen[bool]:
        """`check`'s decision, or `default` when the check fails."""
        if not isinstance(default, bool):
            raise TypeError("Pass default as True or False.")
        call_timeout(seconds)
        path = f"/entitlements/{segment(feature_key(feature))}"
        headers = as_of_header(as_of)
        try:
            found = yield from self.read(
                path, d.check, revalidate=revalidate, call_seconds=seconds, headers=headers
            )
        except EntitlerError as error:
            self.engine.report(error)
            return default
        return found.entitled

    def entitlements(
        self, revalidate: bool, seconds: float | None, as_of: datetime | None = None
    ) -> Gen[Entitlements[Environment]]:
        """`GET /customers/{id}/entitlements`."""
        headers = as_of_header(as_of)
        return self.read(
            "/entitlements",
            d.entitlements,
            revalidate=revalidate,
            call_seconds=seconds,
            headers=headers,
        )

    def plans(
        self, revalidate: bool, seconds: float | None, as_of: datetime | None = None
    ) -> Gen[CustomerPlans]:
        """`GET /customers/{id}/plans`."""
        headers = as_of_header(as_of)
        return self.read(
            "/plans", d.customer_plans, revalidate=revalidate, call_seconds=seconds, headers=headers
        )

    def pricing(self, visitor: str | None, revalidate: bool, seconds: float | None) -> Gen[Pricing]:
        """`GET /customers/{id}/pricing`."""
        headers = {"Entitler-Visitor": visitor_of(visitor)}
        return self.read(
            "/pricing", d.pricing, revalidate=revalidate, call_seconds=seconds, headers=headers
        )

    def usage(
        self,
        cursor: str | None,
        seconds: float | None,
        log: Callable[[Page[UsageEvent]], L],
        as_of: datetime | None = None,
    ) -> Gen[CustomerUsage[L]]:
        """`GET /customers/{id}/usage`, its log's first page wrapped by `log`."""
        return self.send(
            Call(
                "GET",
                "/usage",
                lambda o, _flag: d.customer_usage(o, log),
                timeout=call_timeout(seconds),
                query={"cursor": cursor},
                headers=as_of_header(as_of),
            )
        )

    def usage_page(
        self, cursor: str, seconds: float | None, as_of: datetime | None = None
    ) -> Gen[Page[UsageEvent]]:
        """A later page of the usage log."""
        answer = yield from self.usage(cursor, seconds, lambda page: page, as_of)
        return answer.log

    def record_usage(
        self,
        feature: object,
        amount: object,
        key: object,
        mode: UsageMode | str | None,
        occurred_at: datetime | None,
        register: bool | None,
        seconds: float | None,
    ) -> Gen[UsageResult]:
        """`POST /customers/{id}/usage`."""
        checked = required_key(key)
        body = {
            "feature": feature_key(feature),
            "amount": usage_amount(amount),
            **compact(
                mode=None if mode is None else str(mode),
                occurredAt=None
                if occurred_at is None
                else instant(occurred_at, "Pass occurred_at as a valid date."),
                register=register,
            ),
        }
        return self.write(
            "POST", "/usage", d.usage_result, key=checked, call_seconds=seconds, body=body
        )

    def hold_usage(
        self,
        feature: object,
        amount: object,
        key: object,
        ttl_seconds: int | None,
        seconds: float | None,
        longest: int = 200,
    ) -> Gen[UsageResult]:
        """`POST /customers/{id}/usage/holds`."""
        checked = required_key(key, longest)
        body = {
            "feature": feature_key(feature),
            "amount": usage_amount(amount),
            **compact(ttlSeconds=ttl_seconds),
        }
        return self.write(
            "POST", "/usage/holds", d.usage_result, key=checked, call_seconds=seconds, body=body
        )

    def start_hold(
        self,
        feature: object,
        amount: object,
        key: object,
        ttl_seconds: int | None,
        seconds: float | None,
    ) -> Gen[UsageResult]:
        """A hold that is open, else the refusal or replay it raises."""
        result = yield from self.hold_usage(feature, amount, key, ttl_seconds, seconds, 193)
        if result.outcome == UsageOutcome.REFUSED:
            raise UsageRefusedError(result)
        if not hold_is_open(result):
            raise UsageReplayedError(result)
        return result

    def settle_usage(
        self, hold_id: object, amount: object, key: object, seconds: float | None
    ) -> Gen[UsageResult]:
        """`POST /customers/{id}/usage/holds/{holdId}/settle`."""
        path = f"/usage/holds/{id_segment(hold_id, 'Provide the id of the hold.')}/settle"
        body = {"amount": whole(amount, SETTLED_AMOUNT, 0)}
        return self.write(
            "POST", path, d.usage_result, key=key_or_new(key), call_seconds=seconds, body=body
        )

    def release_usage(
        self, hold_id: object, key: object, seconds: float | None
    ) -> Gen[UsageResult]:
        """`DELETE /customers/{id}/usage/holds/{holdId}`."""
        path = f"/usage/holds/{id_segment(hold_id, 'Provide the id of the hold.')}"
        return self.write("DELETE", path, d.usage_result, key=key_or_new(key), call_seconds=seconds)

    def snapshot(self, ttl_seconds: int | None, seconds: float | None) -> Gen[IssuedSnapshot]:
        """`POST /customers/{id}/snapshots`."""
        return self.write(
            "POST",
            "/snapshots",
            with_flag(d.issued_snapshot),
            key=key_or_new(None),
            call_seconds=seconds,
            body=compact(ttlSeconds=ttl_seconds),
            changes=False,
        )

    def subscribe(
        self,
        plan: object,
        period: str | None,
        quantity: int | None,
        return_url: str | None,
        register: bool | None,
        key: object,
        seconds: float | None,
    ) -> Gen[SubscribeStep]:
        """`POST /customers/{id}/subscription`."""
        body = {
            "plan": require_id(plan, PLAN_KEY),
            **compact(
                period=period,
                quantity=None
                if quantity is None
                else whole(quantity, "Pass quantity as a whole number from 1 to 10000.", 1, 10000),
                returnUrl=return_url,
                register=register,
            ),
        }
        return self.write(
            "POST",
            "/subscription",
            d.subscribe_step,
            key=key_or_new(key),
            call_seconds=seconds,
            body=body,
        )

    def target(
        self,
        suffix: str,
        add_on: str | None,
        product: str | None,
        key: object,
        seconds: float | None,
    ) -> Gen[PlanChange]:
        """`cancel` and `undo_pending_change`: on the plan, or the add-on `add_on` names."""
        if add_on is not None and product is not None:
            raise ValueError("Pass either add_on or product, not both.")
        checked = key_or_new(key)
        if add_on is not None:
            path = f"/subscription/add-ons/{id_segment(add_on, PLAN_KEY)}{suffix}"
            return self.write("DELETE", path, d.plan_change, key=checked, call_seconds=seconds)
        query = {"product": None if product is None else require_id(product, PRODUCT_KEY)}
        return self.write(
            "DELETE",
            f"/subscription{suffix}",
            d.plan_change,
            key=checked,
            call_seconds=seconds,
            query=query,
        )

    def billing_portal(self, return_url: object, seconds: float | None) -> Gen[BillingPortal]:
        """`POST /customers/{id}/billing-portal`."""
        url = require_text(return_url, "Pass return_url as the page to send the customer back to.")
        return self.write(
            "POST",
            "/billing-portal",
            with_flag(d.billing_portal),
            key=key_or_new(None),
            call_seconds=seconds,
            body={"returnUrl": url},
            changes=False,
        )

    def sync_billing(self, seconds: float | None) -> Gen[BillingSync]:
        """`POST /customers/{id}/billing/sync`."""
        return self.write(
            "POST",
            "/billing/sync",
            with_flag(d.billing_sync),
            key=key_or_new(None),
            call_seconds=seconds,
        )

    def register_me(self, key: object, seconds: float | None) -> Gen[RegisteredCustomer]:
        """`PUT /customers/me`, with no body."""
        return self.write("PUT", "", d.registered, key=key_or_new(key), call_seconds=seconds)

    def scopes(self, seconds: float | None, identity: bool) -> Gen[Scopes]:
        """`GET /keys/self`."""
        return scopes(self.engine, seconds, identity)


def scopes(engine: Engine, seconds: float | None, identity: bool) -> Gen[Scopes]:
    """`GET /keys/self`, kept to the scopes the SDK knows."""
    held, registration = yield from _data(
        engine,
        Call("GET", "/keys/self", with_flag(d.credential_self), timeout=call_timeout(seconds)),
    )
    return Scopes(scopes=known_scopes(held), registration=registration if identity else None)


def snapshot_keys(
    engine: Engine, seconds: float | None
) -> Gen[tuple[SnapshotKeys, dict[str, object]]]:
    """`GET /customers/snapshot-keys`, with no credential, answering the keys and the raw body."""
    keys = yield from _data(
        engine,
        Call(
            "GET",
            "/customers/snapshot-keys",
            lambda o, _flag: (d.snapshot_keys(o), o.data),
            timeout=call_timeout(seconds),
            open=True,
        ),
    )
    return keys


def pricing(
    engine: Engine, visitor: str | None, revalidate: bool, seconds: float | None
) -> Gen[Pricing]:
    """`GET /pricing`, through the cache."""
    return _data(
        engine,
        Call(
            "GET",
            "/pricing",
            d.pricing,
            timeout=call_timeout(seconds),
            cached=True,
            revalidate=revalidate is True,
            headers={"Entitler-Visitor": visitor_of(visitor)},
        ),
    )


def features(engine: Engine, revalidate: bool, seconds: float | None) -> Gen[FeatureList]:
    """`GET /pricing/features`, through the cache."""
    return _data(
        engine,
        Call(
            "GET",
            "/pricing/features",
            d.feature_list,
            timeout=call_timeout(seconds),
            cached=True,
            revalidate=revalidate is True,
        ),
    )


def _data(engine: Engine, call: Call[T]) -> Gen[T]:
    answer = yield from engine.send(call)
    return answer.data


class ServerOps(CustomerOps):
    """The calls a server customer adds: registration, details, tokens, tracks and decisions."""

    def register(
        self,
        name: str | None,
        email: str | None,
        metadata: Mapping[str, str | None] | None,
        visitor: str | None,
        key: object,
        seconds: float | None,
    ) -> Gen[RegisteredCustomer]:
        """`PUT /customers/{id}`."""
        details = compact(
            name=name, email=email, metadata=None if metadata is None else dict(metadata)
        )
        return self.write(
            "PUT",
            "",
            d.registered,
            key=key_or_new(key),
            call_seconds=seconds,
            body=details or ABSENT,
            headers={"Entitler-Visitor": visitor_of(visitor)},
        )

    def details(
        self,
        cursor: str | None,
        seconds: float | None,
        log: Callable[[Page[UsageEvent]], L],
        as_of: datetime | None,
    ) -> Gen[CustomerDetail[L]]:
        """`GET /customers/{id}`, its usage log's first page wrapped by `log`."""
        return self.send(
            Call(
                "GET",
                "",
                lambda o, _flag: d.customer_detail(o, log),
                timeout=call_timeout(seconds),
                query={"cursor": cursor},
                headers=as_of_header(as_of),
            )
        )

    def details_page(
        self, cursor: str, seconds: float | None, as_of: datetime | None
    ) -> Gen[Page[UsageEvent]]:
        """A later page of the usage log `details` holds."""
        answer = yield from self.details(cursor, seconds, lambda page: page, as_of)
        return answer.usage

    def update(
        self,
        name: str | None,
        email: str | None,
        metadata: Mapping[str, str | None] | None,
        key: object,
        seconds: float | None,
    ) -> Gen[CustomerSummary]:
        """`PATCH /customers/{id}`."""
        body = compact(
            name=name, email=email, metadata=None if metadata is None else dict(metadata)
        )
        return self.write(
            "PATCH", "", d.customer_summary, key=key_or_new(key), call_seconds=seconds, body=body
        )

    def erase(self, key: object, seconds: float | None) -> Gen[None]:
        """`DELETE /customers/{id}?erase=true`."""
        return self.send(
            Call(
                "DELETE",
                "",
                lambda o, _flag: _erased(o),
                timeout=call_timeout(seconds),
                query={"erase": "true"},
                idempotency_key=key_or_new(key),
                changes=(self.path,),
                empty=True,
            )
        )

    def token(
        self, scopes: Sequence[str] | None, ttl_seconds: int | None, seconds: float | None
    ) -> Gen[IssuedCustomerToken]:
        """`POST /customers/{id}/tokens`."""
        body = compact(
            scopes=None if scopes is None else [str(scope) for scope in scopes],
            ttlSeconds=ttl_seconds,
        )
        return self.write(
            "POST",
            "/tokens",
            with_flag(d.issued_token),
            key=key_or_new(None),
            call_seconds=seconds,
            body=body,
            changes=False,
        )

    def set_track(
        self, track: str | None, key: object, seconds: float | None
    ) -> Gen[CustomerTrack]:
        """`PUT /customers/{id}/track`."""
        body = {
            "track": None if track is None else require_text(track, "Name the track by its name.")
        }
        return self.write(
            "PUT", "/track", d.customer_track, key=key_or_new(key), call_seconds=seconds, body=body
        )

    def billing(self, seconds: float | None) -> Gen[CustomerBilling]:
        """`GET /customers/{id}/billing`."""
        return self.send(
            Call("GET", "/billing", with_flag(d.customer_billing), timeout=call_timeout(seconds))
        )

    def set_plan(
        self,
        plan: str | Sku | None,
        options: dict[str, Any],
        key: object,
        seconds: float | None,
    ) -> Gen[PlanChange]:
        """`PUT /customers/{id}/plan`."""
        product = options.get("product")
        if plan is None:
            if product is None:
                raise ValueError("Pass product: the product whose plan ends.")
            if any(options.get(name) is not None for name in ("period", "until", "version")):
                raise ValueError("Pass no period, until or version when ending a plan.")
            if options.get("billing") == PlanBilling.KEEP:
                raise ValueError(
                    "Pass billing as PlanBilling.PROVIDER or PlanBilling.END when ending a plan."
                )
            chosen: dict[str, object] = {"plan": None, "product": require_id(product, PRODUCT_KEY)}
        elif product is not None:
            raise ValueError("Pass product only when ending a plan.")
        elif isinstance(plan, Sku):
            if options.get("period") is not None:
                raise ValueError("Pass no period with a SKU, which names its own.")
            chosen = {"sku": sku_body(plan)}
        else:
            chosen = {"plan": require_id(plan, PLAN_KEY)}
        until = options.get("until")
        body = {
            **chosen,
            **compact(
                period=options.get("period"),
                when=_text(options.get("when")),
                billing=_text(options.get("billing")),
                until=None if until is None else instant(until, "Pass until as a valid date."),
                version=options.get("version"),
                register=options.get("register"),
                reason=options.get("reason"),
                actor=options.get("actor"),
            ),
        }
        return self.write(
            "PUT", "/plan", d.plan_change, key=key_or_new(key), call_seconds=seconds, body=body
        )

    def set_add_on(
        self,
        add_on: object,
        quantity: object,
        sku: Sku | None,
        replaces: str | None,
        reason: str | None,
        actor: str | None,
        key: object,
        seconds: float | None,
    ) -> Gen[PlanChange]:
        """`PUT /customers/{id}/add-ons/{addOn}`."""
        path = f"/add-ons/{id_segment(add_on, PLAN_KEY)}"
        body = {
            "quantity": whole(
                quantity, "Pass quantity as a whole number from 0 to 10000.", 0, 10000
            ),
            **compact(
                sku=None if sku is None else sku_body(sku),
                replaces=None if replaces is None else require_id(replaces, PLAN_KEY),
                reason=reason,
                actor=actor,
            ),
        }
        return self.write(
            "PUT", path, d.plan_change, key=key_or_new(key), call_seconds=seconds, body=body
        )

    def grant(
        self,
        feature: object,
        value: int | str | None,
        days: int | None,
        reason: str | None,
        actor: str | None,
        key: object,
        seconds: float | None,
    ) -> Gen[GrantChange]:
        """`POST /customers/{id}/grants`."""
        if value is not None and value != "unlimited":
            whole(
                value,
                'Pass value as a whole number from 0 to 999999999, or "unlimited".',
                0,
                999_999_999,
            )
        body = {
            "feature": feature_key(feature),
            **compact(
                value=None if value is None else str(value),
                days=None
                if days is None
                else whole(days, "Pass days as a whole number from 0 to 3650.", 0, 3650),
                reason=reason,
                actor=actor,
            ),
        }
        return self.write(
            "POST", "/grants", d.grant_change, key=key_or_new(key), call_seconds=seconds, body=body
        )

    def revoke_grant(
        self,
        grant_id: object,
        reason: str | None,
        actor: str | None,
        key: object,
        seconds: float | None,
    ) -> Gen[GrantChange]:
        """`DELETE /customers/{id}/grants/{grantId}`."""
        path = f"/grants/{id_segment(grant_id, 'Provide the id of the grant.')}"
        who = compact(reason=reason, actor=actor)
        return self.write(
            "DELETE",
            path,
            d.grant_change,
            key=key_or_new(key),
            call_seconds=seconds,
            body=who or ABSENT,
        )

    def adjust_meter(
        self,
        feature: object,
        by: object,
        to: object,
        reason: str | None,
        actor: str | None,
        key: object,
        seconds: float | None,
    ) -> Gen[UsageResult]:
        """`POST /customers/{id}/meters/{feature}/adjustments`."""
        checked = required_key(key)
        if by is not None and to is None:
            whole(by, ADJUSTMENT, -MAX_SAFE)
            if by == 0:
                raise ValueError(ADJUSTMENT)
            change: dict[str, object] = {"by": by}
        elif to is not None and by is None:
            change = {"to": whole(to, ADJUSTMENT, 0)}
        else:
            raise ValueError(ADJUSTMENT)
        path = f"/meters/{segment(feature_key(feature))}/adjustments"
        body = {**change, **compact(reason=reason, actor=actor)}
        return self.write(
            "POST", path, d.usage_result, key=checked, call_seconds=seconds, body=body
        )

    def cancel_usage(
        self,
        usage_id: object,
        reason: str | None,
        actor: str | None,
        key: object,
        seconds: float | None,
    ) -> Gen[UsageResult]:
        """`DELETE /customers/{id}/usage/{usageId}`."""
        path = f"/usage/{id_segment(usage_id, 'Provide the id of the usage report.')}"
        who = compact(reason=reason, actor=actor)
        return self.write(
            "DELETE",
            path,
            d.usage_result,
            key=key_or_new(key),
            call_seconds=seconds,
            body=who or ABSENT,
        )


def _erased(o: Obj) -> None:
    if o.data:
        d.customer_summary(o)


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def sku_body(sku: Sku) -> dict[str, object]:
    """A SKU as `SkuRef`."""
    if not isinstance(sku, Sku):
        raise TypeError(PLAN_KEY)
    return {"connector": sku.connector, "ids": dict(sku.ids)}


class HoldState:
    """A hold handle's state: the amount used, and the first of finish, release and disposal."""

    def __init__(
        self,
        ops: CustomerOps,
        feature: object,
        key: str,
        result: UsageResult,
        seconds: float | None,
    ) -> None:
        self.ops = ops
        self.feature = feature
        self.key = key
        self.result = result
        self.seconds = seconds
        self.used: int | None = None
        self.done = False
        self.outcome: UsageResult | None = None
        self.failure: Exception | None = None

    @property
    def hold_id(self) -> str:
        """The hold's id."""
        return self.result.hold_id or ""

    def use(self, amount: object) -> None:
        """Reports the total used so far."""
        self.used = whole(amount, AMOUNT_USED, 0)

    def finish(self) -> Gen[UsageResult]:
        """Settles the amount used, or the held amount, recording any excess under `:excess`."""
        held = self.result.amount
        total = held if self.used is None else self.used
        settled = min(total, held)
        excess = total - held if total > held else None
        unrecorded = excess
        try:
            try:
                answer = yield from self.ops.settle_usage(self.hold_id, settled, None, self.seconds)
            except APIError as error:
                if error.code != ErrorCode.HOLD_EXPIRED or total == 0:
                    raise
                unrecorded = total
                return (yield from self._excess(total))
            if excess is not None:
                yield from self._excess(excess)
            return answer
        except EntitlerError as cause:
            failure = self.settlement_error(settled, unrecorded)
            failure.__cause__ = cause
            raise failure from cause

    def settlement_error(
        self, amount: int | None = None, excess: int | None = None
    ) -> UsageSettlementError:
        """The error a failed settlement raises."""
        held = self.result.amount
        total = held if self.used is None else self.used
        if amount is None:
            amount = min(total, held)
            excess = total - held if total > held else None
        return UsageSettlementError(hold_id=self.hold_id, amount=amount, excess=excess)

    def timed_out(self) -> EntitlerTimeoutError:
        """The error for an action that ran past the timeout."""
        seconds = self.ops.engine.timeout if self.seconds is None else self.seconds
        return EntitlerTimeoutError(f"Entitler did not answer within {seconds:g} seconds.")

    def _excess(self, amount: int) -> Gen[UsageResult]:
        return self.ops.record_usage(
            self.feature, amount, f"{self.key}:excess", UsageMode.OBSERVE, None, None, self.seconds
        )

    def release(self) -> Gen[UsageResult]:
        """Frees the hold, recording nothing."""
        return self.ops.release_usage(self.hold_id, None, self.seconds)


class BatchPlan:
    """A usage batch checked and split into requests, before anything is sent."""

    def __init__(self, events: Sequence[UsageEventInput], register: bool | None) -> None:
        self.register = register
        self.results: list[UsageEventResult | None] = [None] * len(events)
        self.sendable: list[tuple[int, dict[str, object]]] = []
        for index, event in enumerate(events):
            prepared, refused = _prepare(event)
            if refused is None:
                self.sendable.append((index, prepared))
            else:
                self.results[index] = UsageEventResult(
                    index=index,
                    outcome=BatchOutcome.ERROR,
                    id=None,
                    late=False,
                    error=refused,
                    idempotency_key=_given_key(event),
                    replayed=False,
                )

    def chunks(self) -> list[list[tuple[int, dict[str, object]]]]:
        """The events to send, in requests of at most 500."""
        return [
            self.sendable[start : start + BATCH_SIZE]
            for start in range(0, len(self.sendable), BATCH_SIZE)
        ]

    def key(self, chunk: list[tuple[int, dict[str, object]]]) -> str:
        """The request's idempotency key, derived from its events."""
        rows = [
            [e["customer"], e["feature"], e["amount"], e.get("occurredAt"), e["idempotencyKey"]]
            for _, e in chunk
        ]
        return "batch:" + sha256_hex(
            canonical_json(["entitler-batch-v1", self.register is True, rows])
        )

    def call(
        self, chunk: list[tuple[int, dict[str, object]]], seconds: float | None
    ) -> Call[UsageEventsResult]:
        """The request for one chunk."""
        body: dict[str, object] = {"events": [event for _, event in chunk]}
        if self.register is not None:
            body = {"register": self.register, **body}
        return Call(
            "POST",
            "/usage/events",
            lambda o, _flag: d.usage_events_result(o),
            timeout=seconds,
            body=body,
            idempotency_key=self.key(chunk),
            changes=tuple(f"/customers/{segment(str(e['customer']))}" for _, e in chunk),
        )

    def answered(
        self, chunk: list[tuple[int, dict[str, object]]], answer: UsageEventsResult, replayed: bool
    ) -> None:
        """Records a request's answer, which must hold one result for each event sent."""
        indexes = [result.index for result in answer.results]
        if sorted(indexes) != list(range(len(chunk))):
            raise d.DecodeError("results", "does not hold one result for each event sent")
        for result in answer.results:
            index, event = chunk[result.index]
            self.results[index] = dataclasses.replace(
                result, index=index, idempotency_key=str(event["idempotencyKey"]), replayed=replayed
            )

    def failed(self, chunk: list[tuple[int, dict[str, object]]], error: Exception) -> None:
        """Answers a failed request's events `error`."""
        if isinstance(error, APIError):
            code = str(error.code)
        elif isinstance(error, EntitlerTimeoutError):
            code = ErrorCode.TIMED_OUT.value
        else:
            code = ErrorCode.CONNECTION_FAILED.value
        message = str(error) or "Entitler could not record these events."
        for index, event in chunk:
            self.results[index] = UsageEventResult(
                index=index,
                outcome=BatchOutcome.ERROR,
                id=None,
                late=False,
                error=UsageEventError(code=code, message=message),
                idempotency_key=str(event["idempotencyKey"]),
                replayed=False,
            )

    def result(self) -> UsageEventsResult:
        """One result per event, in input order, and the totals."""
        results = tuple(result for result in self.results if result is not None)
        return UsageEventsResult(
            results=results,
            recorded=sum(1 for r in results if r.outcome == BatchOutcome.RECORDED),
            duplicates=sum(1 for r in results if r.outcome == BatchOutcome.DUPLICATE),
            errors=sum(1 for r in results if r.outcome == BatchOutcome.ERROR),
        )


def record_batch(engine: Engine, plan: BatchPlan, seconds: float | None) -> Gen[UsageEventsResult]:
    """Sends a batch's requests in order; a failed request answers its events `error`."""
    for chunk in plan.chunks():
        try:
            answer = yield from engine.send(plan.call(chunk, seconds))
            try:
                plan.answered(chunk, answer.data, answer.replayed)
            except d.DecodeError as cause:
                raise invalid_response(None, None, cause) from cause
        except EntitlerError as error:
            plan.failed(chunk, error)
    return plan.result()


def _given_key(event: object) -> str | None:
    key = getattr(event, "idempotency_key", None)
    return key if isinstance(key, str) else None


def _prepare(event: UsageEventInput) -> tuple[dict[str, object], UsageEventError | None]:
    steps: list[tuple[str, Callable[[], object]]] = [
        (
            "invalid_body",
            lambda: id_segment(getattr(event, "customer", None), CUSTOMER_ID) and event.customer,
        ),
        ("invalid_body", lambda: feature_key(getattr(event, "feature", None))),
        ("invalid_amount", lambda: usage_amount(getattr(event, "amount", None))),
        ("invalid_idempotency_key", lambda: required_key(getattr(event, "idempotency_key", None))),
        (
            "invalid_body",
            lambda: (
                None
                if getattr(event, "occurred_at", None) is None
                else instant(event.occurred_at, "Pass occurred_at as a valid date.")
            ),
        ),
    ]
    values: list[object] = []
    for code, step in steps:
        try:
            values.append(step())
        except (TypeError, ValueError) as error:
            return {}, UsageEventError(code=code, message=str(error))
    customer, feature, amount, key, occurred = values
    prepared: dict[str, object] = {"customer": customer, "feature": feature, "amount": amount}
    if occurred is not None:
        prepared["occurredAt"] = occurred
    prepared["idempotencyKey"] = key
    return prepared, None


def customer_page(
    engine: Engine,
    cursor: str | None,
    q: str | None,
    cohort: str | None,
    track: str | None,
    include_test: bool,
    seconds: float | None,
) -> Gen[CustomerPage]:
    """`GET /customers`, one page."""
    return _data(
        engine,
        Call(
            "GET",
            "/customers",
            with_flag(d.customer_page),
            timeout=seconds,
            query={
                "q": q,
                "cohort": cohort,
                "track": track,
                "includeTest": "true" if include_test is True else None,
                "cursor": cursor,
            },
        ),
    )


def create_customer(
    engine: Engine,
    external_id: object,
    name: object,
    email: str | None,
    plan: str | None,
    period: str | None,
    metadata: Mapping[str, str] | None,
    key: object,
    seconds: float | None,
    log: Callable[[Page[UsageEvent]], L],
) -> Gen[CustomerDetail[L]]:
    """`POST /customers`."""
    external = require_id(external_id, CUSTOMER_ID)
    body = {
        "externalId": external,
        "name": require_text(name, "Provide the customer's name."),
        **compact(
            email=email,
            plan=None if plan is None else require_id(plan, PLAN_KEY),
            period=period,
            metadata=None if metadata is None else dict(metadata),
        ),
    }
    return _data(
        engine,
        Call(
            "POST",
            "/customers",
            lambda o, replayed: d.customer_detail(o, log, replayed),
            timeout=call_timeout(seconds),
            body=body,
            idempotency_key=key_or_new(key),
            changes=(f"/customers/{segment(external)}",),
        ),
    )
