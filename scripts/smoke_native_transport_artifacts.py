#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from nnrp.native import (
    NATIVE_TRANSPORT_ID_BY_NAME,
    NativeArtifactError,
    load_native_client,
)

TRANSPORT_NAMES = frozenset(NATIVE_TRANSPORT_ID_BY_NAME)


@dataclass(frozen=True)
class NativeTransportSmokeResult:
    transport: str
    server_id: int
    session_id: int
    operation_id: int


def smoke_native_transport_artifacts(
    *,
    root: Path | str | None = None,
    transports: Iterable[str] = ("ipc", "websocket"),
    payload: bytes = b"preview4-native-transport-smoke",
) -> tuple[NativeTransportSmokeResult, ...]:
    results: list[NativeTransportSmokeResult] = []
    for index, transport in enumerate(transports, start=1):
        normalized_transport = _normalize_transport(transport)
        transport_id = NATIVE_TRANSPORT_ID_BY_NAME[normalized_transport]
        base_id = 40_000 + (index * 100)
        client = load_native_client(root=root, transport=normalized_transport)
        server = client.bind_server(
            server_id=base_id,
            generation=1,
            transport_id=int(transport_id),
        )
        session = server.accept_session(
            session_id=base_id + 1,
            generation=1,
            profile_id=2,
            schema_id=0x1001,
            schema_version=1,
        )
        operation = session.receive_submit(
            operation_id=base_id + 2,
            frame_id=1,
            payload=payload,
        )
        operation.send_result(payload)
        session.send_flow_update(frame_id=1)
        session.close()
        server.close()
        results.append(
            NativeTransportSmokeResult(
                transport=normalized_transport,
                server_id=base_id,
                session_id=base_id + 1,
                operation_id=base_id + 2,
            )
        )
    return tuple(results)


def _normalize_transport(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if normalized not in TRANSPORT_NAMES:
        raise NativeArtifactError(f"unsupported native transport smoke target: {value}")
    return normalized


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-test preview4 native transport artifacts.")
    parser.add_argument("--root", type=Path, default=None, help="Native artifact root; defaults to packaged artifacts.")
    parser.add_argument(
        "--transport",
        action="append",
        dest="transports",
        default=None,
        help="Transport scope to smoke. Repeat for multiple transports. Defaults to ipc and websocket.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    transports = args.transports or ["ipc", "websocket"]
    for result in smoke_native_transport_artifacts(root=args.root, transports=transports):
        print(
            "smoked native transport "
            f"{result.transport}: server={result.server_id} "
            f"session={result.session_id} operation={result.operation_id}"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
