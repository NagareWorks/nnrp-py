#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import shutil
import zipfile
from pathlib import Path

NATIVE_ARTIFACT_PREFIX = "nnrp/native_artifacts/"
WHEEL_SUFFIX = ".whl"

DEFAULT_PLATFORM_TAGS = {
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


def build_native_wheels(
    source_wheel: Path,
    output_dir: Path,
    *,
    clean: bool = False,
) -> list[Path]:
    if clean and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(source_wheel) as archive:
        names = archive.namelist()
        artifact_tags = _artifact_tags(names)
        if not artifact_tags:
            raise ValueError(f"source wheel does not contain native artifacts: {source_wheel}")

        dist_info = _find_dist_info(names)
        wheel_metadata_name = f"{dist_info}/WHEEL"
        base_entries = {name: archive.read(name) for name in names if name != f"{dist_info}/RECORD"}

    built: list[Path] = []
    for artifact_tag in artifact_tags:
        platform_tag = DEFAULT_PLATFORM_TAGS.get(artifact_tag)
        if platform_tag is None:
            raise ValueError(f"no Python wheel platform tag is configured for native artifact {artifact_tag}")

        entries = {name: data for name, data in base_entries.items() if _keep_entry_for_artifact(name, artifact_tag)}
        entries[wheel_metadata_name] = _retag_wheel_metadata(
            entries[wheel_metadata_name],
            python_tag="py3",
            abi_tag="none",
            platform_tag=platform_tag,
        )
        output_wheel = output_dir / _retag_wheel_name(
            source_wheel.name,
            python_tag="py3",
            abi_tag="none",
            platform_tag=platform_tag,
        )
        entries[f"{dist_info}/RECORD"] = _build_record(entries)
        with zipfile.ZipFile(output_wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(entries.items()):
                archive.writestr(name, data)
        built.append(output_wheel)
    if not built:
        raise ValueError("no platform wheels were built")
    return built


def _artifact_tags(names: list[str]) -> tuple[str, ...]:
    tags = {
        parts[2]
        for name in names
        if name.startswith(NATIVE_ARTIFACT_PREFIX)
        for parts in [name.split("/")]
        if len(parts) >= 4 and parts[2]
    }
    return tuple(sorted(tags))


def _find_dist_info(names: list[str]) -> str:
    candidates = sorted({name.split("/", 1)[0] for name in names if ".dist-info/" in name})
    if len(candidates) != 1:
        raise ValueError("wheel must contain exactly one .dist-info directory")
    return candidates[0]


def _keep_entry_for_artifact(name: str, artifact_tag: str) -> bool:
    if not name.startswith(NATIVE_ARTIFACT_PREFIX):
        return True
    return name.startswith(f"{NATIVE_ARTIFACT_PREFIX}{artifact_tag}/")


def _retag_wheel_metadata(source: bytes, *, python_tag: str, abi_tag: str, platform_tag: str) -> bytes:
    lines = source.decode("utf-8").splitlines()
    rewritten: list[str] = []
    wrote_tag = False
    for line in lines:
        if line.startswith("Root-Is-Purelib:"):
            rewritten.append("Root-Is-Purelib: false")
        elif line.startswith("Tag:"):
            if not wrote_tag:
                rewritten.append(f"Tag: {python_tag}-{abi_tag}-{platform_tag}")
                wrote_tag = True
        else:
            rewritten.append(line)
    if not wrote_tag:
        rewritten.append(f"Tag: {python_tag}-{abi_tag}-{platform_tag}")
    return ("\n".join(rewritten) + "\n").encode("utf-8")


def _retag_wheel_name(name: str, *, python_tag: str, abi_tag: str, platform_tag: str) -> str:
    if not name.endswith(WHEEL_SUFFIX):
        raise ValueError(f"source file is not a wheel: {name}")
    stem = name[: -len(WHEEL_SUFFIX)]
    parts = stem.split("-")
    if len(parts) < 5:
        raise ValueError(f"invalid wheel filename: {name}")
    parts[-3:] = [python_tag, abi_tag, platform_tag]
    return "-".join(parts) + WHEEL_SUFFIX


def _build_record(entries: dict[str, bytes]) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    for name in sorted(entries):
        digest = base64.urlsafe_b64encode(hashlib.sha256(entries[name]).digest()).rstrip(b"=").decode("ascii")
        writer.writerow([name, f"sha256={digest}", str(len(entries[name]))])
    writer.writerow([_record_name(entries), "", ""])
    return output.getvalue().encode("utf-8")


def _record_name(entries: dict[str, bytes]) -> str:
    dist_infos = sorted({name.split("/", 1)[0] for name in entries if ".dist-info/" in name})
    if len(dist_infos) != 1:
        raise ValueError("wheel entries must contain exactly one .dist-info directory")
    return f"{dist_infos[0]}/RECORD"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build platform wheels from a native-artifact staging wheel.")
    parser.add_argument(
        "--wheel",
        type=Path,
        required=True,
        help="Wheel containing one or more staged native artifacts.",
    )
    parser.add_argument("--dist", type=Path, default=Path("dist"), help="Output directory for platform wheels.")
    parser.add_argument("--clean", action="store_true", help="Remove the output directory before writing wheels.")
    args = parser.parse_args()

    for wheel in build_native_wheels(
        args.wheel,
        args.dist,
        clean=args.clean,
    ):
        print(wheel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
