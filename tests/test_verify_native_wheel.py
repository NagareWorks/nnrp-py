from __future__ import annotations

import json
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


def _write_wheel(path: Path, names: list[str], *, abi_version: str = "1.11.0") -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            if name.endswith("manifest.json"):
                scope = "all"
                if "/tcp/" in name:
                    scope = "tcp"
                elif "/quic/" in name:
                    scope = "quic"
                elif "/ipc/" in name:
                    scope = "ipc"
                elif "/websocket/" in name:
                    scope = "websocket"
                archive.writestr(
                    name,
                    json.dumps(
                        {
                            "package": "nnrp-ffi" if scope == "all" else f"nnrp-ffi-transport-{scope}",
                            "transport_name": scope,
                            "transport_scope": scope,
                            "transport_slots": [scope] if scope != "all" else ["tcp", "quic", "ipc", "websocket"],
                            "protocol_version": "NNRP/1",
                            "abi_version": abi_version,
                            "enabled_features": (
                                [f"transport-{scope}"]
                                if scope != "all"
                                else ["transport-tcp", "transport-quic", "transport-ipc", "transport-websocket"]
                            ),
                        }
                    ).encode(),
                )
            else:
                archive.writestr(name, b"wheel-data")
    return path


def test_inspect_wheel_finds_packaged_native_artifacts(tmp_path: Path) -> None:
    wheel = _write_wheel(
        tmp_path / "nnrp_py-1.0.0rc3-py3-none-win_amd64.whl",
        [
            "nnrp/native_artifacts/windows-x86_64/manifest.json",
            "nnrp/native_artifacts/windows-x86_64/nnrp_ffi.dll",
            "nnrp/_nnrp_cffi_api_submit_result.py",
        ],
    )

    summary = inspect_wheel(wheel)

    assert summary.has_native_artifacts is True
    assert summary.manifests == ("nnrp/native_artifacts/windows-x86_64/manifest.json",)
    assert summary.libraries == ("nnrp/native_artifacts/windows-x86_64/nnrp_ffi.dll",)
    assert summary.artifact_tags == ("windows-x86_64",)
    assert summary.platform_tag == "win_amd64"
    assert summary.cffi_api_entries == ("nnrp/_nnrp_cffi_api_submit_result.py",)
    assert summary.transport_scopes == ("all",)
    assert summary.manifest_packages == ("nnrp-ffi",)
    assert summary.protocol_versions == ("NNRP/1",)
    assert summary.abi_versions == ("1.11.0",)


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


def test_verify_native_wheels_requires_split_transport_artifacts(tmp_path: Path) -> None:
    legacy = _write_wheel(
        tmp_path / "nnrp_py-1.0.0rc3-py3-none-manylinux_2_28_x86_64.whl",
        [
            "nnrp/native_artifacts/linux-x86_64/manifest.json",
            "nnrp/native_artifacts/linux-x86_64/libnnrp_ffi.so",
        ],
    )
    split = _write_wheel(
        tmp_path / "nnrp_py-1.0.0rc3.post4-py3-none-manylinux_2_28_x86_64.whl",
        [
            "nnrp/native_artifacts/linux-x86_64/tcp/manifest.json",
            "nnrp/native_artifacts/linux-x86_64/tcp/libnnrp_ffi.so",
            "nnrp/native_artifacts/linux-x86_64/quic/manifest.json",
            "nnrp/native_artifacts/linux-x86_64/quic/libnnrp_ffi.so",
            "nnrp/native_artifacts/linux-x86_64/ipc/manifest.json",
            "nnrp/native_artifacts/linux-x86_64/ipc/libnnrp_ffi.so",
            "nnrp/native_artifacts/linux-x86_64/websocket/manifest.json",
            "nnrp/native_artifacts/linux-x86_64/websocket/libnnrp_ffi.so",
        ],
    )

    with pytest.raises(ValueError, match="split TCP, QUIC, IPC, and WebSocket"):
        verify_native_wheels([inspect_wheel(legacy)], require_native=True, require_split_transports=True)

    verify_native_wheels([inspect_wheel(split)], require_native=True, require_split_transports=True)


def test_verify_native_wheels_requires_preview4_native_artifact_shape(tmp_path: Path) -> None:
    legacy = _write_wheel(
        tmp_path / "nnrp_py-1.0.0rc3-py3-none-manylinux_2_28_x86_64.whl",
        [
            "nnrp/native_artifacts/linux-x86_64/manifest.json",
            "nnrp/native_artifacts/linux-x86_64/libnnrp_ffi.so",
        ],
    )
    preview4 = _write_wheel(
        tmp_path / "nnrp_py-1.0.0rc4-py3-none-manylinux_2_28_x86_64.whl",
        [
            "nnrp/native_artifacts/linux-x86_64/tcp/manifest.json",
            "nnrp/native_artifacts/linux-x86_64/tcp/libnnrp_ffi.so",
            "nnrp/native_artifacts/linux-x86_64/quic/manifest.json",
            "nnrp/native_artifacts/linux-x86_64/quic/libnnrp_ffi.so",
            "nnrp/native_artifacts/linux-x86_64/ipc/manifest.json",
            "nnrp/native_artifacts/linux-x86_64/ipc/libnnrp_ffi.so",
            "nnrp/native_artifacts/linux-x86_64/websocket/manifest.json",
            "nnrp/native_artifacts/linux-x86_64/websocket/libnnrp_ffi.so",
        ],
    )

    with pytest.raises(ValueError, match="non-preview4 native artifact metadata"):
        verify_native_wheels([inspect_wheel(legacy)], require_native=True, require_preview4_native_artifacts=True)

    verify_native_wheels([inspect_wheel(preview4)], require_native=True, require_preview4_native_artifacts=True)


