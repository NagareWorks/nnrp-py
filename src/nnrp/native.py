"""Native artifact discovery and ABI probe helpers for Rust-backed NNRP runtimes."""

from __future__ import annotations

import ctypes
import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXPECTED_PROTOCOL_MAJOR = 1
EXPECTED_PROTOCOL_WIRE_FORMAT = 0
EXPECTED_ABI_MAJOR = 1
MINIMUM_ABI_MINOR = 0
TRANSPORT_SLOT_QUIC = 0x00000001
TRANSPORT_SLOT_TCP = 0x00000002
RUNTIME_FEATURE_PROTOCOL_CORE = 0x0000000000000001
RUNTIME_FEATURE_CLIENT_API = 0x0000000000000002
RUNTIME_FEATURE_SERVER_API = 0x0000000000000004
RUNTIME_FEATURE_EVENT_POLLING = 0x0000000000000008
RUNTIME_FEATURE_CALLBACK_DISPATCH = 0x0000000000000010
RUNTIME_FEATURE_CACHE_SCHEMA = 0x0000000000000020
RUNTIME_FEATURE_RECOVERY = 0x0000000000000040
RUNTIME_FEATURE_TYPED_PAYLOAD = 0x0000000000000080
RUNTIME_FEATURE_TRANSPORT_SLOTS = 0x0000000000000100
REQUIRED_RUNTIME_FEATURES = (
    RUNTIME_FEATURE_PROTOCOL_CORE
    | RUNTIME_FEATURE_CLIENT_API
    | RUNTIME_FEATURE_SERVER_API
    | RUNTIME_FEATURE_EVENT_POLLING
    | RUNTIME_FEATURE_CALLBACK_DISPATCH
    | RUNTIME_FEATURE_CACHE_SCHEMA
    | RUNTIME_FEATURE_RECOVERY
    | RUNTIME_FEATURE_TYPED_PAYLOAD
    | RUNTIME_FEATURE_TRANSPORT_SLOTS
)
REQUIRED_TRANSPORT_SLOTS = TRANSPORT_SLOT_TCP
DEFAULT_ARTIFACT_ROOT_ENV = "NNRP_NATIVE_ARTIFACT_ROOT"


class NativeArtifactError(RuntimeError):
    """Raised when a native artifact cannot be resolved, loaded, or accepted."""


@dataclass(frozen=True)
class NativePlatform:
    os_name: str
    arch: str

    @property
    def tag(self) -> str:
        return f"{self.os_name}-{self.arch}"


@dataclass(frozen=True)
class NativeProbeResult:
    artifact_path: Path
    abi_major: int
    abi_minor: int
    abi_patch: int
    protocol_major: int
    protocol_wire_format: int
    sdk_major: int
    sdk_minor: int
    sdk_patch: int
    sdk_preview: int
    sdk_revision: int
    transport_slots: int
    feature_flags: int


class _NnrpProtocolVersion(ctypes.Structure):
    _fields_ = [
        ("major", ctypes.c_uint8),
        ("wire_format", ctypes.c_uint8),
    ]


class _NnrpRuntimeCapabilities(ctypes.Structure):
    _fields_ = [
        ("abi_major", ctypes.c_uint16),
        ("abi_minor", ctypes.c_uint16),
        ("abi_patch", ctypes.c_uint16),
        ("reserved0", ctypes.c_uint16),
        ("protocol_version", _NnrpProtocolVersion),
        ("sdk_major", ctypes.c_uint16),
        ("sdk_minor", ctypes.c_uint16),
        ("sdk_patch", ctypes.c_uint16),
        ("sdk_preview", ctypes.c_uint16),
        ("sdk_revision", ctypes.c_uint16),
        ("reserved1", ctypes.c_uint16),
        ("transport_slots", ctypes.c_uint32),
        ("feature_flags", ctypes.c_uint64),
    ]


def current_native_platform() -> NativePlatform:
    return NativePlatform(_normalize_os(platform.system()), _normalize_arch(platform.machine()))


def native_library_name(os_name: str) -> str:
    normalized = _normalize_os(os_name)
    if normalized == "windows":
        return "nnrp_ffi.dll"
    if normalized in {"macos", "ios"}:
        return "libnnrp_ffi.dylib"
    if normalized in {"linux", "android"}:
        return "libnnrp_ffi.so"
    raise NativeArtifactError(f"unsupported native artifact OS: {os_name}")


def default_artifact_root() -> Path:
    configured = os.environ.get(DEFAULT_ARTIFACT_ROOT_ENV)
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent / "native_artifacts"


