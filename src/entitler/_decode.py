"""Strict decoders: each answer read at its schema's paths, refusing what it does not allow."""

from __future__ import annotations

import calendar
import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, TypeVar, cast

from ._models import (
    ActivityEntry,
    AddOnSource,
    Arm,
    BankedSource,
    BatchOutcome,
    Biller,
    BillingDrift,
    BillingPortal,
    BillingStatus,
    BillingSync,
    BooleanCheck,
    Channel,
    ChannelListing,
    Check,
    ConfigCheck,
    CustomerBilling,
    CustomerDetail,
    CustomerKind,
    CustomerPage,
    CustomerPlans,
    CustomerSummary,
    CustomerTokenScope,
    CustomerTrack,
    CustomerUsage,
    Effective,
    Entitlement,
    Entitlements,
    EntitlementSource,
    Environment,
    EnvironmentKind,
    Experiment,
    FeatureList,
    FeatureListing,
    FeatureType,
    FeatureUsage,
    FeatureValue,
    Grant,
    GrantChange,
    GrantSource,
    GroupCheck,
    GroupSource,
    HeldAddOn,
    HeldPlan,
    Impact,
    ImpactKind,
    IssuedCustomerToken,
    IssuedSnapshot,
    Limit,
    ListingMode,
    MeterAnchor,
    MeteredCheck,
    MeterUnit,
    MeterWindow,
    Money,
    Move,
    MoveAction,
    MoveOption,
    NextStep,
    OfferedSku,
    OptionPlan,
    Page,
    PendingBilling,
    PendingCancel,
    PendingChange,
    PendingMove,
    PendingUnknown,
    Period,
    PeriodListing,
    PeriodUnit,
    PlanChange,
    PlanInfo,
    PlanKind,
    PlanOverride,
    PlanSource,
    PlanStatus,
    PlanSummary,
    PriceInterval,
    Pricing,
    PricingPlan,
    ProductBilling,
    ProductHolding,
    ProductPlan,
    ProductSummary,
    ProviderPeriod,
    ProviderPrice,
    PublishedPeriod,
    PublishedProduct,
    Purchase,
    Refusal,
    RegisteredCustomer,
    SnapshotKey,
    SnapshotKeys,
    Store,
    SubscribeStep,
    Subscription,
    Tax,
    TrackRef,
    TrackSource,
    UnknownSource,
    Upgrade,
    UsageEvent,
    UsageEventError,
    UsageEventKind,
    UsageEventResult,
    UsageEventsResult,
    UsageMode,
    UsageOutcome,
    UsageResult,
    UsageSource,
    VersionedPlan,
    When,
)
from ._values import MAX_SAFE

T = TypeVar("T")
E = TypeVar("E", bound=StrEnum)
L = TypeVar("L")

_INSTANT = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})[Tt]([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]+))?"
    r"(?:[Zz]|([+-])([0-9]{2}):([0-9]{2}))"
)
_NOT_JSON = object()


class DecodeError(ValueError):
    """An answer does not fit its schema. It names where, and never holds the body."""

    def __init__(
        self, path: str, problem: str = "lacks a required field, or holds another type"
    ) -> None:
        super().__init__(f"The answer {problem} at {path or 'the top level'}.")


def parse_instant(text: str) -> datetime | None:
    """Reads an RFC 3339 instant on the calendar from year 1 to 9999, or answers `None`."""
    found = _INSTANT.fullmatch(text)
    if found is None:
        return None
    year, month, day, hour, minute, second = (int(found.group(i)) for i in range(1, 7))
    fraction, sign, offset_hours, offset_minutes = found.group(7, 8, 9, 10)
    if not (year >= 1 and 1 <= month <= 12) or not 1 <= day <= calendar.monthrange(year, month)[1]:
        return None
    if hour > 23 or minute > 59 or second > 59:
        return None
    offset = timedelta(0)
    if sign is not None:
        if int(offset_hours) > 23 or int(offset_minutes) > 59:
            return None
        offset = timedelta(hours=int(offset_hours), minutes=int(offset_minutes))
        if sign == "-":
            offset = -offset
    micro = int((fraction or "0")[:6].ljust(6, "0"))
    return datetime(year, month, day, hour, minute, second, micro, timezone(offset))


def parse_json(text: str) -> object:
    """Parses JSON text, raising a `DecodeError` that never holds the text."""
    try:
        data: object = json.loads(text, parse_constant=_refuse_constant)
    except (ValueError, RecursionError):
        data = _NOT_JSON
    if data is _NOT_JSON:
        raise DecodeError("", "is not valid JSON")
    return data


