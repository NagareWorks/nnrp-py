from __future__ import annotations

import sys
import zipfile
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_native_wheel.py"
_SPEC = spec_from_file_location("verify_native_wheel", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

inspect_dist = _MODULE.inspect_dist
inspect_wheel = _MODULE.inspect_wheel
verify_native_wheels = _MODULE.verify_native_wheels
verify_native_wheel_main = _MODULE.main


def _write_wheel(path: Path, names: list[str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, b"wheel-data")
    return path


def test_inspect_wheel_finds_packaged_native_artifacts(tmp_path: Path) -> None:
    wheel = _write_wheel(
        tmp_path / "nnrp_py-1.0.0rc3-py3-none-win_amd64.whl",
        [
            "nnrp/native_artifacts/windows-x86_64/manifest.json",
            "nnrp/native_artifacts/windows-x86_64/nnrp_ffi.dll",
        ],
    )

    summary = inspect_wheel(wheel)

    assert summary.has_native_artifacts is True
    assert summary.manifests == ("nnrp/native_artifacts/windows-x86_64/manifest.json",)
    assert summary.libraries == ("nnrp/native_artifacts/windows-x86_64/nnrp_ffi.dll",)
    assert summary.artifact_tags == ("windows-x86_64",)
    assert summary.platform_tag == "win_amd64"


def test_verify_native_wheels_rejects_empty_native_payload(tmp_path: Path) -> None:
    wheel = _write_wheel(tmp_path / "nnrp_py-1.0.0rc3-py3-none-any.whl", ["nnrp/native_artifacts/.gitkeep"])
    summary = inspect_wheel(wheel)

    with pytest.raises(ValueError, match="missing packaged native artifacts"):
        verify_native_wheels([summary], require_native=True)


def test_verify_native_wheels_allows_empty_native_payload_when_not_required(tmp_path: Path) -> None:
    wheel = _write_wheel(tmp_path / "nnrp_py-1.0.0rc3-py3-none-any.whl", ["nnrp/native_artifacts/.gitkeep"])
    summary = inspect_wheel(wheel)

    verify_native_wheels([summary], require_native=False)


def test_verify_native_wheels_rejects_native_payload_in_universal_wheel(tmp_path: Path) -> None:
    wheel = _write_wheel(
        tmp_path / "nnrp_py-1.0.0rc3-py3-none-any.whl",
        [
            "nnrp/native_artifacts/linux-x86_64/manifest.json",
            "nnrp/native_artifacts/linux-x86_64/libnnrp_ffi.so",
        ],
    )
    summary = inspect_wheel(wheel)

    with pytest.raises(ValueError, match="universal 'any' platform tag"):
        verify_native_wheels([summary], require_native=True, reject_universal_native=True)


def test_verify_native_wheels_rejects_multiple_embedded_platforms(tmp_path: Path) -> None:
    wheel = _write_wheel(
        tmp_path / "nnrp_py-1.0.0rc3-py3-none-manylinux_2_28_x86_64.whl",
        [
            "nnrp/native_artifacts/linux-x86_64/manifest.json",
            "nnrp/native_artifacts/linux-x86_64/libnnrp_ffi.so",
            "nnrp/native_artifacts/linux-arm64/manifest.json",
            "nnrp/native_artifacts/linux-arm64/libnnrp_ffi.so",
        ],
    )
    summary = inspect_wheel(wheel)

    with pytest.raises(ValueError, match="exactly one native artifact platform"):
        verify_native_wheels([summary], require_native=True, require_single_platform=True)


def test_verify_native_wheels_rejects_platform_tag_mismatch(tmp_path: Path) -> None:
    wheel = _write_wheel(
        tmp_path / "nnrp_py-1.0.0rc3-py3-none-win_amd64.whl",
        [
            "nnrp/native_artifacts/linux-x86_64/manifest.json",
            "nnrp/native_artifacts/linux-x86_64/libnnrp_ffi.so",
        ],
    )
    summary = inspect_wheel(wheel)

    with pytest.raises(ValueError, match="platform tag does not match"):
        verify_native_wheels([summary], require_native=True, verify_platform_tag=True)


def test_inspect_wheel_rejects_invalid_wheel_filenames(tmp_path: Path) -> None:
    not_wheel = tmp_path / "artifact.zip"
    not_wheel.write_bytes(b"")
    with pytest.raises(ValueError, match="not a wheel file"):
        inspect_wheel(not_wheel)

    invalid_name = _write_wheel(tmp_path / "bad.whl", [])
    with pytest.raises(ValueError, match="invalid wheel filename"):
        inspect_wheel(invalid_name)


def test_inspect_dist_rejects_missing_wheels(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no wheel files"):
        inspect_dist(tmp_path)


def test_verify_native_wheel_cli_reports_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_wheel(
        tmp_path / "nnrp_py-1.0.0rc3-py3-none-manylinux_2_28_x86_64.whl",
        [
            "nnrp/native_artifacts/linux-x86_64/manifest.json",
            "nnrp/native_artifacts/linux-x86_64/libnnrp_ffi.so",
        ],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_native_wheel.py",
            "--dist",
            str(tmp_path),
            "--require-native",
            "--reject-universal-native",
            "--require-single-platform",
            "--verify-platform-tag",
        ],
    )

    assert verify_native_wheel_main() == 0
    captured = capsys.readouterr().out
    assert "1 manifest(s), 1 native library" in captured
    assert "platform=manylinux_2_28_x86_64" in captured
