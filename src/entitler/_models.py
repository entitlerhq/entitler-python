"""The answers the API sends, as frozen dataclasses named after their schemas."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Generic, Literal, TypeAlias, TypeVar

if TYPE_CHECKING:
    from ._features import Feature, Metered

FeatureValue: TypeAlias = Literal[True] | int | Literal["unlimited"]
"""A feature's value: `True` (on), a whole amount (`0` included) or `"unlimited"`.

Test `value is True` first, since `True` is also an `int` in Python.
"""

Limit: TypeAlias = int | Literal["unlimited"]
"""An amount, or `"unlimited"`."""


class FeatureType(StrEnum):
    """A feature's kind."""

    BOOLEAN = "boolean"
    """On or off."""
    CONFIG = "config"
    """An amount or `unlimited`, such as seats."""
    METERED = "metered"
    """An allowance that usage counts against."""
    GROUP = "group"
    """A group of other features."""


class EnvironmentKind(StrEnum):
    """Whether an environment is for testing or live customers."""

    TEST = "test"
    """A test environment: test money only."""
    LIVE = "live"
    """The live environment."""


class Arm(StrEnum):
    """An experiment's arm."""

    CONTROL = "control"
    """The experiment's control arm."""
    VARIANT = "variant"
    """The experiment's variant arm."""


class Move(StrEnum):
    """How a plan or add-on moves the customer."""

    SUBSCRIBE = "subscribe"
    """Start a plan where the customer holds none."""
    UPGRADE = "upgrade"
    """Move to a higher plan."""
    DOWNGRADE = "downgrade"
    """Move to a lower plan."""
    SWITCH = "switch"
    """Move to a plan at the same level."""
    ADD = "add"
    """Add an add-on."""
    REPLACE = "replace"
    """Replace one add-on with another."""


class MoveAction(StrEnum):
    """Who can make a move: the customer alone, the company's sales team, or nobody now."""

    BUY = "buy"
    """The customer can make the move alone."""
    CONTACT = "contact"
    """A sales-led move, which only the company can make."""
    UNAVAILABLE = "unavailable"
    """Nobody can make the move now; `reason` says why."""


class PlanKind(StrEnum):
    """A plan, or an add-on on top of one."""

    PLAN = "plan"
    """A plan."""
    ADDON = "addon"
    """An add-on, which goes on top of a plan."""


class Store(StrEnum):
    """Who bills a plan: Stripe, the App Store or Google Play."""

    STRIPE = "stripe"
    """Billed by Stripe."""
    APPLE = "apple"
    """Billed by the App Store."""
    GOOGLE = "google"
    """Billed by Google Play."""


class Biller(StrEnum):
    """Who bills a plan after a booked `billing` change."""

    PROVIDER = "provider"
    """The payment provider bills the plan."""
    OUTSIDE = "outside"
    """No payment provider bills it, as with an invoiced contract."""
    STORE = "store"
    """An app store bills it."""


class When(StrEnum):
    """When a move takes effect: now, or at the end of the period."""

    NOW = "now"
    """Now."""
    END = "end"
    """At the end of the current period, at renewal."""


class Effective(StrEnum):
    """When a plan change took or takes effect."""

    NOW = "now"
    """The change took effect now."""
    RENEWAL = "renewal"
    """The change takes effect at renewal, at `at`."""


class ImpactKind(StrEnum):
    """What one line of a move's impact describes."""

    GAINS = "gains"
    """Something the customer gains."""
    LOSES = "loses"
    """Something the customer loses."""
    CHANGES = "changes"
    """Something that changes."""
    SAME = "same"
    """Something that stays the same."""


class PriceInterval(StrEnum):
    """How often a recurring price bills."""

    DAY = "day"
    """Charged daily."""
    WEEK = "week"
    """Charged weekly."""
    MONTH = "month"
    """Charged monthly."""
    YEAR = "year"
    """Charged yearly."""


class Tax(StrEnum):
    """Whether a price includes tax."""

    INCLUSIVE = "inclusive"
    """The price includes tax."""
    EXCLUSIVE = "exclusive"
    """Tax is added to the price."""
    UNSPECIFIED = "unspecified"
    """The provider does not say."""


class UsageOutcome(StrEnum):
    """What a usage write did."""

    RECORDED = "recorded"
    """The usage was recorded."""
    DUPLICATE = "duplicate"
    """The key replays an earlier write, or names a hold still open; nothing new was recorded."""
    REFUSED = "refused"
    """Gate mode refused it: no allowance, or not entitled."""
    HELD = "held"
    """The amount is on hold."""
    SETTLED = "settled"
    """The hold was settled."""
    RELEASED = "released"
    """The hold was released, recording nothing."""
    CANCELLED = "cancelled"
    """The usage report was cancelled."""
    ADJUSTED = "adjusted"
    """The meter was adjusted."""


class Refusal(StrEnum):
    """Why a usage write was refused."""

    NOT_ENTITLED = "not_entitled"
    """The customer is not entitled to the feature."""
    OVER_ALLOWANCE = "over_allowance"
    """The amount does not fit the allowance left."""


class UsageMode(StrEnum):
    """How a report treats the allowance.

    `GATE` records only when the amount fits, else answers `refused`; `OBSERVE` always records
    what happened, and `over_by` says how far past the allowance it went.
    """

    GATE = "gate"
    """Record only if the amount fits the allowance."""
    OBSERVE = "observe"
    """Always record, for work that already happened; the meter may pass the allowance."""


class UsageSource(StrEnum):
    """Where a usage report came from."""

    API = "api"
    """Recorded by a server key."""
    CLIENT = "client"
    """Recorded by an in-app client."""
    DASHBOARD = "dashboard"
    """Recorded in the dashboard."""