def _refuse_constant(name: str) -> object:
    raise ValueError(name)


def open_enum(cls: type[E], raw: str) -> E | str:
    """Answers the enum member for `raw`, or `raw` itself when this version does not know it."""
    try:
        return cls(raw)
    except ValueError:
        return raw


def is_whole(value: object, low: int = -MAX_SAFE) -> bool:
    """True for a JSON whole number from `low` to 2^53 - 1."""
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= MAX_SAFE


class Obj:
    """A JSON object read at a path."""

    __slots__ = ("data", "path")

    def __init__(self, value: object, path: str) -> None:
        if not isinstance(value, dict):
            raise DecodeError(path)
        self.data = cast("dict[str, object]", value)
        self.path = path

    def at(self, name: str) -> str:
        """The path of a member."""
        return f"{self.path}.{name}" if self.path else name

    def has(self, name: str) -> bool:
        """True when the member is present, `null` included."""
        return name in self.data

    def raw(self, name: str) -> object:
        """A required member, as JSON."""
        if name not in self.data:
            raise DecodeError(self.at(name))
        return self.data[name]

    def null(self, name: str) -> bool:
        """True when a required member is `null`."""
        return self.raw(name) is None

    def string(self, name: str) -> str:
        """A required string."""
        value = self.raw(name)
        if not isinstance(value, str):
            raise DecodeError(self.at(name))
        return value

    def string_or_none(self, name: str) -> str | None:
        """A required string or `null`."""
        return None if self.null(name) else self.string(name)

    def boolean(self, name: str) -> bool:
        """A required boolean."""
        value = self.raw(name)
        if not isinstance(value, bool):
            raise DecodeError(self.at(name))
        return value

    def integer(self, name: str, low: int = -MAX_SAFE) -> int:
        """A required whole number."""
        value = self.raw(name)
        if not is_whole(value, low):
            raise DecodeError(self.at(name))
        return cast("int", value)

    def integer_or_none(self, name: str) -> int | None:
        """A required whole number or `null`."""
        return None if self.null(name) else self.integer(name)

    def instant(self, name: str) -> datetime:
        """A required instant."""
        value = self.raw(name)
        parsed = parse_instant(value) if isinstance(value, str) else None
        if parsed is None:
            raise DecodeError(self.at(name))
        return parsed

    def instant_or_none(self, name: str) -> datetime | None:
        """A required instant or `null`."""
        return None if self.null(name) else self.instant(name)

    def enum(self, name: str, cls: type[E]) -> E | str:
        """A required string of an open enum."""
        return open_enum(cls, self.string(name))

    def enum_or_none(self, name: str, cls: type[E]) -> E | str | None:
        """A required string of an open enum, or `null`."""
        return None if self.null(name) else self.enum(name, cls)

    def obj(self, name: str) -> Obj:
        """A required object."""
        return Obj(self.raw(name), self.at(name))

    def one(self, name: str, decode: Callable[[Obj], T]) -> T:
        """A required object, decoded."""
        return decode(self.obj(name))

    def one_or_none(self, name: str, decode: Callable[[Obj], T]) -> T | None:
        """A required object or `null`, decoded."""
        return None if self.null(name) else decode(self.obj(name))

    def array(self, name: str, decode: Callable[[Obj], T]) -> tuple[T, ...]:
        """A required array of objects, each decoded."""
        path = self.at(name)
        items = self._list(name)
        return tuple(decode(Obj(item, f"{path}[{index}]")) for index, item in enumerate(items))

    def _list(self, name: str) -> list[object]:
        items = self.raw(name)
        if not isinstance(items, list):
            raise DecodeError(self.at(name))
        return cast("list[object]", items)

    def strings(self, name: str) -> tuple[str, ...]:
        """A required array of strings."""
        items = self._list(name)
        if not all(isinstance(item, str) for item in items):
            raise DecodeError(self.at(name))
        return tuple(cast("list[str]", items))

    def string_map(self, name: str) -> dict[str, str]:
        """A required object of strings."""
        found = self.obj(name)
        if not all(isinstance(value, str) for value in found.data.values()):
            raise DecodeError(found.path)
        return dict(cast("dict[str, str]", found.data))

    def value(self, name: str) -> FeatureValue:
        """A required feature value: `true`, a whole amount or `"unlimited"`."""
        value = self.raw(name)
        if value is True or value == "unlimited" or is_whole(value, 0):
            return cast("FeatureValue", value)
        raise DecodeError(self.at(name))

    def limit(self, name: str) -> Limit:
        """A required amount or `"unlimited"`."""
        value = self.raw(name)
        if value == "unlimited" or is_whole(value, 0):
            return cast("Limit", value)
        raise DecodeError(self.at(name))

    def optional(self, name: str, read: Callable[[str], T]) -> T | None:
        """A member the schema leaves optional, read when present."""
        return read(name) if name in self.data else None


