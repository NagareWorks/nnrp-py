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
    protocol_major: int
    protocol_wire_format: int


class _NnrpProtocolVersion(ctypes.Structure):
    _fields_ = [
        ("major", ctypes.c_uint8),
        ("wire_format", ctypes.c_uint8),
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
    version = _call_current_protocol_version(loaded_library)
    if version.major != EXPECTED_PROTOCOL_MAJOR or version.wire_format != EXPECTED_PROTOCOL_WIRE_FORMAT:
        raise NativeArtifactError(
            "native artifact protocol mismatch: "
            f"expected {EXPECTED_PROTOCOL_MAJOR}/{EXPECTED_PROTOCOL_WIRE_FORMAT}, "
            f"got {version.major}/{version.wire_format}"
        )
    return NativeProbeResult(resolved_path, int(version.major), int(version.wire_format))


def _call_current_protocol_version(library: Any) -> _NnrpProtocolVersion:
    try:
        function = library.nnrp_current_protocol_version
    except AttributeError as error:
        raise NativeArtifactError("native artifact is missing nnrp_current_protocol_version") from error

    try:
        function.restype = _NnrpProtocolVersion
        function.argtypes = []
    except AttributeError:
        pass

    version = function()
    if not hasattr(version, "major") or not hasattr(version, "wire_format"):
        raise NativeArtifactError("native artifact returned an invalid protocol version shape")
    return version


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
