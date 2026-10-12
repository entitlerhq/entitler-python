"""The errors the SDK raises."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._models import UsageResult


class ErrorCode(StrEnum):
    """Every error code the Entitler API documents, plus the SDK's own.

    `HTTP_ERROR` marks an answer without a readable error body, `INVALID_RESPONSE` a 2xx answer
    the SDK cannot read, and `CONNECTION_FAILED` and `TIMED_OUT` appear only on batch event
    results and in the command-line tool's failure line. A code this version does not know stays
    a plain `str`, so compare with `==`.
    """

    ALLOWANCE_REACHED = "allowance_reached"
    """An organisation's allowance is used up."""
    ALREADY_CONNECTED = "already_connected"
    """Another connection in the environment already holds that payment account and mode."""
    AS_OF_NOT_ALLOWED = "as_of_not_allowed"
    """The request cannot be answered at another instant."""
    BILLED_ELSEWHERE = "billed_elsewhere"
    """Stripe bills the product, so a store SKU cannot be recorded for it; nothing changed."""
    BODY_TOO_LARGE = "body_too_large"
    """The request body is larger than Entitler accepts."""
    BROWSER_NOT_ALLOWED = "browser_not_allowed"
    """The credential may not be used from a browser."""
    CAP_REACHED = "cap_reached"
    """A fixed cap is reached, such as the organisations one person may own."""
    CAPABILITY_REQUIRED = "capability_required"
    """The organisation's plan lacks the capability, such as the customer portal or `as_of`."""
    CARRY_FORWARD_CONFLICT = "carry_forward_conflict"
    """A change and a newer release changed the same field, or together break a catalogue rule."""
    CATALOGUE_NOT_EMPTY = "catalogue_not_empty"
    """The catalogue already holds plans or features."""
    CHANGE_LOCKED = "change_locked"
    """Another open change edits the same plan."""
    CHANGE_REQUIRED = "change_required"
    """The edit must be made in an open change."""
    CONNECTION_FAILED = "connection_failed"
    """No answer arrived: on batch event results and in the command-line tool only."""
    CONNECTION_IN_USE = "connection_in_use"
    """The payment provider connection still bills customers."""
    CONNECTION_MODE = "connection_mode"
    """The payment provider key's mode does not suit the environment."""
    CONNECTION_REQUIRED = "connection_required"
    """The action needs a payment provider connection."""
    CONNECTION_UNREADABLE = "connection_unreadable"
    """Entitler cannot read the key of the connection that bills the customer."""
    CREDENTIAL_NOT_ALLOWED = "credential_not_allowed"
    """The route refuses this kind of credential; it is never retried or refreshed."""
    CUSTOMER_NOT_FOUND = "customer_not_found"
    """The customer is not registered; pass `register=True` where a write takes it."""
    EMAIL_TAKEN = "email_taken"
    """An account already uses that email address."""
    EMAIL_UNCONFIRMED = "email_unconfirmed"
    """The email address is not confirmed yet."""
    EXPERIMENT_RUNNING = "experiment_running"
    """An experiment is running where the change would apply."""
    FEATURE_NOT_FOUND = "feature_not_found"
    """The customer's catalogue has no feature with that key."""
    HOLD_EXPIRED = "hold_expired"
    """The hold expired before it was settled."""
    HOLD_RELEASED = "hold_released"
    """The hold was released, so it cannot be settled."""
    HOLD_SETTLED = "hold_settled"
    """The hold was settled, with another amount when settling again."""
    HTTP_ERROR = "http_error"
    """The SDK's own: an error answer that names no code, and every redirect."""
    IDEMPOTENCY_KEY_REQUIRED = "idempotency_key_required"
    """The write needs an `Idempotency-Key`."""
    IDEMPOTENCY_MISMATCH = "idempotency_mismatch"
    """The idempotency key was already used for a different request."""
    INTERNAL = "internal"
    """Entitler failed; the client retries it."""
    INVALID_AMOUNT = "invalid_amount"
    """An amount is outside the allowed range."""
    INVALID_BODY = "invalid_body"
    """The request body breaks the API's rules, such as settling more than a hold holds."""
    INVALID_IDEMPOTENCY_KEY = "invalid_idempotency_key"
    """The idempotency key is not 1 to 200 printable ASCII characters."""
    INVALID_OCCURRED_AT = "invalid_occurred_at"
    """`occurred_at` is too far back, too far ahead, or before the customer registered."""
    INVALID_PATH = "invalid_path"
    """A path parameter is not valid."""
    INVALID_RESPONSE = "invalid_response"
    """The SDK's own: a 2xx answer this SDK cannot read."""
    LAST_ENVIRONMENT = "last_environment"
    """A project's last environment cannot be removed."""
    LIMIT_REACHED = "limit_reached"
    """The plan's limit is reached, such as its number of open tracks."""
    LISTING_GAPS = "listing_gaps"
    """The store and provider listings leave gaps the change did not accept."""
    LISTING_INVALID = "listing_invalid"
    """A listing fails the provider's checks."""
    METHOD_NOT_ALLOWED = "method_not_allowed"
    """The route does not take this HTTP method."""
    NOT_FOUND = "not_found"
    """Nothing has that id, such as a hold, a grant or an add-on the customer holds."""
    NOT_LISTED = "not_listed"
    """The plan has no listing on that sales channel, so it cannot be bought there."""
    NOT_METERED = "not_metered"
    """Usage is recorded only on metered features."""
    NOT_SELF_SERVE = "not_self_serve"
    """The customer cannot make this move alone; the company makes it with `set_plan`."""
    PAYMENT_REQUIRED = "payment_required"
    """The change waits on a payment; `APIError.payment` says which."""
    PERSON_REQUIRED = "person_required"
    """Only a person, or a key that records who minted it, may do this."""
    PLAN_STILL_BILLED = "plan_still_billed"
    """The payment provider still bills the customer's plan."""
    PROVIDER_ACCOUNT_CHANGED = "provider_account_changed"
    """A new payment provider key is for another account or mode."""
    PROVIDER_PARTIAL = "provider_partial"
    """The payment provider finished only part of the work, such as during an erase."""
    PUBLICATION_FAILED = "publication_failed"
    """Publishing to a sales channel failed."""
    RATE_LIMITED = "rate_limited"
    """Too many requests; the client retries after `Retry-After`."""
    REGISTRATION_CLOSED = "registration_closed"
    """The sign-in provider does not let people register themselves."""
    RETURN_URL_REQUIRED = "return_url_required"
    """The next step is a web page, so the call needs `return_url`."""
    REVIEW_REQUIRED = "review_required"
    """The rollout waits for a second person's review."""
    SCOPE_REQUIRED = "scope_required"
    """The credential lacks the scope the call needs."""
    SIGN_UPS_CLOSED = "sign_ups_closed"
    """Entitler is not taking sign-ups."""
    SNAPSHOT_KEY_UNAVAILABLE = "snapshot_key_unavailable"
    """No snapshot signing key is available."""
    STALE = "stale"
    """The data changed since it was read; from `billing_portal`, no provider is connected."""
    SWITCH_IN_USE = "switch_in_use"
    """A switch cannot be turned off while something uses it."""
    SWITCH_NEEDED = "switch_needed"
    """The project needs a switch turned on first."""
    SWITCH_OFF = "switch_off"
    """The project's switch for this is off."""
    TIMED_OUT = "timed_out"
    """No answer arrived in time: on batch event results and in the command-line tool only."""
    TOO_MANY_CUSTOMERS = "too_many_customers"
    """The request names more customers than one call takes."""
    TRACK_CLOSED = "track_closed"
    """The track is closed to new customers."""
    UNAUTHORISED = "unauthorised"
    """The key or token is missing, unknown or expired."""
    UNAVAILABLE = "unavailable"
    """Entitler is briefly unavailable; the client retries after `Retry-After`."""


