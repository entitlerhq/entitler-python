# Entitler SDK for Python

The official Python SDK for [Entitler](https://entitler.co), which manages plans, feature access,
usage limits and customer grants for SaaS teams. It checks what each customer is entitled to,
records metered usage exactly once, and drives billing pages and the company's own plan decisions,
with blocking and `async` clients that share one surface. It is fully typed, keeps answers in a
cache so a check rarely waits on the network, and keeps answering from that cache while Entitler
is unreachable.

Requires Python 3.11 or later. Its dependencies are `httpx` and `anyio`.

```sh
pip install entitler
# Offline snapshot verification also needs cryptography:
pip install "entitler[snapshots]"
```

## Quick start

On your server, create one client for the app's life with a secret key from the dashboard, and
act on the customer your authenticated session names:

```python
import os

from entitler import EntitlerServer, Feature

EXPORT_PDF = Feature.boolean("export_pdf")
AI_CREDITS = Feature.metered("ai_credits")

server = EntitlerServer(os.environ["ENTITLER_KEY"])


def signed_in(user_id: str, name: str, email: str) -> None:
    """Call at sign-up and at each sign-in, so Entitler's copy of the customer stays current."""
    server.customer(user_id).register(name=name, email=email)


def export(user_id: str, job_id: str) -> None:
    customer = server.customer(user_id)
    if not customer.is_entitled(EXPORT_PDF, default=False):
        raise PermissionError("PDF export is on a paid plan.")

    result = customer.record_usage(AI_CREDITS, 3, idempotency_key=job_id)
    print(f"{result.outcome}: {result.remaining} credits left")
```

Register customers at sign-up and sign-in, not on every request: `register` writes the details it
is given. Reads answer a customer not registered yet from the default plan. The idempotency key
names the event (this job), so a retry from anywhere never charges twice.

With `asyncio` or trio, use `AsyncEntitlerServer` and `await` the same methods:

```python
from entitler import AsyncEntitlerServer


async def can_export(server: AsyncEntitlerServer, user_id: str) -> bool:
    return await server.customer(user_id).is_entitled(EXPORT_PDF, default=False)
```

## Two clients

| Client | Runs on | Credential | Acts on |
| --- | --- | --- | --- |
| `EntitlerServer` / `AsyncEntitlerServer` | your servers, workers and scripts | a secret key from the dashboard | any customer, plus batches, the customer list and the company's decisions |
| `EntitlerClient` / `AsyncEntitlerClient` | a desktop app, a CLI, anything in a customer's hands | a customer token your server mints, a publishable key and an identity token, or a publishable key alone | the signed-in customer, as `client.me`; a publishable key alone reads pricing |

A secret key never goes into an app. Anything shipped to a customer's device can be read, and a
secret key acts on every customer: it changes plans, grants features and erases accounts. Apps use
a customer token with the fewest scopes, minted by your server for the person signed in, or the
sign-in provider's identity token with a publishable key. Each client refuses the other's key.

Both clients are context managers. An in-app client belongs to one signed-in person: create it at
sign-in and close it at sign-out, so nothing kept for one person is answered to the next. See
[the in-app client](https://github.com/entitlerhq/entitler-python/blob/main/docs/in-app-client.md).

## Feature constants and the command-line tool

Declare features by hand with `Feature.boolean`, `Feature.config`, `Feature.metered` and
`Feature.group`, or generate a typed module from your catalogue:

```sh
python -m entitler generate --out entitler_features.py
```

```python
import entitler_features as features

check = customer.check(features.AI_CREDITS)  # a MeteredCheck: used, held, remaining
```

`python -m entitler snapshot-keys` writes the keys an app ships to verify offline snapshots. The
package also installs the same tool as the `entitler` command. See
[feature constants](https://github.com/entitlerhq/entitler-python/blob/main/docs/feature-constants.md).

## Guides

- [Checking access](https://github.com/entitlerhq/entitler-python/blob/main/docs/checking-access.md)
- [Recording usage](https://github.com/entitlerhq/entitler-python/blob/main/docs/recording-usage.md): modes, idempotency keys, holds and batches
- [Pricing pages and visitors](https://github.com/entitlerhq/entitler-python/blob/main/docs/pricing-and-visitors.md)
- [Billing pages](https://github.com/entitlerhq/entitler-python/blob/main/docs/billing.md)
- [Company decisions](https://github.com/entitlerhq/entitler-python/blob/main/docs/company-decisions.md): plans, add-ons, grants and meter corrections
- [Store purchases](https://github.com/entitlerhq/entitler-python/blob/main/docs/store-purchases.md)
- [The in-app client](https://github.com/entitlerhq/entitler-python/blob/main/docs/in-app-client.md)
- [Offline snapshots](https://github.com/entitlerhq/entitler-python/blob/main/docs/offline-snapshots.md)
- [Reliability](https://github.com/entitlerhq/entitler-python/blob/main/docs/reliability.md): timeouts, retries, the cache and stale answers
- [Errors](https://github.com/entitlerhq/entitler-python/blob/main/docs/errors.md)
- [As-of reads](https://github.com/entitlerhq/entitler-python/blob/main/docs/as-of.md)
- [Tracks](https://github.com/entitlerhq/entitler-python/blob/main/docs/tracks.md)
- [Scopes](https://github.com/entitlerhq/entitler-python/blob/main/docs/scopes.md)
- [Configuration](https://github.com/entitlerhq/entitler-python/blob/main/docs/configuration.md)
- [Testing your app](https://github.com/entitlerhq/entitler-python/blob/main/docs/testing.md)
- [Feature constants](https://github.com/entitlerhq/entitler-python/blob/main/docs/feature-constants.md)
- [Versioning and support](https://github.com/entitlerhq/entitler-python/blob/main/docs/versioning.md)
- Frameworks: [FastAPI](https://github.com/entitlerhq/entitler-python/blob/main/docs/fastapi.md), [Django](https://github.com/entitlerhq/entitler-python/blob/main/docs/django.md), [Flask](https://github.com/entitlerhq/entitler-python/blob/main/docs/flask.md)

The API reference is built from the docstrings with `pdoc` (see [CONTRIBUTING.md](https://github.com/entitlerhq/entitler-python/blob/main/CONTRIBUTING.md)).

## Examples

[`examples/`](https://github.com/entitlerhq/entitler-python/blob/main/examples/README.md) holds small programs: [quickstart](https://github.com/entitlerhq/entitler-python/blob/main/examples/quickstart/main.py),
[pricing-page](https://github.com/entitlerhq/entitler-python/blob/main/examples/pricing-page/main.py), [in-app](https://github.com/entitlerhq/entitler-python/blob/main/examples/in-app/main.py),
[metered-work](https://github.com/entitlerhq/entitler-python/blob/main/examples/metered-work/main.py), [offline](https://github.com/entitlerhq/entitler-python/blob/main/examples/offline/main.py),
[billing](https://github.com/entitlerhq/entitler-python/blob/main/examples/billing/main.py), [generated-features](https://github.com/entitlerhq/entitler-python/blob/main/examples/generated-features/main.py),
[fastapi](https://github.com/entitlerhq/entitler-python/blob/main/examples/fastapi/app.py) and [testing](https://github.com/entitlerhq/entitler-python/blob/main/examples/testing/test_gating.py).

## Licence

MIT. See [LICENSE](https://github.com/entitlerhq/entitler-python/blob/main/LICENSE).
