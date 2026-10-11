"""Quick start: register a customer, check a feature and record usage from a server.

Run it with `ENTITLER_KEY=ent_test_… python examples/quickstart/main.py`. It acts on the customer
`ENTITLER_CUSTOMER_ID` names, or on a new one it erases at the end.
"""

import os
import uuid

from entitler import EntitlerServer, Feature

EXPORT_PDF = Feature.boolean("export_pdf")
AI_CREDITS = Feature.metered("ai_credits")


def configured_customer_id() -> str:
    """The customer this script acts on, from its configuration: never from anyone's input."""
    return os.environ.get("ENTITLER_CUSTOMER_ID") or f"quickstart-{uuid.uuid4().hex[:12]}"


def main() -> None:
    with EntitlerServer(os.environ["ENTITLER_KEY"]) as server:
        customer = server.customer(configured_customer_id())
        created = customer.register().created
        try:
            if customer.is_entitled(EXPORT_PDF, default=False):
                print("Exporting the PDF.")
            else:
                print("PDF export is on a paid plan.")
            job_id = f"job-{uuid.uuid4()}"
            result = customer.record_usage(AI_CREDITS, 3, idempotency_key=job_id)
            print(f"Usage {result.outcome}: {result.used} used, {result.remaining} left.")
        finally:
            if created:
                customer.erase()


if __name__ == "__main__":
    main()
