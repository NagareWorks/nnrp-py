# Contributing to nnrp-py

This repository publishes Python SDK packages and protocol tooling, so contribution flow needs to stay predictable.

## Branch Strategy

The protected integration branch should be the repository default branch.

For the public GitHub repository, use `main` as the protected integration branch.

Use short-lived topic branches for day-to-day work:

- `feature/<scope>-<topic>` for new capabilities
- `fix/<scope>-<topic>` for bug fixes
- `docs/<scope>-<topic>` for documentation-only changes
- `chore/<scope>-<topic>` for maintenance and tooling updates
- `release/<version>` only when stabilizing a public package release candidate

Rules:

- Branch from the latest default integration branch, normally `main`.
- Keep topic branches focused on one slice of work.
- Rebase or merge from the default integration branch regularly if the branch stays open.
- Merge back to the default integration branch through a pull request.
- Do not push directly to the default integration branch; enforce this with a GitHub ruleset or branch protection rule.
- Do not publish packages directly from topic branches.

`release/<version>` branches are optional and should be used only when a version needs stabilization passes, packaging rehearsals, or manual workflow runs without publishing from the default integration branch.

## Commit Message Convention

Use Conventional Commits.

Preferred forms:

- `feat: add transport probe state summary`
- `fix: reject malformed session migrate ack`
- `docs: clarify wheel build prerequisites`
- `chore: tighten CI dependency bootstrap`
- `test: add protocol vector regression`
- `refactor: simplify packet decode flow`

Rules:

- Keep the subject line imperative.
- Keep the first line concise.
- Use a scope only when it adds clarity.
- You can use multiple local commits while iterating, but normal PRs from `feature/*`, `fix/*`, `docs/*`, or `chore/*` branches must be squashed to exactly one commit before review.
- Only version-maintenance PRs that target or originate from `release/<version>` branches may keep multiple commits when that history is actually needed.

## Pull Request Expectations

Every PR should:

- target the default integration branch, normally `main`
- use the default GitHub PR template that auto-loads on the PR page; specialized reference variants remain in `.github/PULL_REQUEST_TEMPLATE/` when you need to adapt the structure
- explain the user-facing or engineering motivation
- summarize the main modules or flows changed
- list the validation performed
- mention release impact when distribution output changes
- contain exactly one commit before review unless it is a necessary `release/<version>` branch PR
- pass the `required-checks` GitHub Actions job before merge

PRs that violate the normal one-commit rule are not reviewed until they are squashed.

## Validation Expectations

Before opening or merging a PR, prefer the narrowest validation that proves the touched slice:

- `ruff check .`
- `pytest -q`
- `pytest --cov=src/nnrp --cov-report=xml:artifacts/coverage/coverage.xml --cov-fail-under=90 -q` when validating the repository-wide coverage gate
- `python -m build` when wheel or sdist output changed

PRs that affect CI, packaging, or release assets should include the exact command or workflow path used for validation.

Changed production lines under `src/nnrp/` must also keep at least 90% line coverage in CI before merge.

## Versioning and Release Notes

Do not reuse a published package version. If package contents change after publication, create a new version.

When preparing a release PR:

- update the version source intentionally
- confirm package metadata is correct
- confirm release assets have the expected names
- note any manual steps required on registries

## Review Guidelines

Review for:

- protocol and wire compatibility risk
- packaging and release regressions
- missing tests for changed behavior
- CI workflow correctness
- documentation drift when user-facing behavior changes

Do not start normal feature, fix, docs, or maintenance review while the PR still carries multiple commits.