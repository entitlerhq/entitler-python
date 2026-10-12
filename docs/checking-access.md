# Checking access

Every customer, on either client, answers the same reads: `check`, `is_entitled`, `entitlements`
and `plans`. They go through the answer cache (see [reliability](reliability.md)), so most checks
answer without a request.

## `is_entitled`: a yes or no that never fails

```python
if customer.is_entitled(features.EXPORT_PDF, default=False):
    ...  # render the export
```

`is_entitled` answers `check(feature).entitled`, a stale answer included. When the check fails for
any reason (Entitler unreachable with nothing kept, a token provider failing, a feature key the
catalogue lacks), it answers `default` and passes the error to `on_error`. It never raises because
of Entitler; only argument errors (a blank key, an invalid `timeout`) and cancellation propagate.

`default` is keyword-only and required, because it is a decision:

- **Fail closed** (`default=False`) for paid features: an outage gives nothing away.
- **Fail open** (`default=True`) only where losing a sale is worse than giving the feature away,
  such as a feature a customer is in the middle of using.

## `check`: the whole answer

```python
from entitler import MeteredCheck

credits: MeteredCheck = customer.check(features.AI_CREDITS)
print(credits.entitled, credits.value, credits.used, credits.held, credits.remaining)

seats = customer.check(features.TEAM_SEATS)
if seats.value == "unlimited" or (isinstance(seats.value, int) and seats.value > 10):
    ...
```

A typed feature constant narrows the answer: `Feature[Metered]` answers a `MeteredCheck`,
`Feature[Config]` a `ConfigCheck`, `Feature[Boolean]` a `BooleanCheck` and `Feature[Group]` a
`GroupCheck`. A plain key answers a `Check`.

- `entitled` is Entitler's decision. For a metered feature it means allowance remains: `remaining`
  is above 0 or `"unlimited"`.
- `value` is `True` for an on/off feature, a whole amount (`0` included) or `"unlimited"`. Test
  `value is True` before comparing amounts, since `True == 1` in Python.
- `sources` says where the value comes from: the plan, an add-on, a grant, banked usage or a group.
- `upgrades` lists the moves that would grant the feature, each with `move` and `action`, the same
  rule [`plans()`](billing.md) uses, so the "Upgrade" button on a refused export and the paywall
  agree.
- `stale` is true when the answer came from the cache while Entitler was unreachable.

## Every feature at once

```python
entitlements = customer.entitlements()
if entitlements.has(features.SSO):
    ...
team_seats = entitlements.get(features.TEAM_SEATS)
for entitlement in entitlements:
    print(entitlement.key, entitlement.value)
```

## Showing a change at once: `revalidate`

A kept answer is fresh for its `max-age`. After something changes the customer (back from paying,
an upgrade made on your server), pass `revalidate=True`: the client skips the fresh entry and asks
Entitler with its `ETag`, and a `304` answers the kept body without sending it again.

```python
customer.plans(revalidate=True)
customer.check(features.EXPORT_PDF, revalidate=True)
```

Writes through the same client already mark the customer's kept answers for revalidation, so a
read after `record_usage` or `subscribe` never answers from before the write.

## Customers not registered yet

Every read answers an unregistered customer from the default plan, so a check before `register()`
is safe. Writes to an unregistered customer answer `404 customer_not_found`, except
`record_usage`, `subscribe` and `set_plan` given `register=True`.
