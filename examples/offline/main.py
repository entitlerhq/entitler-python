"""Offline snapshots: fetch a snapshot and the keys, then verify it with no request.

Run it with `ENTITLER_KEY=ent_test_… python examples/offline/main.py`. Install the extra first:
`pip install "entitler[snapshots]"`.
"""

import os
import uuid

from entitler import (
    EntitlerError,
    EntitlerServer,
    Feature,
    is_unreachable,
    verify_snapshot,
)

EXPORT_PDF = Feature.boolean("export_pdf")


def main() -> None:
    with EntitlerServer(os.environ["ENTITLER_KEY"]) as server:
        user_id = f"offline-{uuid.uuid4().hex[:12]}"
        customer = server.customer(user_id)
        customer.register(name="Offline")
        try:
            keys = server.snapshot_keys()
            environment = customer.check(EXPORT_PDF).environment.id
            saved = customer.snapshot().token

            try:
                entitled = customer.check(EXPORT_PDF).entitled
            except EntitlerError as error:
                if not is_unreachable(error):
                    raise
                snapshot = verify_snapshot(
                    saved, keys=keys, customer=user_id, environment=environment
                )
                entitled = snapshot.entitlements.has(EXPORT_PDF)
            print(f"Export entitled: {entitled}")

            offline = verify_snapshot(saved, keys=keys, customer=user_id, environment=environment)
            print(f"Verified offline, valid until {offline.expires_at:%Y-%m-%d %H:%M} UTC.")
        finally:
            customer.erase()


if __name__ == "__main__":
    main()
