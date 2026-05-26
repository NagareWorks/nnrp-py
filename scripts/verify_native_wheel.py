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


@dataclass(frozen=True)
class WheelNativeSummary:
    wheel: Path
    manifests: tuple[str, ...]
    libraries: tuple[str, ...]

    @property
    def has_native_artifacts(self) -> bool:
        return bool(self.manifests and self.libraries)


def inspect_wheel(path: Path) -> WheelNativeSummary:
    with zipfile.ZipFile(path) as archive:
        names = tuple(archive.namelist())
    manifests = tuple(
        name for name in names if name.startswith(NATIVE_ARTIFACT_PREFIX) and name.endswith("/manifest.json")
    )
    libraries = tuple(
        name for name in names if name.startswith(NATIVE_ARTIFACT_PREFIX) and name.endswith(NATIVE_LIBRARY_SUFFIXES)
    )
    return WheelNativeSummary(path, manifests, libraries)


def inspect_dist(dist: Path) -> list[WheelNativeSummary]:
    wheels = sorted(dist.glob("*.whl"))
    if not wheels:
        raise ValueError(f"no wheel files found under {dist}")
    return [inspect_wheel(wheel) for wheel in wheels]


def verify_native_wheels(summaries: Iterable[WheelNativeSummary], *, require_native: bool) -> None:
    failures = [
        summary
        for summary in summaries
        if require_native and not summary.has_native_artifacts
    ]
    if failures:
        wheel_names = ", ".join(summary.wheel.name for summary in failures)
        raise ValueError(f"wheel is missing packaged native artifacts: {wheel_names}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify nnrp-py wheel native artifact contents.")
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--require-native", action="store_true")
    args = parser.parse_args()

    try:
        summaries = inspect_dist(args.dist)
        verify_native_wheels(summaries, require_native=args.require_native)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1

    for summary in summaries:
        library_count = len(summary.libraries)
        library_label = "library" if library_count == 1 else "libraries"
        print(
            f"{summary.wheel.name}: "
            f"{len(summary.manifests)} manifest(s), {library_count} native {library_label}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
