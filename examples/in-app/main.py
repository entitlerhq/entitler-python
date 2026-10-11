"""An in-app client for a desktop app, with a customer token your server mints.

Run it with `ENTITLER_KEY=ent_test_… python examples/in-app/main.py`. The server half mints a
token with `entitlements:read` and `usage:write` for the signed-in customer only; the app half
checks and records usage for `me`, and closes the client at sign-out.
"""

import os
import uuid

from entitler import CustomerTokenScope, EntitlerClient, EntitlerServer, Feature

EXPORT_PDF = Feature.boolean("export_pdf")
AI_CREDITS = Feature.metered("ai_credits")
SCOPES = [CustomerTokenScope.ENTITLEMENTS_READ, CustomerTokenScope.USAGE_WRITE]


def main() -> None:
    server = EntitlerServer(os.environ["ENTITLER_KEY"])
    session_user_id = f"in-app-{uuid.uuid4().hex[:12]}"
    customer = server.customer(session_user_id)
    customer.register(name="Desktop user")

    def token_route() -> str:
        """Your server's token route: it mints only for the customer its own session names."""
        return customer.token(scopes=SCOPES).token

    try:
        client = EntitlerClient(token=token_route)
        if client.me.is_entitled(EXPORT_PDF, default=False):
            print("Export is on.")
        else:
            print("Export needs an upgrade.")
        reply = client.me.record_usage(AI_CREDITS, 1, idempotency_key=f"chat-{uuid.uuid4()}")
        print(f"Recorded: {reply.outcome}, {reply.remaining} credits left.")
        client.close()
    finally:
        customer.erase()
        server.close()


if __name__ == "__main__":
    main()
