# Versioning and support

The SDK follows [Semantic Versioning](https://semver.org). While the version is 0.x, a minor
release (0.2.0) may change the public API and a patch release (0.1.1) never does; each change is
listed in the [changelog](../CHANGELOG.md). The public API is everything exported from `entitler`,
`entitler.testing` and `entitler.cli.render_features`; names starting with `_` are private.

New answer fields and new enum values arrive without a new major version. Enum fields are typed
`Enum | str`, so a value this version does not know stays a string instead of failing, and code
that matches on them keeps a default case.

## Supported Python versions

The SDK supports every Python version that is still supported upstream, from Python 3.11, and is
tested on each of them on Linux, and on the newest on macOS and Windows. A version that reaches its
end of life upstream is dropped in the next minor release, with a changelog entry.

## Dependencies

`httpx` (from 0.27, below 1) and `anyio` (from 4.4, below 5) at run time, and `cryptography` (from
42) for the `snapshots` extra.

## Security fixes

Security fixes go into the newest release. Report a vulnerability privately, as
[SECURITY.md](../SECURITY.md) explains.
