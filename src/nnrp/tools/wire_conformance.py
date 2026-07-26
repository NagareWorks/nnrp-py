"""Wire-level conformance target manifest and result helpers."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nnrp.capabilities import PREVIEW4_TRANSPORT_NAMES

_TARGET_SCHEMA_URL = "https://github.com/NagareWorks/nnrp-conformance/schemas/wire-conformance-target.schema.json"
_RESULT_SCHEMA_URL = (
    "https://github.com/NagareWorks/nnrp-conformance/schemas/wire-conformance-case-results.schema.json"
)
_DEFAULT_TARGET_NAME = "nnrp-py"
_DEFAULT_PROTOCOL_VERSION = "nnrp-1-preview4"
_DEFAULT_SUITE_VERSION = "0.1.0"
_DEFAULT_MAX_FRAME_BYTES = 16 * 1024 * 1024
_DEFAULT_MAX_IN_FLIGHT = 256
_VALID_MODES = frozenset({"suite_as_client", "suite_as_server", "suite_as_proxy"})
_VALID_TRANSPORTS = frozenset(PREVIEW4_TRANSPORT_NAMES)
_VALID_FRAME_DIRECTIONS = frozenset({"sent", "received"})
_VALID_OUTCOMES = frozenset({"passed", "failed", "skipped"})
_VALID_TERMINALS = frozenset({"success", "cancelled", "dropped", "error"})


@dataclass(frozen=True, slots=True)
class WireTargetTransportSecurity:
    server_name: str
    trusted_certificate_der_path: str
    certificate_der_path: str
    private_key_pkcs8_der_path: str


@dataclass(frozen=True, slots=True)
class WireTargetTransport:
    name: str
    endpoint: str
    tls: bool = False
    security: WireTargetTransportSecurity | None = None


@dataclass(frozen=True, slots=True)
class WireObservedFrame:
    direction: str
    frame: str
    payload: Mapping[str, Any] | None = None
    timestamp_us: int | None = None


@dataclass(frozen=True, slots=True)
class WireCaseResult:
    id: str
    outcome: str
    terminal: str
    observed_frames: Sequence[WireObservedFrame] = ()
    message: str | None = None
    evidence_paths: Sequence[str] = ()


def build_wire_target_manifest(
    *,
    target_name: str = _DEFAULT_TARGET_NAME,
    suite_version: str = _DEFAULT_SUITE_VERSION,
    modes: Sequence[str],
    transports: Sequence[WireTargetTransport],
    capabilities: Sequence[str],
    max_frame_bytes: int = _DEFAULT_MAX_FRAME_BYTES,
    max_in_flight: int = _DEFAULT_MAX_IN_FLIGHT,
) -> dict[str, Any]:
    normalized_modes = _normalize_unique_strings(modes, field_name="modes")
    normalized_capabilities = _normalize_unique_strings(capabilities, field_name="capabilities")
    normalized_transports = _normalize_transports(transports)
    if not target_name:
        raise ValueError("target_name must be non-empty")
    if not suite_version:
        raise ValueError("suite_version must be non-empty")
    if max_frame_bytes <= 0:
        raise ValueError("max_frame_bytes must be positive")
    if max_in_flight <= 0:
        raise ValueError("max_in_flight must be positive")

    return {
        "$schema": _TARGET_SCHEMA_URL,
        "target_name": target_name,
        "protocol_version": _DEFAULT_PROTOCOL_VERSION,
        "suite_version": suite_version,
        "wire_conformance": {
            "modes": normalized_modes,
            "transports": [_target_transport_to_dict(transport) for transport in normalized_transports],
            "capabilities": normalized_capabilities,
            "limits": {
                "max_frame_bytes": max_frame_bytes,
                "max_in_flight": max_in_flight,
            },
        },
    }


def build_wire_case_results_report(
    *,
    target_name: str = _DEFAULT_TARGET_NAME,
    suite_version: str = _DEFAULT_SUITE_VERSION,
    results: Sequence[WireCaseResult],
) -> dict[str, Any]:
    if not target_name:
        raise ValueError("target_name must be non-empty")
    if not suite_version:
        raise ValueError("suite_version must be non-empty")
    if not results:
        raise ValueError("results must not be empty")

    normalized_results = [_case_result_to_dict(result) for result in results]
    _reject_duplicate_result_ids(normalized_results)
    return {
        "$schema": _RESULT_SCHEMA_URL,
        "protocol_version": _DEFAULT_PROTOCOL_VERSION,
        "suite_version": suite_version,
        "target_name": target_name,
        "results": normalized_results,
    }


def build_wire_skipped_results_from_plan(
    plan: Mapping[str, Any],
    *,
    message: str,
) -> dict[str, Any]:
    if not message:
        raise ValueError("skip message must be non-empty")
    scenarios = plan.get("scenarios")
    if not isinstance(scenarios, Sequence) or isinstance(scenarios, str) or not scenarios:
        raise ValueError("wire execution plan must contain scenarios")

    results: list[WireCaseResult] = []
    for scenario in scenarios:
        if not isinstance(scenario, Mapping):
            raise ValueError("wire execution plan scenarios must be objects")
        scenario_id = scenario.get("id")
        if not isinstance(scenario_id, str) or not scenario_id:
            raise ValueError("wire execution plan scenario id must be non-empty")
        results.append(
            WireCaseResult(
                id=scenario_id,
                outcome="skipped",
                terminal="error",
                message=message,
            )
        )

    return build_wire_case_results_report(
        target_name=_required_string(plan, "target_name"),
        suite_version=_required_string(plan, "suite_version"),
        results=results,
    )


def validate_wire_case_results_against_plan(
    plan: Mapping[str, Any],
    report: Mapping[str, Any],
) -> None:
    plan_scenarios = _scenario_map(plan)
    raw_results = report.get("results")
    if not isinstance(raw_results, Sequence) or isinstance(raw_results, str) or not raw_results:
        raise ValueError("wire result report must contain results")

    seen: set[str] = set()
    for result in raw_results:
        if not isinstance(result, Mapping):
            raise ValueError("wire result entries must be objects")
        result_id = _required_string(result, "id")
        if result_id not in plan_scenarios:
            raise ValueError(f"wire result contains unexpected scenario id: {result_id}")
        if result_id in seen:
            raise ValueError(f"wire result contains duplicate scenario id: {result_id}")
        seen.add(result_id)

        outcome = _required_string(result, "outcome")
        if outcome == "passed":
            _validate_passed_result_frames(plan_scenarios[result_id], result)
        elif outcome not in _VALID_OUTCOMES:
            raise ValueError(f"unsupported wire result outcome: {outcome}")

    missing = sorted(set(plan_scenarios) - seen)
    if missing:
        raise ValueError(f"wire results are missing scenario ids: {', '.join(missing)}")


def write_wire_target_manifest(output_path: Path, manifest: Mapping[str, Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f"{json.dumps(manifest, indent=2)}\n", encoding="utf-8")


def write_wire_case_results_report(output_path: Path, report: Mapping[str, Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f"{json.dumps(report, indent=2)}\n", encoding="utf-8")


def write_wire_evidence_files(report: Mapping[str, Any]) -> None:
    raw_results = report.get("results")
    if not isinstance(raw_results, Sequence) or isinstance(raw_results, str):
        raise ValueError("wire result report must contain results")
    for result in raw_results:
        if not isinstance(result, Mapping):
            raise ValueError("wire result entries must be objects")
        raw_evidence_paths = result.get("evidence_paths", [])
        if not isinstance(raw_evidence_paths, Sequence) or isinstance(raw_evidence_paths, str):
            raise ValueError("wire result evidence_paths must be a list")
        for raw_path in raw_evidence_paths:
            if not isinstance(raw_path, str) or not raw_path:
                raise ValueError("wire result evidence path must be non-empty")
            evidence_path = Path(raw_path)
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_path.write_text(f"{json.dumps(_evidence_record(result), sort_keys=True)}\n", encoding="utf-8")


def run_wire_harness_plan(
    plan_path: Path,
    output_path: Path,
    *,
    mode: str,
    evidence_dir: Path | None = None,
    skip_message: str | None = None,
) -> dict[str, Any]:
    normalized_mode = _normalize_wire_mode(mode)
    plan = _read_json_object(plan_path, description="wire execution plan")
    scoped_plan = _wire_plan_for_mode(plan, normalized_mode)
    message = skip_message or (
        f"Python wire harness {normalized_mode} is registered; live endpoint execution is not enabled."
    )
    report = build_wire_skipped_results_from_plan(scoped_plan, message=message)
    if evidence_dir is not None:
        report = _attach_wire_evidence_paths(report, evidence_dir)
    validate_wire_case_results_against_plan(scoped_plan, report)
    write_wire_case_results_report(output_path, report)
    if evidence_dir is not None:
        write_wire_evidence_files(report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    if args and args[0] in {"manifest", "run-plan", "serve-target"}:
        command = args.pop(0)
        if command == "run-plan":
            return _run_wire_plan_cli(args)
        if command == "serve-target":
            return _serve_wire_target_cli(args)
        return _target_manifest_cli(args)
    return _target_manifest_cli(args)


def _target_manifest_cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-name", default=_DEFAULT_TARGET_NAME)
    parser.add_argument("--suite-version", default=_DEFAULT_SUITE_VERSION)
    parser.add_argument("--mode", action="append", dest="modes", required=True)
    parser.add_argument(
        "--transport",
        action="append",
        dest="transports",
        required=True,
        help="Transport in name=endpoint form, for example tcp=127.0.0.1:19091 or websocket=wss://host/nnrp.",
    )
    parser.add_argument(
        "--transport-security",
        action="append",
        dest="transport_security",
        default=[],
        help=(
            "JSON object containing transport, server_name, trusted_certificate_der_path, "
            "certificate_der_path, and private_key_pkcs8_der_path. Repeat for each TLS transport."
        ),
    )
    parser.add_argument("--capability", action="append", dest="capabilities", required=True)
    parser.add_argument("--max-frame-bytes", type=int, default=_DEFAULT_MAX_FRAME_BYTES)
    parser.add_argument("--max-in-flight", type=int, default=_DEFAULT_MAX_IN_FLIGHT)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)

    transports = [parse_wire_target_transport(value) for value in args.transports]
    security_by_transport: dict[str, WireTargetTransportSecurity] = {}
    for value in args.transport_security:
        transport_name, security = parse_wire_target_security(value)
        if transport_name in security_by_transport:
            raise ValueError(f"duplicate transport security: {transport_name}")
        security_by_transport[transport_name] = security
    unknown_security = sorted(set(security_by_transport) - {transport.name for transport in transports})
    if unknown_security:
        raise ValueError(f"transport security references undeclared transport: {', '.join(unknown_security)}")
    secured_transports = []
    for transport in transports:
        security = security_by_transport.get(transport.name)
        secured_transports.append(
            dataclasses.replace(
                transport,
                tls=transport.tls or (transport.name == "tcp" and security is not None),
                security=security,
            )
        )
    manifest = build_wire_target_manifest(
        target_name=args.target_name,
        suite_version=args.suite_version,
        modes=args.modes,
        transports=secured_transports,
        capabilities=args.capabilities,
        max_frame_bytes=args.max_frame_bytes,
        max_in_flight=args.max_in_flight,
    )
    write_wire_target_manifest(Path(args.output), manifest)
    return 0


def _run_wire_plan_cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nnrp-wire-conformance run-plan")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--mode", required=True, choices=sorted(_VALID_MODES))
    parser.add_argument("--output", required=True)
    parser.add_argument("--evidence-dir")
    parser.add_argument("--skip-message")
    args = parser.parse_args(list(argv) if argv is not None else None)

    run_wire_harness_plan(
        Path(args.plan),
        Path(args.output),
        mode=args.mode,
        evidence_dir=Path(args.evidence_dir) if args.evidence_dir is not None else None,
        skip_message=args.skip_message,
    )
    return 0


def _serve_wire_target_cli(argv: Sequence[str] | None = None) -> int:
    from nnrp.tools.wire_target import run_live_wire_target

    parser = argparse.ArgumentParser(prog="nnrp-wire-conformance serve-target")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--mode", required=True, choices=sorted(_VALID_MODES))
    parser.add_argument("--ready-file")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    args = parser.parse_args(list(argv) if argv is not None else None)

    run_live_wire_target(
        Path(args.plan),
        Path(args.target),
        mode=args.mode,
        ready_path=Path(args.ready_file) if args.ready_file is not None else None,
        timeout_seconds=args.timeout_seconds,
    )
    return 0


def parse_wire_target_transport(value: str) -> WireTargetTransport:
    name, separator, endpoint = value.partition("=")
    if not separator:
        raise ValueError("transport must use name=endpoint form")
    normalized_name = name.strip().lower()
    normalized_endpoint = endpoint.strip()
    if not normalized_endpoint:
        raise ValueError("transport endpoint must be non-empty")
    return WireTargetTransport(
        name=normalized_name,
        endpoint=normalized_endpoint,
        tls=_endpoint_uses_tls(normalized_name, normalized_endpoint),
    )


def parse_wire_target_security(value: str) -> tuple[str, WireTargetTransportSecurity]:
    try:
        document = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("transport security must be a JSON object") from error
    if not isinstance(document, Mapping):
        raise ValueError("transport security must be a JSON object")
    expected_fields = {
        "transport",
        "server_name",
        "trusted_certificate_der_path",
        "certificate_der_path",
        "private_key_pkcs8_der_path",
    }
    unexpected_fields = sorted(set(document) - expected_fields)
    if unexpected_fields:
        raise ValueError(f"transport security contains unknown fields: {', '.join(unexpected_fields)}")
    missing_fields = sorted(expected_fields - set(document))
    if missing_fields:
        raise ValueError(f"transport security is missing fields: {', '.join(missing_fields)}")
    transport = _required_string(document, "transport").strip().lower()
    return transport, WireTargetTransportSecurity(
        server_name=_required_string(document, "server_name"),
        trusted_certificate_der_path=_required_string(document, "trusted_certificate_der_path"),
        certificate_der_path=_required_string(document, "certificate_der_path"),
        private_key_pkcs8_der_path=_required_string(document, "private_key_pkcs8_der_path"),
    )


def _read_json_object(path: Path, *, description: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"{description} was not found: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"{description} is invalid JSON: {path}") from error
    if not isinstance(document, dict):
        raise ValueError(f"{description} must be a JSON object: {path}")
    return document


def _wire_plan_for_mode(plan: Mapping[str, Any], mode: str) -> dict[str, Any]:
    scenarios = plan.get("scenarios")
    if not isinstance(scenarios, Sequence) or isinstance(scenarios, str) or not scenarios:
        raise ValueError("wire execution plan must contain scenarios")
    scoped_scenarios = []
    for scenario in scenarios:
        if not isinstance(scenario, Mapping):
            raise ValueError("wire execution plan scenarios must be objects")
        scenario_mode = scenario.get("mode")
        if scenario_mode is not None:
            scenario_mode = _normalize_wire_mode(_required_string(scenario, "mode"))
        if scenario_mode is None or scenario_mode == mode:
            scoped_scenarios.append(dict(scenario))
    if not scoped_scenarios:
        raise ValueError(f"wire execution plan contains no scenarios for mode: {mode}")
    scoped_plan = dict(plan)
    scoped_plan["scenarios"] = scoped_scenarios
    return scoped_plan


def _attach_wire_evidence_paths(report: Mapping[str, Any], evidence_dir: Path) -> dict[str, Any]:
    raw_results = report.get("results")
    if not isinstance(raw_results, Sequence) or isinstance(raw_results, str):
        raise ValueError("wire result report must contain results")
    results = []
    for result in raw_results:
        if not isinstance(result, Mapping):
            raise ValueError("wire result entries must be objects")
        result_id = _required_string(result, "id")
        entry = dict(result)
        entry["evidence_paths"] = [str(evidence_dir / f"{_safe_wire_case_filename(result_id)}.jsonl")]
        results.append(entry)
    updated = dict(report)
    updated["results"] = results
    return updated


def _safe_wire_case_filename(case_id: str) -> str:
    return "".join(character if character.isalnum() or character in {"-", "_"} else "-" for character in case_id)


def _normalize_unique_strings(values: Sequence[str], *, field_name: str) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            raise ValueError(f"{field_name} entries must be non-empty")
        if field_name == "modes":
            value = _normalize_wire_mode(value)
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _normalize_wire_mode(mode: str) -> str:
    normalized = mode.strip()
    if normalized not in _VALID_MODES:
        raise ValueError(f"unsupported wire conformance mode: {mode}")
    return normalized


def _normalize_transports(transports: Sequence[WireTargetTransport]) -> list[WireTargetTransport]:
    normalized: list[WireTargetTransport] = []
    seen: set[str] = set()
    for transport in transports:
        if transport.name not in _VALID_TRANSPORTS:
            raise ValueError(f"unsupported wire conformance transport: {transport.name}")
        if not transport.endpoint:
            raise ValueError("transport endpoint must be non-empty")
        if transport.name in seen:
            raise ValueError(f"duplicate wire conformance transport: {transport.name}")
        expected_tls = _endpoint_uses_tls(transport.name, transport.endpoint)
        if transport.name == "quic" and not transport.tls:
            raise ValueError("QUIC wire conformance transport requires TLS")
        if transport.name == "ipc" and transport.tls:
            raise ValueError("ipc wire conformance transport does not use TLS")
        if transport.name == "websocket" and transport.tls != expected_tls:
            raise ValueError("WebSocket TLS flag must match its ws:// or wss:// endpoint")
        if transport.tls and transport.security is None:
            raise ValueError(f"{transport.name} TLS transport requires security material")
        if not transport.tls and transport.security is not None:
            raise ValueError(f"{transport.name} non-TLS transport must not declare security material")
        seen.add(transport.name)
        normalized.append(transport)
    if not normalized:
        raise ValueError("transports must not be empty")
    return normalized


def _target_transport_to_dict(transport: WireTargetTransport) -> dict[str, Any]:
    document: dict[str, Any] = {
        "name": transport.name,
        "endpoint": transport.endpoint,
        "tls": transport.tls,
    }
    if transport.security is not None:
        document["security"] = dataclasses.asdict(transport.security)
    return document


def _endpoint_uses_tls(name: str, endpoint: str) -> bool:
    if name == "websocket":
        return endpoint.lower().startswith("wss://")
    if name == "quic":
        return True
    return False


def _case_result_to_dict(result: WireCaseResult) -> dict[str, Any]:
    if not result.id:
        raise ValueError("wire case result id must be non-empty")
    if result.outcome not in _VALID_OUTCOMES:
        raise ValueError(f"unsupported wire result outcome: {result.outcome}")
    if result.terminal not in _VALID_TERMINALS:
        raise ValueError(f"unsupported wire result terminal: {result.terminal}")
    if result.outcome == "passed" and not result.observed_frames:
        raise ValueError("passed wire result must include observed frames")
    if result.outcome == "skipped" and not result.message:
        raise ValueError("skipped wire result must include a message")

    case_result: dict[str, Any] = {
        "id": result.id,
        "outcome": result.outcome,
        "terminal": result.terminal,
    }
    if result.observed_frames:
        case_result["observed_frames"] = [_observed_frame_to_dict(frame) for frame in result.observed_frames]
    if result.message is not None:
        if not result.message:
            raise ValueError("wire result message must be non-empty")
        case_result["message"] = result.message
    if result.evidence_paths:
        evidence_paths = _normalize_unique_strings(result.evidence_paths, field_name="evidence_paths")
        case_result["evidence_paths"] = evidence_paths
    return case_result


def _observed_frame_to_dict(frame: WireObservedFrame) -> dict[str, Any]:
    if frame.direction not in _VALID_FRAME_DIRECTIONS:
        raise ValueError(f"unsupported wire frame direction: {frame.direction}")
    if not frame.frame:
        raise ValueError("observed frame name must be non-empty")
    observed: dict[str, Any] = {
        "direction": frame.direction,
        "frame": frame.frame,
    }
    if frame.payload is not None:
        observed["payload"] = dict(frame.payload)
    if frame.timestamp_us is not None:
        if frame.timestamp_us < 0:
            raise ValueError("observed frame timestamp_us must be non-negative")
        observed["timestamp_us"] = frame.timestamp_us
    return observed


def _reject_duplicate_result_ids(results: Sequence[Mapping[str, Any]]) -> None:
    seen: set[str] = set()
    for result in results:
        result_id = _required_string(result, "id")
        if result_id in seen:
            raise ValueError(f"duplicate wire result id: {result_id}")
        seen.add(result_id)


def _required_string(mapping: Mapping[str, Any], field_name: str) -> str:
    value = mapping.get(field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be non-empty")
    return value


def _scenario_map(plan: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    scenarios = plan.get("scenarios")
    if not isinstance(scenarios, Sequence) or isinstance(scenarios, str) or not scenarios:
        raise ValueError("wire execution plan must contain scenarios")
    mapped: dict[str, Mapping[str, Any]] = {}
    for scenario in scenarios:
        if not isinstance(scenario, Mapping):
            raise ValueError("wire execution plan scenarios must be objects")
        scenario_id = _required_string(scenario, "id")
        if scenario_id in mapped:
            raise ValueError(f"wire execution plan contains duplicate scenario id: {scenario_id}")
        mapped[scenario_id] = scenario
    return mapped


def _validate_passed_result_frames(scenario: Mapping[str, Any], result: Mapping[str, Any]) -> None:
    expected = scenario.get("expect")
    if not isinstance(expected, Mapping):
        raise ValueError("wire execution plan scenario expect must be an object")
    expected_terminal = _required_string(expected, "terminal")
    result_terminal = _required_string(result, "terminal")
    if result_terminal != expected_terminal:
        raise ValueError(
            f"wire result terminal mismatch for {_required_string(result, 'id')}: "
            f"expected {expected_terminal}, got {result_terminal}"
        )

    expected_frames = expected.get("frames", [])
    if not isinstance(expected_frames, Sequence) or isinstance(expected_frames, str):
        raise ValueError("wire execution plan expect.frames must be a list")
    observed_frames = result.get("observed_frames", [])
    if not isinstance(observed_frames, Sequence) or isinstance(observed_frames, str):
        raise ValueError("wire result observed_frames must be a list")
    observed_frame_names = {
        _required_string(frame, "frame")
        for frame in observed_frames
        if isinstance(frame, Mapping)
    }
    missing_frames = sorted(frame for frame in expected_frames if frame not in observed_frame_names)
    if missing_frames:
        raise ValueError(
            f"wire result {_required_string(result, 'id')} is missing expected frame(s): "
            f"{', '.join(missing_frames)}"
        )


def _evidence_record(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": _required_string(result, "id"),
        "outcome": _required_string(result, "outcome"),
        "terminal": _required_string(result, "terminal"),
        "observed_frames": result.get("observed_frames", []),
        "message": result.get("message"),
    }


__all__ = [
    "WireCaseResult",
    "WireObservedFrame",
    "WireTargetTransport",
    "build_wire_case_results_report",
    "build_wire_skipped_results_from_plan",
    "build_wire_target_manifest",
    "main",
    "parse_wire_target_transport",
    "run_wire_harness_plan",
    "validate_wire_case_results_against_plan",
    "write_wire_case_results_report",
    "write_wire_evidence_files",
    "write_wire_target_manifest",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
