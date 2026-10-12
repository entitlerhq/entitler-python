"""A billing page and the company's side: plans, subscribe, the return page, cancel and grants.

Run it with `ENTITLER_KEY=ent_test_… python examples/billing/main.py`.
"""

import os
import uuid

from entitler import (
    APIError,
    CustomerPlans,
    EntitlerServer,
    ErrorCode,
    NextStep,
    PlanBilling,
    ServerCustomer,
)

RETURN_URL = "https://app.example.com/billing/return"


def billing_page(plans: CustomerPlans) -> None:
    for held in plans.held:
        manager = "the store" if held.billed_by in ("apple", "google") else "the billing portal"
        period = held.period.label if held.period else "no period"
        print(f"You hold {held.plan.name} ({period}); manage it in {manager}.")
    for option in plans.options:
        if option.action == "buy":
            print(f"  [{option.move.title()} to {option.plan.name}]")
        elif option.action == "contact":
            print(f"  [Talk to sales about {option.plan.name}]")


def subscribe(customer: ServerCustomer) -> None:
    step = customer.subscribe("pro", period="monthly", return_url=RETURN_URL)
    match step.next:
        case NextStep.DONE:
            print("Upgraded to Pro.")
        case NextStep.PAY:
            print(f"Send the customer to {step.url}")
        case NextStep.CONFIRMING:
            print("The bank is confirming the payment; show it as pending.")
        case NextStep.MANAGE:
            print(f"The customer changes this plan through {step.billed_by}.")
        case _:
            print(f"A step this SDK does not know: {step.next}")


def return_page(customer: ServerCustomer) -> None:
    customer.sync_billing()
    billing_page(customer.plans(revalidate=True))


def open_portal(customer: ServerCustomer) -> None:
    try:
        print(f"Opening {customer.billing_portal(return_url=RETURN_URL).url}")
    except APIError as error:
        if error.code == ErrorCode.STALE:
            print("No payment provider is connected in this environment.")
        elif error.code == ErrorCode.NOT_FOUND:
            print("The provider has never billed this customer.")
        else:
            raise


def main() -> None:
    with EntitlerServer(os.environ["ENTITLER_KEY"]) as server:
        customer = server.customer(f"billing-{uuid.uuid4().hex[:12]}")
        customer.register(name="Billing")
        try:
            billing_page(customer.plans())
            subscribe(customer)
            return_page(customer)
            change = customer.cancel()
            print(f"Cancel takes effect: {change.effective}, at {change.at:%Y-%m-%d}.")
            customer.undo_pending_change()
            open_portal(customer)
            deal = f"deal-{uuid.uuid4()}"
            customer.set_plan(
                "pro",
                period="yearly",
                billing=PlanBilling.END,
                actor="hubspot",
                reason="Invoiced contract",
                idempotency_key=deal,
            )
            grant = customer.grant("sso", days=30, actor="agent_7", reason="Ticket 4521").grant
            print(f"Granted SSO, grant id {grant.id}.")
        finally:
            customer.erase()


if __name__ == "__main__":
    main()
