# Offline snapshots

A snapshot is a signed copy of a customer's entitlements that an app verifies with no request. It
is the only answer that survives a relaunch or a lost connection: the cache lives in memory.
Verification needs the extra: `pip install "entitler[snapshots]"`.

## Fetching one

```python
issued = customer.snapshot(ttl_seconds=7 * 24 * 3600)
saved = issued.token  # keep it with the app's data
print(issued.expires_at)
```

In an app, `client.me.snapshot()` mints one for the signed-in customer.

## Verifying it

```python
from entitler import EntitlerError, SnapshotKeys, is_unreachable, verify_snapshot


def can_export(user_id: str, environment_id: str, keys: SnapshotKeys, saved: str) -> bool:
    try:
        return customer.check(features.EXPORT_PDF).entitled
    except EntitlerError as error:
        if not is_unreachable(error):
            raise
    snapshot = verify_snapshot(saved, keys=keys, customer=user_id, environment=environment_id)
    return snapshot.entitlements.has(features.EXPORT_PDF)
```

`verify_snapshot` checks the signature, the issuer, the signing time against `now` (with
`clock_skew_seconds`, 60 by default), the expiry, the customer and the environment, and raises
`SnapshotError` (`snapshot_invalid` or `snapshot_expired`) when one fails. It answers a
`VerifiedSnapshot` whose `entitlements` behave as `entitlements()` does, with `as_of` the signing
time. Meters in a snapshot are frozen when it is signed.

`is_unreachable(error)` is true for the failures that mean Entitler could not be reached
(connection failures, timeouts, `429`, `5xx`, an unreadable answer, a token provider failing), so
an app falls back to its snapshot without copying that rule.

## Where the expected values come from

A mistake here shows only offline, so take each from a source the snapshot cannot change:

- **`customer`**: the signed-in person's external id from the app's own session, never from the
  stored snapshot. For an identity-token customer it is `<provider id>:<subject>`, with the sign-in
  provider's Entitler id from the app's configuration.
- **`environment`**: the environment id the app's build ships, beside its snapshot keys.

## Key pinning

The keys decide which snapshots an app trusts. Ship them with the app, written at build time:

```sh
python -m entitler snapshot-keys --out entitler-snapshot-keys.json
```

Run it in the build (a packaging script, a Makefile target, a CI step), so pinning needs no
hand-written download. Load the file and pass its `keys` list:

```python
import json
from pathlib import Path

pinned = json.loads(Path("entitler-snapshot-keys.json").read_text())["keys"]
verify_snapshot("...", keys=pinned, customer="user_1", environment="env_1")
```

Replace them only with keys fetched from Entitler over HTTPS (`snapshot_keys()`), and never store
them beside the token or load them from the same record: anyone who can edit that record could
replace both.

## Key rotation

- Entitler publishes a new signing key before signing with it.
- It keeps retired keys published for 30 days after their last use, longer than any snapshot lives.
- An emergency replacement withdraws the old keys at once, and snapshots they signed stop verifying
  once the app refetches.

So refresh the keys from `snapshot_keys()` whenever the app is online, keep them in the app's own
trusted storage, and verify offline against the keys it holds. A snapshot signed by a key the app
has not fetched yet fails with `None of the keys passed signed this snapshot.` until the app is next
online. Verification itself never makes a request.