class UsageEventKind(StrEnum):
    """A usage log entry: a use, or a meter adjustment."""

    USE = "use"
    """Usage was recorded."""
    ADJUST = "adjust"
    """The meter was adjusted."""


class BatchOutcome(StrEnum):
    """What happened to one event of a usage batch."""

    RECORDED = "recorded"
    """The event was recorded."""
    DUPLICATE = "duplicate"
    """The event's key replays an earlier one; nothing new was recorded."""
    ERROR = "error"
    """The event was not recorded; `error` says why. Resend it with the same key."""


class CustomerKind(StrEnum):
    """How a customer holds their plan."""

    RECURRING = "recurring"
    """Holds a recurring plan."""
    ONE_TIME = "one_time"
    """Holds a one-time purchase."""
    CHANGING = "changing"
    """Has a plan change booked."""
    DEFAULT = "default"
    """Holds only the default plan."""
    NONE = "none"
    """Holds no plan."""


class Money(StrEnum):
    """Whether a purchase moved real money or test money."""

    REAL = "real"
    """Money that is charged."""
    TEST = "test"
    """Test money, never charged."""


class TrackSource(StrEnum):
    """Who put a customer on their track."""

    SERVER = "server"
    """Placed by the vendor's server."""
    DASHBOARD = "dashboard"
    """Placed by a person in the dashboard."""
    STORE_SANDBOX = "store_sandbox"
    """Placed by a verified sandbox build of the app."""


class PlanStatus(StrEnum):
    """Whether a plan is sold, or kept only for the customers who hold it."""

    ACTIVE = "active"
    """On sale."""
    LEGACY = "legacy"
    """No longer sold; customers who hold it keep it."""


class PeriodUnit(StrEnum):
    """The unit of a billing period."""

    DAYS = "days"
    """Days."""
    WEEKS = "weeks"
    """Weeks."""
    MONTHS = "months"
    """Months."""


class MeterUnit(StrEnum):
    """The unit of a meter's reset window."""

    HOURS = "hours"
    """Hours."""
    DAYS = "days"
    """Days."""
    WEEKS = "weeks"
    """Weeks."""
    MONTHS = "months"
    """Months."""


class MeterAnchor(StrEnum):
    """What a meter's reset window counts from."""

    PERIOD = "period"
    """Windows start with the customer's billing period (the default)."""
    CALENDAR = "calendar"
    """Windows start on calendar boundaries in the project's time zone."""


class ListingMode(StrEnum):
    """Whether a sales channel sells in live or test mode."""

    LIVE = "live"
    """A live listing."""
    TEST = "test"
    """A test listing."""


class BillingStatus(StrEnum):
    """The payment provider's subscription status."""

    INCOMPLETE = "incomplete"
    """The first payment is not complete."""
    INCOMPLETE_EXPIRED = "incomplete_expired"
    """The first payment was never completed."""
    TRIALING = "trialing"
    """In a trial."""
    ACTIVE = "active"
    """Paid and current."""
    PAST_DUE = "past_due"
    """A renewal payment failed and is being retried."""
    CANCELED = "canceled"
    """Ended, in the provider's spelling."""
    UNPAID = "unpaid"
    """Renewal payments failed."""
    PAUSED = "paused"
    """Paused."""


class NextStep(StrEnum):
    """What `subscribe` needs next.

    `DONE`: the change is made. `PAY`: send the customer to `url`. `CONFIRMING`: the provider is
    still confirming a payment, so show it as pending. `MANAGE`: a store bills the product, so
    the customer changes it there.
    """

    DONE = "done"
    """The change is made; `change` holds it."""
    PAY = "pay"
    """Send the customer to `url` to pay."""
    CONFIRMING = "confirming"
    """The provider is confirming a payment; show it as pending."""
    MANAGE = "manage"
    """A store bills the product; the customer changes it there (`billed_by`)."""


class Scope(StrEnum):
    """A scope a credential can hold."""

    PLANS_READ = "plans:read"
    """Read pricing and the feature catalogue."""
    ENTITLEMENTS_READ = "entitlements:read"
    """Read checks, entitlements and plans, and mint snapshots."""
    USAGE_READ = "usage:read"
    """Read usage."""
    USAGE_WRITE = "usage:write"
    """Record usage and holds."""
    BILLING_SELF = "billing:self"
    """Make the customer's own billing choices in an app."""
    CUSTOMERS_REGISTER = "customers:register"
    """Register customers."""
    CUSTOMERS_READ = "customers:read"
    """Read customers' details and billing."""
    CUSTOMERS_WRITE = "customers:write"
    """Change customers: plans, add-ons, grants, meters and erasure."""
    CUSTOMERS_PROFILE = "customers:profile"
    """Change customers' names, emails and metadata."""
    CUSTOMERS_SAMPLE = "customers:sample"
    """Manage sample customers."""
    TOKENS_MINT = "tokens:mint"
    """Mint customer tokens."""
    PLANS_WRITE = "plans:write"
    """Edit the catalogue."""
    PLANS_RELEASE = "plans:release"
    """Release catalogue changes."""
    TRACKS_MANAGE = "tracks:manage"
    """Manage tracks."""
    TRACKS_PROMOTE = "tracks:promote"
    """Promote releases between tracks."""
    TRACKS_ASSIGN = "tracks:assign"
    """Put customers on tracks."""
    KEYS_MANAGE = "keys:manage"
    """Manage API keys."""
    MEMBERS_MANAGE = "members:manage"
    """Manage members."""
    PROJECTS_MANAGE = "projects:manage"
    """Manage projects."""
    ORG_MANAGE = "org:manage"
    """Manage the organisation."""


