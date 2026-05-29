from __future__ import annotations

import sys
import zipfile
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "extract_cffi_api_wheels.py"
_SPEC = spec_from_file_location("extract_cffi_api_wheels", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

extract_cffi_api_wheels = _MODULE.extract_cffi_api_wheels
extract_cffi_api_wheels_main = _MODULE.main
_artifact_tag_from_wheel = _MODULE._artifact_tag_from_wheel


def _write_cffi_wheel(path: Path, extension_name: str = "nnrp/_nnrp_cffi_api_submit_result.abi3.so") -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(extension_name, b"compiled")
        archive.writestr("nnrp_py_cffi_api-0.0.0.dist-info/WHEEL", b"Tag: cp311-abi3-test\n")
        archive.writestr("nnrp_py_cffi_api-0.0.0.dist-info/RECORD", b"")
    return path


def test_extract_cffi_api_wheels_extracts_compiled_extension_by_platform_tag(tmp_path: Path) -> None:
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    _write_cffi_wheel(wheel_dir / "nnrp_py_cffi_api-0.0.0-cp311-abi3-win_amd64.whl")

    extracted = extract_cffi_api_wheels(wheel_dir, tmp_path / "out")

    assert extracted == [
        tmp_path / "out" / "windows-x86_64" / "nnrp" / "_nnrp_cffi_api_submit_result.abi3.so"
    ]
    assert extracted[0].read_bytes() == b"compiled"


@pytest.mark.parametrize(
    ("platform_tag", "artifact_tag"),
    [
        ("android_21_x86", "android-x86"),
        ("android_24_x86_64", "android-x86_64"),
        ("android_24_armeabi_v7a", "android-arm"),
        ("android_24_arm64_v8a", "android-arm64"),
        ("ios_13_0_arm64_iphoneos", "ios-arm64"),
        ("ios_13_0_arm64_iphonesimulator", "ios-arm64-sim"),
        ("ios_13_0_x86_64_iphonesimulator", "ios-x86_64-sim"),
    ],
)
def test_extract_cffi_api_wheels_maps_mobile_tags(tmp_path: Path, platform_tag: str, artifact_tag: str) -> None:
    wheel = tmp_path / f"nnrp_py_cffi_api-0.0.0-cp311-abi3-{platform_tag}.whl"
    wheel.touch()

    assert _artifact_tag_from_wheel(wheel) == artifact_tag


def test_extract_cffi_api_wheels_rejects_missing_compiled_extension(tmp_path: Path) -> None:
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    wheel = wheel_dir / "nnrp_py_cffi_api-0.0.0-cp311-abi3-manylinux_2_28_x86_64.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("nnrp/_nnrp_cffi_api_submit_result.py", b"pure python")

    with pytest.raises(ValueError, match="expected exactly one compiled cffi API extension"):
        extract_cffi_api_wheels(wheel_dir, tmp_path / "out")


def test_extract_cffi_api_wheels_rejects_unknown_platform(tmp_path: Path) -> None:
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    _write_cffi_wheel(wheel_dir / "nnrp_py_cffi_api-0.0.0-cp311-abi3-plan9_x86_64.whl")

    with pytest.raises(ValueError, match="no native artifact tag mapping"):
        extract_cffi_api_wheels(wheel_dir, tmp_path / "out")


def test_extract_cffi_api_wheels_rejects_empty_wheel_dir(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no cffi API wheels found"):
        extract_cffi_api_wheels(tmp_path / "wheels", tmp_path / "out")


def test_extract_cffi_api_wheels_cli_prints_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    _write_cffi_wheel(wheel_dir / "nnrp_py_cffi_api-0.0.0-cp311-abi3-win_amd64.whl")
    monkeypatch.setattr(
        sys,
        "argv",
        ["extract_cffi_api_wheels.py", "--wheel-dir", str(wheel_dir), "--output", str(tmp_path / "out")],
    )

    assert extract_cffi_api_wheels_main() == 0

    assert "windows-x86_64" in capsys.readouterr().out
