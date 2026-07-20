from __future__ import annotations

import json
import sys
import zipfile
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "prepare_native_artifacts.py"
_SPEC = spec_from_file_location("prepare_native_artifacts", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)
prepare_native_artifacts = _MODULE.prepare_native_artifacts
prepare_native_artifacts_main = _MODULE.main


def _write_package(
    root: Path,
    name: str,
    *,
    os_name: str,
    arch: str,
    library: str,
    transport_scope: str = "tcp",
) -> Path:
    package_dir = root / name
    package_dir.mkdir(parents=True)
    (package_dir / library).write_bytes(b"native")
    (package_dir / "manifest.json").write_text(
        json.dumps(
            {
                "package": f"nnrp-ffi-transport-{transport_scope}",
                "os": os_name,
                "arch": arch,
                "library_kind": "dynamic",
                "library": library,
                "libraries": [library],
                "transport_scope": transport_scope,
                "transport_slots": [transport_scope],
            }
        ),
        encoding="utf-8",
    )
    return package_dir


def test_prepare_native_artifacts_installs_directory_packages(tmp_path: Path) -> None:
    package_dir = _write_package(
        tmp_path,
        "linux-aarch64",
        os_name="linux",
        arch="aarch64",
        library="libnnrp_ffi.so",
        transport_scope="tcp",
    )
    output = tmp_path / "out"

    installed = prepare_native_artifacts([package_dir], output)

    assert output.joinpath("linux-arm64", "tcp", "libnnrp_ffi.so").read_bytes() == b"native"
    assert output.joinpath("linux-arm64", "tcp", "manifest.json").is_file()
    assert installed == [
        output / "linux-arm64" / "tcp" / "libnnrp_ffi.so",
        output / "linux-arm64" / "tcp" / "manifest.json",
    ]


def test_prepare_native_artifacts_keeps_split_transport_artifacts_side_by_side(tmp_path: Path) -> None:
    tcp_package = _write_package(
        tmp_path,
        "tcp-linux-x86_64",
        os_name="linux",
        arch="x86_64",
        library="libnnrp_ffi.so",
        transport_scope="tcp",
    )
    quic_package = _write_package(
        tmp_path,
        "quic-linux-x86_64",
        os_name="linux",
        arch="x86_64",
        library="libnnrp_ffi.so",
        transport_scope="quic",
    )
    ipc_package = _write_package(
        tmp_path,
        "ipc-linux-x86_64",
        os_name="linux",
        arch="x86_64",
        library="libnnrp_ffi.so",
        transport_scope="ipc",
    )
    websocket_package = _write_package(
        tmp_path,
        "websocket-linux-x86_64",
        os_name="linux",
        arch="x86_64",
        library="libnnrp_ffi.so",
        transport_scope="websocket",
    )
    output = tmp_path / "out"

    prepare_native_artifacts([tcp_package, quic_package, ipc_package, websocket_package], output)

    assert output.joinpath("linux-x86_64", "tcp", "libnnrp_ffi.so").read_bytes() == b"native"
    assert output.joinpath("linux-x86_64", "quic", "libnnrp_ffi.so").read_bytes() == b"native"
    assert output.joinpath("linux-x86_64", "ipc", "libnnrp_ffi.so").read_bytes() == b"native"
    assert output.joinpath("linux-x86_64", "websocket", "libnnrp_ffi.so").read_bytes() == b"native"


def test_prepare_native_artifacts_normalizes_ios_simulator_arch(tmp_path: Path) -> None:
    package_dir = _write_package(
        tmp_path,
        "ios-aarch64-sim",
        os_name="ios",
        arch="aarch64-sim",
        library="libnnrp_ffi.a",
    )
    output = tmp_path / "out"

    prepare_native_artifacts([package_dir], output)

    assert output.joinpath("ios-arm64-sim", "tcp", "libnnrp_ffi.a").read_bytes() == b"native"