def answer(text: str) -> Obj:
    """Parses an answer body, which must be a JSON object."""
    return Obj(parse_json(text), "")


def environment(o: Obj) -> Environment:
    """Decodes `AnswerEnvironment`."""
    return Environment(
        id=o.string("id"), name=o.string("name"), kind=o.enum("kind", EnvironmentKind)
    )


def track(o: Obj) -> TrackRef:
    """Decodes `TrackRef`."""
    return TrackRef(id=o.string("id"), name=o.string("name"))


def experiment(o: Obj) -> Experiment:
    """Decodes `ExperimentAssignment`."""
    return Experiment(id=o.string("id"), arm=o.enum("arm", Arm))


def context(o: Obj) -> dict[str, Any]:
    """Decodes the answer context's fields."""
    return {
        "environment": o.one("environment", environment),
        "track": o.one("track", track),
        "release": o.integer_or_none("release"),
        "change": o.string_or_none("change"),
        "testers": o.boolean("testers"),
        "experiment": o.one_or_none("experiment", experiment),
    }


def source(o: Obj) -> EntitlementSource:
    """Decodes `EntitlementSource`, keeping an unknown type."""
    kind = o.string("type")
    if kind == "plan":
        return PlanSource(
            plan=o.string("plan"),
            name=o.string("name"),
            version=o.integer("version"),
            by_default=o.boolean("byDefault"),
            value=o.value("value"),
        )
    if kind == "addon":
        return AddOnSource(
            plan=o.string("plan"),
            name=o.string("name"),
            version=o.integer("version"),
            quantity=o.integer("quantity"),
            value=o.value("value"),
        )
    if kind == "grant":
        return GrantSource(
            grant=o.string("grant"), until=o.instant_or_none("until"), value=o.value("value")
        )
    if kind == "banked":
        return BankedSource(value=o.integer("value"))
    if kind == "group":
        return GroupSource(features=o.strings("features"))
    return UnknownSource(type=kind)


def upgrade(o: Obj) -> Upgrade:
    """Decodes `Upgrade`."""
    return Upgrade(
        plan=o.string("plan"),
        name=o.string("name"),
        move=o.enum("move", Move),
        action=o.enum("action", MoveAction),
        reason=o.string_or_none("reason"),
    )


def _meter(o: Obj) -> dict[str, Any]:
    return {
        "used": o.optional("used", o.integer),
        "held": o.optional("held", o.integer),
        "remaining": o.optional("remaining", o.limit),
        "resets_at": o.optional("resetsAt", o.instant_or_none),
    }


def _check_fields(o: Obj) -> dict[str, Any]:
    return {
        "customer": o.string("customer"),
        "as_of": o.instant("asOf"),
        "feature": o.string("feature"),
        "type": o.enum("type", FeatureType),
        "entitled": o.boolean("entitled"),
        "value": o.value("value"),
        "sources": o.array("sources", source),
        "upgrades": o.array("upgrades", upgrade),
        **_meter(o),
        **context(o),
    }


def check(o: Obj, stale: bool = False) -> Check:
    """Decodes `TrackedCheck` as the check class its feature type names."""
    fields = _check_fields(o)
    kind = fields["type"]
    metered = all(fields[name] is not None for name in ("used", "held", "remaining"))
    if kind == FeatureType.METERED and metered and fields["value"] is not True:
        return MeteredCheck(**fields, stale=stale)
    cls = {
        FeatureType.BOOLEAN: BooleanCheck,
        FeatureType.CONFIG: ConfigCheck,
        FeatureType.GROUP: GroupCheck,
    }.get(kind, Check)
    return cls(**fields, stale=stale)


def entitlement(o: Obj) -> Entitlement:
    """Decodes `CustomerEntitlement`."""
    return Entitlement(
        key=o.string("key"),
        type=o.enum("type", FeatureType),
        value=o.value("value"),
        entitled=o.boolean("entitled"),
        sources=o.array("sources", source),
        upgrades=o.array("upgrades", upgrade),
        **_meter(o),
    )


