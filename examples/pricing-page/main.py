"""A pricing page: plans, period labels and prices, signed out.

Run it with `ENTITLER_KEY=ent_test_… ENTITLER_PUBLISHABLE_KEY=ent_pk_test_… python
examples/pricing-page/main.py`. It prints the pricing once through the in-app client with the
publishable key, and once through the server client with a visitor id.
"""

import os

from entitler import EntitlerClient, EntitlerServer, Pricing


def show(pricing: Pricing) -> None:
    for plan in pricing.plans:
        if plan.kind != "plan":
            continue
        print(plan.name)
        for period in plan.periods:
            listing = next(
                (item for item in plan.listings if item.period and item.period.key == period.key),
                None,
            )
            prices = [c.price for c in listing.channels if c.price] if listing else []
            if prices:
                price = prices[0]
                print(f"  {period.label}: {price.amount / 100:.2f} {price.currency.upper()}")
            else:
                print(f"  {period.label}: Coming soon")


def main() -> None:
    publishable = os.environ.get("ENTITLER_PUBLISHABLE_KEY")
    if publishable:
        with EntitlerClient(key=publishable) as client:
            print("Signed out, in the app:")
            show(client.pricing())
    with EntitlerServer(os.environ["ENTITLER_KEY"]) as server:
        visitor = server.new_visitor_id()
        print(f"Signed out, on the server, for visitor {visitor}:")
        show(server.pricing(visitor=visitor))


if __name__ == "__main__":
    main()
