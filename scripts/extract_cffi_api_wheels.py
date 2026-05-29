#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
import zipfile
from pathlib import Path

CFFI_MODULE_PREFIX = "nnrp/_nnrp_cffi_api_submit_result"
CFFI_EXTENSION_SUFFIXES = (".pyd", ".so", ".dylib")

PLATFORM_TO_ARTIFACT = {
    "win32": "windows-x86",
    "win_amd64": "windows-x86_64",
    "win_arm64": "windows-arm64",
    "manylinux_2_28_i686": "linux-x86",
    "manylinux_2_28_x86_64": "linux-x86_64",
    "manylinux_2_28_armv7l": "linux-arm",
    "manylinux_2_28_aarch64": "linux-arm64",
}


def extract_cffi_api_wheels(wheel_dir: Path, output: Path, *, clean: bool = False) -> list[Path]:
    if clean and output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)

    extracted: list[Path] = []
    for wheel in sorted(wheel_dir.glob("*.whl")):
        artifact_tag = _artifact_tag_from_wheel(wheel)
        with zipfile.ZipFile(wheel) as archive:
            entries = [
                name
                for name in archive.namelist()
                if name.startswith(CFFI_MODULE_PREFIX) and name.endswith(CFFI_EXTENSION_SUFFIXES)
            ]
            if len(entries) != 1:
                raise ValueError(f"expected exactly one compiled cffi API extension in {wheel}: {entries}")
            entry = entries[0]
            target = output / artifact_tag / entry
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(entry))
            extracted.append(target)
    if not extracted:
        raise ValueError(f"no cffi API wheels found under {wheel_dir}")
    return extracted


def _artifact_tag_from_wheel(wheel: Path) -> str:
    parts = wheel.name.removesuffix(".whl").split("-")
    if len(parts) < 5:
        raise ValueError(f"invalid wheel filename: {wheel}")
    platform_tag = parts[-1]
    artifact_tag = PLATFORM_TO_ARTIFACT.get(platform_tag)
    if artifact_tag is None:
        artifact_tag = _macos_artifact_tag(platform_tag) or _mobile_artifact_tag(platform_tag)
    if artifact_tag is None:
        raise ValueError(f"no native artifact tag mapping for wheel platform {platform_tag}: {wheel}")
    return artifact_tag


def _macos_artifact_tag(platform_tag: str) -> str | None:
    macos = re.fullmatch(r"macosx_(?P<major>\d+)_(?P<minor>\d+)_(?P<arch>x86_64|arm64)", platform_tag)
    if macos is None:
        return None
    return f"macos-{macos.group('arch')}"


def _mobile_artifact_tag(platform_tag: str) -> str | None:
    android = re.fullmatch(r"android_(?P<api>\d+)_(?P<arch>x86|x86_64|armeabi_v7a|arm64_v8a)", platform_tag)
    if android is not None:
        return {
            "x86": "android-x86",
            "x86_64": "android-x86_64",
            "armeabi_v7a": "android-arm",
            "arm64_v8a": "android-arm64",
        }[android.group("arch")]

    ios = re.fullmatch(
        r"ios_(?P<major>\d+)_(?P<minor>\d+)_(?P<arch>arm64|x86_64)_(?P<kind>iphoneos|iphonesimulator)",
        platform_tag,
    )
    if ios is None:
        return None
    arch = ios.group("arch")
    kind = ios.group("kind")
    if kind == "iphoneos":
        return "ios-arm64" if arch == "arm64" else None
    return "ios-arm64-sim" if arch == "arm64" else "ios-x86_64-sim"


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract compiled cffi API extensions from platform wheels.")
    parser.add_argument("--wheel-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("artifacts") / "cffi-api")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    for path in extract_cffi_api_wheels(args.wheel_dir, args.output, clean=args.clean):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
