# Pricing pages and visitors

## A signed-out pricing page

`pricing()` answers the plans the environment sells, their periods and labels, and each sales
channel's price. On your server, read it with the server client:

```python
from entitler import Pricing


def show(pricing: Pricing) -> None:
    for plan in pricing.plans:
        if plan.kind != "plan":
            continue
        print(plan.name)
        for listing in plan.listings:
            label = listing.period.label if listing.period else "One time"
            prices = [channel.price for channel in listing.channels if channel.price]
            if prices:
                print(f"  {label}: {prices[0].amount / 100:.2f} {prices[0].currency.upper()}")
            else:
                print(f"  {label}: Coming soon")


show(server.pricing(visitor=server.new_visitor_id()))
```

Amounts are whole numbers in the currency's smallest unit, and `currency` is the ISO 4217 code in
lower case. A period's `key` (`monthly`, `yearly`) stays the same when its `label` is renamed or
translated: send the key, show the label.

An app with no backend route for pricing reads it with a publishable key alone. Publishable keys
are public by design; they read pricing and nothing else:

```python
from entitler import EntitlerClient

with EntitlerClient(key="ent_pk_test_...") as pricing_client:
    show(pricing_client.pricing())
```

A signed-in customer's own pricing is `customer.pricing()` (`client.me.pricing()` in an app),
which reflects the plans they hold.

## Visitors

An experiment shows each visitor one arm. The visitor id keeps that arm the same on every page and
after the visitor signs up. It matches `VISITOR_ID_PATTERN` (16 to 64 letters, digits, `-` and
`_`), and `new_visitor_id()` makes one (24 random bytes, base64url, 32 characters).

**On a server**, the client never keeps a visitor, since one server serves many people. Mint one on
the first visit, keep it in a first-party cookie, and pass it to every pricing read:

```python
from entitler import VISITOR_ID_PATTERN, new_visitor_id


def visitor_from_cookie(cookie: str | None) -> str:
    if cookie and VISITOR_ID_PATTERN.fullmatch(cookie):
        return cookie
    return new_visitor_id()
```

**Through sign-up**, the signed-out page's visitor goes into your own sign-up request, and your
server passes it to `register`, so the customer keeps the arm they saw:

```python
def sign_up(user_id: str, email: str, visitor_cookie: str | None) -> None:
    customer = server.customer(user_id)
    customer.register(email=email, visitor=visitor_from_cookie(visitor_cookie))
```

**In an app**, the in-app client generates a visitor and keeps it for the client's lifetime, sending
it on every request. Python has no device storage convention, so a desktop app that wants the same
visitor across launches stores `client.visitor` itself and passes it back as `visitor=`. Reset it
(drop the stored value) at sign-out and at account deletion, so the next person on a shared
computer is never linked to the last. A `visitor=` given to one call replaces the client's for that
request only.