class CustomerTokenScope(StrEnum):
    """A scope a customer token can hold.

    Ask for `USAGE_WRITE` only when the app records usage itself, and for `BILLING_SELF` only for
    people who may buy for the customer.
    """

    ENTITLEMENTS_READ = "entitlements:read"
    """Read checks, entitlements and plans, and mint snapshots. The default."""
    USAGE_READ = "usage:read"
    """Read usage."""
    USAGE_WRITE = "usage:write"
    """Record usage and holds, only when the app records usage itself."""
    BILLING_SELF = "billing:self"
    """Make billing choices, only for people who may buy for the customer."""


class PlanBilling(StrEnum):
    """How `set_plan` treats the payment provider.

    `PROVIDER` (the default) charges the change through the provider that bills the product.
    `KEEP` moves the customer in Entitler while the provider keeps billing its plan. `END` ends
    the provider's subscription when the change takes effect, as with an invoiced contract.
    """

    PROVIDER = "provider"
    """Charge the change through the provider that bills the product (the default)."""
    KEEP = "keep"
    """Move the customer in Entitler while the provider keeps billing what it bills."""
    END = "end"
    """End the provider's subscription when the change takes effect, holding the plan outside it."""


@dataclass(frozen=True, slots=True, kw_only=True)
class EnvironmentRef:
    """An environment named by its id alone, as a snapshot names it."""

    id: str
    """The environment's id."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Environment(EnvironmentRef):
    """The environment an answer comes from."""

    name: str
    """The environment's name."""
    kind: EnvironmentKind | str
    """`test` or `live`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class TrackRef:
    """A track, which decides the release a customer sees."""

    id: str
    """The track's id."""
    name: str
    """The track's name, the same in every environment."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Experiment:
    """The experiment and arm a customer is in."""

    id: str
    """The experiment's id."""
    arm: Arm | str
    """`control` or `variant`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class AnswerContext:
    """Where an answer comes from: the environment, the track and the release or change."""

    environment: Environment
    """The environment the answer comes from."""
    track: TrackRef
    """The track the customer resolved to."""
    release: int | None
    """The release the answer comes from, or `None`."""
    change: str | None
    """The change the answer comes from, when the track follows one, or `None`."""
    testers: bool
    """True on a track for testers and in a test environment."""
    experiment: Experiment | None
    """The experiment and arm the customer is in, or `None`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanSource:
    """A value from a plan the customer holds."""

    plan: str
    """The plan's key."""
    name: str
    """The plan's name."""
    version: int
    """The plan's version."""
    by_default: bool
    """True when the customer holds the plan because it is the default."""
    value: FeatureValue
    """The value the plan gives."""
    type: Literal["plan"] = "plan"
    """`plan`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class AddOnSource:
    """A value from an add-on the customer holds."""

    plan: str
    """The add-on's key."""
    name: str
    """The add-on's name."""
    version: int
    """The add-on's version."""
    quantity: int
    """How many the customer holds."""
    value: FeatureValue
    """The value the add-on gives."""
    type: Literal["addon"] = "addon"
    """`addon`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class GrantSource:
    """A value from a grant."""

    grant: str
    """The grant's id."""
    until: datetime | None
    """When the grant ends, or `None` for no end."""
    value: FeatureValue
    """The value the grant gives."""
    type: Literal["grant"] = "grant"
    """`grant`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class BankedSource:
    """An amount banked from earlier periods."""

    value: int
    """The amount banked."""
    type: Literal["banked"] = "banked"
    """`banked`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class GroupSource:
    """A group feature's members."""

    features: tuple[str, ...]
    """The member features' keys."""
    type: Literal["group"] = "group"
    """`group`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class UnknownSource:
    """A source this version of the SDK does not know."""

    type: str
    """The source's type, as Entitler sent it."""


EntitlementSource: TypeAlias = (
    PlanSource | AddOnSource | GrantSource | BankedSource | GroupSource | UnknownSource
)
"""Where an entitlement's value comes from."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Upgrade:
    """A plan that would entitle the customer, with the move and who can make it."""

    plan: str
    """The plan's key."""
    name: str
    """The plan's name."""
    move: Move | str
    """How the plan moves the customer."""
    action: MoveAction | str
    """`buy` when the customer can make the move alone, `contact` for a sales-led move."""
    reason: str | None
    """Why the move is unavailable, for the developer, or `None`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class CheckBase(AnswerContext):
    """The fields a check and a usage result share."""

    customer: str
    """The customer's external id."""
    as_of: datetime
    """The instant the answer was computed for."""
    feature: str
    """The feature's key."""
    type: FeatureType | str
    """The feature's kind."""
    entitled: bool
    """Entitler's decision. For a metered feature, true while allowance remains."""
    value: FeatureValue
    """The feature's value: `True`, an amount (`0` when not entitled) or `"unlimited"`."""
    sources: tuple[EntitlementSource, ...]
    """Where the value comes from."""
    used: int | None
    """A metered feature's amount used this period, else `None`."""
    held: int | None
    """A metered feature's amount on hold, else `None`."""
    remaining: Limit | None
    """A metered feature's allowance left, else `None`."""
    resets_at: datetime | None
    """When a metered feature's meter resets, or `None`."""
    upgrades: tuple[Upgrade, ...]
    """The plans that would entitle the customer, filled only when they are not entitled."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Check(CheckBase):
    """One feature's entitlement for a customer."""

    stale: bool
    """True only when answered from a kept copy because Entitler was unreachable."""


@dataclass(frozen=True, slots=True, kw_only=True)
class BooleanCheck(Check):
    """An on/off feature's check: `value` is `True`, or `0` when not entitled."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ConfigCheck(Check):
    """A config feature's check: `value` is the configured amount or `"unlimited"`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class GroupCheck(Check):
    """A group feature's check, with Entitler's decision for the whole group."""


