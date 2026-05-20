"""Preview3 adapter conformance wrapper for suite-owned execution plans."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_RESULTS_SCHEMA_URL = "https://raw.githubusercontent.com/NagareWorks/nnrp-conformance/main/schemas/adapter-case-results.schema.json"
_DEFAULT_IMPLEMENTATION_NAME = "nnrp-py"
_NOT_IMPLEMENTED_MESSAGE = "Preview3 adapter execution is not implemented in nnrp-py yet."


def build_adapter_case_results_report(
    plan_document: dict[str, Any],
    *,
    implementation_name: str = _DEFAULT_IMPLEMENTATION_NAME,
) -> dict[str, Any]:
    protocol_version = _require_string(plan_document, "protocol_version")
    cases = _require_case_list(plan_document)

    return {
        "$schema": _RESULTS_SCHEMA_URL,
        "protocol_version": protocol_version,
        "implementation_name": implementation_name,
        "results": [
            {
                "id": _require_string(case, "id"),
                "outcome": "error",
                "failure_kind": "not_implemented",
                "message": _NOT_IMPLEMENTED_MESSAGE,
            }
            for case in cases
        ],
    }


def write_adapter_case_results(plan_path: Path, output_path: Path) -> None:
    if not plan_path.is_file():
        raise ValueError(f"adapter execution plan path does not exist: {plan_path}")

    plan_document = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan_document, dict):
        raise ValueError("adapter execution plan must be a JSON object")

    report = build_adapter_case_results_report(plan_document)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f"{json.dumps(report, indent=2)}\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m nnrp.tools.adapter_conformance")
    parser.add_argument("--plan", default=os.environ.get("NNRP_CONFORMANCE_ADAPTER_PLAN"))
    parser.add_argument("--output", default=os.environ.get("NNRP_CONFORMANCE_ADAPTER_RESULTS"))
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.plan:
        parser.error("adapter execution plan path is required via --plan or NNRP_CONFORMANCE_ADAPTER_PLAN")

    if not args.output:
        parser.error("adapter result path is required via --output or NNRP_CONFORMANCE_ADAPTER_RESULTS")

    write_adapter_case_results(Path(args.plan), Path(args.output))
    return 0


def _require_string(document: dict[str, Any], field_name: str) -> str:
    value = document.get(field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"adapter document field '{field_name}' must be a non-empty string")
    return value


def _require_case_list(document: dict[str, Any]) -> list[dict[str, Any]]:
    cases = document.get("cases")
    if not isinstance(cases, list):
        raise ValueError("adapter execution plan must contain a cases list")

    normalized_cases: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("adapter execution plan cases must be JSON objects")
        normalized_cases.append(case)
    return normalized_cases


if __name__ == "__main__":
    raise SystemExit(main())