def test_verify_native_wheels_requires_native_abi_version(tmp_path: Path) -> None:
    wheel = _write_wheel(
        tmp_path / "nnrp_py-1.0.0rc4-py3-none-manylinux_2_28_x86_64.whl",
        [
            "nnrp/native_artifacts/linux-x86_64/tcp/manifest.json",
            "nnrp/native_artifacts/linux-x86_64/tcp/libnnrp_ffi.so",
            "nnrp/native_artifacts/linux-x86_64/quic/manifest.json",
            "nnrp/native_artifacts/linux-x86_64/quic/libnnrp_ffi.so",
            "nnrp/native_artifacts/linux-x86_64/ipc/manifest.json",
            "nnrp/native_artifacts/linux-x86_64/ipc/libnnrp_ffi.so",
            "nnrp/native_artifacts/linux-x86_64/websocket/manifest.json",
            "nnrp/native_artifacts/linux-x86_64/websocket/libnnrp_ffi.so",
        ],
        abi_version="1.10.0",
    )

    with pytest.raises(ValueError, match="ABI version mismatch"):
        verify_native_wheels([inspect_wheel(wheel)], require_native=True, require_abi_version="1.11.0")


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


def test_verify_native_wheels_rejects_missing_cffi_api_when_required(tmp_path: Path) -> None:
    wheel = _write_wheel(
        tmp_path / "nnrp_py-1.0.0rc3-py3-none-win_amd64.whl",
        [
            "nnrp/native_artifacts/windows-x86_64/manifest.json",
            "nnrp/native_artifacts/windows-x86_64/nnrp_ffi.dll",
        ],
    )
    summary = inspect_wheel(wheel)

    with pytest.raises(ValueError, match="missing packaged cffi API module"):
        verify_native_wheels([summary], require_native=True, require_cffi_api=True)


def test_verify_native_wheels_can_require_compiled_cffi_api(tmp_path: Path) -> None:
    source_only = _write_wheel(
        tmp_path / "nnrp_py-1.0.0rc3-py3-none-win_amd64.whl",
        [
            "nnrp/native_artifacts/windows-x86_64/manifest.json",
            "nnrp/native_artifacts/windows-x86_64/nnrp_ffi.dll",
            "nnrp/_nnrp_cffi_api_submit_result.py",
        ],
    )
    compiled = _write_wheel(
        tmp_path / "nnrp_py-1.0.0rc3-cp313-cp313-win_amd64.whl",
        [
            "nnrp/native_artifacts/windows-x86_64/manifest.json",
            "nnrp/native_artifacts/windows-x86_64/nnrp_ffi.dll",
            "nnrp/_nnrp_cffi_api_submit_result.cp313-win_amd64.pyd",
        ],
    )

    source_only_summary = inspect_wheel(source_only)
    compiled_summary = inspect_wheel(compiled)

    verify_native_wheels([source_only_summary], require_native=True, require_cffi_api=True)
    with pytest.raises(ValueError, match="missing packaged compiled cffi API module"):
        verify_native_wheels([source_only_summary], require_native=True, require_compiled_cffi_api=True)
    verify_native_wheels([compiled_summary], require_native=True, require_compiled_cffi_api=True)


@pytest.mark.parametrize(
    ("artifact_tag", "wheel_tag", "library"),
    [
        ("ios-arm64-sim", "ios_13_0_arm64_iphonesimulator", "libnnrp_ffi.a"),
        ("ios-aarch64-sim", "ios_13_0_arm64_iphonesimulator", "libnnrp_ffi.a"),
        ("ios-x86_64-sim", "ios_13_0_x86_64_iphonesimulator", "libnnrp_ffi.a"),
    ],
)
def test_verify_native_wheels_accepts_ios_simulator_tags(
    tmp_path: Path,
    artifact_tag: str,
    wheel_tag: str,
    library: str,
) -> None:
    wheel = _write_wheel(
        tmp_path / f"nnrp_py-1.0.0rc3-py3-none-{wheel_tag}.whl",
        [
            f"nnrp/native_artifacts/{artifact_tag}/manifest.json",
            f"nnrp/native_artifacts/{artifact_tag}/{library}",
        ],
    )
    summary = inspect_wheel(wheel)

    verify_native_wheels([summary], require_native=True, require_single_platform=True, verify_platform_tag=True)


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
    assert "cffi_api=0, compiled_cffi_api=0" in captured
