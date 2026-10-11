# Scopes

Entitler decides which calls a credential may make. The SDK never refuses a call itself: it sends
the request, and a missing scope comes back as `APIError` `403 scope_required`.

## Which scope each call needs

| Calls | Scope |
| --- | --- |
| `pricing()`, `features()` | `plans:read` |
| `check`, `is_entitled`, `entitlements`, `plans`, `customer.pricing`, `snapshot` | `entitlements:read` |
| `usage` | `usage:read` |
| `record_usage`, holds, `settle_usage`, `release_usage`, `record_usage_batch` | `usage:write` |
| `subscribe`, `cancel`, `undo_pending_change`, `billing_portal`, `sync_billing` | `customers:write`; in an app, `billing:self` |
| `register` | `customers:register`; changing an existing customer's details also needs `customers:write` or `customers:profile` |
| `details`, `billing`, `customers.list` | `customers:read` |
| `update` | `customers:write` or `customers:profile` |
| `customers.create`, `erase`, `set_plan`, `set_add_on`, `grant`, `revoke_grant`, `adjust_meter`, `cancel_usage` | `customers:write` |
| `token` | `tokens:mint` |
| `set_track` | `tracks:assign`, which the server key preset lacks |
| `scopes`, `snapshot_keys` | none |

A customer token holds at most `entitlements:read`, `usage:read`, `usage:write` and
`billing:self` (`CustomerTokenScope`); without `scopes=`, `token()` mints `entitlements:read` only.

## Asking which scopes a client holds

```python
from entitler import Scope

held = server.scopes()
if Scope.USAGE_WRITE in held.scopes:
    ...
```

`scopes()` answers the scopes the SDK knows, in a fixed order, so an app can decide which controls
to show. It asks Entitler each time, so a newly granted scope shows without a restart. For an
in-app client with an identity token, `registration` says whether the sign-in provider lets new
people register themselves.
