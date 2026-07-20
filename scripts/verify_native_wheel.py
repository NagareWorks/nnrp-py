#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

NATIVE_ARTIFACT_PREFIX = "nnrp/native_artifacts/"
NATIVE_LIBRARY_SUFFIXES = (".dll", ".so", ".dylib", ".a")
UNIVERSAL_PLATFORM_TAG = "any"

ARTIFACT_PLATFORM_TAGS = {
    "windows-x86": "win32",
    "windows-x86_64": "win_amd64",
    "windows-arm64": "win_arm64",
    "macos-x86_64": "macosx_11_0_x86_64",
    "macos-arm64": "macosx_11_0_arm64",
    "linux-x86": "manylinux_2_28_i686",
    "linux-x86_64": "manylinux_2_28_x86_64",
    "linux-arm": "manylinux_2_28_armv7l",
    "linux-arm64": "manylinux_2_28_aarch64",
    "android-x86": "android_24_x86",
    "android-x86_64": "android_24_x86_64",
    "android-arm": "android_24_armeabi_v7a",
    "android-arm64": "android_24_arm64_v8a",
    "ios-arm64": "ios_13_0_arm64_iphoneos",
    "ios-aarch64-sim": "ios_13_0_arm64_iphonesimulator",
    "ios-arm64-sim": "ios_13_0_arm64_iphonesimulator",
    "ios-x86_64-sim": "ios_13_0_x86_64_iphonesimulator",
    "ios-x86_64": "ios_13_0_x86_64_iphonesimulator",
}


@dataclass(frozen=True)
class WheelNativeSummary:
    wheel: Path
    manifests: tuple[str, ...]
    libraries: tuple[str, ...]
    artifact_tags: tuple[str, ...]
    platform_tag: str
    transport_scopes: tuple[str, ...] = ()
    manifest_packages: tuple[str, ...] = ()
    transport_names: tuple[str, ...] = ()
    protocol_versions: tuple[str, ...] = ()
    abi_versions: tuple[str, ...] = ()
    enabled_features: tuple[str, ...] = ()

    @property
    def has_native_artifacts(self) -> bool:
        return bool(self.manifests and self.libraries)

    @property
    def is_universal(self) -> bool:
        return self.platform_tag == UNIVERSAL_PLATFORM_TAG

def inspect_wheel(path: Path) -> WheelNativeSummary:
    platform_tag = _wheel_platform_tag(path)
    with zipfile.ZipFile(path) as archive:
        names = tuple(archive.namelist())
        manifest_metadata = _manifest_metadata(archive, names)
    manifests = tuple(
        name for name in names if name.startswith(NATIVE_ARTIFACT_PREFIX) and name.endswith("/manifest.json")
    )
    libraries = tuple(
        name for name in names if name.startswith(NATIVE_ARTIFACT_PREFIX) and name.endswith(NATIVE_LIBRARY_SUFFIXES)
    )
    artifact_tags = tuple(sorted(_artifact_tags(manifests + libraries)))
    return WheelNativeSummary(
        path,
        manifests,
        libraries,
        artifact_tags,
        platform_tag,
        manifest_metadata["transport_scopes"],
        manifest_metadata["manifest_packages"],
        manifest_metadata["transport_names"],
        manifest_metadata["protocol_versions"],
        manifest_metadata["abi_versions"],
        manifest_metadata["enabled_features"],
    )


def inspect_dist(dist: Path) -> list[WheelNativeSummary]:
    wheels = sorted(dist.glob("*.whl"))
    if not wheels:
        raise ValueError(f"no wheel files found under {dist}")
    return [inspect_wheel(wheel) for wheel in wheels]


