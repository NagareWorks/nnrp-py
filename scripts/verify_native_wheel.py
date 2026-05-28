#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

NATIVE_ARTIFACT_PREFIX = "nnrp/native_artifacts/"
NATIVE_LIBRARY_SUFFIXES = (".dll", ".so", ".dylib", ".a")
UNIVERSAL_PLATFORM_TAG = "any"
CFFI_API_MODULE_PREFIX = "nnrp/_nnrp_cffi_api_submit_result"
CFFI_API_SUFFIXES = (".py", ".pyd", ".so", ".dylib")

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
    cffi_api_entries: tuple[str, ...] = ()

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
    manifests = tuple(
        name for name in names if name.startswith(NATIVE_ARTIFACT_PREFIX) and name.endswith("/manifest.json")
    )
    libraries = tuple(
        name for name in names if name.startswith(NATIVE_ARTIFACT_PREFIX) and name.endswith(NATIVE_LIBRARY_SUFFIXES)
    )
    artifact_tags = tuple(sorted(_artifact_tags(manifests + libraries)))
    cffi_api_entries = tuple(
        name
        for name in names
        if name.startswith(CFFI_API_MODULE_PREFIX) and name.endswith(CFFI_API_SUFFIXES)
    )
    return WheelNativeSummary(path, manifests, libraries, artifact_tags, platform_tag, cffi_api_entries)


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
    require_cffi_api: bool = False,
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

    missing_cffi_api = [
        summary
        for summary in summary_list
        if require_cffi_api and summary.has_native_artifacts and not summary.cffi_api_entries
    ]
    if missing_cffi_api:
        wheel_names = ", ".join(summary.wheel.name for summary in missing_cffi_api)
        raise ValueError(f"wheel is missing packaged cffi API module: {wheel_names}")


def _artifact_tags(names: Iterable[str]) -> set[str]:
    return {
        parts[2]
        for name in names
        if name.startswith(NATIVE_ARTIFACT_PREFIX)
        for parts in [name.split("/")]
        if len(parts) >= 4 and parts[2]
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify nnrp-py wheel native artifact contents.")
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--require-native", action="store_true")
    parser.add_argument("--reject-universal-native", action="store_true")
    parser.add_argument("--require-single-platform", action="store_true")
    parser.add_argument("--verify-platform-tag", action="store_true")
    parser.add_argument("--require-cffi-api", action="store_true")
    args = parser.parse_args()

    try:
        summaries = inspect_dist(args.dist)
        verify_native_wheels(
            summaries,
            require_native=args.require_native,
            reject_universal_native=args.reject_universal_native,
            require_single_platform=args.require_single_platform,
            verify_platform_tag=args.verify_platform_tag,
            require_cffi_api=args.require_cffi_api,
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
            f"cffi_api={len(summary.cffi_api_entries)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
