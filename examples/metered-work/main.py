"""Metered work: a hold around streamed work, a hold around work of unknown cost, and observe.

Run it with `ENTITLER_KEY=ent_test_… python examples/metered-work/main.py`.
"""

import os
import uuid
from collections.abc import Iterator

from entitler import EntitlerServer, Feature, UsageMode, UsageRefusedError

AI_CREDITS = Feature.metered("ai_credits")


def stream_reply(prompt: str) -> Iterator[int]:
    """Stands in for a model's streamed reply: the running total of tokens after each chunk."""
    total = 0
    for word in prompt.split():
        total += len(word)
        yield total


def main() -> None:
    with EntitlerServer(os.environ["ENTITLER_KEY"]) as server:
        customer = server.customer(f"metered-{uuid.uuid4().hex[:12]}")
        customer.register(name="Metered work")
        try:
            message_id = f"message-{uuid.uuid4()}"
            try:
                hold = customer.start_hold(AI_CREDITS, 50, idempotency_key=message_id)
            except UsageRefusedError as refused:
                print(f"No allowance left: {refused.result.refusal}")
                return
            with hold:
                hold.use(0)
                for total in stream_reply("Write a short poem about entitlements"):
                    hold.use(total)
                settled = hold.finish()
            print(f"Streamed work settled: {settled.amount} credits.")

            job_id = f"job-{uuid.uuid4()}"
            with customer.with_hold(AI_CREDITS, 20, idempotency_key=job_id) as work:
                tokens = sum(len(word) for word in ["Summarise", "this", "document"])
                work.use(tokens)
            print(f"Job charged {tokens} credits.")

            observed = customer.record_usage(
                AI_CREDITS, 7, idempotency_key=f"bandwidth-{uuid.uuid4()}", mode=UsageMode.OBSERVE
            )
            print(f"Observed usage: {observed.outcome}, over by {observed.over_by}.")
        finally:
            customer.erase()


if __name__ == "__main__":
    main()
