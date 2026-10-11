# Changelog

All notable changes to this project are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-11

### Added

- `EntitlerServer` and `AsyncEntitlerServer` for servers, with a secret key: customers, the
  customer list, usage batches, pricing, the feature catalogue, scopes and snapshot keys.
- `EntitlerClient` and `AsyncEntitlerClient` for apps, with a customer token or token provider, a
  publishable key and an identity token, or a publishable key alone for pricing.
- `Customer` and `ServerCustomer` (and their async twins): checks, entitlement lists, plans,
  pricing, usage, holds (`start_hold`, `with_hold`), snapshots and the customer's own billing
  choices; on the server also registration, details, tokens, tracks and the company's decisions
  (`set_plan`, `set_add_on`, grants, meter adjustments and usage corrections).
- An answer cache with revalidation, custom stores for servers, and stale answers while Entitler is
  unreachable.
- Retries with idempotency keys, timeouts, token refresh and typed errors.
- Offline snapshot verification with `verify_snapshot` (the `snapshots` extra).
- The `entitler` command (`python -m entitler`): `generate` for typed feature constants and
  `snapshot-keys` for the keys an app ships.
- `entitler.testing`: `fake_customer`, `fake_async_customer` and answer builders.

[Unreleased]: https://github.com/entitlerhq/entitler-python/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/entitlerhq/entitler-python/releases/tag/v0.1.0
