# Contributing to nnrp-py

This repository publishes Python SDK packages and protocol tooling, so contribution flow needs to stay predictable.

## Branch Strategy

The repository default branch is the stable branch for released or release-ready SDK state.

For the public GitHub repository, use `main` as the stable branch.

`develop` is the version integration branch for active preview work. Preview feature, fix, documentation, and maintenance branches should merge into `develop` first. For the preview3 line, `develop` carries the work that used to live on `release/1.0.0-preview.3`.

Use short-lived topic branches for day-to-day work:

- `feature/<scope>-<topic>` for new capabilities
- `fix/<scope>-<topic>` for bug fixes
- `docs/<scope>-<topic>` for documentation-only changes
- `chore/<scope>-<topic>` for maintenance and tooling updates
- `release/<version>` only after `develop` is ready to freeze into a public package release candidate

Rules:

- Branch from the latest `develop` for active preview work.
- Branch from `main` only for hotfixes against already released stable state.
- Keep topic branches focused on one slice of work.
- Rebase or merge from `develop` regularly if the branch stays open.
- Merge normal preview work back to `develop` through a pull request.
- Do not push directly to `main`; enforce this with a GitHub ruleset or branch protection rule.
- Do not push directly to `develop`; enforce this with a GitHub ruleset or branch protection rule when the repository is public.
- Do not publish packages directly from topic branches.

`release/<version>` branches are freeze branches. Cut them from `develop` only when the version is feature-complete enough for stabilization passes, packaging rehearsals, or manual workflow runs. Keep release branches short-lived unless a published line needs explicit long-term maintenance.

After a release branch is cut:

- accept only release-blocking fixes, version metadata, package metadata, and release documentation on that branch
- merge accepted fixes back to `develop`
- tag the final release from the release branch or from the merged stable state, according to the release workflow
- delete the release branch after publication unless it represents an explicitly maintained LTS line

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

- target `develop` for normal preview work, `main` for stable hotfixes, or `release/<version>` only during an active release freeze
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
- `pytest --cov=src/nnrp --cov=scripts --cov-report=xml:artifacts/coverage/coverage.xml --cov-fail-under=90 -q` when validating the repository-wide coverage gate
- `python -m build` when wheel or sdist output changed

PRs that affect CI, packaging, or release assets should include the exact command or workflow path used for validation.

Changed production lines under `src/nnrp/` or `scripts/` must also keep at least 90% line coverage in CI before merge.

## Versioning and Release Notes

Do not reuse a published package version. If package contents change after publication, create a new version.

When preparing a release PR:

- update the version source intentionally
- confirm package metadata is correct
- confirm release assets have the expected names
- note any manual steps required on registries

Public package publishing is gated through the `Release` workflow and should only happen from a short release tag or an explicit manual dispatch.

- `Release` runs on pushed `v*` tags and on manual `workflow_dispatch`; normal branch pushes must not publish GitHub releases or PyPI packages.
- Manual `workflow_dispatch` runs should leave external publishing disabled unless you intentionally enable `create_tag`; package publication from an untagged ref is not allowed.
- Use the `release` GitHub environment for any publish-capable job.
- Set `PYPI_PUBLISH_MODE` on the `release` environment to `disabled`, `trusted`, or `token` so tags do not publish to PyPI accidentally before registry binding is ready.
- If you keep token-based publishing, store `PYPI_API_TOKEN` as an environment secret on `release`, not as an unrestricted repository secret.
- If you switch to PyPI Trusted Publisher, keep `PYPI_PUBLISH_MODE=trusted`; no PyPI secret is required, but the `release` environment is still recommended for approvals, tag restrictions, and job scoping.

## Review Guidelines

Review for:

- protocol and wire compatibility risk
- packaging and release regressions
- missing tests for changed behavior
- CI workflow correctness
- documentation drift when user-facing behavior changes

Do not start normal feature, fix, docs, or maintenance review while the PR still carries multiple commits.