def claimed_entitlement(o: Obj) -> Entitlement:
    """Decodes `ClaimedEntitlement`, which carries no upgrades."""
    return Entitlement(
        key=o.string("key"),
        type=o.enum("type", FeatureType),
        value=o.value("value"),
        entitled=o.boolean("entitled"),
        sources=o.array("sources", source),
        upgrades=(),
        **_meter(o),
    )


def entitlements(o: Obj, stale: bool = False) -> Entitlements[Environment]:
    """Decodes `TrackedEntitlementList`."""
    return Entitlements(
        customer=o.string("customer"),
        as_of=o.instant("asOf"),
        entitlements=o.array("entitlements", entitlement),
        stale=stale,
        **context(o),
    )


def plan_summary(o: Obj) -> PlanSummary:
    """Decodes a plan named by id, key and name."""
    return PlanSummary(id=o.string("id"), key=o.string("key"), name=o.string("name"))


def product(o: Obj) -> ProductSummary:
    """Decodes `ProductSummary`."""
    return ProductSummary(key=o.string("key"), name=o.string("name"))


def period(o: Obj) -> Period:
    """Decodes `PeriodRef`."""
    return Period(key=o.string("key"), label=o.string("label"))


def price(o: Obj) -> ProviderPrice:
    """Decodes `ProviderPrice`."""
    return ProviderPrice(
        amount=o.integer("amount"),
        currency=o.string("currency"),
        interval=o.enum_or_none("interval", PriceInterval),
        interval_count=o.integer("intervalCount", 1),
        tax=o.enum("tax", Tax),
    )


def offered_sku(o: Obj) -> OfferedSku:
    """Decodes `OfferedSku`."""
    return OfferedSku(
        period=o.one_or_none("period", period),
        connector=o.string("connector"),
        ids=o.string_map("ids"),
        price=o.one_or_none("price", price) if o.has("price") else None,
    )


def pending(o: Obj) -> PendingChange:
    """Decodes a booked change, keeping an unknown type."""
    kind = o.string("type")
    if kind == "move":
        return PendingMove(plan=o.one("plan", plan_summary))
    if kind == "cancel":
        return PendingCancel(moving_to=o.one_or_none("movingTo", plan_summary))
    if kind == "billing":
        return PendingBilling(billing=o.enum("billing", Biller))
    return PendingUnknown(type=kind)


def held_plan(o: Obj) -> HeldPlan:
    """Decodes `HeldPlan`."""
    plan = o.obj("plan")
    return HeldPlan(
        plan=PlanInfo(
            id=plan.string("id"),
            key=plan.string("key"),
            name=plan.string("name"),
            kind=plan.enum("kind", PlanKind),
        ),
        product=o.one_or_none("product", product),
        version=o.integer_or_none("version"),
        by_default=o.boolean("byDefault"),
        period=o.one_or_none("period", period),
        renews_at=o.instant_or_none("renewsAt"),
        pending=o.one_or_none("pending", pending),
        billed_by=o.enum_or_none("billedBy", Store),
    )


def impact(o: Obj) -> Impact:
    """Decodes `Impact`."""
    return Impact(kind=o.enum("kind", ImpactKind), text=o.string("text"))


def move_option(o: Obj) -> MoveOption:
    """Decodes `MoveOption`."""
    plan = o.obj("plan")
    return MoveOption(
        plan=OptionPlan(
            id=plan.string("id"),
            key=plan.string("key"),
            name=plan.string("name"),
            kind=plan.enum("kind", PlanKind),
            description=plan.string("description"),
            default=plan.boolean("default"),
        ),
        product=o.one_or_none("product", product),
        from_=o.one_or_none("from", plan_summary),
        move=o.enum("move", Move),
        action=o.enum("action", MoveAction),
        reason=o.string_or_none("reason"),
        when=o.enum("when", When),
        periods=o.array("periods", period),
        impact=o.array(
            "impact", lambda i: Impact(kind=i.enum("kind", ImpactKind), text=i.string("text"))
        ),
        skus=o.array("skus", offered_sku),
    )


def customer_plans(o: Obj, stale: bool = False) -> CustomerPlans:
    """Decodes `CustomerPlans`."""
    return CustomerPlans(
        customer=o.string("customer"),
        as_of=o.instant("asOf"),
        held=o.array("held", held_plan),
        options=o.array("options", move_option),
        stale=stale,
        **context(o),
    )


