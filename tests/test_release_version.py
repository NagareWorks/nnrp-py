from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "resolve_version.py"
SPEC = importlib.util.spec_from_file_location("resolve_version", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
resolve_version = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resolve_version)


def test_read_release_version_reads_version_from_pyproject(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text('[project]\nversion = "1.0.0rc2.dev202605182"\n', encoding="utf-8")

    monkeypatch.setattr(resolve_version, "PYPROJECT_PATH", pyproject_path)

    assert resolve_version.read_release_version() == "1.0.0rc2.dev202605182"


def test_build_package_version_returns_release_version_without_run_number() -> None:
    assert resolve_version.build_package_version("1.0.0rc2", "20260518", None) == "1.0.0rc2"


def test_build_package_version_uses_current_date_when_version_date_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    real_datetime = dt.datetime

    class FrozenDateTime:
        @classmethod
        def utcnow(cls) -> dt.datetime:
            return real_datetime(2026, 5, 18, 12, 0, 0)

    monkeypatch.setattr(resolve_version.dt, "datetime", FrozenDateTime)

    assert resolve_version.build_package_version("1.0.0rc2", None, "7") == "1.0.0rc2.dev202605187"


def test_build_tag_name_uses_short_preview_tag_for_release_candidates() -> None:
    assert resolve_version.build_tag_name("1.0.0rc2") == "v1.0.0-preview.2"
    assert resolve_version.build_tag_name("1.0.0rc3.post1") == "v1.0.0-preview.3.post1"
    assert resolve_version.build_tag_name("1.0.0rc4") == "v1.0.0-preview.4"
    assert resolve_version.build_tag_name("1.0.0rc4.post1") == "v1.0.0-preview.4.post1"


def test_current_release_version_tracks_preview4() -> None:
    release_version = resolve_version.read_release_version()

    assert release_version == "1.0.0rc4.post15"
    assert resolve_version.build_tag_name(release_version) == "v1.0.0-preview.4.post15"


def test_build_tag_name_keeps_full_version_for_non_preview_release() -> None:
    assert resolve_version.build_tag_name("1.0.0") == "v1.0.0"


def test_build_release_name_uses_complete_package_version() -> None:
    package_version = resolve_version.build_package_version("1.0.0rc2", "20260518", "42")

    assert package_version == "1.0.0rc2.dev2026051842"
    assert resolve_version.build_release_name(package_version) == "nnrp-py v1.0.0rc2.dev2026051842"


def test_is_prerelease_distinguishes_preview_and_stable_versions() -> None:
    assert resolve_version.is_prerelease("1.0.0rc4.post15") is True
    assert resolve_version.is_prerelease("1.0.0.dev202608171") is True
    assert resolve_version.is_prerelease("1.0.0") is False


def test_write_outputs_prints_values_when_github_output_disabled(capsys: pytest.CaptureFixture[str]) -> None:
    resolve_version.write_outputs(
        {"tag_name": "v1.0.0-preview.2", "release_name": "nnrp-py v1.0.0rc2"},
        github_output=False,
    )

    assert capsys.readouterr().out == "tag_name=v1.0.0-preview.2\nrelease_name=nnrp-py v1.0.0rc2\n"


def test_write_outputs_appends_to_github_output_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output_path = tmp_path / "github-output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))

    resolve_version.write_outputs({"tag_name": "v1.0.0-preview.2"}, github_output=True)

    assert output_path.read_text(encoding="utf-8") == "tag_name=v1.0.0-preview.2\n"


def test_replace_once_updates_matching_line(tmp_path: Path) -> None:
    target = tmp_path / "pyproject.toml"
    target.write_text('version = "1.0.0rc2"\n', encoding="utf-8")

    resolve_version.replace_once(target, r'^version = "[^"]+"$', 'version = "1.0.0rc2.dev2026051842"')

    assert target.read_text(encoding="utf-8") == 'version = "1.0.0rc2.dev2026051842"\n'


def test_replace_once_requires_exactly_one_match(tmp_path: Path) -> None:
    target = tmp_path / "pyproject.toml"
    target.write_text("no version here\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Expected exactly one replacement"):
        resolve_version.replace_once(target, r'^version = "[^"]+"$', 'version = "1.0.0"')


def test_cmd_show_writes_all_release_outputs(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_write_outputs(values: dict[str, str], github_output: bool) -> None:
        captured["values"] = values
        captured["github_output"] = github_output

    monkeypatch.setattr(resolve_version, "read_release_version", lambda: "1.0.0rc2")
    monkeypatch.setattr(resolve_version, "write_outputs", fake_write_outputs)

    resolve_version.cmd_show(argparse.Namespace(version_date="20260518", run_number="42", github_output=True))

    assert captured == {
        "values": {
            "release_version": "1.0.0rc2",
            "package_version": "1.0.0rc2.dev2026051842",
            "tag_name": "v1.0.0-preview.2",
            "release_name": "nnrp-py v1.0.0rc2.dev2026051842",
            "is_prerelease": "true",
        },
        "github_output": True,
    }


def test_cmd_show_uses_release_version_for_manual_or_tag_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_write_outputs(values: dict[str, str], github_output: bool) -> None:
        captured["values"] = values
        captured["github_output"] = github_output

    monkeypatch.setattr(resolve_version, "read_release_version", lambda: "1.0.0rc2")
    monkeypatch.setattr(resolve_version, "write_outputs", fake_write_outputs)

    resolve_version.cmd_show(argparse.Namespace(version_date="20260518", run_number="", github_output=True))

    assert captured == {
        "values": {
            "release_version": "1.0.0rc2",
            "package_version": "1.0.0rc2",
            "tag_name": "v1.0.0-preview.2",
            "release_name": "nnrp-py v1.0.0rc2",
            "is_prerelease": "true",
        },
        "github_output": True,
    }


def test_cmd_apply_updates_pyproject_and_package_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pyproject_path = tmp_path / "pyproject.toml"
    package_init_path = tmp_path / "__init__.py"
    pyproject_path.write_text('version = "1.0.0rc2"\n', encoding="utf-8")
    package_init_path.write_text('__version__ = "1.0.0rc2"\n', encoding="utf-8")

    monkeypatch.setattr(resolve_version, "PYPROJECT_PATH", pyproject_path)
    monkeypatch.setattr(resolve_version, "PACKAGE_INIT_PATH", package_init_path)

    resolve_version.cmd_apply(argparse.Namespace(package_version="1.0.0rc2.dev2026051842"))

    assert pyproject_path.read_text(encoding="utf-8") == 'version = "1.0.0rc2.dev2026051842"\n'
    assert package_init_path.read_text(encoding="utf-8") == '__version__ = "1.0.0rc2.dev2026051842"\n'


def test_build_parser_sets_show_and_apply_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_show(args: argparse.Namespace) -> None:
        raise AssertionError(f"unexpected call: {args}")

    def fake_apply(args: argparse.Namespace) -> None:
        raise AssertionError(f"unexpected call: {args}")

    monkeypatch.setattr(resolve_version, "cmd_show", fake_show)
    monkeypatch.setattr(resolve_version, "cmd_apply", fake_apply)

    parser = resolve_version.build_parser()

    assert parser.parse_args(["show"]).func is fake_show
    assert parser.parse_args(["apply", "--package-version", "1.0.0"]).func is fake_apply


def test_main_dispatches_selected_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, str] = {}

    def fake_show(args: argparse.Namespace) -> None:
        called["command"] = args.command

    monkeypatch.setattr(resolve_version, "cmd_show", fake_show)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT_PATH), "show"])

    resolve_version.main()

    assert called == {"command": "show"}
