"""Metered work: a hold around streamed work, a hold around work of unknown cost, and observe.

Run it with `ENTITLER_KEY=ent_test_… python examples/metered-work/main.py`.
"""

import os
import uuid
from collections.abc import Iterator

from entitler import (
    EntitlerServer,
    Feature,
    ServerCustomer,
    UsageMode,
    UsageRefusedError,
    UsageReplayedError,
)

AI_CREDITS = Feature.metered("ai_credits")


def stream_reply(prompt: str) -> Iterator[int]:
    """Stands in for a model's streamed reply: the running total of tokens, one per word."""
    for total, _word in enumerate(prompt.split(), start=1):
        yield total


def streamed_reply(customer: ServerCustomer, message_id: str, prompt: str) -> None:
    """A hold around streamed work that reports as it goes."""
    try:
        hold = customer.start_hold(AI_CREDITS, 8, idempotency_key=message_id)
    except UsageRefusedError as refused:
        print(f"No allowance left: {refused.result.refusal}. Show an upgrade.")
        return
    except UsageReplayedError:
        print("This message was already answered: show that answer, and charge nothing.")
        return
    with hold:
        hold.use(0)
        for total in stream_reply(prompt):
            hold.use(total)
        settled = hold.finish()
    print(f"Streamed work settled: {settled.amount} credits.")


def summary_job(customer: ServerCustomer, job_id: str) -> None:
    """A hold around work whose cost is known only afterwards."""
    try:
        with customer.with_hold(AI_CREDITS, 5, idempotency_key=job_id) as work:
            tokens = len(["Summarise", "this", "document"])
            work.use(tokens)
    except UsageRefusedError:
        print("No allowance left for the summary.")
        return
    except UsageReplayedError:
        print("This job already ran.")
        return
    print(f"Job charged {tokens} credits.")


def main() -> None:
    with EntitlerServer(os.environ["ENTITLER_KEY"]) as server:
        customer = server.customer(f"metered-{uuid.uuid4().hex[:12]}")
        customer.register(name="Metered work")
        try:
            message_id = f"message-{uuid.uuid4()}"
            streamed_reply(customer, message_id, "Write a short poem about entitlements")
            streamed_reply(customer, message_id, "Write a short poem about entitlements")
            summary_job(customer, f"job-{uuid.uuid4()}")
            observed = customer.record_usage(
                AI_CREDITS, 7, idempotency_key=f"bandwidth-{uuid.uuid4()}", mode=UsageMode.OBSERVE
            )
            print(f"Observed usage: {observed.outcome}, over by {observed.over_by}.")
        finally:
            customer.erase()


if __name__ == "__main__":
    main()