def verify_native_wheels(
    summaries: Iterable[WheelNativeSummary],
    *,
    require_native: bool,
    reject_universal_native: bool = False,
    require_single_platform: bool = False,
    verify_platform_tag: bool = False,
    require_split_transports: bool = False,
    require_preview4_native_artifacts: bool = False,
    require_abi_version: str | None = None,
) -> None:
    summary_list = list(summaries)
    failures = [
        summary
        for summary in summary_list
        if require_native and not summary.has_native_artifacts
    ]
    if failures:
        wheel_names = ", ".join(summary.wheel.name for summary in failures)
        raise ValueError(f"wheel is missing packaged native artifacts: {wheel_names}")

    universal_native = [
        summary
        for summary in summary_list
        if reject_universal_native and summary.has_native_artifacts and summary.is_universal
    ]
    if universal_native:
        wheel_names = ", ".join(summary.wheel.name for summary in universal_native)
        raise ValueError(f"native artifact wheel must not use the universal 'any' platform tag: {wheel_names}")

    multi_platform = [
        summary
        for summary in summary_list
        if require_single_platform and summary.has_native_artifacts and len(summary.artifact_tags) != 1
    ]
    if multi_platform:
        wheel_names = ", ".join(summary.wheel.name for summary in multi_platform)
        raise ValueError(f"wheel must contain exactly one native artifact platform: {wheel_names}")

    tag_mismatches = [
        summary
        for summary in summary_list
        if verify_platform_tag and summary.has_native_artifacts and not _matches_artifact_platform_tag(summary)
    ]
    if tag_mismatches:
        details = ", ".join(
            f"{summary.wheel.name} embeds {summary.artifact_tags} but is tagged {summary.platform_tag}"
            for summary in tag_mismatches
        )
        raise ValueError(f"wheel platform tag does not match embedded native artifact: {details}")

    split_transport_failures = [
        summary
        for summary in summary_list
        if require_split_transports
        and summary.has_native_artifacts
        and (
            set(summary.transport_scopes) != {"tcp", "quic", "ipc", "websocket"}
            or any(scope == "all" for scope in summary.transport_scopes)
        )
    ]
    if split_transport_failures:
        details = ", ".join(
            f"{summary.wheel.name} has transport scopes {summary.transport_scopes or ('-',)}"
            for summary in split_transport_failures
        )
        raise ValueError(f"wheel must embed split TCP, QUIC, IPC, and WebSocket native transport artifacts: {details}")

    preview4_native_failures = [
        summary
        for summary in summary_list
        if require_preview4_native_artifacts
        and summary.has_native_artifacts
        and not _has_preview4_native_artifact_shape(summary)
    ]
    if preview4_native_failures:
        details = ", ".join(
            (
                f"{summary.wheel.name} packages={summary.manifest_packages or ('-',)} "
                f"transports={summary.transport_names or ('-',)} "
                f"features={summary.enabled_features or ('-',)} "
                f"protocols={summary.protocol_versions or ('-',)}"
            )
            for summary in preview4_native_failures
        )
        raise ValueError(f"wheel embeds non-preview4 native artifact metadata: {details}")

    abi_mismatches = [
        summary
        for summary in summary_list
        if require_abi_version
        and summary.has_native_artifacts
        and set(summary.abi_versions) != {require_abi_version}
    ]
    if abi_mismatches:
        details = ", ".join(
            f"{summary.wheel.name} abi_versions={summary.abi_versions or ('-',)}"
            for summary in abi_mismatches
        )
        raise ValueError(f"wheel native artifact ABI version mismatch: {details}")


def _artifact_tags(names: Iterable[str]) -> set[str]:
    return {
        parts[2]
        for name in names
        if name.startswith(NATIVE_ARTIFACT_PREFIX)
        for parts in [name.split("/")]
        if len(parts) >= 4 and parts[2]
    }