def resolve_native_artifact(
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
) -> Path:
    selected_platform = native_platform or current_native_platform()
    artifact_root = Path(root) if root is not None else default_artifact_root()
    artifact_path = artifact_root / selected_platform.tag / native_library_name(selected_platform.os_name)
    if not artifact_path.is_file():
        raise NativeArtifactError(f"native artifact was not found: {artifact_path}")
    return artifact_path


def load_native_library(artifact_path: Path | str) -> ctypes.CDLL:
    try:
        return ctypes.CDLL(str(artifact_path))
    except OSError as error:
        raise NativeArtifactError(f"failed to load native artifact {artifact_path}: {error}") from error


def probe_native_artifact(
    artifact_path: Path | str | None = None,
    *,
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
    library: Any | None = None,
) -> NativeProbeResult:
    resolved_path = Path(artifact_path) if artifact_path is not None else resolve_native_artifact(root, native_platform)
    loaded_library = library if library is not None else load_native_library(resolved_path)
    capabilities = _call_runtime_capabilities(loaded_library)
    _validate_runtime_capabilities(capabilities)
    return NativeProbeResult(
        artifact_path=resolved_path,
        abi_major=int(capabilities.abi_major),
        abi_minor=int(capabilities.abi_minor),
        abi_patch=int(capabilities.abi_patch),
        protocol_major=int(capabilities.protocol_version.major),
        protocol_wire_format=int(capabilities.protocol_version.wire_format),
        sdk_major=int(capabilities.sdk_major),
        sdk_minor=int(capabilities.sdk_minor),
        sdk_patch=int(capabilities.sdk_patch),
        sdk_preview=int(capabilities.sdk_preview),
        sdk_revision=int(capabilities.sdk_revision),
        transport_slots=int(capabilities.transport_slots),
        feature_flags=int(capabilities.feature_flags),
    )


def _validate_runtime_capabilities(capabilities: _NnrpRuntimeCapabilities) -> None:
    if capabilities.abi_major != EXPECTED_ABI_MAJOR or capabilities.abi_minor < MINIMUM_ABI_MINOR:
        raise NativeArtifactError(
            "native artifact ABI mismatch: "
            f"expected {EXPECTED_ABI_MAJOR}.{MINIMUM_ABI_MINOR}.x, "
            f"got {capabilities.abi_major}.{capabilities.abi_minor}.{capabilities.abi_patch}"
        )
    version = capabilities.protocol_version
    if version.major != EXPECTED_PROTOCOL_MAJOR or version.wire_format != EXPECTED_PROTOCOL_WIRE_FORMAT:
        raise NativeArtifactError(
            "native artifact protocol mismatch: "
            f"expected {EXPECTED_PROTOCOL_MAJOR}/{EXPECTED_PROTOCOL_WIRE_FORMAT}, "
            f"got {version.major}/{version.wire_format}"
        )
    missing_features = REQUIRED_RUNTIME_FEATURES & ~int(capabilities.feature_flags)
    if missing_features:
        raise NativeArtifactError(
            f"native artifact is missing required runtime feature flags: 0x{missing_features:016x}"
        )
    missing_transport_slots = REQUIRED_TRANSPORT_SLOTS & ~int(capabilities.transport_slots)
    if missing_transport_slots:
        raise NativeArtifactError(
            f"native artifact is missing required transport slots: 0x{missing_transport_slots:08x}"
        )


def _call_runtime_capabilities(library: Any) -> _NnrpRuntimeCapabilities:
    try:
        function = library.nnrp_runtime_capabilities
    except AttributeError as error:
        raise NativeArtifactError("native artifact is missing nnrp_runtime_capabilities") from error

    try:
        function.restype = _NnrpRuntimeCapabilities
        function.argtypes = []
    except AttributeError:
        pass

    capabilities = function()
    if not hasattr(capabilities, "protocol_version") or not hasattr(capabilities, "feature_flags"):
        raise NativeArtifactError("native artifact returned an invalid runtime capabilities shape")
    return capabilities


def _normalize_os(value: str) -> str:
    normalized = value.strip().lower()
    aliases = {
        "darwin": "macos",
        "macosx": "macos",
        "osx": "macos",
        "win32": "windows",
        "cygwin": "windows",
        "msys": "windows",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"windows", "macos", "linux", "android", "ios"}:
        raise NativeArtifactError(f"unsupported native artifact OS: {value}")
    return normalized


def _normalize_arch(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    aliases = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "i386": "x86",
        "i686": "x86",
        "aarch64": "arm64",
        "armv8": "arm64",
        "armv7": "arm",
        "armv7l": "arm",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"x86", "x86_64", "arm", "arm64"}:
        raise NativeArtifactError(f"unsupported native artifact architecture: {value}")
    return normalized
