from __future__ import annotations

import argparse
import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any


class ReleaseIdentityError(RuntimeError):
    pass


def git_commit(repository: Path, revision: str) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"],
        cwd=repository,
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode == 1:
        return None
    if result.returncode != 0:
        raise ReleaseIdentityError(result.stderr.strip() or f"failed to resolve {revision}")
    return result.stdout.strip()


def validate_git_identity(repository: Path, expected_ref: str, tag_name: str) -> str:
    head = git_commit(repository, "HEAD")
    expected = git_commit(repository, expected_ref)
    if head is None or expected is None:
        raise ReleaseIdentityError(f"release source or expected ref is missing: HEAD, {expected_ref}")
    if head != expected:
        raise ReleaseIdentityError(f"release source {head} does not match {expected_ref} at {expected}")

    tag_commit = git_commit(repository, f"refs/tags/{tag_name}")
    if tag_commit is not None and tag_commit != head:
        raise ReleaseIdentityError(f"release tag {tag_name} already points to {tag_commit}, not {head}")
    return head


def pypi_version_exists(
    package_name: str,
    package_version: str,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> bool:
    package = urllib.parse.quote(package_name, safe="")
    version = urllib.parse.quote(package_version, safe="")
    request = urllib.request.Request(
        f"https://pypi.org/pypi/{package}/{version}/json",
        headers={"Accept": "application/json"},
    )
    try:
        with opener(request, timeout=20) as response:
            json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise ReleaseIdentityError(f"PyPI identity lookup failed with HTTP {error.code}") from error
    except (OSError, ValueError) as error:
        raise ReleaseIdentityError(f"PyPI identity lookup failed: {error}") from error
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Reject ambiguous or reused NNRP Python release identities.")
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--expected-ref", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--check-pypi", action="store_true")
    args = parser.parse_args()

    source_commit = validate_git_identity(args.repository.resolve(), args.expected_ref, args.tag)
    if args.check_pypi and pypi_version_exists(args.package, args.version):
        raise ReleaseIdentityError(f"{args.package} {args.version} already exists on PyPI")
    print(f"verified release identity: {args.package} {args.version} from {source_commit}")


if __name__ == "__main__":
    main()
