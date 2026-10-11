# Instructions for coding agents

This file holds this repository's rules for automated contributors. People follow
[CONTRIBUTING.md](CONTRIBUTING.md), which these rules extend.

## Copy and comments

- All prose (README, docs, docstrings, error messages, CLI output, changelog) is Australian English,
  sentence case, with no emoji. Never write "get" in prose: name the thing instead ("the customer's
  entitlements", "Quick start"). No vague "what" clauses.
- Every public type, function, method, property and constant has a docstring. No other comments;
  tool directives (lint suppressions) are the only exception, and each carries a reason.
- Python conventions decide every name and shape.

## Before pushing

Run every command in CONTRIBUTING.md's "Checks" section, and the live API tests when
`ENTITLER_TEST_KEY` is available. The repository's only required check is `All checks passed`;
keep its name, and keep every other job in its `needs`.

## Authorship and pull requests

- Commits are authored and committed by the repository owner's identity, set with
  `git config user.name` and `git config user.email` before the first commit.
- Commit messages carry no assistant attribution: no `Co-Authored-By` or session trailers, no
  "Generated with" line, no session link and no model name. No file, commit or branch names the
  tool that wrote it, and this file is the only instruction file for agents.
- Write to GitHub only by pushing your branch: open, edit or comment on no pull request, issue or
  review, and create no tags or releases. Report to whoever assigned the work, who opens the pull
  request.
- Never skip, disable or weaken a test or check to make it pass.
- Keep `LICENSE` unchanged.
