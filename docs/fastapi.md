# FastAPI

FastAPI runs on asyncio, so use `AsyncEntitlerServer`: one client for the app's life, created in
the lifespan, and a dependency that answers the signed-in customer. The full app is in
[`examples/fastapi`](../examples/fastapi/app.py).

```python
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, params
from fastapi.responses import StreamingResponse

from entitler import (
    AsyncEntitlerServer,
    AsyncServerCustomer,
    CustomerTokenScope,
    Feature,
    UsageRefusedError,
    UsageReplayedError,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    async with AsyncEntitlerServer("ent_test_...") as entitler:
        app.state.entitler = entitler
        yield


app = FastAPI(lifespan=lifespan)


async def current_customer(request: Request) -> AsyncServerCustomer:
    """The signed-in customer, from the verified session; 401 when nobody is signed in."""
    user_id = signed_in_user_id(request)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Sign in first.")
    entitler: AsyncEntitlerServer = request.app.state.entitler
    return entitler.customer(user_id)


SignedIn = Annotated[AsyncServerCustomer, Depends(current_customer)]


def requires(feature: Feature[object]) -> params.Depends:
    """A dependency that answers 402 unless the customer is entitled to `feature`."""

    async def check(customer: SignedIn) -> None:
        if not await customer.is_entitled(feature, default=False):
            raise HTTPException(status_code=402, detail="Upgrade to use this feature.")

    return params.Depends(check)


@app.get("/exports/{document_id}.pdf", dependencies=[requires(features.EXPORT_PDF)])
async def export_pdf(document_id: str) -> dict[str, str]:
    return {"document": document_id}
```

`signed_in_user_id(request)` stands in for your authentication: the user id your verified session
cookie or token names. Never take the customer id from a header, query string or body the caller
controls.

## Streaming metered work

Start the hold before the response starts, so a refusal is still a `402`, and a replayed message
a `409` rather than a second charge; the hold settles when the stream ends, even when the client
disconnects halfway:

```python
async def generate(prompt: str) -> AsyncGenerator[str, None]:
    for word in prompt.split():
        yield word + " "


@app.post("/chat/{message_id}")
async def chat(message_id: str, prompt: str, customer: SignedIn) -> StreamingResponse:
    try:
        hold = await customer.start_hold(
            features.AI_CREDITS, 2000, idempotency_key=f"chat-{message_id}"
        )
    except UsageRefusedError:
        raise HTTPException(status_code=402, detail="No credits left.") from None
    except UsageReplayedError:
        raise HTTPException(status_code=409, detail="This message was already answered.") from None

    async def stream() -> AsyncGenerator[str, None]:
        async with hold:
            hold.use(0)
            used = 0
            async for chunk in generate(prompt):
                used += 1
                hold.use(used)
                yield chunk
            await hold.finish()

    return StreamingResponse(stream(), media_type="text/plain")
```

The idempotency key comes from the message, so a retried request never charges twice.

## The token route for an in-app client

A route that mints customer tokens mints them only for the signed-in customer. One that minted a
token for any id it was sent would hand every customer's access to anyone who asked.

```python
@app.post("/entitler/token")
async def entitler_token(customer: SignedIn) -> dict[str, str]:
    issued = await customer.token(scopes=[CustomerTokenScope.ENTITLEMENTS_READ])
    return {"token": issued.token}
```

## Tests

Override the dependency with a fake:

```python
from fastapi.testclient import TestClient

from entitler.testing import fake_async_customer


def test_export_needs_the_feature() -> None:
    fake = fake_async_customer({"export_pdf": False})
    app.dependency_overrides[current_customer] = lambda: fake
    with TestClient(app) as test_client:
        assert test_client.get("/exports/d1.pdf").status_code == 402
```
