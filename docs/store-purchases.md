# Store purchases

Apps whose plans the App Store or Google Play bills never call `subscribe` or use a return URL:
most storefronts forbid card charges and web checkout for digital goods. The app buys the `skus`
that [`plans()`](billing.md) lists through StoreKit or Play Billing, and your server records each
verified purchase with `set_plan`. Clients never record purchases.

A store SKU never touches Stripe; when Stripe already bills that product, Entitler refuses with
`409 billed_elsewhere` and changes nothing.

## The recipe

1. The app reads `plans()`. Each `MoveOption` lists `skus`: the `connector` (`apple`, `google`),
   the store's `ids` and the `period`.
2. The app buys through the store and sends the transaction to your server.
3. Your server verifies the transaction with the store (the App Store Server API, or the Google
   Play Developer API), never trusting the app's word.
4. Your server records it with the transaction's expiry as `until`, keyed by the transaction:

```python
from datetime import datetime

from entitler import ServerCustomer, Sku


def record_purchase(
    customer: ServerCustomer, product_id: str, transaction_id: str, expires: datetime
) -> None:
    customer.set_plan(
        Sku(connector="apple", ids={"productId": product_id}),
        until=expires,
        actor="app-store",
        idempotency_key=f"apple-{transaction_id}",
    )
```

A SKU names its own period, so `set_plan` takes no `period=` with one. For Google Play the
connector is `google`, with the ids the `skus` entry lists.

## Renewals, expiry and refunds

- **Renewals**: each store renewal notification (App Store Server Notifications `DID_RENEW`, Google
  Real-time developer notifications `SUBSCRIPTION_RENEWED`) carries a new transaction and expiry.
  Verify it and call `set_plan` again with the new `until`, keyed by the new transaction.
- **Expiry**: nothing to do. At `until` the customer returns to the product's default plan, so a
  missed notification ends the plan rather than keeping it for ever.
- **Refunds and revocations** (`REFUND`, `REVOKE`, `SUBSCRIPTION_REVOKED`): end the plan now with
  `set_plan(None, product=..., when=When.NOW)`, keyed by the notification.

## Account deletion

`erase()` ends a Stripe subscription, but Entitler cannot see store subscriptions. Before erasing a
customer who bought through a store, have them cancel in the store's subscription settings, or
cancel through the store's server API where it allows that.
