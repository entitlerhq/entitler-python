"""Typed feature constants from `entitler generate`.

`entitler_features.py` beside this file was written by
`python -m entitler generate --out examples/generated-features/entitler_features.py`.
Run it with `ENTITLER_KEY=ent_test_… python examples/generated-features/main.py`.
"""

import os
import uuid

import entitler_features as features

from entitler import EntitlerServer


def main() -> None:
    with EntitlerServer(os.environ["ENTITLER_KEY"]) as server:
        customer = server.customer(f"generated-{uuid.uuid4().hex[:12]}")
        customer.register(name="Generated")
        try:
            usage = customer.check(features.AI_CREDITS)
            print(f"{usage.used} of {usage.value} credits used, {usage.remaining} left.")
            seats = customer.check(features.TEAM_SEATS)
            print(f"Team seats: {seats.value}")
            entitlements = customer.entitlements()
            print(f"Collaboration: {entitlements.has(features.COLLABORATION)}")
        finally:
            customer.erase()


if __name__ == "__main__":
    main()
