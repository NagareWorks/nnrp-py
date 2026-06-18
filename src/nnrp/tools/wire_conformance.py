"""Wire-level conformance target manifest generation."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_TARGET_SCHEMA_URL = "https://github.com/NagareWorks/nnrp-conformance/schemas/wire-conformance-target.schema.json"
_DEFAULT_TARGET_NAME = "nnrp-py"
_DEFAULT_PROTOCOL_VERSION = "nnrp-1-preview4"
_DEFAULT_SUITE_VERSION = "0.1.0"
_DEFAULT_MAX_FRAME_BYTES = 16 * 1024 * 1024
_DEFAULT_MAX_IN_FLIGHT = 256
_VALID_MODES = frozenset({"suite_as_client", "suite_as_server", "suite_as_proxy"})
_VALID_TRANSPORTS = frozenset({"tcp", "quic", "ipc", "websocket"})


@dataclass(frozen=True, slots=True)
class WireTargetTransport:
    name: str
    endpoint: str
    tls: bool = False


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
            "transports": [
                {
                    "name": transport.name,
                    "endpoint": transport.endpoint,
                    "tls": transport.tls,
                }
                for transport in normalized_transports
            ],
            "capabilities": normalized_capabilities,
            "limits": {
                "max_frame_bytes": max_frame_bytes,
                "max_in_flight": max_in_flight,
            },
        },
    }


def write_wire_target_manifest(output_path: Path, manifest: Mapping[str, Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f"{json.dumps(manifest, indent=2)}\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nnrp-wire-target-manifest")
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
    parser.add_argument("--capability", action="append", dest="capabilities", required=True)
    parser.add_argument("--max-frame-bytes", type=int, default=_DEFAULT_MAX_FRAME_BYTES)
    parser.add_argument("--max-in-flight", type=int, default=_DEFAULT_MAX_IN_FLIGHT)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)

    manifest = build_wire_target_manifest(
        target_name=args.target_name,
        suite_version=args.suite_version,
        modes=args.modes,
        transports=[parse_wire_target_transport(value) for value in args.transports],
        capabilities=args.capabilities,
        max_frame_bytes=args.max_frame_bytes,
        max_in_flight=args.max_in_flight,
    )
    write_wire_target_manifest(Path(args.output), manifest)
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


def _normalize_unique_strings(values: Sequence[str], *, field_name: str) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            raise ValueError(f"{field_name} entries must be non-empty")
        if field_name == "modes" and value not in _VALID_MODES:
            raise ValueError(f"unsupported wire conformance mode: {value}")
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
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
        seen.add(transport.name)
        normalized.append(transport)
    if not normalized:
        raise ValueError("transports must not be empty")
    return normalized


def _endpoint_uses_tls(name: str, endpoint: str) -> bool:
    if name == "websocket":
        return endpoint.lower().startswith("wss://")
    if name == "quic":
        return endpoint.lower().startswith("quic+tls://")
    return False


__all__ = [
    "WireTargetTransport",
    "build_wire_target_manifest",
    "main",
    "parse_wire_target_transport",
    "write_wire_target_manifest",
]