@dataclass(frozen=True, slots=True, kw_only=True)
class MeteredCheck(Check):
    """A metered feature's check, with its meter."""

    value: Limit
    """The allowance per period."""
    used: int
    """The amount used this period."""
    held: int
    """The amount on hold."""
    remaining: Limit
    """The allowance left, after `used` and `held`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Entitlement:
    """One feature in a customer's entitlement list."""

    key: str
    """The feature's key."""
    type: FeatureType | str
    """The feature's kind."""
    value: FeatureValue
    """The feature's value."""
    entitled: bool
    """Entitler's decision."""
    sources: tuple[EntitlementSource, ...]
    """Where the value comes from."""
    used: int | None
    """A metered feature's amount used, else `None`."""
    held: int | None
    """A metered feature's amount on hold, else `None`."""
    remaining: Limit | None
    """A metered feature's allowance left, else `None`."""
    resets_at: datetime | None
    """When a metered feature's meter resets, or `None`."""
    upgrades: tuple[Upgrade, ...]
    """The plans that would entitle the customer, filled only when they are not entitled."""


EnvironmentT = TypeVar("EnvironmentT", bound=EnvironmentRef)


@dataclass(frozen=True, slots=True, kw_only=True)
class Entitlements(Generic[EnvironmentT]):
    """A customer's entitlement list, groups included, each with Entitler's decision.

    A snapshot's list names its environment by id alone.

    Example:
        ```python
        entitlements = customer.entitlements()
        if entitlements.has(features.COLLABORATION):
            show_sharing()
        ```
    """

    customer: str
    """The customer's external id."""
    as_of: datetime
    """The instant the answer was computed for."""
    entitlements: tuple[Entitlement, ...]
    """Every entitlement, groups included."""
    environment: EnvironmentT
    """The environment the answer comes from."""
    track: TrackRef
    """The track the customer resolved to."""
    release: int | None
    """The release the answer comes from, or `None`."""
    change: str | None
    """The change the answer comes from, or `None`."""
    testers: bool
    """True on a track for testers and in a test environment."""
    experiment: Experiment | None
    """The experiment and arm the customer is in, or `None`."""
    stale: bool
    """True only when answered from a kept copy because Entitler was unreachable."""

    def get(self, feature: Feature[object] | str) -> Entitlement | None:
        """Answers the feature's entitlement, or `None` when the list does not hold it."""
        key = feature if isinstance(feature, str) else feature.key
        return next((item for item in self.entitlements if item.key == key), None)

    def has(self, feature: Feature[object] | str) -> bool:
        """Answers Entitler's decision for the feature, and `False` when the list lacks it.

        Groups are in the list with the server's decision, so this never expands a group.
        """
        found = self.get(feature)
        return found is not None and found.entitled

    def __iter__(self) -> Iterator[Entitlement]:
        return iter(self.entitlements)

    def __len__(self) -> int:
        return len(self.entitlements)


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanSummary:
    """A plan or add-on named by id, key and name."""

    id: str
    """The plan's public id."""
    key: str
    """The plan's key."""
    name: str
    """The plan's name."""


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanInfo(PlanSummary):
    """A plan or add-on with its kind."""

    kind: PlanKind | str
    """`plan` or `addon`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class OptionPlan(PlanInfo):
    """The plan a move option leads to."""

    description: str
    """The plan's description."""
    default: bool
    """True for the product's default plan."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ProductSummary:
    """A product, named by key and name."""

    key: str
    """The product's key."""
    name: str
    """The product's name."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Period:
    """A billing period: its stable key, which writes take, and its display label."""

    key: str
    """The period's key, such as `monthly`, the same when the label is renamed."""
    label: str
    """The period's label, such as `Monthly`: display copy."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ProviderPrice:
    """A price the payment provider or store charges."""

    amount: int
    """A whole number in the currency's smallest unit."""
    currency: str
    """The ISO 4217 code, in lower case."""
    interval: PriceInterval | str | None
    """How often it bills, or `None` for a one-time price."""
    interval_count: int
    """How many intervals each bill covers, 1 or more."""
    tax: Tax | str
    """Whether the amount includes tax."""


@dataclass(frozen=True, slots=True, kw_only=True)
class OfferedSku:
    """A product a store or provider sells for a move: buy it through StoreKit or Play Billing."""

    period: Period | None
    """The billing period it sells, or `None`."""
    connector: str
    """Where it is bought, such as `stripe`, `apple` or `google`."""
    ids: Mapping[str, str]
    """The connector's ids, by field."""
    price: ProviderPrice | None
    """The price, or `None` when unknown."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Impact:
    """One line of what a move changes."""

    kind: ImpactKind | str
    """`gains`, `loses`, `changes` or `same`."""
    text: str
    """The line, in English."""


@dataclass(frozen=True, slots=True, kw_only=True)
class PendingMove:
    """A move to another plan, booked for renewal."""

    plan: PlanSummary
    """The plan the customer moves to."""
    type: Literal["move"] = "move"
    """`move`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class PendingCancel:
    """A cancel booked for renewal."""

    moving_to: PlanSummary | None
    """The default plan it leads to, or `None` when the product has none."""
    type: Literal["cancel"] = "cancel"
    """`cancel`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class PendingBilling:
    """A change of who bills the plan, booked for renewal by the company."""

    billing: Biller | str
    """Who bills the plan from then: `provider`, `outside` or `store`."""
    type: Literal["billing"] = "billing"
    """`billing`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class PendingUnknown:
    """A booked change this version of the SDK does not know."""

    type: str
    """The change's type, as Entitler sent it."""


PendingChange: TypeAlias = PendingMove | PendingCancel | PendingBilling | PendingUnknown
"""A change booked for renewal."""