def usage_result(o: Obj, replayed: bool = False) -> UsageResult:
    """Decodes `TrackedUsageResult`."""
    return UsageResult(
        **_check_fields(o),
        outcome=o.enum("outcome", UsageOutcome),
        refusal=o.enum_or_none("refusal", Refusal),
        id=o.string_or_none("id"),
        hold_id=o.string_or_none("holdId"),
        mode=o.enum("mode", UsageMode),
        amount=o.integer("amount"),
        meter_change=o.integer("meterChange"),
        over_by=o.integer("overBy"),
        late=o.boolean("late"),
        occurred_at=o.instant_or_none("occurredAt"),
        expires_at=o.instant_or_none("expiresAt"),
        reported_as=o.enum("reportedAs", UsageSource),
        replayed=replayed,
    )


def feature_usage(o: Obj) -> FeatureUsage:
    """Decodes `CustomerFeatureUsage`."""
    return FeatureUsage(
        feature=o.string("feature"),
        type=o.enum("type", FeatureType),
        entitled=o.boolean("entitled"),
        value=o.value("value"),
        sources=o.array("sources", source),
        used=o.integer("used"),
        held=o.integer("held"),
        remaining=None if o.null("remaining") else o.limit("remaining"),
        resets_at=o.instant_or_none("resetsAt"),
    )


def usage_event(o: Obj) -> UsageEvent:
    """Decodes `UsageEvent`."""
    return UsageEvent(
        id=o.string("id"),
        feature=o.string("feature"),
        kind=o.enum("kind", UsageEventKind),
        amount=o.integer("amount"),
        set_to=o.integer_or_none("setTo"),
        source=o.enum("source", UsageSource),
        by=o.string_or_none("by"),
        actor=o.string_or_none("actor"),
        reason=o.string_or_none("reason"),
        at=o.instant("at"),
        cancelled_at=o.instant_or_none("cancelledAt"),
    )


def usage_page(o: Obj) -> Page[UsageEvent]:
    """Decodes `UsageLog`."""
    return Page(items=o.array("items", usage_event), next=o.string_or_none("next"))


def customer_usage(o: Obj, log: Callable[[Page[UsageEvent]], L]) -> CustomerUsage[L]:
    """Decodes `TrackedCustomerUsage`, wrapping its log's first page with `log`."""
    return CustomerUsage(
        customer=o.string("customer"),
        as_of=o.instant("asOf"),
        meters_start_again_at=o.instant_or_none("metersStartAgainAt"),
        features=o.array("features", feature_usage),
        log=log(o.one("log", usage_page)),
        **context(o),
    )


def registered(o: Obj, replayed: bool = False) -> RegisteredCustomer:
    """Decodes `RegisteredCustomer`."""
    return RegisteredCustomer(
        id=o.string("id"),
        external_id=o.string("externalId"),
        environment_id=o.string("environmentId"),
        created_at=o.instant("createdAt"),
        created=o.boolean("created"),
        replayed=replayed,
    )


def customer_summary(o: Obj, replayed: bool = False) -> CustomerSummary:
    """Decodes `TrackedCustomerSummary`."""
    return CustomerSummary(
        id=o.string("id"),
        external_id=o.string("externalId"),
        name=o.string("name"),
        email=o.string("email"),
        environment_id=o.string("environmentId"),
        sample=o.boolean("sample"),
        created_at=o.instant("createdAt"),
        plan=o.one_or_none(
            "plan",
            lambda p: VersionedPlan(
                id=p.string("id"),
                key=p.string("key"),
                name=p.string("name"),
                version=p.integer("version"),
            ),
        ),
        plans=o.array(
            "plans",
            lambda p: ProductPlan(
                id=p.string("id"),
                key=p.string("key"),
                name=p.string("name"),
                version=p.integer("version"),
                product=p.string("product"),
            ),
        ),
        default_plan=o.one_or_none("defaultPlan", plan_summary),
        status=o.string("status"),
        kind=o.enum("kind", CustomerKind),
        metadata=o.string_map("metadata"),
        track=o.one("track", track),
        test_customer=o.boolean("testCustomer"),
        replayed=replayed,
    )


def customer_page(o: Obj) -> CustomerPage:
    """Decodes `TrackedCustomerList`."""
    return CustomerPage(
        items=o.array("items", customer_summary),
        next=o.string_or_none("next"),
        used=o.integer("used"),
        limit=o.limit("limit"),
    )


def channel(o: Obj) -> Channel:
    """Decodes `Channel`."""
    return Channel(provider=o.enum("provider", Store), connection_id=o.string("connectionId"))


