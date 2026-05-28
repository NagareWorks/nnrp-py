from __future__ import annotations

import sys
import zipfile
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_native_wheels.py"
_SPEC = spec_from_file_location("build_native_wheels", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

build_native_wheels = _MODULE.build_native_wheels
build_native_wheels_main = _MODULE.main
_cffi_entries_for_artifact = _MODULE._cffi_entries_for_artifact
_python_abi_tags = _MODULE._python_abi_tags
_retag_wheel_metadata = _MODULE._retag_wheel_metadata


def _write_staging_wheel(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("nnrp/__init__.py", b"")
        archive.writestr("nnrp/native_artifacts/linux-x86_64/manifest.json", b"{}")
        archive.writestr("nnrp/native_artifacts/linux-x86_64/libnnrp_ffi.so", b"linux")
        archive.writestr("nnrp/native_artifacts/windows-x86_64/manifest.json", b"{}")
        archive.writestr("nnrp/native_artifacts/windows-x86_64/nnrp_ffi.dll", b"windows")
        archive.writestr("nnrp/native_artifacts/ios-arm64-sim/manifest.json", b"{}")
        archive.writestr("nnrp/native_artifacts/ios-arm64-sim/libnnrp_ffi.a", b"ios-sim")
        archive.writestr(
            "nnrp_py-1.0.0rc3.dist-info/WHEEL",
            b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr("nnrp_py-1.0.0rc3.dist-info/METADATA", b"Name: nnrp-py\n")
        archive.writestr("nnrp_py-1.0.0rc3.dist-info/RECORD", b"")
    return path


def test_build_native_wheels_splits_artifacts_and_retags_platforms(tmp_path: Path) -> None:
    source = _write_staging_wheel(tmp_path / "nnrp_py-1.0.0rc3-py3-none-any.whl")
    output = tmp_path / "dist"

    built = build_native_wheels(source, output)

    assert [wheel.name for wheel in built] == [
        "nnrp_py-1.0.0rc3-py3-none-ios_13_0_arm64_iphonesimulator.whl",
        "nnrp_py-1.0.0rc3-py3-none-manylinux_2_28_x86_64.whl",
        "nnrp_py-1.0.0rc3-py3-none-win_amd64.whl",
    ]

    linux_wheel = built[1]
    with zipfile.ZipFile(linux_wheel) as archive:
        names = set(archive.namelist())
        wheel_metadata = archive.read("nnrp_py-1.0.0rc3.dist-info/WHEEL").decode("utf-8")

    assert "nnrp/native_artifacts/linux-x86_64/libnnrp_ffi.so" in names
    assert "nnrp/native_artifacts/windows-x86_64/nnrp_ffi.dll" not in names
    assert "Root-Is-Purelib: false" in wheel_metadata
    assert "Tag: py3-none-manylinux_2_28_x86_64" in wheel_metadata
    assert "nnrp_py-1.0.0rc3.dist-info/RECORD" in names

    ios_wheel = built[0]
    with zipfile.ZipFile(ios_wheel) as archive:
        ios_names = set(archive.namelist())
        ios_metadata = archive.read("nnrp_py-1.0.0rc3.dist-info/WHEEL").decode("utf-8")

    assert "nnrp/native_artifacts/ios-arm64-sim/libnnrp_ffi.a" in ios_names
    assert "nnrp/native_artifacts/linux-x86_64/libnnrp_ffi.so" not in ios_names
    assert "Tag: py3-none-ios_13_0_arm64_iphonesimulator" in ios_metadata


def test_build_native_wheels_injects_matching_cffi_api_artifacts(tmp_path: Path) -> None:
    source = _write_staging_wheel(tmp_path / "nnrp_py-1.0.0rc3-py3-none-any.whl")
    output = tmp_path / "dist"
    cffi_dir = tmp_path / "cffi-api"
    linux_cffi = cffi_dir / "linux-x86_64" / "nnrp" / "_nnrp_cffi_api_submit_result.cpython-311-x86_64-linux-gnu.so"
    windows_cffi = cffi_dir / "windows-x86_64" / "nnrp" / "_nnrp_cffi_api_submit_result.cp312-win_amd64.pyd"
    linux_cffi.parent.mkdir(parents=True)
    windows_cffi.parent.mkdir(parents=True)
    linux_cffi.write_bytes(b"linux-cffi")
    windows_cffi.write_bytes(b"windows-cffi")

    built = build_native_wheels(source, output, cffi_dir=cffi_dir)

    assert [wheel.name for wheel in built] == [
        "nnrp_py-1.0.0rc3-py3-none-ios_13_0_arm64_iphonesimulator.whl",
        "nnrp_py-1.0.0rc3-cp311-cp311-manylinux_2_28_x86_64.whl",
        "nnrp_py-1.0.0rc3-cp312-cp312-win_amd64.whl",
    ]
    with zipfile.ZipFile(built[1]) as archive:
        linux_names = set(archive.namelist())
        linux_metadata = archive.read("nnrp_py-1.0.0rc3.dist-info/WHEEL").decode("utf-8")
        linux_cffi_payload = archive.read("nnrp/_nnrp_cffi_api_submit_result.cpython-311-x86_64-linux-gnu.so")
    with zipfile.ZipFile(built[2]) as archive:
        windows_names = set(archive.namelist())
        windows_metadata = archive.read("nnrp_py-1.0.0rc3.dist-info/WHEEL").decode("utf-8")
        windows_cffi_payload = archive.read("nnrp/_nnrp_cffi_api_submit_result.cp312-win_amd64.pyd")
    with zipfile.ZipFile(built[0]) as archive:
        ios_metadata = archive.read("nnrp_py-1.0.0rc3.dist-info/WHEEL").decode("utf-8")

    assert "Tag: py3-none-ios_13_0_arm64_iphonesimulator" in ios_metadata
    assert "nnrp/_nnrp_cffi_api_submit_result.cpython-311-x86_64-linux-gnu.so" in linux_names
    assert "nnrp/_nnrp_cffi_api_submit_result.cp312-win_amd64.pyd" not in linux_names
    assert linux_cffi_payload == b"linux-cffi"
    assert "Tag: cp311-cp311-manylinux_2_28_x86_64" in linux_metadata
    assert "nnrp/_nnrp_cffi_api_submit_result.cp312-win_amd64.pyd" in windows_names
    assert "nnrp/_nnrp_cffi_api_submit_result.cpython-311-x86_64-linux-gnu.so" not in windows_names
    assert windows_cffi_payload == b"windows-cffi"
    assert "Tag: cp312-cp312-win_amd64" in windows_metadata


def test_build_native_wheels_ignores_missing_cffi_dir_and_rejects_file_path(tmp_path: Path) -> None:
    assert _cffi_entries_for_artifact(tmp_path / "missing", "linux-x86_64") == {}

    cffi_file = tmp_path / "cffi-api" / "linux-x86_64"
    cffi_file.parent.mkdir()
    cffi_file.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ValueError, match="must be a directory"):
        _cffi_entries_for_artifact(tmp_path / "cffi-api", "linux-x86_64")


def test_build_native_wheels_rejects_mixed_cffi_python_abi_tags() -> None:
    with pytest.raises(ValueError, match="exactly one Python ABI tag"):
        _python_abi_tags(
            {
                "nnrp/_nnrp_cffi_api_submit_result.cpython-311-x86_64-linux-gnu.so": b"py311",
                "nnrp/_nnrp_cffi_api_submit_result.cp312-win_amd64.pyd": b"py312",
            }
        )


def test_build_native_wheels_adds_missing_metadata_tag() -> None:
    metadata = _retag_wheel_metadata(
        b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\n",
        python_tag="cp311",
        abi_tag="cp311",
        platform_tag="manylinux_2_28_x86_64",
    ).decode("utf-8")

    assert "Root-Is-Purelib: false" in metadata
    assert "Tag: cp311-cp311-manylinux_2_28_x86_64" in metadata


def test_build_native_wheels_clean_removes_stale_outputs(tmp_path: Path) -> None:
    source = _write_staging_wheel(tmp_path / "nnrp_py-1.0.0rc3-py3-none-any.whl")
    output = tmp_path / "dist"
    stale = output / "stale.whl"
    stale.parent.mkdir()
    stale.write_text("stale", encoding="utf-8")

    build_native_wheels(source, output, clean=True)

    assert not stale.exists()


def test_build_native_wheels_rejects_missing_native_payload(tmp_path: Path) -> None:
    wheel = tmp_path / "nnrp_py-1.0.0rc3-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("nnrp/__init__.py", b"")
        archive.writestr("nnrp_py-1.0.0rc3.dist-info/WHEEL", b"Tag: py3-none-any\n")
        archive.writestr("nnrp_py-1.0.0rc3.dist-info/RECORD", b"")

    with pytest.raises(ValueError, match="does not contain native artifacts"):
        build_native_wheels(wheel, tmp_path / "dist")


def test_build_native_wheels_rejects_unknown_artifact_platform(tmp_path: Path) -> None:
    wheel = tmp_path / "nnrp_py-1.0.0rc3-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("nnrp/native_artifacts/plan9-x86_64/manifest.json", b"{}")
        archive.writestr("nnrp/native_artifacts/plan9-x86_64/libnnrp_ffi.so", b"native")
        archive.writestr("nnrp_py-1.0.0rc3.dist-info/WHEEL", b"Tag: py3-none-any\n")
        archive.writestr("nnrp_py-1.0.0rc3.dist-info/RECORD", b"")

    with pytest.raises(ValueError, match="no Python wheel platform tag"):
        build_native_wheels(wheel, tmp_path / "dist")


def test_build_native_wheels_rejects_invalid_wheel_shapes(tmp_path: Path) -> None:
    no_dist_info = tmp_path / "nnrp_py-1.0.0rc3-py3-none-any.whl"
    with zipfile.ZipFile(no_dist_info, "w") as archive:
        archive.writestr("nnrp/native_artifacts/linux-x86_64/manifest.json", b"{}")

    with pytest.raises(ValueError, match="exactly one .dist-info"):
        build_native_wheels(no_dist_info, tmp_path / "dist")

    invalid_name = tmp_path / "bad.whl"
    with zipfile.ZipFile(invalid_name, "w") as archive:
        archive.writestr("nnrp/native_artifacts/linux-x86_64/manifest.json", b"{}")
        archive.writestr("bad.dist-info/WHEEL", b"Tag: py3-none-any\n")
        archive.writestr("bad.dist-info/RECORD", b"")

    with pytest.raises(ValueError, match="invalid wheel filename"):
        build_native_wheels(invalid_name, tmp_path / "dist")


def test_build_native_wheels_cli_prints_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = _write_staging_wheel(tmp_path / "nnrp_py-1.0.0rc3-py3-none-any.whl")
    output = tmp_path / "dist"
    monkeypatch.setattr(
        sys,
        "argv",
        ["build_native_wheels.py", "--wheel", str(source), "--dist", str(output), "--clean"],
    )

    assert build_native_wheels_main() == 0

    captured = capsys.readouterr().out
    assert "manylinux_2_28_x86_64" in captured
    assert "win_amd64" in captured
