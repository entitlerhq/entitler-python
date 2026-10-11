# Errors

Every failure the SDK raises is an `EntitlerError`, in one of these kinds:

| Error | When | Carries |
| --- | --- | --- |
| `APIError` | Entitler answered with an error status, or with an answer this SDK cannot read | `status`, `code`, `message`, `request_id`, `retry_after` (seconds), `idempotency_key`, `payment` |
| `EntitlerConnectionError` | no answer arrived (DNS, TLS, a refused or reset connection) | the cause, `idempotency_key` |
| `EntitlerTimeoutError` | an attempt passed its timeout | `idempotency_key` |
| `TokenError` | a token provider failed or answered an unusable token | the cause, never the token |
| `SnapshotError` | a snapshot failed verification | `code`: `snapshot_invalid` or `snapshot_expired` |
| `UsageRefusedError` | `start_hold` or `with_hold` was refused | `result`, the `UsageResult` |
| `UsageReplayedError` | a hold's key replays a hold already settled, released or expired | `result` |
| `UsageSettlementError` | a hold's `finish()` could not settle after its retries | `hold_id`, `amount`, `excess` |

Causes chain with `raise ... from`, so `error.__cause__` holds the underlying failure.

Arguments the SDK can check itself (a blank key, an idempotency key that is too long, a naive
`datetime`) raise `ValueError` or `TypeError` before any request. A closed client raises
`RuntimeError`. Cancellation is never wrapped.

## Codes

`APIError.code` is an `ErrorCode` (a `StrEnum`) for every code Entitler documents, or the raw string
for a code this version does not know, so a new code never breaks your `match`:

```python
from entitler import APIError, ErrorCode


def upgrade_or_explain(customer_id: str) -> str:
    try:
        step = server.customer(customer_id).subscribe("pro", period="monthly")
    except APIError as error:
        match error.code:
            case ErrorCode.RETURN_URL_REQUIRED:
                return "Pass a return URL: Stripe bills this customer."
            case ErrorCode.NOT_SELF_SERVE:
                return "This move is not one the customer can make alone."
            case ErrorCode.PAYMENT_REQUIRED if error.payment is not None:
                return f"Payment {error.payment.status}."
            case _:
                raise
    return str(step.next)
```

Besides Entitler's own codes, the SDK uses `http_error` (an error status whose body names no code,
and every `3xx`, since the clients never follow redirects), `invalid_response` (an answer that is
not JSON or lacks a field Entitler documents; its message is `Entitler sent an answer this SDK
cannot read.` and its cause names the failing path, never the body), and `connection_failed` and
`timed_out`, which appear only on batch results and in the command-line tool's failure line
(`Entitler request failed: Entitler could not be reached (connection_failed).`).

API messages are written for you, the developer, not for your customers: log them, and show
customers copy of your own.

## Payments

On a `402 payment_required`, `error.payment` holds `status` (`declined`, `requires_action`,
`processing` or `pending`) and the provider's `url`, when Entitler sends one. `subscribe` answers a
confirmation or a pending payment as a [next step](billing.md), so its `402` is a declined card;
`set_plan` charging through the provider can answer any of them.

## Is Entitler reachable?

`is_unreachable(error)` is true for a connection failure, a timeout, a `TokenError`, and an
`APIError` with status `429`, a `5xx` or code `invalid_response`. An app that works offline falls
back to its [snapshot](offline-snapshots.md) when it is true.

## Codes by call

| Call | Codes it can answer |
| --- | --- |
| any call | `401 unauthorised`, `403 scope_required`, `403 credential_not_allowed`, `429 rate_limited`, and `5xx` answers such as `internal` and `unavailable` |
| writes to a customer | `404 customer_not_found`, `422 idempotency_mismatch` |
| `check` | `404 feature_not_found` |
| `record_usage`, `hold_usage`, `start_hold`, `with_hold` | `400 not_metered`, `400 idempotency_key_required`, `400 invalid_occurred_at`, `404 feature_not_found` |
| `settle_usage`, `release_usage` | `400 invalid_body` (more than the hold), `409 hold_settled`, `409 hold_released`, `409 hold_expired` |
| reads with `as_of` | `409 capability_required` |
| `register` | `403 registration_closed` |
| billing calls | see [billing pages](billing.md#errors-billing-calls-answer) |
| `set_plan` | `400 invalid_body`, `402 payment_required`, `409 billed_elsewhere` |
| `set_add_on` | `400 invalid_body`, `404 not_found`, `409 billed_elsewhere` |
