"""The async customer: `server.customer(id)` and `client.me` on the async clients."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime
from typing import Any, Literal, overload

import anyio

from ._clock import MAX_WAIT
from ._errors import EntitlerError
from ._features import Boolean, Config, Feature, Group, Metered
from ._io import AsyncDriver
from ._models import (
    BillingPortal,
    BillingSync,
    BooleanCheck,
    Check,
    ConfigCheck,
    CustomerBilling,
    CustomerDetail,
    CustomerPlans,
    CustomerSummary,
    CustomerTokenScope,
    CustomerTrack,
    CustomerUsage,
    Entitlements,
    Environment,
    GrantChange,
    GroupCheck,
    IssuedCustomerToken,
    IssuedSnapshot,
    MeteredCheck,
    Page,
    PlanBilling,
    PlanChange,
    Pricing,
    RegisteredCustomer,
    Sku,
    SubscribeStep,
    UsageEvent,
    UsageMode,
    UsageResult,
    When,
)
from ._ops import CustomerOps, HoldState, ServerOps
from ._pages import AsyncUsageLog


class AsyncHold:
    """An open hold, from `start_hold`: report the amount used, then finish however the work ended.

    Only the first of `finish()`, `release()` and leaving `with hold:` acts; a later call answers
    the first one's result without a request. Leaving the `with` block when neither ran settles
    the amount reported with `use()`, or releases the hold when nothing was reported, and passes a
    failure to `on_error` rather than raise over the block's own exception.

    Example:
        ```python
        hold = await customer.start_hold(features.AI_CREDITS, 2_000, idempotency_key=message_id)
        async with hold:
            hold.use(0)
            async for chunk in stream:
                hold.use(chunk.total_tokens)
            await hold.finish()
        ```
    """

    def __init__(self, state: HoldState, driver: AsyncDriver) -> None:
        self._state = state
        self._driver = driver
        self._lock: anyio.Lock | None = None

    @property
    def id(self) -> str:
        """The hold's id."""
        return self._state.hold_id

    @property
    def amount(self) -> int:
        """The amount held."""
        return self._state.result.amount

    @property
    def expires_at(self) -> datetime:
        """When the hold expires unless settled or released."""
        expires = self._state.result.expires_at
        assert expires is not None  # noqa: S101 - an open hold always has an expiry
        return expires

    @property
    def result(self) -> UsageResult:
        """The hold's answer, with the meter."""
        return self._state.result

    @property
    def duplicate(self) -> bool:
        """True when another caller holds the same key and may be doing the same work."""
        return self._state.result.outcome == "duplicate"

    def use(self, amount: int) -> None:
        """Reports the total the work used so far, a whole number of 0 or more, with no request.

        A later call replaces an earlier one. Work that reports as it goes calls `use(0)` before
        it starts, so finishing after a failure that used nothing frees the whole hold.
        """
        self._state.use(amount)

    async def finish(self) -> UsageResult:
        """Ends the work however it ended, and charges for what happened.

        Settles the amount reported with `use()` (the held amount when none was), up to the held
        amount, and records any excess in observe mode under the hold's key plus `:excess`; when
        the hold had expired, records the whole amount there. It runs shielded from cancellation,
        within the timeout, so a cancelled task still settles.

        Raises:
            UsageSettlementError: settling or recording the excess failed after its retries.
        """
        return await self._act(True)

    async def release(self) -> UsageResult:
        """Frees the hold, recording nothing, for work that never ran."""
        return await self._act(False)

    async def _act(self, finish: bool) -> UsageResult:
        state = self._state
        seconds = self._driver.engine.timeout if state.seconds is None else state.seconds
        if self._lock is None:
            self._lock = anyio.Lock()
        with anyio.move_on_after(min(seconds, MAX_WAIT), shield=True) as scope:
            async with self._lock:
                if not state.done:
                    state.done = True
                    try:
                        state.outcome = await self._driver.run(
                            state.finish() if finish else state.release()
                        )
                    except Exception as error:
                        state.failure = error
        if scope.cancelled_caught and state.outcome is None and state.failure is None:
            state.failure = state.settlement_error() if finish else state.timed_out()
            if finish:
                state.failure.__cause__ = state.timed_out()
        if state.failure is not None:
            raise state.failure
        assert state.outcome is not None  # noqa: S101 - set by the first action
        return state.outcome

    async def dispose(self) -> None:
        """Settles the amount reported, else releases, unless `finish` or `release` already ran.

        A failure goes to `on_error`; it never raises.
        """
        if self._state.done:
            return
        try:
            await self._act(self._state.used is not None)
        except EntitlerError as error:
            self._driver.engine.report(error)

    async def __aenter__(self) -> AsyncHold:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.dispose()

    def __repr__(self) -> str:
        return f"AsyncHold(id={self.id!r}, amount={self.amount}, duplicate={self.duplicate})"


