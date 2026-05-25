from __future__ import annotations

from pathlib import Path

import pytest

from nnrp.native import (
    DEFAULT_ARTIFACT_ROOT_ENV,
    REQUIRED_RUNTIME_FEATURES,
    TRANSPORT_SLOT_TCP,
    NativeArtifactError,
    NativePlatform,
    _NnrpProtocolVersion,
    _NnrpRuntimeCapabilities,
    _normalize_arch,
    current_native_platform,
    default_artifact_root,
    load_native_library,
    native_library_name,
    probe_native_artifact,
    resolve_native_artifact,
)


class FakeLibrary:
    def __init__(
        self,
        *,
        abi_major: int = 1,
        abi_minor: int = 0,
        abi_patch: int = 0,
        protocol_major: int = 1,
        wire_format: int = 0,
        transport_slots: int = TRANSPORT_SLOT_TCP,
        feature_flags: int = REQUIRED_RUNTIME_FEATURES,
    ) -> None:
        self._capabilities = _NnrpRuntimeCapabilities(
            abi_major,
            abi_minor,
            abi_patch,
            0,
            _NnrpProtocolVersion(protocol_major, wire_format),
            1,
            0,
            0,
            3,
            1,
            0,
            transport_slots,
            feature_flags,
        )

    def nnrp_runtime_capabilities(self) -> _NnrpRuntimeCapabilities:
        return self._capabilities


class InvalidCapabilitiesLibrary:
    def nnrp_runtime_capabilities(self) -> object:
        return object()


def test_current_native_platform_normalizes_host_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.machine", lambda: "aarch64")

    assert current_native_platform() == NativePlatform("macos", "arm64")


def test_default_artifact_root_prefers_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DEFAULT_ARTIFACT_ROOT_ENV, str(tmp_path))

    assert default_artifact_root() == tmp_path


def test_default_artifact_root_falls_back_to_package_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DEFAULT_ARTIFACT_ROOT_ENV, raising=False)

    assert default_artifact_root().name == "native_artifacts"


def test_native_library_name_matches_supported_platforms() -> None:
    assert native_library_name("windows") == "nnrp_ffi.dll"
    assert native_library_name("linux") == "libnnrp_ffi.so"
    assert native_library_name("android") == "libnnrp_ffi.so"
    assert native_library_name("darwin") == "libnnrp_ffi.dylib"
    assert native_library_name("ios") == "libnnrp_ffi.dylib"


def test_native_platform_rejects_unsupported_values() -> None:
    with pytest.raises(NativeArtifactError, match="unsupported native artifact OS"):
        native_library_name("plan9")
    with pytest.raises(NativeArtifactError, match="unsupported native artifact architecture"):
        _normalize_arch("sparc")


def test_resolve_native_artifact_uses_platform_tag_and_library_name(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "linux-x86_64"
    artifact_dir.mkdir()
    artifact = artifact_dir / "libnnrp_ffi.so"
    artifact.write_bytes(b"not-a-real-shared-library")

    assert resolve_native_artifact(tmp_path, NativePlatform("linux", "x86_64")) == artifact


def test_resolve_native_artifact_uses_current_platform_when_not_provided(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    artifact_dir = tmp_path / "windows-x86_64"
    artifact_dir.mkdir()
    artifact = artifact_dir / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")

    assert resolve_native_artifact(tmp_path) == artifact


def test_resolve_native_artifact_rejects_missing_artifact(tmp_path: Path) -> None:
    with pytest.raises(NativeArtifactError, match="native artifact was not found"):
        resolve_native_artifact(tmp_path, NativePlatform("linux", "x86_64"))


def test_load_native_library_surfaces_loader_errors(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"not-a-real-dll")

    with pytest.raises(NativeArtifactError, match="failed to load native artifact"):
        load_native_library(artifact)


def test_probe_native_artifact_accepts_matching_protocol(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")

    result = probe_native_artifact(artifact, library=FakeLibrary())

    assert result.artifact_path == artifact
    assert result.abi_major == 1
    assert result.abi_minor == 0
    assert result.abi_patch == 0
    assert result.protocol_major == 1
    assert result.protocol_wire_format == 0
    assert result.sdk_preview == 3
    assert result.sdk_revision == 1
    assert result.transport_slots == TRANSPORT_SLOT_TCP
    assert result.feature_flags == REQUIRED_RUNTIME_FEATURES


def test_probe_native_artifact_resolves_path_from_root(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "linux-arm64"
    artifact_dir.mkdir()
    artifact = artifact_dir / "libnnrp_ffi.so"
    artifact.write_bytes(b"fake")

    result = probe_native_artifact(
        root=tmp_path,
        native_platform=NativePlatform("linux", "arm64"),
        library=FakeLibrary(),
    )

    assert result.artifact_path == artifact


def test_probe_native_artifact_rejects_protocol_mismatch(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")

    with pytest.raises(NativeArtifactError, match="protocol mismatch"):
        probe_native_artifact(artifact, library=FakeLibrary(protocol_major=2, wire_format=0))


def test_probe_native_artifact_rejects_abi_mismatch(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")

    with pytest.raises(NativeArtifactError, match="ABI mismatch"):
        probe_native_artifact(artifact, library=FakeLibrary(abi_major=2))


def test_probe_native_artifact_rejects_missing_required_feature(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")

    with pytest.raises(NativeArtifactError, match="required runtime feature flags"):
        probe_native_artifact(artifact, library=FakeLibrary(feature_flags=REQUIRED_RUNTIME_FEATURES & ~1))


def test_probe_native_artifact_rejects_missing_tcp_transport_slot(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")

    with pytest.raises(NativeArtifactError, match="required transport slots"):
        probe_native_artifact(artifact, library=FakeLibrary(transport_slots=0))


def test_probe_native_artifact_rejects_missing_probe_symbol(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")

    with pytest.raises(NativeArtifactError, match="missing nnrp_runtime_capabilities"):
        probe_native_artifact(artifact, library=object())


def test_probe_native_artifact_rejects_invalid_probe_shape(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")

    with pytest.raises(NativeArtifactError, match="invalid runtime capabilities shape"):
        probe_native_artifact(artifact, library=InvalidCapabilitiesLibrary())
