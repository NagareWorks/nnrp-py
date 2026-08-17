from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_release_identity.py"
SPEC = importlib.util.spec_from_file_location("check_release_identity", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
check_release_identity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_release_identity)


def git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout.strip()


def repository(tmp_path: Path) -> Path:
    git(tmp_path, "init")
    git(tmp_path, "config", "user.email", "release-test@example.com")
    git(tmp_path, "config", "user.name", "Release Test")
    (tmp_path / "version.txt").write_text("one\n", encoding="utf-8")
    git(tmp_path, "add", "version.txt")
    git(tmp_path, "commit", "-m", "initial")
    git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    return tmp_path


def test_validate_git_identity_accepts_main_and_same_commit_tag(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    git(repo, "tag", "v1.0.0-preview.4.post15")

    source = check_release_identity.validate_git_identity(
        repo,
        "origin/main",
        "v1.0.0-preview.4.post15",
    )

    assert source == git(repo, "rev-parse", "HEAD")


def test_validate_git_identity_rejects_source_or_tag_drift(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    git(repo, "tag", "v1.0.0-preview.4.post15")
    (repo / "version.txt").write_text("two\n", encoding="utf-8")
    git(repo, "commit", "-am", "second")

    with pytest.raises(check_release_identity.ReleaseIdentityError, match="does not match"):
        check_release_identity.validate_git_identity(repo, "origin/main", "unused")

    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    with pytest.raises(check_release_identity.ReleaseIdentityError, match="already points"):
        check_release_identity.validate_git_identity(repo, "origin/main", "v1.0.0-preview.4.post15")


def test_git_commit_reports_missing_and_command_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = repository(tmp_path)
    assert check_release_identity.git_commit(repo, "refs/tags/missing") is None

    monkeypatch.setattr(
        check_release_identity.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=2, stdout="", stderr="git failed"),
    )
    with pytest.raises(check_release_identity.ReleaseIdentityError, match="git failed"):
        check_release_identity.git_commit(repo, "HEAD")


def test_pypi_version_exists_distinguishes_available_missing_and_failed() -> None:
    def available(request: object, *, timeout: int) -> io.BytesIO:
        assert "nnrp-py/1.0.0rc4.post15" in request.full_url  # type: ignore[attr-defined]
        assert timeout == 20
        return io.BytesIO(json.dumps({"info": {"version": "1.0.0rc4.post15"}}).encode())

    assert check_release_identity.pypi_version_exists("nnrp-py", "1.0.0rc4.post15", opener=available)

    def missing(*_args: object, **_kwargs: object) -> None:
        raise urllib.error.HTTPError("https://pypi.org", 404, "missing", {}, None)

    assert not check_release_identity.pypi_version_exists("nnrp-py", "missing", opener=missing)

    def failed(*_args: object, **_kwargs: object) -> None:
        raise urllib.error.HTTPError("https://pypi.org", 503, "unavailable", {}, None)

    with pytest.raises(check_release_identity.ReleaseIdentityError, match="HTTP 503"):
        check_release_identity.pypi_version_exists("nnrp-py", "failed", opener=failed)


def test_main_checks_pypi_only_when_requested(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(check_release_identity, "validate_git_identity", lambda *_args: "a" * 40)
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        check_release_identity,
        "pypi_version_exists",
        lambda package, version: calls.append((package, version)) or False,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check_release_identity.py",
            "--expected-ref",
            "origin/main",
            "--tag",
            "v1.0.0-preview.4.post15",
            "--package",
            "nnrp-py",
            "--version",
            "1.0.0rc4.post15",
            "--check-pypi",
        ],
    )

    check_release_identity.main()

    assert calls == [("nnrp-py", "1.0.0rc4.post15")]
    assert "verified release identity" in capsys.readouterr().out