class AsyncCustomer:
    """A customer, shared by both clients: `server.customer(id)` on a server, `client.me` in an app.

    Code that gates features, records usage and offers the customer's own billing choices takes a
    `AsyncCustomer`, so it is written once.

    Example:
        ```python
        def export_document(customer: AsyncCustomer, document: Document) -> bytes:
            if not customer.is_entitled(features.EXPORT_PDF, default=False):
                raise UpgradeRequired()
            return render_pdf(document)
        ```
    """

    def __init__(self, ops: CustomerOps, driver: AsyncDriver) -> None:
        self._ops = ops
        self._driver = driver

    @property
    def id(self) -> str | None:
        """The customer's external id; in an app, the one the latest answer named, else `None`."""
        return self._ops.named

    @overload
    async def check(
        self, feature: Feature[Metered], *, revalidate: bool = False, timeout: float | None = None
    ) -> MeteredCheck: ...
    @overload
    async def check(
        self, feature: Feature[Boolean], *, revalidate: bool = False, timeout: float | None = None
    ) -> BooleanCheck: ...
    @overload
    async def check(
        self, feature: Feature[Config], *, revalidate: bool = False, timeout: float | None = None
    ) -> ConfigCheck: ...
    @overload
    async def check(
        self, feature: Feature[Group], *, revalidate: bool = False, timeout: float | None = None
    ) -> GroupCheck: ...
    @overload
    async def check(
        self, feature: Feature[Any] | str, *, revalidate: bool = False, timeout: float | None = None
    ) -> Check: ...
    async def check(
        self, feature: Feature[Any] | str, *, revalidate: bool = False, timeout: float | None = None
    ) -> Check:
        """Checks one feature, through the answer cache.

        A metered feature's `entitled` means allowance remains, and `upgrades` lists the plans
        that would entitle the customer, filled only when they are not entitled.

        Args:
            feature: A feature constant, which types the answer, or a plain key.
            revalidate: Skip a fresh kept answer and revalidate it with its `ETag`, so a page that
                knows the customer just changed shows the change.
            timeout: This call's per-attempt timeout in seconds, in place of the client's.
        """
        return await self._driver.run(self._ops.check(feature, revalidate, timeout))

    async def is_entitled(
        self,
        feature: Feature[Any] | str,
        *,
        default: bool,
        revalidate: bool = False,
        timeout: float | None = None,
    ) -> bool:
        """Answers whether the customer is entitled, and never fails because of Entitler.

        When the check fails it answers `default` and passes the error to `on_error`. Pass
        `default=False` for paid features (fail closed), and `True` only where losing a sale is
        worse than giving the feature away (fail open).

        Raises:
            ValueError: the feature key is blank, or the timeout is not a positive number.
        """
        return await self._driver.run(self._ops.is_entitled(feature, default, revalidate, timeout))

    async def entitlements(
        self, *, revalidate: bool = False, timeout: float | None = None
    ) -> Entitlements[Environment]:
        """The customer's entitlements, groups included, through the answer cache."""
        return await self._driver.run(self._ops.entitlements(revalidate, timeout))

    async def plans(
        self, *, revalidate: bool = False, timeout: float | None = None
    ) -> CustomerPlans:
        """The paywall's and the billing page's one read, through the answer cache.

        `held` holds the plans held: a store's `billed_by` sends the customer to the store's own
        page, anything else to `billing_portal`. `options` holds the moves open: `action` decides
        the button, and `skus` are the store products. In an app it needs the organisation's
        customer portal capability (`409 capability_required`).
        """
        return await self._driver.run(self._ops.plans(revalidate, timeout))

    async def pricing(
        self, *, visitor: str | None = None, revalidate: bool = False, timeout: float | None = None
    ) -> Pricing:
        """The pricing on sale to the customer, through the answer cache."""
        return await self._driver.run(self._ops.pricing(visitor, revalidate, timeout))

    async def usage(
        self, *, cursor: str | None = None, timeout: float | None = None
    ) -> CustomerUsage[AsyncUsageLog]:
        """The customer's meters and usage log, from the page `cursor` names when given."""
        return await self._driver.run(self._ops.usage(cursor, timeout, self._log(timeout, None)))

    def _log(
        self, timeout: float | None, as_of: datetime | None
    ) -> Callable[[Page[UsageEvent]], AsyncUsageLog]:
        return lambda first: AsyncUsageLog(
            first, lambda cursor: self._driver.run(self._ops.usage_page(cursor, timeout, as_of))
        )

    async def record_usage(
        self,
        feature: Feature[Metered],
        amount: int,
        *,
        idempotency_key: str,
        mode: UsageMode | None = None,
        occurred_at: datetime | None = None,
        register: bool | None = None,
        timeout: float | None = None,
    ) -> UsageResult:
        """Records usage of a metered feature: a whole number from 1 in the feature's unit.

        A refusal is an answer (`outcome` `refused`), not an error.

        Args:
            feature: The metered feature; a plain key goes through `Feature.metered(key)`.
            amount: The amount used.
            idempotency_key: A key naming the event in your own system (this message, this job),
                never an object whose state changes: the API keeps it for good, so a retry from
                anywhere never charges twice.
            mode: `UsageMode.GATE` (the default) records only within the allowance;
                `UsageMode.OBSERVE` records what already happened.
            occurred_at: When the usage happened, an aware `datetime`, so it counts in that period.
            register: Register a customer not registered yet, if the credential may.
            timeout: This call's per-attempt timeout in seconds.
        """
        return await self._driver.run(
            self._ops.record_usage(
                feature, amount, idempotency_key, mode, occurred_at, register, timeout
            )
        )

    async def start_hold(
        self,
        feature: Feature[Metered],
        amount: int,
        *,
        idempotency_key: str,
        ttl_seconds: int | None = None,
        timeout: float | None = None,
    ) -> AsyncHold:
        """Holds `amount` before any work starts, and answers the open `AsyncHold`.

        The key is at most 193 characters, leaving room for `:excess`. `remaining` drops by the
        whole hold while the work runs and rises when it settles.

        Raises:
            UsageRefusedError: the customer has no allowance for the work.
            UsageReplayedError: the key replays a hold already settled, released or expired.
        """
        result = await self._driver.run(
            self._ops.start_hold(feature, amount, idempotency_key, ttl_seconds, timeout)
        )
        return AsyncHold(
            HoldState(self._ops, feature, idempotency_key, result, timeout), self._driver
        )

    def with_hold(
        self,
        feature: Feature[Metered],
        amount: int,
        *,
        idempotency_key: str,
        ttl_seconds: int | None = None,
        timeout: float | None = None,
    ) -> AbstractAsyncContextManager[AsyncHold]:
        """Holds `amount` around a block of work, and finishes the hold when the block ends.

        It raises on entry, so the block never runs after a refusal or a replay. A clean exit calls
        `finish()`; an exception settles the amount reported with `hold.use()`, else releases,
        and re-raises. It keeps the accounting exactly once, not the work: read `hold.duplicate`
        to learn that another caller is already doing it.

        Example:
            ```python
            async with customer.with_hold(features.AI_CREDITS, 500, idempotency_key=job.id) as hold:
                reply = await run()
                hold.use(reply.tokens)
            ```
        """
        return self._with_hold(feature, amount, idempotency_key, ttl_seconds, timeout)

    @asynccontextmanager
    async def _with_hold(
        self,
        feature: Feature[Metered],
        amount: int,
        key: str,
        ttl_seconds: int | None,
        timeout: float | None,
    ) -> AsyncGenerator[AsyncHold]:
        hold = await self.start_hold(
            feature, amount, idempotency_key=key, ttl_seconds=ttl_seconds, timeout=timeout
        )
        try:
            yield hold
        except BaseException:
            await hold.dispose()
            raise
        await hold.finish()

    async def hold_usage(
        self,
        feature: Feature[Metered],
        amount: int,
        *,
        idempotency_key: str,
        ttl_seconds: int | None = None,
        timeout: float | None = None,
    ) -> UsageResult:
        """Holds an amount and answers the hold's `UsageResult`, for a hold settled elsewhere."""
        return await self._driver.run(
            self._ops.hold_usage(feature, amount, idempotency_key, ttl_seconds, timeout)
        )

    async def settle_usage(
        self,
        hold_id: str,
        amount: int,
        *,
        idempotency_key: str | None = None,
        timeout: float | None = None,
    ) -> UsageResult:
        """Settles a hold with the real amount, from 0 to the amount held."""
        return await self._driver.run(
            self._ops.settle_usage(hold_id, amount, idempotency_key, timeout)
        )

    async def release_usage(
        self, hold_id: str, *, idempotency_key: str | None = None, timeout: float | None = None
    ) -> UsageResult:
        """Releases a hold, recording nothing. Releasing twice is safe."""
        return await self._driver.run(self._ops.release_usage(hold_id, idempotency_key, timeout))

    async def snapshot(
        self, *, ttl_seconds: int | None = None, timeout: float | None = None
    ) -> IssuedSnapshot:
        """Signs the customer's entitlements for offline use; check it with `verify_snapshot`."""
        return await self._driver.run(self._ops.snapshot(ttl_seconds, timeout))

    async def subscribe(
        self,
        plan: str,
        *,
        period: str | None = None,
        quantity: int | None = None,
        return_url: str | None = None,
        register: bool | None = None,
        idempotency_key: str | None = None,
        timeout: float | None = None,
    ) -> SubscribeStep:
        """Moves the customer to a plan, or adds an add-on, as their own choice.

        Self-serve rules apply (`403 not_self_serve` otherwise), and in an app the token needs
        `billing:self`. The answer's `next` says what is needed: `DONE`, `PAY` (send the customer
        to `url`), `CONFIRMING` or `MANAGE` (a store bills it). A declined card is an `APIError`
        (`402 payment_required`).

        Args:
            plan: The plan or add-on, by key or public id.
            period: The billing period's key, such as `monthly`.
            quantity: An add-on's quantity.
            return_url: The page the provider sends the customer back to, needed when the next
                step is a web page; an app passes a universal link or an app link.
            register: Register a customer not registered yet, if the credential may.
            idempotency_key: A key naming this purchase; the SDK generates one when left out.
            timeout: This call's per-attempt timeout in seconds.
        """
        return await self._driver.run(
            self._ops.subscribe(
                plan, period, quantity, return_url, register, idempotency_key, timeout
            )
        )

    async def cancel(
        self,
        *,
        add_on: str | None = None,
        product: str | None = None,
        idempotency_key: str | None = None,
        timeout: float | None = None,
    ) -> PlanChange:
        """Cancels the plan, or the add-on `add_on` names, as the customer's own choice.

        The project's policy decides when it takes effect. Pass at most one of `add_on` and
        `product`; `changed` is false when there was nothing to cancel.
        """
        return await self._driver.run(
            self._ops.target("", add_on, product, idempotency_key, timeout)
        )

    async def undo_pending_change(
        self,
        *,
        add_on: str | None = None,
        product: str | None = None,
        idempotency_key: str | None = None,
        timeout: float | None = None,
    ) -> PlanChange:
        """Undoes the change booked for renewal on the plan, or on the add-on `add_on` names.

        A `billing` change, which only the company books, answers `403 not_self_serve`; the
        company undoes it with `set_plan`.
        """
        return await self._driver.run(
            self._ops.target("/pending", add_on, product, idempotency_key, timeout)
        )

    async def billing_portal(
        self, *, return_url: str, timeout: float | None = None
    ) -> BillingPortal:
        """Opens the provider's page for payment details and invoices. It changes no plan.

        A customer the provider has never billed answers `404 not_found`, and an environment with
        no Stripe connection `409 stale`.
        """
        return await self._driver.run(self._ops.billing_portal(return_url, timeout))

    async def sync_billing(self, *, timeout: float | None = None) -> BillingSync:
        """Reads the customer's state from the provider now, for the page they return to.

        Call it before `plans(revalidate=True)`, so the customer sees the plan they paid for.
        """
        return await self._driver.run(self._ops.sync_billing(timeout))

    def __repr__(self) -> str:
        return f"AsyncCustomer(id={self.id!r})"


