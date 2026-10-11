# Django

Django serves requests from threads, so use the blocking `EntitlerServer`: one per process, shared
by every request (it is thread-safe), created on first use so `manage.py` commands that never call
Entitler need no key.

```python
# billing/entitler.py
import functools

from django.conf import settings

from entitler import EntitlerServer


@functools.cache
def entitler() -> EntitlerServer:
    return EntitlerServer(settings.ENTITLER_KEY)
```

## The signed-in customer

Take the customer from Django's authenticated user, never from the request's own data, and answer
`401` when nobody is signed in:

```python
from collections.abc import Callable
from functools import wraps
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse

from entitler import Feature, ServerCustomer

View = Callable[..., HttpResponse]


def signed_in_customer(request: HttpRequest) -> ServerCustomer | None:
    if not request.user.is_authenticated:
        return None
    return entitler().customer(str(request.user.pk))


def requires(feature: Feature[object]) -> Callable[[View], View]:
    """A view decorator: 401 when signed out, 402 unless the customer has `feature`."""

    def decorate(view: View) -> View:
        @wraps(view)
        def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            customer = signed_in_customer(request)
            if customer is None:
                return JsonResponse({"error": "Sign in first."}, status=401)
            if not customer.is_entitled(feature, default=False):
                return JsonResponse({"error": "Upgrade to use this feature."}, status=402)
            return view(request, *args, **kwargs)

        return wrapped

    return decorate


@requires(features.EXPORT_PDF)
def export_pdf(request: HttpRequest, document_id: str) -> HttpResponse:
    return HttpResponse(b"%PDF-1.7 ...", content_type="application/pdf")
```

## Recording usage

Key the report by the request's own unit of work, so a retry never charges twice:

```python
def summarise(request: HttpRequest, job_id: str) -> HttpResponse:
    customer = signed_in_customer(request)
    if customer is None:
        return JsonResponse({"error": "Sign in first."}, status=401)
    with customer.with_hold(features.AI_CREDITS, 500, idempotency_key=f"summary-{job_id}") as hold:
        words = 120
        hold.use(words)
    return JsonResponse({"words": words})
```

`with_hold` raises `UsageRefusedError` before the body runs when the customer has no allowance;
handle it as a `402`.

## Keeping Entitler's copy current

Register customers when they sign up and sign in, for example from Django's `user_logged_in`
signal:

```python
from django.contrib.auth.signals import user_logged_in


def register_customer(sender: Any, request: HttpRequest, user: Any, **kwargs: Any) -> None:
    entitler().customer(str(user.pk)).register(name=user.get_full_name(), email=user.email)


user_logged_in.connect(register_customer)
```

## The token route for an in-app client

Mint tokens only for the signed-in customer. A view that minted a token for any id it was sent
would hand every customer's access to anyone who asked.

```python
from entitler import CustomerTokenScope


def entitler_token(request: HttpRequest) -> HttpResponse:
    customer = signed_in_customer(request)
    if customer is None:
        return JsonResponse({"error": "Sign in first."}, status=401)
    issued = customer.token(scopes=[CustomerTokenScope.ENTITLEMENTS_READ])
    return JsonResponse({"token": issued.token})
```

Async views can use `AsyncEntitlerServer` the same way, created per event loop; Django's ASGI
server runs one loop per process.

## Tests

Pass `fake_customer` where your code takes a customer, or patch `signed_in_customer` in view tests
(see [testing](testing.md)).