def purchase(o: Obj) -> Purchase:
    """Decodes `Purchase`."""
    return Purchase(
        money=o.enum("money", Money),
        channel=o.one_or_none("channel", channel),
        release=o.integer_or_none("release"),
        change=o.string_or_none("change"),
        arm=o.enum_or_none("arm", Arm),
    )


def held_add_on(o: Obj) -> HeldAddOn:
    """Decodes `TrackedHeldAddOn`."""
    return HeldAddOn(
        plan=o.one("plan", plan_summary),
        version=o.integer("version"),
        quantity=o.integer("quantity"),
        countable=o.boolean("countable"),
        added_at=o.instant("addedAt"),
        moving_to=o.one_or_none("movingTo", plan_summary),
        purchase=o.one("purchase", purchase),
    )


def provider_period(o: Obj) -> ProviderPeriod:
    """Decodes `ProviderPeriod`."""
    return ProviderPeriod(
        provider=o.string("provider"),
        starts_at=o.instant("startsAt"),
        ends_at=o.instant("endsAt"),
        trial_ends_at=o.instant_or_none("trialEndsAt"),
        cancels_at=o.instant_or_none("cancelsAt"),
    )


def plan_override(o: Obj) -> PlanOverride:
    """Decodes `PlanOverride`."""
    return PlanOverride(
        from_=o.one("from", plan_summary),
        period=o.one_or_none("period", period),
        billed_by=o.one_or_none("billedBy", channel),
        since=o.instant("since"),
        by=o.string("by"),
    )


def subscription(o: Obj) -> Subscription:
    """Decodes `TrackedSubscriptionView`."""
    return Subscription(
        product=o.one("product", product),
        plan=o.one("plan", plan_summary),
        version=o.integer("version"),
        cohort=o.integer_or_none("cohort"),
        period=o.one_or_none("period", period),
        started_at=o.instant("startedAt"),
        renews_at=o.instant("renewsAt"),
        billing=o.one_or_none("billing", provider_period),
        until=o.instant_or_none("until"),
        add_ons=o.array("addOns", held_add_on),
        pending=o.one_or_none("pending", pending),
        purchase=o.one("purchase", purchase),
        override=o.one_or_none("override", plan_override),
    )


def grant_value(o: Obj, name: str) -> FeatureValue:
    """Decodes a grant's value, sent as a string: empty is on, digits an amount."""
    text = o.string(name)
    if text == "":
        return True
    if text == "unlimited":
        return "unlimited"
    if re.fullmatch(r"[0-9]{1,16}", text) and int(text) <= MAX_SAFE:
        return int(text)
    raise DecodeError(o.at(name))


def grant(o: Obj) -> Grant:
    """Decodes `Grant`."""
    return Grant(
        id=o.string("id"),
        feature=o.string("feature"),
        value=grant_value(o, "value"),
        from_=o.instant("from"),
        until=o.instant_or_none("until"),
        revoked_at=o.instant_or_none("revokedAt"),
        reason=o.string("reason"),
        by=o.string("by"),
        actor=o.string_or_none("actor"),
    )


def activity(o: Obj) -> ActivityEntry:
    """Decodes `ActivityEntry`."""
    return ActivityEntry(
        text=o.string("text"),
        at=o.instant("at"),
        by=o.string_or_none("by"),
        actor=o.string_or_none("actor"),
        reason=o.string_or_none("reason"),
    )


def customer_detail(
    o: Obj, log: Callable[[Page[UsageEvent]], L], replayed: bool = False
) -> CustomerDetail[L]:
    """Decodes `TrackedCustomerDetail`, wrapping its log's first page with `log`."""
    banked = o.obj("banked")
    if not all(is_whole(value) for value in banked.data.values()):
        raise DecodeError(banked.path)
    return CustomerDetail(
        customer=o.one("customer", customer_summary),
        as_of=o.instant("asOf"),
        subscription=o.one_or_none("subscription", subscription),
        default_plan=o.one_or_none("defaultPlan", plan_summary),
        products=o.array(
            "products",
            lambda p: ProductHolding(
                product=p.one("product", product),
                default_plan=p.one_or_none("defaultPlan", plan_summary),
                subscription=p.one_or_none("subscription", subscription),
            ),
        ),
        add_ons=o.array("addOns", held_add_on),
        entitlements=o.array("entitlements", entitlement),
        banked=dict(cast("dict[str, int]", banked.data)),
        move_options=o.array("moveOptions", move_option),
        grants=o.array("grants", grant),
        usage=log(o.one("usage", usage_page)),
        activity=o.array("activity", activity),
        environment=o.one("environment", environment),
        replayed=replayed,
    )


