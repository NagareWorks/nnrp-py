#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "src" / "nnrp" / "native_artifacts"

_ARCH_ALIASES = {
    "amd64": "x86_64",
    "x64": "x86_64",
    "aarch64": "arm64",
    "aarch64-sim": "arm64-sim",
    "armv7": "arm",
    "armv7l": "arm",
    "i386": "x86",
    "i686": "x86",
}

_LIBRARY_NAMES = {
    "nnrp_ffi.dll",
    "libnnrp_ffi.so",
    "libnnrp_ffi.dylib",
    "libnnrp_ffi.a",
}


def prepare_native_artifacts(
    inputs: Iterable[Path],
    output: Path = DEFAULT_OUTPUT,
    *,
    clean: bool = False,
) -> list[Path]:
    if clean and output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)

    installed: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="nnrp-native-artifacts-") as temp_dir:
        temp_root = Path(temp_dir)
        for source in inputs:
            for package_dir in _iter_package_dirs(source, temp_root):
                installed.extend(_install_package(package_dir, output))
    return installed


def _iter_package_dirs(source: Path, temp_root: Path) -> Iterable[Path]:
    if source.is_file():
        if source.suffix.lower() != ".zip":
            raise ValueError(f"native artifact input must be a directory or zip file: {source}")
        extract_dir = temp_root / source.stem
        with zipfile.ZipFile(source) as archive:
            archive.extractall(extract_dir)
        yield from _iter_package_dirs(extract_dir, temp_root)
        return

    if not source.is_dir():
        raise ValueError(f"native artifact input does not exist: {source}")

    if (source / "manifest.json").is_file():
        yield source
        return

    for manifest in sorted(source.rglob("manifest.json")):
        yield manifest.parent


def _install_package(package_dir: Path, output: Path) -> list[Path]:
    manifest = _load_manifest(package_dir / "manifest.json")
    os_name = _require_string(manifest, "os")
    arch = _normalize_arch(_require_string(manifest, "arch"))
    target_dir = output / f"{os_name}-{arch}"
    transport_scope = _transport_scope(manifest)
    if transport_scope != "all":
        target_dir = target_dir / transport_scope
    target_dir.mkdir(parents=True, exist_ok=True)

    installed: list[Path] = []
    for library_name in _library_names(manifest):
        library = package_dir / library_name
        if not library.is_file():
            raise ValueError(f"native artifact manifest references missing library: {library}")
        target = target_dir / library.name
        shutil.copy2(library, target)
        installed.append(target)

    manifest_target = target_dir / "manifest.json"
    shutil.copy2(package_dir / "manifest.json", manifest_target)
    installed.append(manifest_target)
    return installed


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"native artifact manifest was not found: {path}") from error
    if not isinstance(document, dict):
        raise ValueError(f"native artifact manifest must be a JSON object: {path}")
    return document


def _require_string(document: dict[str, Any], field_name: str) -> str:
    value = document.get(field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"native artifact manifest field '{field_name}' must be a non-empty string")
    return value


def _normalize_arch(value: str) -> str:
    return _ARCH_ALIASES.get(value.lower(), value.lower())


def _library_names(manifest: dict[str, Any]) -> tuple[str, ...]:
    libraries = manifest.get("libraries")
    if isinstance(libraries, list):
        selected = [name for name in libraries if isinstance(name, str) and name in _LIBRARY_NAMES]
        if selected:
            return tuple(selected)

    library = manifest.get("library")
    if isinstance(library, str) and library in _LIBRARY_NAMES:
        return (library,)

    raise ValueError("native artifact manifest does not list a supported nnrp-ffi library")


def _transport_scope(manifest: dict[str, Any]) -> str:
    scope = manifest.get("transport_scope")
    if scope is None:
        return "all"
    if scope in {"all", "tcp", "quic"}:
        return scope
    raise ValueError(f"native artifact manifest lists unsupported transport scope: {scope}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare nnrp-rs native artifacts for the Python package.")
    parser.add_argument("inputs", nargs="+", type=Path, help="Artifact package directories or zip files.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--clean", action="store_true", help="Remove the output directory before installing artifacts.")
    args = parser.parse_args()

    installed = prepare_native_artifacts(args.inputs, args.output, clean=args.clean)
    for path in installed:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