@dataclass(frozen=True, slots=True, kw_only=True)
class HeldPlan:
    """A plan or add-on the customer holds."""

    plan: PlanInfo
    """The plan."""
    product: ProductSummary | None
    """Its product, or `None`."""
    version: int | None
    """The version held, or `None`."""
    by_default: bool
    """True when held because it is the product's default plan."""
    period: Period | None
    """The billing period, or `None` when Entitler sets none."""
    renews_at: datetime | None
    """When it renews, or `None`."""
    pending: PendingChange | None
    """The change booked for `renews_at`, or `None`."""
    billed_by: Store | str | None
    """Who bills it, or `None` when nothing does. A store's plan is managed in the store."""


@dataclass(frozen=True, slots=True, kw_only=True)
class MoveOption:
    """A plan or add-on the customer can move to, and how."""

    plan: OptionPlan
    """The plan it leads to."""
    product: ProductSummary | None
    """Its product, or `None`."""
    from_: PlanSummary | None
    """The plan it moves from, or `None`."""
    move: Move | str
    """How it moves the customer."""
    action: MoveAction | str
    """`buy` decides a purchase button, `contact` a sales button, `unavailable` none."""
    reason: str | None
    """Why the move is unavailable, for the developer, or `None`."""
    when: When | str
    """When the move takes effect."""
    periods: tuple[Period, ...]
    """The billing periods it sells."""
    impact: tuple[Impact, ...]
    """What the move changes, line by line."""
    skus: tuple[OfferedSku, ...]
    """The store products that sell it."""


@dataclass(frozen=True, slots=True, kw_only=True)
class CustomerPlans(AnswerContext):
    """The paywall's and the billing page's one read: the plans held and the moves open."""

    customer: str
    """The customer's external id."""
    as_of: datetime
    """The instant the answer was computed for."""
    held: tuple[HeldPlan, ...]
    """The plans and add-ons the customer holds."""
    options: tuple[MoveOption, ...]
    """The moves open to the customer."""
    stale: bool
    """True only when answered from a kept copy because Entitler was unreachable."""


@dataclass(frozen=True, slots=True, kw_only=True)
class UsageResult(CheckBase):
    """What a usage write did, with the meter after it."""

    outcome: UsageOutcome | str
    """What happened."""
    refusal: Refusal | str | None
    """Why it was refused, or `None`."""
    id: str | None
    """The usage report's id, or `None`."""
    hold_id: str | None
    """The hold's id, or `None`."""
    mode: UsageMode | str
    """The report's mode."""
    amount: int
    """The amount reported or held."""
    meter_change: int
    """How far the meter moved."""
    over_by: int
    """How far past the allowance an observe-mode report took the meter."""
    late: bool
    """True when the report arrived after its period closed."""
    occurred_at: datetime | None
    """When the usage happened, or `None`."""
    expires_at: datetime | None
    """When a hold expires, or `None`."""
    reported_as: UsageSource | str
    """Where the report came from."""
    replayed: bool
    """True when the idempotency key replayed an earlier call, so this call changed nothing."""


@dataclass(frozen=True, slots=True, kw_only=True)
class FeatureUsage:
    """One feature's meter in a usage answer."""

    feature: str
    """The feature's key."""
    type: FeatureType | str
    """The feature's kind."""
    entitled: bool
    """Entitler's decision."""
    value: FeatureValue
    """The feature's value."""
    sources: tuple[EntitlementSource, ...]
    """Where the value comes from."""
    used: int
    """The amount used this period."""
    held: int
    """The amount on hold."""
    remaining: Limit | None
    """The allowance left, or `None`."""
    resets_at: datetime | None
    """When the meter resets, or `None`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class UsageEvent:
    """One entry of a usage log: a use, or a meter adjustment."""

    id: str
    """The usage report's id."""
    feature: str
    """The feature's key."""
    kind: UsageEventKind | str
    """`use` or `adjust`."""
    amount: int
    """The amount used, or how far an adjustment moved the meter."""
    set_to: int | None
    """The amount an adjustment set the meter to, or `None`."""
    source: UsageSource | str
    """Where the report came from."""
    by: str | None
    """The key or person that sent it, or `None`."""
    actor: str | None
    """The person or system that decided an adjustment, or `None`."""
    reason: str | None
    """Why, or `None`."""
    at: datetime
    """When it was recorded."""
    cancelled_at: datetime | None
    """When it was cancelled, or `None`."""


ItemT = TypeVar("ItemT")
LogT = TypeVar("LogT")


@dataclass(frozen=True, slots=True, kw_only=True)
class Page(Generic[ItemT]):
    """One page of a paged list."""

    items: tuple[ItemT, ...]
    """The page's items. A page may be empty while `next` continues."""
    next: str | None
    """The cursor of the next page, or `None` on the last."""


@dataclass(frozen=True, slots=True, kw_only=True)
class CustomerUsage(AnswerContext, Generic[LogT]):
    """A customer's meters and usage log."""

    customer: str
    """The customer's external id."""
    as_of: datetime
    """The instant the answer was computed for."""
    meters_start_again_at: datetime | None
    """When the meters next reset, or `None`."""
    features: tuple[FeatureUsage, ...]
    """Each metered feature's meter."""
    log: LogT
    """The usage log: its first page, and every entry on iteration."""


@dataclass(frozen=True, slots=True, kw_only=True)
class RegisteredCustomer:
    """The customer `register` created or found."""

    id: str
    """Entitler's id for the customer."""
    external_id: str
    """The id your app uses for the customer."""
    environment_id: str
    """The environment's id."""
    created_at: datetime
    """When the customer was created."""
    created: bool
    """True when this call created the customer."""
    replayed: bool
    """True when the idempotency key replayed an earlier call, so this call changed nothing."""


@dataclass(frozen=True, slots=True, kw_only=True)
class VersionedPlan(PlanSummary):
    """A plan with the version held."""

    version: int
    """The version held."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ProductPlan(VersionedPlan):
    """A plan held in a product."""

    product: str
    """The product's key."""