def details_page(o: Obj) -> Page[UsageEvent]:
    """Decodes a whole `TrackedCustomerDetail`, answering only its usage log's page."""
    return customer_detail(o, lambda page: page).usage


def issued_token(o: Obj) -> IssuedCustomerToken:
    """Decodes `IssuedCustomerToken`."""
    scopes = o.strings("scopes")
    return IssuedCustomerToken(
        token=o.string("token"),
        customer=o.string("customer"),
        scopes=tuple(open_enum(CustomerTokenScope, scope) for scope in scopes),
        expires_at=o.instant("expiresAt"),
    )


def plan_change(o: Obj, replayed: bool = False) -> PlanChange:
    """Decodes `PlanChange`."""
    return PlanChange(
        product=o.one_or_none("product", product),
        plan=o.one_or_none("plan", plan_summary),
        quantity=o.integer_or_none("quantity"),
        effective=o.enum("effective", Effective),
        at=o.instant("at"),
        until=o.instant_or_none("until"),
        changed=o.boolean("changed"),
        replayed=replayed,
    )


def subscribe_step(o: Obj, replayed: bool = False) -> SubscribeStep:
    """Decodes `SubscribeStep`, keeping an unknown step's raw `next`."""
    step = o.enum("next", NextStep)
    if step == NextStep.DONE:
        return SubscribeStep(next=step, change=plan_change(o, replayed), url=None, billed_by=None)
    if step == NextStep.PAY:
        return SubscribeStep(next=step, change=None, url=o.string("url"), billed_by=None)
    if step == NextStep.MANAGE:
        return SubscribeStep(next=step, change=None, url=None, billed_by=o.enum("billedBy", Store))
    return SubscribeStep(next=step, change=None, url=None, billed_by=None)


def grant_change(o: Obj, replayed: bool = False) -> GrantChange:
    """Decodes `GrantChange`."""
    return GrantChange(grant=o.one("grant", grant), replayed=replayed)


def customer_track(o: Obj, replayed: bool = False) -> CustomerTrack:
    """Decodes `CustomerTrack`."""
    return CustomerTrack(
        customer=o.string("customer"),
        environment_id=o.string("environmentId"),
        track=o.one("track", track),
        source=o.enum_or_none("source", TrackSource),
        previous_track_id=o.string_or_none("previousTrackId"),
        replayed=replayed,
    )


def _features_map(o: Obj) -> dict[str, FeatureValue]:
    found = o.obj("features")
    return {name: found.value(name) for name in found.data}


def pricing_plan(o: Obj) -> PricingPlan:
    """Decodes `PricingPlan`."""
    return PricingPlan(
        id=o.string("id"),
        key=o.string("key"),
        name=o.string("name"),
        description=o.string("description"),
        kind=o.enum("kind", PlanKind),
        product=o.string_or_none("product"),
        sales_led=o.boolean("salesLed"),
        status=o.enum("status", PlanStatus),
        version=o.integer_or_none("version"),
        default=o.boolean("default"),
        periods=o.array(
            "periods",
            lambda p: PublishedPeriod(
                key=p.string("key"),
                label=p.string("label"),
                count=p.integer("count"),
                unit=p.enum("unit", PeriodUnit),
            ),
        ),
        attaches_to=o.strings("attachesTo"),
        features=_features_map(o),
        listings=o.array(
            "listings",
            lambda p: PeriodListing(
                period=p.one_or_none("period", period),
                channels=p.array("channels", channel_listing),
            ),
        ),
    )


def channel_listing(o: Obj) -> ChannelListing:
    """Decodes one channel of a `PeriodListing`."""
    return ChannelListing(
        channel=o.one("channel", channel),
        name=o.string("name"),
        mode=o.enum_or_none("mode", ListingMode),
        purchasable=o.boolean("purchasable"),
        ids=None if o.null("ids") else o.string_map("ids"),
        price=o.one_or_none("price", price) if o.has("price") else None,
    )


def pricing(o: Obj, stale: bool = False) -> Pricing:
    """Decodes `Pricing`."""
    return Pricing(
        customer=o.string_or_none("customer"),
        default_plan=o.string_or_none("defaultPlan"),
        products=o.array(
            "products",
            lambda p: PublishedProduct(
                key=p.string("key"),
                name=p.string("name"),
                default_plan=p.string_or_none("defaultPlan"),
            ),
        ),
        plans=o.array("plans", pricing_plan),
        stale=stale,
        **context(o),
    )


