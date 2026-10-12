# The in-app client

`EntitlerClient` (and `AsyncEntitlerClient`) runs in software a customer holds: a desktop app, a
command-line tool, a kiosk. It acts on one customer, `client.me`, and never holds a secret key.
Create it with exactly one of:

- `token=`: a customer token your server minted with `ServerCustomer.token()`, or a provider that
  fetches one from your server;
- `key=` and `identity_token=`: a publishable key, and an identity token from a sign-in provider
  registered on the project (or a provider for it);
- `key=` alone: a publishable key, for a signed-out pricing page ([pricing](pricing-and-visitors.md)).

Any other combination raises `TypeError`; a secret key raises `ValueError`.

## Customer tokens from your server

Your server mints tokens with the fewest scopes the app needs, and only for the customer its own
session names. A route that minted a token for any id it was sent would hand every customer's
access to anyone who asked.

```python
from entitler import CustomerTokenScope


def token_route(request: object) -> dict[str, str]:
    user_id = signed_in_user_id(request)
    if user_id is None:
        raise PermissionError("401: sign in first")
    issued = server.customer(user_id).token(
        scopes=[CustomerTokenScope.ENTITLEMENTS_READ, CustomerTokenScope.USAGE_WRITE]
    )
    return {"token": issued.token}
```

Without `scopes`, a token holds `entitlements:read` only. Ask for `usage:write` only when the app
records usage itself, and `billing:self` only for people who may buy for the customer (a
workspace's owners, not every member).

## Token providers

A provider is a function the client calls for a fresh token: before the first request, when the
kept token expires within a minute (or within half its lifetime, when that is shorter), and once
after a `401`. Concurrent
calls share one refresh, which has its own deadline (the client's `timeout`). A provider that fails
or answers an unusable token fails the call with `TokenError`, carrying the cause.

```python
import httpx

from entitler import EntitlerClient


def fetch_token() -> str:
    answer = httpx.post("https://app.example.com/entitler/token", cookies={"session": "..."})
    answer.raise_for_status()
    token: str = answer.json()["token"]
    return token


app_client = EntitlerClient(token=fetch_token)
```

The async client takes an `async def` provider. A fixed token string cannot be refreshed, so a
`401` fails the call.

## Identity tokens

With a sign-in provider registered on the project, the app sends the provider's identity token
with a publishable key, and needs no token route. Call `register()` after each sign-in: it creates
the customer the first time and answers an existing one without creating it again.

```python
def firebase_id_token() -> str:
    return "..."  # the sign-in SDK's current ID token, refreshed by the SDK


signed_in = EntitlerClient(key="ent_pk_live_...", identity_token=firebase_id_token)
registered = signed_in.register()
if signed_in.scopes().registration is False:
    print("This provider does not let people register themselves.")
```

Pick a provider whose SDK refreshes its own identity tokens, such as Firebase Auth, and pass its
token getter as the provider. Sign in with Apple's identity tokens last ten minutes and the device
cannot refresh them, so use them to sign in to a provider that issues its own.

Identity-token customers have external ids of the form `<provider id>:<subject>`; a server acting
on one uses that id, which every answer about them names. `register()` answers
`403 registration_closed` when the provider does not let people register themselves (`scopes()`'s
`registration` says so ahead), and `403 scope_required` when the key lacks `customers:register`.
On a token client, `register()` is sent and Entitler refuses it.

## One client per signed-in customer

An in-app client acts for one person. Create it at sign-in, keep it where the app keeps state for
the session (shared by every window, not owned by one), and close it at sign-out. The next sign-in
creates a new client, so nothing kept for one person is ever answered to another.

```python
class Session:
    """The app's signed-in state."""

    def __init__(self) -> None:
        self.entitler: EntitlerClient | None = None

    def sign_in(self) -> None:
        self.entitler = EntitlerClient(token=fetch_token)

    def sign_out(self) -> None:
        if self.entitler is not None:
            self.entitler.close()
            self.entitler = None
```

Closing cancels the pending refresh and calls in flight, drops the in-memory cache, and makes every
later call raise `RuntimeError`. Closing twice is safe.

The client keeps answers in memory only: it takes a cache size or `None`, never a custom store,
since its credential changes with every token. Only [snapshots](offline-snapshots.md) survive a
relaunch.

## What the client cannot do

`me`, `register()` and `scopes()` on a key-only client, and `pricing()` on a signed-in one (use
`me.pricing()`), raise `TypeError` before any request. The in-app client has no batches, no
customer list and none of the company's decisions. Usage counted on a device is advisory, since a
modified app can skip it; see [recording usage](recording-usage.md).
