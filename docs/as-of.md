# As-of reads

A server customer's reads can answer as of another instant: `check`, `is_entitled`,
`entitlements`, `plans`, `usage` and `details` take `as_of=`, an aware `datetime`.

```python
from datetime import UTC, datetime, timedelta

next_month = datetime.now(UTC) + timedelta(days=31)
after_renewal = customer.plans(as_of=next_month)
credits_then = customer.check(features.AI_CREDITS, as_of=next_month)
```

A naive `datetime` raises `ValueError`. The instant is sent as `Entitler-As-Of` on that request
only, and the answer is cached under its own key, apart from answers for now.

- Reads at another instant need the organisation's `as_of` capability (`409 capability_required`
  otherwise).
- Only `ServerCustomer` takes it. The in-app client, pricing and every write always act now, so a
  preview can never break writes in a live environment.
- As-of shows the effects of time on the plans, grants and meters already in place: renewals,
  booked moves, grant expiries and meter resets. It never shows a release nobody has rolled out
  yet, since nothing is derived on a schedule. To preview an unreleased change, put a test customer
  on a track for testers (see [tracks](tracks.md)).