def feature_listing(o: Obj) -> FeatureListing:
    """Decodes `FeatureListing`."""
    return FeatureListing(
        id=o.string("id"),
        key=o.string("key"),
        name=o.string("name"),
        type=o.enum("type", FeatureType),
        description=o.string("description"),
        unit=o.string("unit"),
        reset_every=o.one_or_none(
            "resetEvery",
            lambda w: MeterWindow(
                count=w.integer("count"),
                unit=w.enum("unit", MeterUnit),
                anchor=w.optional("anchor", lambda name: w.enum(name, MeterAnchor)),
            ),
        ),
        archived=o.boolean("archived"),
        includes=o.strings("includes"),
    )


def feature_list(o: Obj, stale: bool = False) -> FeatureList:
    """Decodes `FeatureList`."""
    return FeatureList(
        environment=o.one("environment", environment),
        track=o.one("track", track),
        release=o.integer_or_none("release"),
        change=o.string_or_none("change"),
        features=o.array("features", feature_listing),
        stale=stale,
    )


def snapshot_keys(o: Obj) -> SnapshotKeys:
    """Decodes `Jwks`."""
    return SnapshotKeys(
        keys=o.array(
            "keys",
            lambda k: SnapshotKey(
                kty=k.string("kty"),
                crv=k.string("crv"),
                x=k.string("x"),
                y=k.string("y"),
                kid=k.string("kid"),
                alg=k.string("alg"),
                use=k.string("use"),
            ),
        )
    )


def issued_snapshot(o: Obj) -> IssuedSnapshot:
    """Decodes `IssuedSnapshot`."""
    return IssuedSnapshot(
        token=o.string("token"), expires_at=o.instant("expiresAt"), key_id=o.string("keyId")
    )


def _drift(o: Obj) -> BillingDrift:
    return BillingDrift(
        billed_plan=o.string("billedPlan"),
        held_plan=o.string_or_none("heldPlan"),
        observed_at=o.instant("observedAt"),
    )


def customer_billing(o: Obj) -> CustomerBilling:
    """Decodes `CustomerBilling`."""
    return CustomerBilling(
        provider=o.string_or_none("provider"),
        status=o.enum_or_none("status", BillingStatus),
        sku=o.one_or_none("sku", offered_sku),
        items=o.integer("items"),
        drift=o.one_or_none("drift", _drift),
        products=o.array(
            "products",
            lambda p: ProductBilling(
                product=p.string_or_none("product"),
                status=p.enum_or_none("status", BillingStatus),
                sku=p.one_or_none("sku", offered_sku),
                items=p.integer("items"),
                drift=p.one_or_none("drift", _drift),
            ),
        ),
    )


def billing_portal(o: Obj) -> BillingPortal:
    """Decodes `BillingPortal`."""
    return BillingPortal(provider=o.string("provider"), url=o.string("url"))


def billing_sync(o: Obj) -> BillingSync:
    """Decodes `BillingSync`."""
    return BillingSync(changed=o.boolean("changed"))


def credential_self(o: Obj) -> tuple[tuple[str, ...], bool | None]:
    """Decodes `CredentialSelf`, a key or a customer token: its scopes and registration."""
    if o.has("customer"):
        o.string("customer")
        o.string("expiresAt")
    else:
        for name in ("id", "name", "kind", "orgId"):
            o.string(name)
        o.string_or_none("projectId")
        o.string_or_none("environmentId")
        o.strings("allowedOrigins")
        o.instant("createdAt")
        o.instant_or_none("revokedAt")
    return o.strings("scopes"), o.optional("registration", o.boolean)


def event_error(o: Obj) -> UsageEventError:
    """Decodes a usage batch event's error."""
    return UsageEventError(code=o.string("code"), message=o.string("message"))


def usage_events_result(o: Obj) -> UsageEventsResult:
    """Decodes `UsageEventsResult`."""
    return UsageEventsResult(
        results=o.array(
            "results",
            lambda r: UsageEventResult(
                index=r.integer("index"),
                outcome=r.enum("outcome", BatchOutcome),
                id=r.string_or_none("id"),
                late=r.boolean("late"),
                error=r.one_or_none(
                    "error",
                    lambda e: UsageEventError(code=e.string("code"), message=e.string("message")),
                ),
                idempotency_key=None,
                replayed=False,
            ),
        ),
        recorded=o.integer("recorded"),
        duplicates=o.integer("duplicates"),
        errors=o.integer("errors"),
    )