class AsyncServerCustomer(AsyncCustomer):
    """A customer as the server sees it: a `AsyncCustomer` plus registration, tokens and decisions.

    Its reads the API answers at an instant also take `as_of`, an aware `datetime`, which needs
    the organisation's `as_of` capability and shows the effects of time on the plans, grants and
    meters in place, never an unreleased change.
    """

    _ops: ServerOps

    def __init__(self, ops: ServerOps, driver: AsyncDriver) -> None:
        super().__init__(ops, driver)

    @property
    def id(self) -> str:
        """The customer's external id."""
        return self._ops.id or ""

    @overload
    async def check(
        self,
        feature: Feature[Metered],
        *,
        revalidate: bool = False,
        as_of: datetime | None = None,
        timeout: float | None = None,
    ) -> MeteredCheck: ...
    @overload
    async def check(
        self,
        feature: Feature[Boolean],
        *,
        revalidate: bool = False,
        as_of: datetime | None = None,
        timeout: float | None = None,
    ) -> BooleanCheck: ...
    @overload
    async def check(
        self,
        feature: Feature[Config],
        *,
        revalidate: bool = False,
        as_of: datetime | None = None,
        timeout: float | None = None,
    ) -> ConfigCheck: ...
    @overload
    async def check(
        self,
        feature: Feature[Group],
        *,
        revalidate: bool = False,
        as_of: datetime | None = None,
        timeout: float | None = None,
    ) -> GroupCheck: ...
    @overload
    async def check(
        self,
        feature: Feature[Any] | str,
        *,
        revalidate: bool = False,
        as_of: datetime | None = None,
        timeout: float | None = None,
    ) -> Check: ...
    async def check(
        self,
        feature: Feature[Any] | str,
        *,
        revalidate: bool = False,
        as_of: datetime | None = None,
        timeout: float | None = None,
    ) -> Check:
        """Checks one feature, through the answer cache, at `as_of` when given."""
        return await self._driver.run(self._ops.check(feature, revalidate, timeout, as_of))

    async def is_entitled(
        self,
        feature: Feature[Any] | str,
        *,
        default: bool,
        revalidate: bool = False,
        as_of: datetime | None = None,
        timeout: float | None = None,
    ) -> bool:
        """As `AsyncCustomer.is_entitled`, at `as_of` when given."""
        return await self._driver.run(
            self._ops.is_entitled(feature, default, revalidate, timeout, as_of)
        )

    async def entitlements(
        self,
        *,
        revalidate: bool = False,
        as_of: datetime | None = None,
        timeout: float | None = None,
    ) -> Entitlements[Environment]:
        """As `AsyncCustomer.entitlements`, at `as_of` when given."""
        return await self._driver.run(self._ops.entitlements(revalidate, timeout, as_of))

    async def plans(
        self,
        *,
        revalidate: bool = False,
        as_of: datetime | None = None,
        timeout: float | None = None,
    ) -> CustomerPlans:
        """As `AsyncCustomer.plans`, at `as_of` when given."""
        return await self._driver.run(self._ops.plans(revalidate, timeout, as_of))

    async def usage(
        self,
        *,
        cursor: str | None = None,
        as_of: datetime | None = None,
        timeout: float | None = None,
    ) -> CustomerUsage[AsyncUsageLog]:
        """As `AsyncCustomer.usage`, at `as_of` when given."""
        return await self._driver.run(
            self._ops.usage(cursor, timeout, self._log(timeout, as_of), as_of)
        )

    async def register(
        self,
        *,
        name: str | None = None,
        email: str | None = None,
        metadata: Mapping[str, str | None] | None = None,
        visitor: str | None = None,
        idempotency_key: str | None = None,
        timeout: float | None = None,
    ) -> RegisteredCustomer:
        """Registers the customer, or keeps their details current.

        Call it at sign-up and at sign-in with the latest details; `created` says whether this
        call created them. Pass the `visitor` they had signed out to keep their experiment arm.
        A `None` metadata value removes that key.
        """
        return await self._driver.run(
            self._ops.register(name, email, metadata, visitor, idempotency_key, timeout)
        )

    async def details(
        self,
        *,
        cursor: str | None = None,
        as_of: datetime | None = None,
        timeout: float | None = None,
    ) -> CustomerDetail[AsyncUsageLog]:
        """The customer's plans, add-ons, grants, entitlements and usage log."""

        def log(first: Page[UsageEvent]) -> AsyncUsageLog:
            return AsyncUsageLog(
                first,
                lambda cursor: self._driver.run(self._ops.details_page(cursor, timeout, as_of)),
            )

        return await self._driver.run(self._ops.details(cursor, timeout, log, as_of))

    async def update(
        self,
        *,
        name: str | None = None,
        email: str | None = None,
        metadata: Mapping[str, str | None] | None = None,
        idempotency_key: str | None = None,
        timeout: float | None = None,
    ) -> CustomerSummary:
        """Changes the customer's details; a `None` metadata value removes that key."""
        return await self._driver.run(
            self._ops.update(name, email, metadata, idempotency_key, timeout)
        )

    async def erase(
        self, *, idempotency_key: str | None = None, timeout: float | None = None
    ) -> None:
        """Erases the customer, as app stores require for account deletion.

        It ends any Stripe subscription now, then erases their details, usage and events.
        Subscriptions a store bills are invisible to Entitler: cancel them through Apple or Google
        first. Erasing twice is safe.
        """
        await self._driver.run(self._ops.erase(idempotency_key, timeout))

    async def token(
        self,
        *,
        scopes: Sequence[CustomerTokenScope] | None = None,
        ttl_seconds: int | None = None,
        timeout: float | None = None,
    ) -> IssuedCustomerToken:
        """Mints a customer token for an in-app client.

        Without `scopes` it holds `entitlements:read` alone. Ask for `usage:write` only when the
        app records usage itself, and for `billing:self` only for people who may buy for the
        customer. Mint tokens only for the signed-in customer your own session names.
        """
        return await self._driver.run(self._ops.token(scopes, ttl_seconds, timeout))

    async def set_track(
        self,
        track: str | None,
        *,
        idempotency_key: str | None = None,
        timeout: float | None = None,
    ) -> CustomerTrack:
        """Puts the customer on a track by its name, or with `None` back on All customers.

        A track's name is the same in every environment. The server key preset lacks
        `tracks:assign` (`403 scope_required`), so add that scope to the key.
        """
        return await self._driver.run(self._ops.set_track(track, idempotency_key, timeout))

    async def billing(self, *, timeout: float | None = None) -> CustomerBilling:
        """The payment provider's view, for support tools. Billing pages read `plans()`."""
        return await self._driver.run(self._ops.billing(timeout))

    @overload
    async def set_plan(
        self,
        plan: str,
        *,
        period: str | None = None,
        when: When | None = None,
        billing: PlanBilling | None = None,
        until: datetime | None = None,
        version: Literal["latest"] | None = None,
        register: bool | None = None,
        reason: str | None = None,
        actor: str | None = None,
        idempotency_key: str | None = None,
        timeout: float | None = None,
    ) -> PlanChange: ...
    @overload
    async def set_plan(
        self,
        plan: Sku,
        *,
        when: When | None = None,
        billing: PlanBilling | None = None,
        until: datetime | None = None,
        register: bool | None = None,
        reason: str | None = None,
        actor: str | None = None,
        idempotency_key: str | None = None,
        timeout: float | None = None,
    ) -> PlanChange: ...
    @overload
    async def set_plan(
        self,
        plan: None,
        *,
        product: str,
        when: When | None = None,
        billing: Literal[PlanBilling.PROVIDER, PlanBilling.END] | None = None,
        register: bool | None = None,
        reason: str | None = None,
        actor: str | None = None,
        idempotency_key: str | None = None,
        timeout: float | None = None,
    ) -> PlanChange: ...
    async def set_plan(
        self,
        plan: str | Sku | None,
        *,
        product: str | None = None,
        period: str | None = None,
        when: When | None = None,
        billing: PlanBilling | None = None,
        until: datetime | None = None,
        version: Literal["latest"] | None = None,
        register: bool | None = None,
        reason: str | None = None,
        actor: str | None = None,
        idempotency_key: str | None = None,
        timeout: float | None = None,
    ) -> PlanChange:
        """Moves the customer to any plan as the company's decision, under no self-serve rules.

        A plan is named by key or public id; a `Sku` records a verified store purchase, with
        `until` its expiry; `None` with `product` ends the customer's plan in that product.

        Args:
            plan: The plan, a store purchase's `Sku`, or `None` to end the plan in `product`.
            product: The product whose plan ends, only with `None`.
            period: The billing period's key; a SKU names its own.
            when: `When.NOW`, or `When.END` at renewal; the project's policy when left out.
            billing: `PROVIDER` (the default) charges through the provider; `KEEP` moves the
                customer while the provider keeps billing its plan; `END` ends the provider's
                subscription, as with an invoiced contract.
            until: An aware `datetime` in the future when the customer returns to the default
                plan unless renewed. While Stripe bills the product it needs `billing=END`.
            version: `"latest"` moves the customer now to the newest version of their plan.
            register: Register a customer not registered yet.
            reason: Why, at most 200 characters, for the customer's activity.
            actor: The person or system that decided, such as a support agent's id.
            idempotency_key: A key naming the event, such as a webhook delivery's id.
            timeout: This call's per-attempt timeout in seconds.

        Example:
            ```python
            customer.set_plan(
                "enterprise", period="yearly", billing=PlanBilling.END, actor="hubspot"
            )
            ```
        """
        options = {
            "product": product,
            "period": period,
            "when": when,
            "billing": billing,
            "until": until,
            "version": version,
            "register": register,
            "reason": reason,
            "actor": actor,
        }
        return await self._driver.run(self._ops.set_plan(plan, options, idempotency_key, timeout))

    async def set_add_on(
        self,
        add_on: str,
        *,
        quantity: int,
        sku: Sku | None = None,
        replaces: str | None = None,
        reason: str | None = None,
        actor: str | None = None,
        idempotency_key: str | None = None,
        timeout: float | None = None,
    ) -> PlanChange:
        """Adds an add-on, sets its quantity, or removes it at `0`, taking effect now.

        With `sku`, a store SKU that sells it, the store is recorded as its biller and Stripe is
        never charged (`409 billed_elsewhere` when Stripe bills the product). With `replaces`,
        the add-on it takes the place of, swapped in one write.
        """
        return await self._driver.run(
            self._ops.set_add_on(
                add_on, quantity, sku, replaces, reason, actor, idempotency_key, timeout
            )
        )

    async def grant(
        self,
        feature: Feature[Any] | str,
        *,
        value: int | Literal["unlimited"] | None = None,
        days: int | None = None,
        reason: str | None = None,
        actor: str | None = None,
        idempotency_key: str | None = None,
        timeout: float | None = None,
    ) -> GrantChange:
        """Grants a feature for `days` (0 or none for good), answering the grant so you keep its id.

        `value` is an amount from 0 to 999,999,999 or `"unlimited"`, left out for on/off features.
        """
        return await self._driver.run(
            self._ops.grant(feature, value, days, reason, actor, idempotency_key, timeout)
        )

    async def revoke_grant(
        self,
        grant_id: str,
        *,
        reason: str | None = None,
        actor: str | None = None,
        idempotency_key: str | None = None,
        timeout: float | None = None,
    ) -> GrantChange:
        """Revokes a grant, and answers it."""
        return await self._driver.run(
            self._ops.revoke_grant(grant_id, reason, actor, idempotency_key, timeout)
        )

    async def adjust_meter(
        self,
        feature: Feature[Metered],
        *,
        idempotency_key: str,
        by: int | None = None,
        to: int | None = None,
        reason: str | None = None,
        actor: str | None = None,
        timeout: float | None = None,
    ) -> UsageResult:
        """Moves a meter `by` an amount other than 0, or sets it `to` an amount; never below 0.

        `by=-500` gives back 500 without racing new usage. The key is required, so a support
        tool's double submit applies once.
        """
        return await self._driver.run(
            self._ops.adjust_meter(feature, by, to, reason, actor, idempotency_key, timeout)
        )

    async def cancel_usage(
        self,
        usage_id: str,
        *,
        reason: str | None = None,
        actor: str | None = None,
        idempotency_key: str | None = None,
        timeout: float | None = None,
    ) -> UsageResult:
        """Cancels a usage report, a correction answered with outcome `cancelled`."""
        return await self._driver.run(
            self._ops.cancel_usage(usage_id, reason, actor, idempotency_key, timeout)
        )

    def __repr__(self) -> str:
        return f"AsyncServerCustomer(id={self.id!r})"
