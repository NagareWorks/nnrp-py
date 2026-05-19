from __future__ import annotations

import argparse
import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_incremental_coverage.py"
SPEC = importlib.util.spec_from_file_location("check_incremental_coverage", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
check_incremental_coverage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_incremental_coverage)


def test_parse_args_reads_required_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            str(SCRIPT_PATH),
            "--base-sha",
            "base",
            "--head-sha",
            "head",
            "--threshold",
            "90",
            "--coverage-xml",
            "coverage.xml",
        ],
    )

    args = check_incremental_coverage.parse_args()

    assert args.base_sha == "base"
    assert args.head_sha == "head"
    assert args.threshold == 90
    assert args.coverage_xml == "coverage.xml"


def test_load_coverage_parses_relative_paths_and_hits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    coverage_xml = tmp_path / "coverage.xml"
    coverage_xml.write_text(
                f"""<?xml version=\"1.0\" ?>
<coverage>
  <sources>
        <source>{tmp_path.as_posix()}</source>
  </sources>
  <packages>
    <package>
      <classes>
        <class filename=\"scripts/resolve_version.py\">
          <lines>
            <line number=\"10\" hits=\"1\" />
            <line number=\"11\" hits=\"0\" />
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    coverage_by_file = check_incremental_coverage.load_coverage(coverage_xml)

    assert coverage_by_file == {"scripts/resolve_version.py": {10: True, 11: False}}


def test_load_coverage_requires_source_roots(tmp_path: Path) -> None:
    coverage_xml = tmp_path / "coverage.xml"
    coverage_xml.write_text("<coverage><sources /></coverage>", encoding="utf-8")

    with pytest.raises(ValueError, match="coverage xml does not contain any source roots"):
        check_incremental_coverage.load_coverage(coverage_xml)


def test_load_coverage_prefers_existing_candidate_in_multi_source_xml(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
) -> None:
        repo_root = tmp_path / "repo"
        scripts_root = repo_root / "scripts"
        src_root = repo_root / "src" / "nnrp"
        (src_root / "tools").mkdir(parents=True)
        (src_root / "tools" / "conformance.py").write_text("pass\n", encoding="utf-8")

        coverage_xml = tmp_path / "coverage.xml"
        coverage_xml.write_text(
                f"""<?xml version=\"1.0\" ?>
<coverage>
    <sources>
        <source>{scripts_root.as_posix()}</source>
        <source>{src_root.as_posix()}</source>
    </sources>
    <packages>
        <package>
            <classes>
                <class filename=\"tools/conformance.py\">
                    <lines>
                        <line number=\"10\" hits=\"1\" />
                    </lines>
                </class>
            </classes>
        </package>
    </packages>
</coverage>
""",
                encoding="utf-8",
        )
        monkeypatch.chdir(repo_root)

        coverage_by_file = check_incremental_coverage.load_coverage(coverage_xml)

        assert coverage_by_file == {"src/nnrp/tools/conformance.py": {10: True}}


def test_load_changed_lines_parses_added_lines_in_scripts_and_src(monkeypatch: pytest.MonkeyPatch) -> None:
    diff_output = "\n".join(
        [
            "diff --git a/scripts/resolve_version.py b/scripts/resolve_version.py",
            "+++ b/scripts/resolve_version.py",
            "@@ -10,0 +11,2 @@",
            "+first",
            "+second",
            "diff --git a/src/nnrp/core/packet.py b/src/nnrp/core/packet.py",
            "+++ b/src/nnrp/core/packet.py",
            "@@ -20,0 +21 @@",
            "+third",
        ]
    )

    def fake_run(command: list[str], check: bool, capture_output: bool, text: bool) -> subprocess.CompletedProcess[str]:
        assert command == [
            "git",
            "diff",
            "--unified=0",
            "base",
            "head",
            "--",
            "src/nnrp",
            "scripts",
        ]
        assert check is True
        assert capture_output is True
        assert text is True
        return subprocess.CompletedProcess(command, 0, stdout=diff_output, stderr="")

    monkeypatch.setattr(check_incremental_coverage.subprocess, "run", fake_run)

    assert check_incremental_coverage.load_changed_lines("base", "head") == {
        "scripts/resolve_version.py": {11, 12},
        "src/nnrp/core/packet.py": {21},
    }


def test_load_changed_lines_rejects_unsupported_hunk_header(monkeypatch: pytest.MonkeyPatch) -> None:
    diff_output = "\n".join(
        [
            "diff --git a/scripts/resolve_version.py b/scripts/resolve_version.py",
            "+++ b/scripts/resolve_version.py",
            "@@ invalid hunk @@",
        ]
    )

    def fake_run(command: list[str], check: bool, capture_output: bool, text: bool) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout=diff_output, stderr="")

    monkeypatch.setattr(check_incremental_coverage.subprocess, "run", fake_run)

    with pytest.raises(ValueError, match="unsupported diff hunk header"):
        check_incremental_coverage.load_changed_lines("base", "head")


def test_is_uncomparable_git_range_detects_missing_base_commit() -> None:
    error = subprocess.CalledProcessError(
        128,
        ["git", "diff"],
        stderr="fatal: bad object 5ddffa6ee3b569f90c4aff21f78bfcd37569501a\n",
    )

    assert check_incremental_coverage.is_uncomparable_git_range(error) is True


def test_is_uncomparable_git_range_rejects_other_git_failures() -> None:
    error = subprocess.CalledProcessError(1, ["git", "diff"], stderr="fatal: ambiguous argument\n")

    assert check_incremental_coverage.is_uncomparable_git_range(error) is False


def test_main_skips_when_git_range_is_not_comparable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        check_incremental_coverage,
        "parse_args",
        lambda: argparse.Namespace(
            base_sha="0000000000000000000000000000000000000000",
            head_sha="head",
            threshold=90.0,
            coverage_xml="coverage.xml",
        ),
    )

    assert check_incremental_coverage.main() == 0
    assert "No comparable git range was provided" in capsys.readouterr().out


def test_main_skips_when_no_changed_executable_lines_are_found(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        check_incremental_coverage,
        "parse_args",
        lambda: argparse.Namespace(base_sha="base", head_sha="head", threshold=90.0, coverage_xml="coverage.xml"),
    )
    monkeypatch.setattr(
        check_incremental_coverage,
        "load_coverage",
        lambda path: {"scripts/resolve_version.py": {20: True}},
    )
    monkeypatch.setattr(
        check_incremental_coverage,
        "load_changed_lines",
        lambda base, head: {"scripts/resolve_version.py": {10}},
    )

    assert check_incremental_coverage.main() == 0
    assert "No changed executable production lines were found under src/nnrp, scripts" in capsys.readouterr().out


def test_main_skips_when_git_range_is_not_available_locally(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        check_incremental_coverage,
        "parse_args",
        lambda: argparse.Namespace(base_sha="base", head_sha="head", threshold=90.0, coverage_xml="coverage.xml"),
    )
    monkeypatch.setattr(
        check_incremental_coverage,
        "load_coverage",
        lambda path: {"scripts/resolve_version.py": {10: True}},
    )

    def raise_uncomparable(base: str, head: str) -> dict[str, set[int]]:
        raise subprocess.CalledProcessError(
            128,
            ["git", "diff", "--unified=0", base, head],
            stderr="fatal: bad object 5ddffa6ee3b569f90c4aff21f78bfcd37569501a\n",
        )

    monkeypatch.setattr(check_incremental_coverage, "load_changed_lines", raise_uncomparable)

    assert check_incremental_coverage.main() == 0
    assert "No comparable git range was available locally" in capsys.readouterr().out


def test_main_fails_when_incremental_coverage_is_below_threshold(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        check_incremental_coverage,
        "parse_args",
        lambda: argparse.Namespace(base_sha="base", head_sha="head", threshold=90.0, coverage_xml="coverage.xml"),
    )
    monkeypatch.setattr(
        check_incremental_coverage,
        "load_coverage",
        lambda path: {"scripts/resolve_version.py": {10: True, 11: False}},
    )
    monkeypatch.setattr(
        check_incremental_coverage,
        "load_changed_lines",
        lambda base, head: {"scripts/resolve_version.py": {10, 11}},
    )

    assert check_incremental_coverage.main() == 1
    output = capsys.readouterr().out
    assert "Incremental coverage: 1/2 changed executable lines covered (50.00%)." in output
    assert "Required incremental coverage threshold: 90.00%." in output
    assert "- scripts/resolve_version.py:11" in output


def test_main_passes_when_incremental_coverage_meets_threshold(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        check_incremental_coverage,
        "parse_args",
        lambda: argparse.Namespace(base_sha="base", head_sha="head", threshold=90.0, coverage_xml="coverage.xml"),
    )
    monkeypatch.setattr(
        check_incremental_coverage,
        "load_coverage",
        lambda path: {"scripts/resolve_version.py": {10: True, 11: True}},
    )
    monkeypatch.setattr(
        check_incremental_coverage,
        "load_changed_lines",
        lambda base, head: {"scripts/resolve_version.py": {10, 11}},
    )

    assert check_incremental_coverage.main() == 0
    assert "Incremental coverage: 2/2 changed executable lines covered (100.00%)." in capsys.readouterr().out