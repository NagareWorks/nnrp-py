from __future__ import annotations

from pathlib import Path

import pytest

from nnrp.native import (
    DEFAULT_ARTIFACT_ROOT_ENV,
    HANDLE_KIND_BUFFER,
    HANDLE_KIND_CONNECTION,
    HANDLE_KIND_EVENT_PUMP,
    HANDLE_KIND_OPERATION,
    HANDLE_KIND_SESSION,
    REQUIRED_RUNTIME_FEATURES,
    TRANSPORT_SLOT_TCP,
    NativeArtifactError,
    NativeBufferHandle,
    NativeBufferView,
    NativeConnectionHandle,
    NativeEventPumpHandle,
    NativeHandle,
    NativeHandleError,
    NativeMutableBufferView,
    NativeOperationHandle,
    NativePlatform,
    NativeSessionHandle,
    _NnrpBufferView,
    _NnrpBufferViewMut,
    _NnrpHandle,
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


def test_native_handle_roundtrips_ffi_shape() -> None:
    handle = NativeHandle(HANDLE_KIND_CONNECTION, 7, 2, 0)

    ffi = handle.to_ffi()
    decoded = NativeHandle.from_ffi(ffi)

    assert (ffi.kind, ffi.id, ffi.generation, ffi.flags) == (HANDLE_KIND_CONNECTION, 7, 2, 0)
    assert decoded == handle
    assert decoded.is_valid is True


def test_native_handle_invalid_shape_is_zero_only() -> None:
    assert NativeHandle.invalid().to_ffi().kind == 0

    with pytest.raises(NativeHandleError, match="invalid handles"):
        NativeHandle(0, 1, 0)


def test_native_handle_requires_valid_kind_id_and_generation() -> None:
    with pytest.raises(NativeHandleError, match="uint32"):
        NativeHandle(-1, 1, 1)
    with pytest.raises(NativeHandleError, match="non-zero id"):
        NativeHandle(HANDLE_KIND_SESSION, 0, 1)
    with pytest.raises(NativeHandleError, match="non-zero id"):
        NativeHandle(HANDLE_KIND_SESSION, 1, 0)


@pytest.mark.parametrize(
    ("wrapper_type", "kind"),
    [
        (NativeConnectionHandle, HANDLE_KIND_CONNECTION),
        (NativeSessionHandle, HANDLE_KIND_SESSION),
        (NativeOperationHandle, HANDLE_KIND_OPERATION),
        (NativeEventPumpHandle, HANDLE_KIND_EVENT_PUMP),
        (NativeBufferHandle, HANDLE_KIND_BUFFER),
    ],
)
def test_typed_native_handles_accept_only_matching_kind(wrapper_type: type, kind: int) -> None:
    wrapper = wrapper_type.from_ffi(_NnrpHandle(kind, 11, 3, 0))

    assert wrapper.to_ffi().kind == kind

    with pytest.raises(NativeHandleError, match="expected native handle kind"):
        mismatched_kind = HANDLE_KIND_CONNECTION if kind != HANDLE_KIND_CONNECTION else HANDLE_KIND_SESSION
        wrapper_type(NativeHandle(mismatched_kind, 11, 3))


def test_native_buffer_views_roundtrip_ffi_shape() -> None:
    view = NativeBufferView(0x1000, 64)
    mutable_view = NativeMutableBufferView(0x2000, 128)

    assert NativeBufferView.from_ffi(view.to_ffi()) == view
    assert NativeMutableBufferView.from_ffi(mutable_view.to_ffi()) == mutable_view
    assert NativeBufferView.empty().to_ffi().ptr is None
    assert NativeMutableBufferView.empty().to_ffi().ptr is None
    assert NativeBufferView.from_ffi(_NnrpBufferView(None, 0)) == NativeBufferView.empty()
    assert NativeMutableBufferView.from_ffi(_NnrpBufferViewMut(None, 0)) == NativeMutableBufferView.empty()


def test_native_buffer_views_reject_non_empty_null_pointer() -> None:
    with pytest.raises(NativeHandleError, match="non-null pointer"):
        NativeBufferView(0, 1)
    with pytest.raises(NativeHandleError, match="non-null pointer"):
        NativeMutableBufferView(0, 1)