@dataclass(frozen=True, slots=True, kw_only=True)
class CustomerSummary:
    """A customer's details."""

    id: str
    """Entitler's id for the customer."""
    external_id: str
    """The id your app uses for the customer."""
    name: str
    """The customer's name."""
    email: str
    """The customer's email."""
    environment_id: str
    """The environment's id."""
    sample: bool
    """True for a sample customer."""
    created_at: datetime
    """When the customer was created."""
    plan: VersionedPlan | None
    """The plan held, or `None`."""
    plans: tuple[ProductPlan, ...]
    """The plans held, one per product."""
    default_plan: PlanSummary | None
    """The default plan, or `None`."""
    status: str
    """The customer's status."""
    kind: CustomerKind | str
    """How the customer holds their plan."""
    metadata: Mapping[str, str]
    """Your app's metadata, exactly as kept."""
    track: TrackRef
    """The customer's track."""
    test_customer: bool
    """True for a test customer."""
    replayed: bool
    """True when an `update`'s idempotency key replayed an earlier call; false on reads."""


@dataclass(frozen=True, slots=True, kw_only=True)
class CustomerPage(Page[CustomerSummary]):
    """A page of the customer list, with the project's customer count and limit."""

    used: int
    """How many customers the project holds."""
    limit: Limit
    """How many it may hold."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Channel:
    """A sales channel: a provider and its connection."""

    provider: Store | str
    """The provider."""
    connection_id: str
    """The connection's id."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Purchase:
    """How a plan or add-on was bought."""

    money: Money | str
    """Real money or test money."""
    channel: Channel | None
    """The sales channel, or `None`."""
    release: int | None
    """The release it was bought from, or `None`."""
    change: str | None
    """The change it was bought from, or `None`."""
    arm: Arm | str | None
    """The experiment arm it was bought in, or `None`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class HeldAddOn:
    """An add-on the customer holds."""

    plan: PlanSummary
    """The add-on."""
    version: int
    """The version held."""
    quantity: int
    """How many are held."""
    countable: bool
    """True when it is sold by quantity."""
    added_at: datetime
    """When it was added."""
    moving_to: PlanSummary | None
    """The add-on it moves to at renewal, or `None`."""
    purchase: Purchase
    """How it was bought."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ProviderPeriod:
    """The payment provider's billing period."""

    provider: str
    """The provider, `stripe`."""
    starts_at: datetime
    """When the period started."""
    ends_at: datetime
    """When the period ends."""
    trial_ends_at: datetime | None
    """When a trial ends, or `None`."""
    cancels_at: datetime | None
    """When the subscription cancels, or `None`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanOverride:
    """A `keep` exception: Entitler holds one plan while the provider bills another."""

    from_: PlanSummary
    """The plan the provider bills."""
    period: Period | None
    """Its period, or `None`."""
    billed_by: Channel | None
    """The channel that bills it, or `None`."""
    since: datetime
    """When the exception started."""
    by: str
    """Who made it."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Subscription:
    """The plan a customer holds in a product, with its add-ons and billing."""

    product: ProductSummary
    """The product."""
    plan: PlanSummary
    """The plan held."""
    version: int
    """The version held."""
    cohort: int | None
    """The cohort, or `None`."""
    period: Period | None
    """The billing period, or `None`."""
    started_at: datetime
    """When it started."""
    renews_at: datetime
    """When it renews."""
    billing: ProviderPeriod | None
    """The provider's billing period, or `None`."""
    until: datetime | None
    """When it ends unless renewed, or `None`."""
    add_ons: tuple[HeldAddOn, ...]
    """The add-ons held."""
    pending: PendingChange | None
    """The change booked for renewal, or `None`."""
    purchase: Purchase
    """How it was bought."""
    override: PlanOverride | None
    """A `keep` exception, or `None`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ProductHolding:
    """What a customer holds in one product."""

    product: ProductSummary
    """The product."""
    default_plan: PlanSummary | None
    """Its default plan, or `None`."""
    subscription: Subscription | None
    """The plan held, or `None`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Grant:
    """A grant of a feature to one customer."""

    id: str
    """The grant's id, which `revoke_grant` takes."""
    feature: str
    """The feature's key."""
    value: FeatureValue
    """The value granted."""
    from_: datetime
    """When it started."""
    until: datetime | None
    """When it ends, or `None` for no end."""
    revoked_at: datetime | None
    """When it was revoked, or `None`."""
    reason: str
    """Why it was made."""
    by: str
    """The key or person that made it."""
    actor: str | None
    """The person or system that decided, or `None`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ActivityEntry:
    """One line of a customer's activity."""

    text: str
    """What happened."""
    at: datetime
    """When."""
    by: str | None
    """The key or person that did it, or `None`."""
    actor: str | None
    """The person or system that decided, or `None`."""
    reason: str | None
    """Why, or `None`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class CustomerDetail(Generic[LogT]):
    """Everything about a customer: plans, add-ons, grants, entitlements and usage log."""

    customer: CustomerSummary
    """The customer's details."""
    as_of: datetime
    """The instant the answer was computed for."""
    subscription: Subscription | None
    """The plan held in the main product, or `None`."""
    default_plan: PlanSummary | None
    """The default plan, or `None`."""
    products: tuple[ProductHolding, ...]
    """What the customer holds in each product."""
    add_ons: tuple[HeldAddOn, ...]
    """The add-ons held."""
    entitlements: tuple[Entitlement, ...]
    """Every entitlement."""
    banked: Mapping[str, int]
    """Amounts banked from earlier periods, by feature key."""
    move_options: tuple[MoveOption, ...]
    """The moves open to the customer."""
    grants: tuple[Grant, ...]
    """The customer's grants."""
    usage: LogT
    """The usage log: its first page, and every entry on iteration."""
    activity: tuple[ActivityEntry, ...]
    """The customer's activity."""
    environment: Environment
    """The environment the answer comes from."""
    replayed: bool
    """True when `customers.create`'s idempotency key replayed an earlier call."""


