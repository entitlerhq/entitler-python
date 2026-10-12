# Flask

Flask serves requests from threads, so use the blocking `EntitlerServer`, created once with the app
and shared by every request (it is thread-safe).

```python
import os
from collections.abc import Callable
from functools import wraps
from typing import Any

from flask import Flask, abort, current_app, jsonify, session

from entitler import (
    CustomerTokenScope,
    EntitlerServer,
    Feature,
    ServerCustomer,
    UsageRefusedError,
    UsageReplayedError,
)


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ["SECRET_KEY"]
    app.extensions["entitler"] = EntitlerServer(os.environ["ENTITLER_KEY"])
    return app


def signed_in_customer() -> ServerCustomer:
    """The customer the signed session cookie names; 401 when nobody is signed in."""
    user_id = session.get("user_id")
    if not isinstance(user_id, str):
        abort(401)
    entitler: EntitlerServer = current_app.extensions["entitler"]
    return entitler.customer(user_id)


def requires(feature: Feature[object]) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """A view decorator: 402 unless the signed-in customer has `feature`."""

    def decorate(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if not signed_in_customer().is_entitled(feature, default=False):
                return jsonify(error="Upgrade to use this feature."), 402
            return view(*args, **kwargs)

        return wrapped

    return decorate
```

The customer id comes from Flask's signed session, set by your login view, never from a header,
query string or body the caller controls.

## Views

```python
app = create_app()


@app.get("/exports/<document_id>.pdf")
@requires(features.EXPORT_PDF)
def export_pdf(document_id: str) -> Any:
    return jsonify(document=document_id)


@app.post("/summaries/<job_id>")
def summarise(job_id: str) -> Any:
    customer = signed_in_customer()
    try:
        with customer.with_hold(
            features.AI_CREDITS, 500, idempotency_key=f"summary-{job_id}"
        ) as hold:
            words = 120
            hold.use(words)
    except UsageRefusedError:
        return jsonify(error="No credits left."), 402
    except UsageReplayedError:
        return jsonify(error="This summary was already made."), 409
    return jsonify(words=words)


@app.post("/entitler/token")
def entitler_token() -> Any:
    """Mints a token for the signed-in customer only, never for an id the request names."""
    issued = signed_in_customer().token(scopes=[CustomerTokenScope.ENTITLEMENTS_READ])
    return jsonify(token=issued.token)
```

A token route that minted a token for any id it was sent would hand every customer's access to
anyone who asked.

## Tests

Pass `fake_customer` to the code that takes a customer, or patch `signed_in_customer` in view tests
(see [testing](testing.md)).
