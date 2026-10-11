"""Quick start: register a customer, check a feature and record usage from a server.

Run it with `ENTITLER_KEY=ent_test_… python examples/quickstart/main.py`.
"""

import os
import uuid

from entitler import EntitlerServer, Feature

EXPORT_PDF = Feature.boolean("export_pdf")
AI_CREDITS = Feature.metered("ai_credits")


def signed_in_user_id() -> str:
    """Stands in for the id your app's authenticated session names; never one a caller sends."""
    return f"quickstart-{uuid.uuid4().hex[:12]}"


def main() -> None:
    with EntitlerServer(os.environ["ENTITLER_KEY"]) as server:
        customer = server.customer(signed_in_user_id())
        customer.register(name="Ada Lovelace", email="ada@example.com")
        try:
            if customer.is_entitled(EXPORT_PDF, default=False):
                print("Exporting the PDF.")
            else:
                print("PDF export is on a paid plan.")
            job_id = f"job-{uuid.uuid4()}"
            result = customer.record_usage(AI_CREDITS, 3, idempotency_key=job_id)
            print(f"Usage {result.outcome}: {result.used} used, {result.remaining} left.")
        finally:
            customer.erase()


if __name__ == "__main__":
    main()