def error_code(raw: str) -> ErrorCode | str:
    """Answers the known code for `raw`, or `raw` itself when this version does not know it."""
    try:
        return ErrorCode(raw)
    except ValueError:
        return raw


class PaymentStatus(StrEnum):
    """Why a payment a plan change waits on has not succeeded yet."""

    DECLINED = "declined"
    """The payment method was declined."""
    REQUIRES_ACTION = "requires_action"
    """The customer must confirm the payment, at `url`."""
    PROCESSING = "processing"
    """The provider is processing the payment."""
    PENDING = "pending"
    """The payment is pending, such as a bank debit."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Payment:
    """The payment a plan change waits on, from a `402 payment_required` answer."""

    status: PaymentStatus
    """Why the payment has not succeeded yet."""
    url: str | None
    """The provider's page where the customer pays or confirms the payment, or `None`."""


class EntitlerError(Exception):
    """The base class of every error the SDK raises, so one `except` catches them all.

    Argument errors are `ValueError` or `TypeError`, and a closed client raises `RuntimeError`:
    neither is an `EntitlerError`.
    """


class APIError(EntitlerError):
    """The API answered with a status other than 2xx, or with a 2xx answer the SDK cannot read.

    API messages are written for the developer, not for end users: map `code` to your own copy.

    Example:
        ```python
        try:
            customer.subscribe("pro", period="monthly")
        except APIError as error:
            if error.code == ErrorCode.NOT_SELF_SERVE:
                show_contact_sales()
            else:
                raise
        ```
    """

    status: int
    """The HTTP status."""
    code: ErrorCode | str
    """The API's error code, `http_error` without a readable error body, or `invalid_response`."""
    message: str
    """The API's message, or one naming the status."""
    request_id: str | None
    """The `x-request-id` header, for Entitler support."""
    retry_after: float | None
    """How long the API asked to wait before trying again, in seconds."""
    idempotency_key: str | None
    """The idempotency key the request sent, so the call can be repeated later with it."""
    payment: Payment | None
    """The payment a plan change waits on, from `402 payment_required`."""

    def __init__(
        self,
        *,
        status: int,
        code: ErrorCode | str,
        message: str,
        request_id: str | None = None,
        retry_after: float | None = None,
        idempotency_key: str | None = None,
        payment: Payment | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.request_id = request_id
        self.retry_after = retry_after
        self.idempotency_key = idempotency_key
        self.payment = payment

    def __repr__(self) -> str:
        return f"APIError(status={self.status}, code={str(self.code)!r}, message={self.message!r})"


class EntitlerConnectionError(EntitlerError):
    """No answer arrived: DNS, TLS, a connection refused or reset, or an answer cut off.

    The cause is a sanitised summary of the HTTP library's error, never the request it held.
    """

    idempotency_key: str | None
    """The idempotency key the request sent, when it sent one."""

    def __init__(self, message: str, *, idempotency_key: str | None = None) -> None:
        super().__init__(message)
        self.idempotency_key = idempotency_key


class EntitlerTimeoutError(EntitlerError):
    """An attempt took longer than the client's or the call's `timeout`."""

    idempotency_key: str | None
    """The idempotency key the request sent, when it sent one."""

    def __init__(self, message: str, *, idempotency_key: str | None = None) -> None:
        super().__init__(message)
        self.idempotency_key = idempotency_key


class TokenError(EntitlerError):
    """A token provider failed, or answered a blank or unreadable token.

    The provider's own error is chained as `__cause__`, as it was raised: its contents are outside
    the SDK's control.
    """


class SnapshotErrorCode(StrEnum):
    """Why a snapshot failed verification."""

    SNAPSHOT_INVALID = "snapshot_invalid"
    """The snapshot is malformed, signed by an unknown key, changed, or for someone else."""
    SNAPSHOT_EXPIRED = "snapshot_expired"
    """The snapshot has expired; fetch a new one while online."""


class SnapshotError(EntitlerError):
    """A snapshot failed verification. No request was made, so there is no HTTP status."""

    code: SnapshotErrorCode
    """`SNAPSHOT_EXPIRED` once the snapshot's `exp` has passed, else `SNAPSHOT_INVALID`."""
    message: str
    """What failed, and what to do about it."""

    def __init__(self, code: SnapshotErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class UsageRefusedError(EntitlerError):
    """Raised only by `start_hold` and `with_hold` when Entitler refuses the hold.

    The customer has no allowance for the work, so none of it runs. Everywhere else a refusal is
    an answer, not an error.
    """

    result: UsageResult
    """The refused answer, with its `refusal`, the meter and `upgrades`."""

    def __init__(self, result: UsageResult) -> None:
        if result.refusal == "not_entitled":
            message = f"The customer is not entitled to {result.feature}."
        else:
            message = f"The customer has too little {result.feature} left for {result.amount}."
        super().__init__(message)
        self.result = result


class UsageReplayedError(EntitlerError):
    """Raised only by `start_hold` and `with_hold` when the key replays a finished hold.

    The hold was already settled, released or expired: the work this key stands for already
    happened, so show that work's result, never an upgrade prompt.
    """

    result: UsageResult
    """The replayed answer."""

    def __init__(self, result: UsageResult) -> None:
        super().__init__(
            "This idempotency key's hold was already settled, released or expired. "
            "Use a new key for new work."
        )
        self.result = result


class UsageSettlementError(EntitlerError):
    """Raised by a hold's `finish()` when settling it or recording the excess fails.

    Keep the work's output, settle again with `settle_usage(hold_id, amount)` before the hold
    expires, and record `excess` in observe mode under the hold's key plus `:excess`. The cause is
    chained as `__cause__`.
    """

    hold_id: str
    """The hold that is still open."""
    amount: int
    """The amount to settle it with."""
    excess: int | None
    """The excess over the hold still to record in observe mode, or `None`."""

    def __init__(self, *, hold_id: str, amount: int, excess: int | None) -> None:
        super().__init__(
            f"Entitler could not settle hold {hold_id}. Settle it again with settle_usage()."
        )
        self.hold_id = hold_id
        self.amount = amount
        self.excess = excess


def stands_in(error: BaseException) -> bool:
    """True for the failures a kept answer may stand in for."""
    if isinstance(error, EntitlerConnectionError | EntitlerTimeoutError):
        return True
    return isinstance(error, APIError) and (
        error.status == 429 or error.status >= 500 or error.code == ErrorCode.INVALID_RESPONSE
    )


def is_unreachable(error: BaseException) -> bool:
    """True when `error` means Entitler could not be reached.

    That is an `EntitlerConnectionError`, an `EntitlerTimeoutError`, a `TokenError` (a token
    provider failing, offline typically), and an `APIError` with status 429, a 5xx status or code
    `invalid_response`. An offline-capable app falls back to its snapshot when it is true.

    Example:
        ```python
        try:
            check = customer.check(features.EXPORT_PDF)
        except EntitlerError as error:
            if not is_unreachable(error):
                raise
            snapshot = verify_snapshot(saved, keys=keys, customer=user_id, environment=env_id)
            entitled = snapshot.entitlements.has(features.EXPORT_PDF)
        ```
    """
    return isinstance(error, TokenError) or stands_in(error)
