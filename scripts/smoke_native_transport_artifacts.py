#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from nnrp.core import build_ping_packet, build_pong_packet
from nnrp.native import (
    NATIVE_TRANSPORT_ID_BY_NAME,
    NativeArtifactError,
    NativeTransportBinding,
    current_native_platform,
    load_native_transport_binding,
)

TRANSPORT_NAMES = frozenset(NATIVE_TRANSPORT_ID_BY_NAME)


@dataclass(frozen=True)
class NativeTransportSmokeResult:
    transport: str
    endpoint: str
    packets_exchanged: int


def smoke_native_transport_artifacts(
    *,
    root: Path | str | None = None,
    transports: Iterable[str] = ("ipc", "websocket"),
) -> tuple[NativeTransportSmokeResult, ...]:
    return asyncio.run(
        _smoke_native_transport_artifacts(
            root=root,
            transports=transports,
        )
    )


async def _smoke_native_transport_artifacts(
    *,
    root: Path | str | None,
    transports: Iterable[str],
) -> tuple[NativeTransportSmokeResult, ...]:
    results: list[NativeTransportSmokeResult] = []
    for index, transport in enumerate(transports, start=1):
        normalized_transport = _normalize_transport(transport)
        binding = load_native_transport_binding(normalized_transport, root=root)
        endpoint = _smoke_endpoint(normalized_transport, index)
        results.append(await _smoke_binding(binding, endpoint, index))
    return tuple(results)


async def _smoke_binding(
    binding: NativeTransportBinding,
    endpoint: str,
    index: int,
) -> NativeTransportSmokeResult:
    listener = await binding.listen(endpoint, timeout_ms=10_000)
    client = None
    server = None
    accept_task = asyncio.create_task(listener.accept(timeout_ms=10_000))
    try:
        client = await binding.connect(listener.endpoint, timeout_ms=10_000)
        server = await accept_task
        ping = build_ping_packet(session_id=index, trace_id=index).pack()
        pong = build_pong_packet(session_id=index, trace_id=index).pack()
        await client.send(ping)
        if await server.receive(max_packets=1, timeout_ms=10_000) != (ping,):
            raise NativeArtifactError(f"{binding.kind} server received a different complete-packet batch")
        await server.send(pong)
        if await client.receive(max_packets=1, timeout_ms=10_000) != (pong,):
            raise NativeArtifactError(f"{binding.kind} client received a different complete-packet batch")
        return NativeTransportSmokeResult(binding.kind, listener.endpoint.uri, 2)
    finally:
        if server is None:
            if not accept_task.done():
                accept_task.cancel()
            try:
                server = await accept_task
            except (asyncio.CancelledError, Exception):
                pass
        if client is not None:
            await client.close()
        if server is not None:
            await server.close()
        await listener.close()


def _smoke_endpoint(transport: str, index: int) -> str:
    if transport == "websocket":
        return "ws://127.0.0.1:0/nnrp"
    if transport != "ipc":
        raise NativeArtifactError(f"release smoke does not define a loopback endpoint for {transport}")
    platform = current_native_platform()
    unique = f"nnrp-py-smoke-{os.getpid()}-{index}"
    if platform.os_name == "windows":
        return f"npipe://{unique}"
    return f"unix://{Path(tempfile.gettempdir(), unique + '.sock').as_posix()}"


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
            f"smoked native transport {result.transport}: endpoint={result.endpoint} packets={result.packets_exchanged}"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