def _manifest_metadata(archive: zipfile.ZipFile, names: Iterable[str]) -> dict[str, tuple[str, ...]]:
    scopes: set[str] = set()
    packages: set[str] = set()
    transport_names: set[str] = set()
    protocol_versions: set[str] = set()
    abi_versions: set[str] = set()
    enabled_features: set[str] = set()
    for name in names:
        if not name.startswith(NATIVE_ARTIFACT_PREFIX) or not name.endswith("/manifest.json"):
            continue
        try:
            manifest = json.loads(archive.read(name))
        except json.JSONDecodeError:
            continue
        if isinstance(manifest, dict):
            package = manifest.get("package")
            if isinstance(package, str) and package:
                packages.add(package)
            transport_name = manifest.get("transport_name")
            if isinstance(transport_name, str) and transport_name:
                transport_names.add(transport_name)
            protocol_version = manifest.get("protocol_version")
            if isinstance(protocol_version, str) and protocol_version:
                protocol_versions.add(protocol_version)
            abi_version = manifest.get("abi_version")
            if isinstance(abi_version, str) and abi_version:
                abi_versions.add(abi_version)
            scope = manifest.get("transport_scope")
            if isinstance(scope, str) and scope:
                scopes.add(scope)
            features = manifest.get("enabled_features")
            if isinstance(features, list):
                enabled_features.update(feature for feature in features if isinstance(feature, str) and feature)
    return {
        "transport_scopes": tuple(sorted(scopes)),
        "manifest_packages": tuple(sorted(packages)),
        "transport_names": tuple(sorted(transport_names)),
        "protocol_versions": tuple(sorted(protocol_versions)),
        "abi_versions": tuple(sorted(abi_versions)),
        "enabled_features": tuple(sorted(enabled_features)),
    }


def _wheel_platform_tag(path: Path) -> str:
    name = path.name
    if not name.endswith(".whl"):
        raise ValueError(f"not a wheel file: {path}")
    parts = name[:-4].split("-")
    if len(parts) < 5:
        raise ValueError(f"invalid wheel filename: {path}")
    return parts[-1]


def _matches_artifact_platform_tag(summary: WheelNativeSummary) -> bool:
    if len(summary.artifact_tags) != 1:
        return False
    expected = ARTIFACT_PLATFORM_TAGS.get(summary.artifact_tags[0])
    return expected is not None and expected == summary.platform_tag


def _has_preview4_native_artifact_shape(summary: WheelNativeSummary) -> bool:
    expected_transports = {"tcp", "quic", "ipc", "websocket"}
    expected_packages = {f"nnrp-ffi-transport-{transport}" for transport in expected_transports}
    expected_features = {f"transport-{transport}" for transport in expected_transports}
    return (
        set(summary.transport_scopes) == expected_transports
        and set(summary.transport_names) == expected_transports
        and set(summary.manifest_packages) == expected_packages
        and set(summary.protocol_versions) == {"NNRP/1"}
        and expected_features.issubset(summary.enabled_features)
        and "all" not in summary.transport_scopes
        and "nnrp-ffi" not in summary.manifest_packages
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify nnrp-py wheel native artifact contents.")
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--require-native", action="store_true")
    parser.add_argument("--reject-universal-native", action="store_true")
    parser.add_argument("--require-single-platform", action="store_true")
    parser.add_argument("--verify-platform-tag", action="store_true")
    parser.add_argument("--require-split-transports", action="store_true")
    parser.add_argument("--require-preview4-native-artifacts", action="store_true")
    parser.add_argument("--require-abi-version")
    args = parser.parse_args()

    try:
        summaries = inspect_dist(args.dist)
        verify_native_wheels(
            summaries,
            require_native=args.require_native,
            reject_universal_native=args.reject_universal_native,
            require_single_platform=args.require_single_platform,
            verify_platform_tag=args.verify_platform_tag,
            require_split_transports=args.require_split_transports,
            require_preview4_native_artifacts=args.require_preview4_native_artifacts,
            require_abi_version=args.require_abi_version,
        )
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1

    for summary in summaries:
        library_count = len(summary.libraries)
        library_label = "library" if library_count == 1 else "libraries"
        print(
            f"{summary.wheel.name}: "
            f"{len(summary.manifests)} manifest(s), {library_count} native {library_label}, "
            f"platform={summary.platform_tag}, artifacts={','.join(summary.artifact_tags) or '-'}, "
            f"transports={','.join(summary.transport_scopes) or '-'}, "
            f"packages={','.join(summary.manifest_packages) or '-'}, "
            f"abi={','.join(summary.abi_versions) or '-'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