@dataclass(frozen=True, slots=True, kw_only=True)
class IssuedCustomerToken:
    """A customer token for an in-app client."""

    token: str = field(repr=False)
    """The token. Hand it only to the signed-in customer it names."""
    customer: str
    """The customer's external id."""
    scopes: tuple[CustomerTokenScope | str, ...]
    """The scopes it holds."""
    expires_at: datetime
    """When it expires."""


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanChange:
    """A plan change: the plan the customer is left on, and when it takes effect."""

    product: ProductSummary | None
    """The product, or `None`."""
    plan: PlanSummary | None
    """The plan or add-on the change leaves them on, or `None` when they hold no plan there."""
    quantity: int | None
    """An add-on's quantity after the change (`0` when removed), or `None` for a plan."""
    effective: Effective | str
    """`now`, or `renewal`."""
    at: datetime
    """When it takes effect."""
    until: datetime | None
    """When the plan ends unless renewed, or `None`."""
    changed: bool
    """False when nothing changed, such as a plan they already hold."""
    replayed: bool
    """True when the idempotency key replayed an earlier call, so this call changed nothing."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SubscribeStep:
    """What `subscribe` needs next.

    Example:
        ```python
        step = customer.subscribe("pro", period="monthly", return_url=url)
        match step.next:
            case NextStep.DONE:
                show_plan(step.change)
            case NextStep.PAY:
                redirect(step.url)
            case NextStep.CONFIRMING:
                show_pending()
            case NextStep.MANAGE:
                open_store_page(step.billed_by)
        ```
    """

    next: NextStep | str
    """The next step; a step this version does not know stays a `str`."""
    change: PlanChange | None
    """The change made, on `DONE`; else `None`."""
    url: str | None
    """The provider's page to send the customer to, on `PAY`; else `None`."""
    billed_by: Store | str | None
    """The store that bills the product, on `MANAGE`; else `None`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class GrantChange:
    """A grant made or revoked."""

    grant: Grant
    """The grant, so a support tool keeps its id."""
    replayed: bool
    """True when the idempotency key replayed an earlier call, so this call changed nothing."""


@dataclass(frozen=True, slots=True, kw_only=True)
class CustomerTrack:
    """The track a customer is on."""

    customer: str
    """The customer's external id."""
    environment_id: str
    """The environment's id."""
    track: TrackRef
    """The track."""
    source: TrackSource | str | None
    """Who put the customer on it, or `None`."""
    previous_track_id: str | None
    """The track they were on before, or `None`."""
    replayed: bool
    """True when the idempotency key replayed an earlier call, so this call changed nothing."""


@dataclass(frozen=True, slots=True, kw_only=True)
class PublishedProduct:
    """A product on sale."""

    key: str
    """The product's key."""
    name: str
    """The product's name."""
    default_plan: str | None
    """Its default plan's key, or `None`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class PublishedPeriod(Period):
    """A billing period a plan sells."""

    count: int
    """How many units the period lasts."""
    unit: PeriodUnit | str
    """The period's unit."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ChannelListing:
    """A plan's listing in one sales channel."""

    channel: Channel
    """The channel."""
    name: str
    """The channel's name."""
    mode: ListingMode | str | None
    """Whether it sells in live or test mode, or `None`."""
    purchasable: bool
    """True when it can be bought now."""
    ids: Mapping[str, str] | None
    """The channel's product ids, by field, or `None`."""
    price: ProviderPrice | None
    """The channel's price, or `None` when unknown."""


@dataclass(frozen=True, slots=True, kw_only=True)
class PeriodListing:
    """A plan's listings for one billing period."""

    period: Period | None
    """The period, or `None`."""
    channels: tuple[ChannelListing, ...]
    """The listings in each channel."""


@dataclass(frozen=True, slots=True, kw_only=True)
class PricingPlan:
    """A plan or add-on on sale."""

    id: str
    """The plan's public id."""
    key: str
    """The plan's key."""
    name: str
    """The plan's name."""
    description: str
    """The plan's description."""
    kind: PlanKind | str
    """`plan` or `addon`."""
    product: str | None
    """Its product's key, or `None`."""
    sales_led: bool
    """True when only the company sells it."""
    status: PlanStatus | str
    """`active`, or `legacy` when kept only for the customers who hold it."""
    version: int | None
    """The version on sale, or `None`."""
    default: bool
    """True for the product's default plan."""
    periods: tuple[PublishedPeriod, ...]
    """The billing periods it sells."""
    attaches_to: tuple[str, ...]
    """For an add-on, the plans it attaches to."""
    features: Mapping[str, FeatureValue]
    """Each feature's value, by key."""
    listings: tuple[PeriodListing, ...]
    """Where it is sold, by period: a provider price, or none yet."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Pricing(AnswerContext):
    """The plans on sale."""

    customer: str | None
    """The customer's external id, or `None` signed out."""
    default_plan: str | None
    """The default plan's key, or `None`."""
    products: tuple[PublishedProduct, ...]
    """The products on sale."""
    plans: tuple[PricingPlan, ...]
    """The plans and add-ons on sale."""
    stale: bool
    """True only when answered from a kept copy because Entitler was unreachable."""


@dataclass(frozen=True, slots=True, kw_only=True)
class MeterWindow:
    """How often a meter resets."""

    count: int
    """How many units the window lasts."""
    unit: MeterUnit | str
    """The window's unit."""
    anchor: MeterAnchor | str | None
    """What the window counts from, or `None` when the answer leaves it out."""


