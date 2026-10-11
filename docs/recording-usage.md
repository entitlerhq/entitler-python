# Recording usage

Usage is recorded on metered features, in whole amounts of the feature's unit from 1 to
9,007,199,254,740,991. Usage methods take only a `Feature[Metered]`, so a plain key goes through
`Feature.metered("ai_credits")`.

## Idempotency keys name the event

`record_usage`, `hold_usage`, `start_hold` and `with_hold` require `idempotency_key`. Take it from
your own unit of work: this chat message, this export request, this webhook delivery. The SDK never
makes one up for these calls, because a generated key protects only its own retries: a retry from
the browser, another worker or a restart would charge twice.

Entitler keeps each key per customer and never frees it, so a key names an event, never an object
whose state changes. `export-{document.id}` is wrong: the second export of the same document
replays the first answer and records nothing. `export-{request.id}` is right. Reusing a key for a
different request answers `422 idempotency_mismatch`.

## Modes

```python
from entitler import UsageMode, UsageOutcome

result = customer.record_usage(features.AI_CREDITS, 3, idempotency_key="job-8812")
if result.outcome == UsageOutcome.REFUSED:
    print(f"Refused: {result.refusal}")  # not_entitled or over_allowance

customer.record_usage(
    features.AI_CREDITS, 1200, idempotency_key="stream-77", mode=UsageMode.OBSERVE
)
```

- `UsageMode.GATE` (the default) records only if the amount fits the allowance; otherwise it
  records nothing and answers `refused`. Use it before work that must not start without
  allowance.
- `UsageMode.OBSERVE` always records the amount: the work already happened (streamed tokens,
  bandwidth, minutes). The meter may pass the allowance, and `over_by` says by how much.

A refusal is an answer, not an error, and stores no key, so the same key can be sent again once
there is allowance. A replay of a key records nothing and answers `duplicate` (or `cancelled`,
`settled` or `released`) with `replayed` true.

`occurred_at` (an aware `datetime`) counts usage in the period it happened. Entitler refuses an
instant more than the project's offline days back, more than five minutes ahead, or before the
customer registered (`400 invalid_occurred_at`). `register=True` registers a new customer with the
report.

## Holds: work whose cost is known afterwards

A hold reserves an estimate before the work starts and settles the real amount afterwards.

### `start_hold` for streamed work

```python
from entitler import UsageRefusedError, UsageReplayedError


def stream_chat(message_id: str, prompt: str) -> None:
    try:
        hold = customer.start_hold(features.AI_CREDITS, 2000, idempotency_key=message_id)
    except UsageRefusedError as refused:
        print(f"Upgrade to continue: {refused.result.upgrades}")
        return
    except UsageReplayedError:
        print("This message was already answered.")
        return
    with hold:
        hold.use(0)
        total = 0
        for chunk in prompt.split():
            total += len(chunk)
            hold.use(total)
        hold.finish()
```

`start_hold` answers a `Hold` before any work starts, so a web route can answer a refusal before
it starts streaming. Then:

- `hold.use(n)` reports the total used so far, with no request; a later call replaces an earlier
  one. A stream calls `use(0)` before it starts, so a failure that used nothing frees the whole
  hold.
- `hold.finish()` ends the work however it ended and charges what happened: it settles the amount
  reported (the held amount when nothing was reported). Above the held amount, the excess is
  recorded in `observe` mode under the key plus `:excess`; when the hold expired first, the whole
  amount is recorded that way. When settling fails after its retries it raises
  `UsageSettlementError` with `hold_id`, `amount` and `excess`: keep the output, call
  `settle_usage(hold_id, amount)` before the hold expires, and record the excess under the same
  `:excess` key.
- `hold.release()` frees the hold, recording nothing, for work that never ran.
- Leaving `with hold:` when neither ran settles the amount reported, else releases, and sends a
  failure to `on_error` rather than raising over the body's own exception.
- Only the first of the three acts; later calls answer its result without a request.
- `hold.duplicate` is true when another caller holds the same key and may be doing the same work,
  so the app can wait for that result instead of paying for it twice.

The async hold settles under `anyio.move_on_after(timeout, shield=True)`, so a cancelled task (a
client that disconnected) still settles or releases before the cancellation propagates: a
disconnect after 3,000 tokens charges for 3,000 tokens.

The `:excess` suffix limits these keys to 193 characters.

### `with_hold` for work inside one call

```python
def summarise(job_id: str, text: str) -> str:
    with customer.with_hold(features.AI_CREDITS, 500, idempotency_key=job_id) as hold:
        summary = text[:200]
        hold.use(len(summary.split()))
    return summary
```

It raises `UsageRefusedError` or `UsageReplayedError` on entry, so the body never runs after a
refusal or a replay. A clean exit calls `finish()`; an exception disposes of the hold and
re-raises. `with_hold` keeps the accounting exactly once, not the work: apps whose work must run
once coordinate it themselves, reading `hold.duplicate`.

### Holds settled elsewhere

`hold_usage` answers the hold's `UsageResult` (with `hold_id`) for a hold another process settles
with `settle_usage(hold_id, amount)` or frees with `release_usage(hold_id)`. Holds last
`ttl_seconds` (1 to 3600, default 300) and expire on their own, recording nothing.

### A live meter

While a hold is open, `remaining` drops by the whole hold and rises when it settles. Show `held`
apart in a live meter, so a customer never sees credits vanish and come back:

```python
meter = customer.check(features.AI_CREDITS)
print(f"{meter.used} used, {meter.held} reserved, {meter.remaining} left")
```

## Batches from the server

```python
from entitler import UsageEventInput

events = [
    UsageEventInput(
        customer="user_1", feature=features.AI_CREDITS, amount=40, idempotency_key="e1"
    ),
    UsageEventInput(
        customer="user_2", feature=features.AI_CREDITS, amount=12, idempotency_key="e2"
    ),
]
batch = server.record_usage_batch(events)
retry = [events[r.index] for r in batch.results if r.outcome == "error"]
```

`record_usage_batch` records in `observe` mode, 500 events per request, and never raises: an event
the SDK refuses itself, or one whose request failed after its retries, answers `error` with a code
and message. Each request's key derives from its events, so resending the events answered `error`
is always safe.

## Rate limits and devices

Reports and holds sent by the in-app client count towards 100 every 10 seconds per customer,
shared by all of that customer's devices (`429 rate_limited`, retried as [reliability](reliability.md)
describes); settlements and releases do not count. Server writes are not limited this way.

Usage counted on a customer's device is advisory: a modified app can skip the call. Only work done
on your server can be enforced.

## Corrections

The company corrects meters with `adjust_meter` and `cancel_usage`; see
[company decisions](company-decisions.md).