def test_prepare_native_artifacts_installs_release_zip_packages(tmp_path: Path) -> None:
    package_dir = _write_package(
        tmp_path / "packages",
        "windows-x86_64",
        os_name="windows",
        arch="x86_64",
        library="nnrp_ffi.dll",
    )
    archive_path = tmp_path / "nnrp-ffi-native-windows-x86_64.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for item in package_dir.rglob("*"):
            archive.write(item, item.relative_to(package_dir.parent).as_posix())

    output = tmp_path / "out"
    prepare_native_artifacts([archive_path], output)

    assert output.joinpath("windows-x86_64", "tcp", "nnrp_ffi.dll").read_bytes() == b"native"


def test_prepare_native_artifacts_rejects_missing_manifest_library(tmp_path: Path) -> None:
    package_dir = tmp_path / "bad"
    package_dir.mkdir()
    (package_dir / "manifest.json").write_text(
        json.dumps(
            {
                "os": "linux",
                "arch": "x86_64",
                "library": "libnnrp_ffi.so",
                "transport_scope": "tcp",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing library"):
        prepare_native_artifacts([package_dir], tmp_path / "out")


def test_prepare_native_artifacts_clean_removes_previous_output(tmp_path: Path) -> None:
    package_dir = _write_package(tmp_path, "macos-x86_64", os_name="macos", arch="x86_64", library="libnnrp_ffi.dylib")
    output = tmp_path / "out"
    stale = output / "stale.txt"
    stale.parent.mkdir()
    stale.write_text("stale", encoding="utf-8")

    prepare_native_artifacts([package_dir], output, clean=True)

    assert not stale.exists()
    assert output.joinpath("macos-x86_64", "tcp", "libnnrp_ffi.dylib").is_file()


@pytest.mark.parametrize("transport_scope", [None, "all"])
def test_prepare_native_artifacts_rejects_missing_or_aggregate_transport_scope(
    tmp_path: Path,
    transport_scope: str | None,
) -> None:
    package_dir = tmp_path / "legacy"
    package_dir.mkdir()
    manifest = {
        "package": "nnrp-ffi",
        "os": "linux",
        "arch": "x86_64",
        "library": "libnnrp_ffi.so",
        "libraries": ["libnnrp_ffi.so"],
    }
    if transport_scope is not None:
        manifest["transport_scope"] = transport_scope
    (package_dir / "libnnrp_ffi.so").write_bytes(b"native")
    (package_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="transport[_ ]scope"):
        prepare_native_artifacts([package_dir], tmp_path / "out")


def test_prepare_native_artifacts_rejects_invalid_inputs(tmp_path: Path) -> None:
    unsupported_file = tmp_path / "artifact.txt"
    unsupported_file.write_text("bad", encoding="utf-8")

    with pytest.raises(ValueError, match="directory or zip"):
        prepare_native_artifacts([unsupported_file], tmp_path / "out")

    with pytest.raises(ValueError, match="does not exist"):
        prepare_native_artifacts([tmp_path / "missing"], tmp_path / "out")


def test_prepare_native_artifacts_rejects_invalid_manifest_shapes(tmp_path: Path) -> None:
    package_dir = tmp_path / "bad-shape"
    package_dir.mkdir()
    (package_dir / "manifest.json").write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON object"):
        prepare_native_artifacts([package_dir], tmp_path / "out")

    (package_dir / "manifest.json").write_text(
        json.dumps({"os": "linux", "library": "libnnrp_ffi.so"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="'arch'"):
        prepare_native_artifacts([package_dir], tmp_path / "out")

    (package_dir / "manifest.json").write_text(
        json.dumps(
            {
                "os": "linux",
                "arch": "x86_64",
                "library": "not-nnrp.so",
                "transport_scope": "tcp",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="supported nnrp-ffi"):
        prepare_native_artifacts([package_dir], tmp_path / "out")


def test_prepare_native_artifacts_cli_prints_installed_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package_dir = _write_package(tmp_path, "android-armv7", os_name="android", arch="armv7", library="libnnrp_ffi.so")
    output = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["prepare_native_artifacts.py", str(package_dir), "--output", str(output)])

    assert prepare_native_artifacts_main() == 0

    captured = capsys.readouterr()
    assert "android-arm" in captured.out
    assert output.joinpath("android-arm", "tcp", "libnnrp_ffi.so").is_file()
