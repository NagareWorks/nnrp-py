"""Suite-owned conformance exporter entrypoints for SDK workflows."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from nnrp.tools.golden_vectors import export_cross_language_golden_vectors


def build_conformance_vector_manifest(protocol_version: str) -> dict[str, Any]:
    if protocol_version != "nnrp-1-preview2":
        raise ValueError(f"unsupported protocol version for Python conformance export: {protocol_version}")

    return {
        "protocol_version": protocol_version,
        "generator": "nnrp-py",
        "vectors": [vector.to_manifest_entry() for vector in export_cross_language_golden_vectors()],
    }


def write_conformance_vector_manifest(protocol_version: str, output_path: Path) -> None:
    manifest = build_conformance_vector_manifest(protocol_version)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f"{json.dumps(manifest, indent=2)}\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nnrp-export-conformance-vectors")
    parser.add_argument("--protocol-version", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)

    write_conformance_vector_manifest(args.protocol_version, Path(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())