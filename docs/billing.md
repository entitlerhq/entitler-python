# Billing pages

A customer's billing choices are methods on `Customer`, on either client: `plans`, `subscribe`,
`cancel`, `undo_pending_change`, `billing_portal` and `sync_billing`. They act as the customer
choosing for themselves, under self-serve rules: a move must be one `plans()` offers along a
self-serve path, or Entitler refuses it with `403 not_self_serve`. The project's policies decide
when a change takes effect (an upgrade now, a downgrade or a cancel at renewal).

On the server these calls need `customers:write`. In an app, the customer token or identity
provider needs `billing:self`, and the organisation needs the customer portal capability
(`409 capability_required` otherwise). Mint `billing:self` only for people who may buy for the
customer: a workspace's owners, not every member. A customer credential never charges a saved
payment method, so a paid change from an app always answers `pay`.

## `plans()`: the one read

`plans()` is the paywall's and the billing page's one source. It answers what the customer holds
and every move open to them:

```python
from entitler import CustomerPlans, MoveAction


def billing_page(plans: CustomerPlans) -> None:
    for held in plans.held:
        period = held.period.label if held.period else ""
        print(f"You hold {held.plan.name} {period}")
        if held.billed_by in ("apple", "google"):
            print("  Manage it in the store's subscription settings.")
        elif held.billed_by == "stripe":
            print("  Manage payment details in the billing portal.")
        if held.pending is not None:
            print(f"  A change is booked for {held.renews_at}: {held.pending.type}")
    for option in plans.options:
        match option.action:
            case MoveAction.BUY:
                labels = ", ".join(period.label for period in option.periods)
                print(f"  [{option.move.title()} to {option.plan.name}] {labels}")
            case MoveAction.CONTACT:
                print(f"  [Talk to sales about {option.plan.name}]")
            case _:
                pass  # unavailable: option.reason says why, for you rather than the customer
```

- `action` decides the button: `buy` (the customer can make the move alone), `contact` (a
  sales-led move only the company can make) or `unavailable` (with `reason`, for the developer).
- `move` names it: `subscribe`, `upgrade`, `downgrade`, `switch`, `add` or `replace`.
- `impact` lists what the move gains, loses or changes, as display text.
- `skus` are the store products that sell the move, for apps a store bills
  ([store purchases](store-purchases.md)).
- The held plan's `billed_by` decides between the store's own management page and the billing
  portal.

## `subscribe` and its next step

```python
from entitler import Customer, NextStep


def upgrade(customer: Customer) -> str:
    step = customer.subscribe(
        "pro", period="monthly", return_url="https://app.example.com/billing/return"
    )
    match step.next:
        case NextStep.DONE:
            assert step.change is not None
            return f"Changed, effective {step.change.effective}"
        case NextStep.PAY:
            return f"Redirect to {step.url}"
        case NextStep.CONFIRMING:
            return "The bank is confirming the payment."
        case NextStep.MANAGE:
            return f"Change it in {step.billed_by}'s subscription settings."
        case _:
            return "Reload the billing page."
```

- `done`: the change is made; `step.change` is a `PlanChange` whose `effective` is `now`, or
  `renewal` with `at`.
- `pay`: send the customer to `url`, the provider's page (Stripe Checkout, or a confirmation such
  as 3-D Secure). The plan changes once they pay.
- `confirming`: the provider is still confirming a payment (a bank debit). Show it as pending.
- `manage`: a store bills the product (`billed_by` is `apple` or `google`), so the customer changes
  it there.

Name a plan or add-on by its key or id; `quantity=` sets an add-on's quantity. A declined card is
an error, not a step: `APIError` `402 payment_required` with `payment.status` `declined`.

### Return URLs

`return_url` matters only when the next step is a web page; the provider sends the customer back
to it whether they paid or left. A change that needs a page and has no `return_url` is refused with
`400 return_url_required` before anything changes, so pass one whenever Stripe may bill the
customer. Web apps pass a page of their own. A desktop app passes a page on your site that tells
the customer to return to the app, or a custom URL scheme the app handles.

### The return page

Back from the provider, call `sync_billing()` and then read with `revalidate=True`, so the customer
sees the plan they paid for without waiting for the provider's notification:

```python
def return_page(customer: Customer) -> CustomerPlans:
    customer.sync_billing()
    return customer.plans(revalidate=True)
```

`sync_billing()` answers `changed`, true when the provider's state moved the customer.

## Cancel and undo

```python
change = customer.cancel()
print(change.effective, change.at)  # renewal, and when
customer.undo_pending_change()  # keeps the plan after all
customer.cancel(add_on="sso_addon")
```

Each takes at most one of `add_on=` and `product=` and answers a `PlanChange`, whose `changed` is
false when there was nothing to cancel or undo. `undo_pending_change` cannot undo a change of who
bills the plan, which only the company books; the company undoes it with `set_plan`.

## The billing portal

The provider's own page for payment details and invoices. It changes no plan, and always needs
`return_url`:

```python
from entitler import APIError, ErrorCode


def portal_url(customer: Customer) -> str | None:
    try:
        return customer.billing_portal(return_url="https://app.example.com/billing").url
    except APIError as error:
        if error.code == ErrorCode.NOT_FOUND:
            return None  # the provider has never billed this customer
        if error.code == ErrorCode.STALE:
            return None  # this environment has no payment provider connected
        raise
```

## Errors billing calls answer

| Call | Codes |
| --- | --- |
| `plans` | `409 capability_required` (in-app, without the customer portal capability) |
| `subscribe` | `400 return_url_required`, `402 payment_required`, `403 not_self_serve`, `403 scope_required`, `404 customer_not_found` (without `register=True`), `409 capability_required` |
| `cancel`, `undo_pending_change` | `403 not_self_serve`, `403 scope_required`, `404 not_found` (an add-on not held), `409 capability_required` |
| `billing_portal` | `404 not_found` (never billed), `409 stale` (no provider connected), `403 scope_required`, `409 capability_required` |
| `sync_billing` | `403 scope_required`, `409 capability_required` |

Any call can also answer `429 rate_limited` and `503 unavailable`, which the client retries. See
[errors](errors.md).
