"""A FastAPI app: one async server client for the app's life, and a customer per request.

Run it with `ENTITLER_KEY=ent_test_… uvicorn app:app` from this folder (`pip install fastapi
uvicorn`). The customer comes from the signed-in session, never from the request's own data.
"""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, params

from entitler import (
    AsyncEntitlerServer,
    AsyncServerCustomer,
    CustomerTokenScope,
    Feature,
    UsageRefusedError,
    UsageReplayedError,
)

EXPORT_PDF = Feature.boolean("export_pdf")
AI_CREDITS = Feature.metered("ai_credits")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    async with AsyncEntitlerServer(os.environ["ENTITLER_KEY"]) as server:
        app.state.entitler = server
        yield


app = FastAPI(lifespan=lifespan)


def signed_in_user_id(request: Request) -> str | None:
    """Stands in for your authentication: the user id your verified session cookie names."""
    user_id = request.session.get("user_id") if "session" in request.scope else None
    return user_id if isinstance(user_id, str) else None


async def current_customer(request: Request) -> AsyncServerCustomer:
    """A FastAPI dependency: the signed-in customer, or `401` when nobody is signed in."""
    user_id = signed_in_user_id(request)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Sign in first.")
    server: AsyncEntitlerServer = request.app.state.entitler
    return server.customer(user_id)


Customer = Annotated[AsyncServerCustomer, Depends(current_customer)]


def requires(feature: Feature[object]) -> params.Depends:
    """A dependency that answers `402` unless the customer is entitled to `feature`."""

    async def check(customer: Customer) -> None:
        if not await customer.is_entitled(feature, default=False):
            raise HTTPException(status_code=402, detail="Upgrade to use this feature.")

    return params.Depends(check)


@app.get("/exports/{document_id}.pdf", dependencies=[requires(EXPORT_PDF)])
async def export_pdf(document_id: str) -> dict[str, str]:
    return {"document": document_id, "status": "rendering"}


@app.post("/chat/{message_id}")
async def chat(message_id: str, customer: Customer) -> dict[str, int]:
    """Runs metered work: `402` without credits, `409` when this message was already answered."""
    try:
        async with customer.with_hold(AI_CREDITS, 5, idempotency_key=message_id) as hold:
            tokens = 3
            hold.use(tokens)
    except UsageRefusedError:
        raise HTTPException(status_code=402, detail="No credits left.") from None
    except UsageReplayedError:
        raise HTTPException(status_code=409, detail="This message was already answered.") from None
    return {"tokens": tokens}


@app.post("/entitler/token")
async def entitler_token(customer: Customer) -> dict[str, str]:
    """Mints a token for the signed-in customer only: never for an id the request names."""
    issued = await customer.token(scopes=[CustomerTokenScope.ENTITLEMENTS_READ])
    return {"token": issued.token}