@dataclass(frozen=True, slots=True, kw_only=True)
class FeatureListing:
    """A feature in the catalogue."""

    id: str
    """The feature's id."""
    key: str
    """The feature's key."""
    name: str
    """The feature's name."""
    type: FeatureType | str
    """The feature's kind."""
    description: str
    """The feature's description."""
    unit: str
    """The unit it is counted in, or an empty string."""
    reset_every: MeterWindow | None
    """How often a metered feature resets, or `None`."""
    archived: bool
    """True once archived."""
    includes: tuple[str, ...]
    """A group's direct members' keys."""


@dataclass(frozen=True, slots=True, kw_only=True)
class FeatureList:
    """The catalogue's features."""

    environment: Environment
    """The environment the answer comes from."""
    track: TrackRef
    """The track it was read from."""
    release: int | None
    """The release, or `None`."""
    change: str | None
    """The change, or `None`."""
    features: tuple[FeatureListing, ...]
    """The features."""
    stale: bool
    """True only when answered from a kept copy because Entitler was unreachable."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SnapshotKey:
    """A public key that verifies snapshots: an EC P-256 JSON Web Key."""

    kty: str
    """`EC`."""
    crv: str
    """`P-256`."""
    x: str
    """The x coordinate, base64url."""
    y: str
    """The y coordinate, base64url."""
    kid: str
    """The key's id."""
    alg: str
    """`ES256`."""
    use: str
    """`sig`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SnapshotKeys:
    """The public keys that verify snapshots. Ship them with the app."""

    keys: tuple[SnapshotKey, ...]
    """The keys."""


@dataclass(frozen=True, slots=True, kw_only=True)
class IssuedSnapshot:
    """A signed snapshot of a customer's entitlements, for offline use."""

    token: str = field(repr=False)
    """The snapshot. Verify it with `verify_snapshot`."""
    expires_at: datetime
    """When it expires."""
    key_id: str
    """The id of the key that signed it."""


@dataclass(frozen=True, slots=True, kw_only=True)
class BillingDrift:
    """A difference between the plan the provider bills and the plan Entitler holds."""

    billed_plan: str
    """The plan the provider bills."""
    held_plan: str | None
    """The plan Entitler holds, or `None`."""
    observed_at: datetime
    """When the difference was seen."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ProductBilling:
    """The payment provider's view of one product."""

    product: str | None
    """The product's key, or `None`."""
    status: BillingStatus | str | None
    """The subscription's status, or `None`."""
    sku: OfferedSku | None
    """The SKU it bills, or `None`."""
    items: int
    """How many items the subscription holds."""
    drift: BillingDrift | None
    """A difference from the plan held, or `None`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class CustomerBilling:
    """The payment provider's view of a customer, for support tools."""

    provider: str | None
    """The provider, `stripe`, or `None`."""
    status: BillingStatus | str | None
    """The subscription's status, or `None`."""
    sku: OfferedSku | None
    """The SKU it bills, or `None`."""
    items: int
    """How many items the subscription holds."""
    drift: BillingDrift | None
    """A difference from the plan held, or `None`."""
    products: tuple[ProductBilling, ...]
    """The view of each product."""


@dataclass(frozen=True, slots=True, kw_only=True)
class BillingPortal:
    """The provider's billing portal page."""

    provider: str
    """The provider, `stripe`."""
    url: str
    """The page to send the customer to."""


@dataclass(frozen=True, slots=True, kw_only=True)
class BillingSync:
    """The result of reading the customer's state from the provider."""

    changed: bool
    """True when the provider's state moved the customer."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Scopes:
    """The scopes a client's credential holds, for deciding which controls to show."""

    scopes: tuple[Scope, ...]
    """The scopes this SDK knows, in a fixed order."""
    registration: bool | None
    """For an identity client, whether the sign-in provider lets a new person register."""


@dataclass(frozen=True, slots=True, kw_only=True)
class UsageEventError:
    """Why one event of a usage batch was not recorded."""

    code: str
    """The error code, such as `invalid_amount` or `connection_failed`."""
    message: str
    """What went wrong, for the developer."""


@dataclass(frozen=True, slots=True, kw_only=True)
class UsageEventResult:
    """What happened to one event of a usage batch."""

    index: int
    """The event's position in the input."""
    outcome: BatchOutcome | str
    """`recorded`, `duplicate` or `error`."""
    id: str | None
    """The usage report's id, or `None`."""
    late: bool
    """True when the event arrived after its period closed."""
    error: UsageEventError | None
    """Why it failed, on `error`; resend these events, whose keys make it safe."""
    idempotency_key: str | None
    """The event's idempotency key, or `None` when it had none."""
    replayed: bool
    """True when its request's key replayed an earlier batch."""


@dataclass(frozen=True, slots=True, kw_only=True)
class UsageEventsResult:
    """What a usage batch recorded: one result per event, in input order, and the totals."""

    results: tuple[UsageEventResult, ...]
    """One result per event, in input order."""
    recorded: int
    """How many events were recorded."""
    duplicates: int
    """How many replayed an earlier report."""
    errors: int
    """How many failed."""


@dataclass(frozen=True, slots=True, kw_only=True)
class UsageEventInput:
    """One usage event for `record_usage_batch`, always recorded in observe mode."""

    customer: str
    """The customer's external id."""
    feature: Feature[Metered]
    """The metered feature: a constant, or `Feature.metered("key")` for a plain key."""
    amount: int
    """The amount used, a whole number from 1."""
    idempotency_key: str
    """A key naming the event in your own system."""
    occurred_at: datetime | None = None
    """When it happened, or `None` for now."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Sku:
    """A SKU the customer bought: the connector and the provider's ids."""

    connector: str
    """Where it was bought, such as `apple` or `google`."""
    ids: Mapping[str, str]
    """The provider's ids, by field, such as `{"productId": "pro_monthly"}`."""


PlanChoice: TypeAlias = str | Sku
"""The plan `set_plan` moves a customer to: a plan's key or id, or a `Sku` a store sold."""
