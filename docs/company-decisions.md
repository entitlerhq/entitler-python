# Company decisions

`ServerCustomer` adds the changes your company makes: for a deal, a support ticket or a verified
store purchase. They follow no self-serve rules, so sales-led plans are open to them, and they need
`customers:write`.

Each takes `actor=` (1 to 200 characters naming the person or system that decided, such as a
support agent's id or `hubspot`) and `reason=` (up to 200 characters), which the customer's
activity shows beside the key's name.

## Keys come from the event

Every write takes `idempotency_key=`. Take it from the event that caused the write: the CRM's
deal-won event id, the ticket's action id, the webhook delivery id. Without one the SDK sends a
random key, which protects only its own retries. A key is never freed, so it names an event, never
an object: a deal won a second time after churning needs a new key, or its write replays the first
and moves nobody. `adjust_meter` requires the key, since a support tool's double submit of `by`
would otherwise apply twice.

## `set_plan`

```python
from entitler import PlanBilling, When

customer.set_plan(
    "enterprise",
    period="yearly",
    billing=PlanBilling.END,
    actor="hubspot",
    reason="Deal 4471 won",
    idempotency_key="hubspot-deal-won-4471",
)
```

- **The plan**: a plan's key or id; a `Sku` a store sold ([store purchases](store-purchases.md));
  or `None` with `product=` to end the customer's plan in that product (they move to its default
  plan, or hold none).
- **`when=`**: `When.NOW` or `When.END` (at renewal); the project's policy when left out.
- **`billing=`**:
  - `PlanBilling.PROVIDER` (the default) charges the change through the provider that bills the
    product;
  - `PlanBilling.KEEP` moves the customer in Entitler while the provider keeps billing what it
    bills, as a deliberate exception such as a goodwill upgrade, until a later `set_plan`;
  - `PlanBilling.END` ends the provider's subscription when the change takes effect, and the
    customer holds the plan outside any provider, as with an invoiced contract.
- **`until=`**: an aware `datetime` in the future. At `until` the customer returns to the product's
  default plan unless a later `set_plan` moved them or set a later `until`. While Stripe bills the
  product it needs `billing=PlanBilling.END`.
- **`version="latest"`** moves the customer now to the newest version of the plan they hold,
  ending a grandfathered version.

`set_plan` answers a `PlanChange`. Naming the plan the customer already holds, with the same
period, billing and `until`, changes nothing and answers `changed` false, so a redelivered webhook
is harmless even with a fresh key. Naming it does undo a change booked for renewal; while a change
is booked, a move to another plan is refused with `400 invalid_body` until that undo.

## Add-ons

```python
customer.set_add_on("extra_seats", quantity=150, actor="agent_7", reason="Ticket 991")
customer.set_add_on("extra_seats", quantity=0)  # removes it
customer.set_add_on("support_premium", quantity=1, replaces="support_standard")
```

Quantity runs from 0 to 10,000 and takes effect now. `sku=` records a store as the add-on's biller
(`409 billed_elsewhere` when Stripe bills the product).

## Grants

```python
change = customer.grant(features.SSO, days=30, actor="agent_7", reason="Trial for ticket 4521")
grant_id = change.grant.id  # keep it to revoke the grant later
customer.grant(features.TEAM_SEATS, value=25, days=0)  # 0 days: no end
customer.revoke_grant(grant_id)
```

`value` is a whole number (0 to 999,999,999) or `"unlimited"`, left out for on/off features.
`days` runs from 0 to 3650, 0 meaning no end.

## Meter corrections

```python
customer.adjust_meter(
    features.AI_CREDITS, by=-500, idempotency_key="ticket-4521-refund", reason="Failed batch"
)
customer.adjust_meter(features.AI_CREDITS, to=0, idempotency_key="ticket-4522-reset")
customer.cancel_usage("usg_8f2k")
```

`adjust_meter` takes exactly one of `by` (any whole number but 0: `-500` gives back 500 credits
without racing new usage) and `to` (0 or more). The meter never goes below 0. `cancel_usage`
cancels one report and moves the meter back.

## Erasing a customer

`erase()` is the account deletion app stores require: it ends a Stripe subscription in the same
call and erases the customer's details, usage and events. Erasing twice is safe. Store
subscriptions are invisible to Entitler, so cancel them through Apple or Google first.
