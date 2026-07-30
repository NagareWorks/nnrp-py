from __future__ import annotations

import asyncio
import ipaddress
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from nnrp.client import SubmitIdentity, SubmitPolicy, SubmitRequest, TokenChunk, TokenSubmitInput
from nnrp.client.native import (
    NativeClientProviderRoute,
    NativeClientSessionOpenOptions,
    connect_native_client_connection,
)
from nnrp.core import (
    BudgetPolicy,
    MessageType,
    PayloadKind,
    ResultClass,
    ResultFlags,
    ResultPushMetadata,
    build_ping_packet,
    build_pong_packet,
)
from nnrp.native import (
    NativeArtifactError,
    NativeInvalidHandleError,
    NativeTransportClientSecurity,
    NativeTransportServerSecurity,
    NativeWouldBlockError,
    _shutdown_registered_native_runtimes,
    load_native_transport_binding,
)
from nnrp.runtime import NativeRuntimeEvent, PartialResultMetadata, ProgressMetadata
from nnrp.schema import StandardProfile
from nnrp.server import (
    NativeServerAcceptOptions,
    NativeServerOptions,
    NativeServerProviderRoute,
    listen_native_server,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("NNRP_NATIVE_E2E") != "1",
    reason="requires prepared preview4 native transport artifacts",
)


def _new_tcp_tls_material() -> tuple[bytes, bytes]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(private_key, hashes.SHA256())
    )
    return (
        certificate.public_bytes(serialization.Encoding.DER),
        private_key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )


def _native_token_result_metadata() -> ResultPushMetadata:
    return ResultPushMetadata(
        status_code=200,
        result_flags=ResultFlags.NONE,
        section_count=0,
        tile_count=0,
        active_profile_id=int(StandardProfile.TOKEN),
        reserved0=0,
        inference_ms=1,
        queue_ms=0,
        server_total_ms=1,
        reserved1=0,
        tile_base_id=0,
        tile_index_bytes=0,
        result_class=ResultClass.COMPLETE,
        applied_budget_policy=BudgetPolicy.NONE,
        payload_kind_bitmap=PayloadKind.TOKEN_CHUNK,
        payload_frame_count=1,
    )


def _drain_native_setup_events(session: Any) -> None:
    try:
        session.poll_events()
    except NativeWouldBlockError:
        pass


@contextmanager
def _open_native_role_loopback() -> Any:
    suffix = uuid4().hex
    provider_endpoint = (
        f"npipe://nnrp-py-abi-{suffix}"
        if os.name == "nt"
        else f"unix://{(Path(tempfile.gettempdir()) / f'nnrp-py-abi-{suffix}.sock').as_posix()}"
    )
    socket_path = Path(provider_endpoint.removeprefix("unix://")) if provider_endpoint.startswith("unix://") else None
    if socket_path is not None:
        socket_path.unlink(missing_ok=True)
    try:
        with listen_native_server(
            "nnrp://abi.local",
            provider_routes={"ipc": NativeServerProviderRoute(provider_endpoint=provider_endpoint)},
            transport_policy="force_ipc",
            require_native=True,
            options=NativeServerOptions(server_id=2),
        ) as server:
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="nnrp-abi-accept") as executor:
                accepted = executor.submit(
                    server.accept,
                    NativeServerAcceptOptions(session_handle_id=4, session_generation=1, timeout_ms=5_000),
                )
                with connect_native_client_connection(
                    "nnrp://abi.local",
                    provider_routes={"ipc": NativeClientProviderRoute(provider_endpoint=provider_endpoint)},
                    transport_policy="force_ipc",
                    require_native=True,
                ) as client:
                    client_session = client.open_session(
                        NativeClientSessionOpenOptions(requested_session_id=3, session_generation=1)
                    )
                    server_session = accepted.result(timeout=10)
                    try:
                        yield client, client_session, server_session
                    finally:
                        client_close = executor.submit(client_session.close)
                        try:
                            close_events = server_session.poll_events(max_events=8, timeout_ms=5_000)
                            assert any(
                                isinstance(event, NativeRuntimeEvent)
                                and event.header.message_type is MessageType.SESSION_CLOSE
                                for event in close_events
                            )
                        finally:
                            server_session.close()
                            client_close.result(timeout=10)
    finally:
        if socket_path is not None:
            socket_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_tcp_artifact_exchanges_packets_over_route_local_tls() -> None:
    certificate_der, private_key_der = _new_tcp_tls_material()
    tcp = load_native_transport_binding("tcp")
    listener = await tcp.listen(
        "tcp://127.0.0.1:0",
        security=NativeTransportServerSecurity(certificate_der, private_key_der),
        timeout_ms=10_000,
    )
    accept_task = asyncio.create_task(listener.accept(timeout_ms=10_000))
    client = await tcp.connect(
        listener.endpoint,
        security=NativeTransportClientSecurity("localhost", certificate_der),
        timeout_ms=10_000,
    )
    server = await accept_task
    try:
        ping = build_ping_packet(session_id=11, trace_id=21).pack()
        pong = build_pong_packet(session_id=11, trace_id=22).pack()
        await client.send(ping)
        assert await server.receive(max_packets=1, timeout_ms=10_000) == (ping,)
        await server.send(pong)
        assert await client.receive(max_packets=1, timeout_ms=10_000) == (pong,)
    finally:
        await client.close()
        await server.close()
        await listener.close()


@pytest.mark.asyncio
async def test_foreign_artifact_cannot_take_carrier_or_listener_ownership() -> None:
    tcp = load_native_transport_binding("tcp")
    quic = load_native_transport_binding("quic")
    listener = await tcp.listen("tcp://127.0.0.1:0", timeout_ms=10_000)
    accept_task = asyncio.create_task(listener.accept(timeout_ms=10_000))
    client = await tcp.connect(listener.endpoint, timeout_ms=10_000)
    server = await accept_task
    try:
        with pytest.raises(NativeArtifactError, match="owning transport artifact"):
            quic.adopt_client(client, connection_id=1, generation=1)

        ping = build_ping_packet(session_id=1, trace_id=1).pack()
        await client.send(ping)
        assert await server.receive(max_packets=1, timeout_ms=10_000) == (ping,)
    finally:
        await client.close()
        await client.close()
        await server.close()
        await server.close()
        await listener.close()
        await listener.close()

    second_listener = await tcp.listen("tcp://127.0.0.1:0", timeout_ms=10_000)
    try:
        with pytest.raises(NativeArtifactError, match="owning transport artifact"):
            quic.adopt_server(second_listener, server_id=1, generation=1)

        second_accept = asyncio.create_task(second_listener.accept(timeout_ms=10_000))
        second_client = await tcp.connect(second_listener.endpoint, timeout_ms=10_000)
        second_server = await second_accept
        try:
            pong = build_pong_packet(session_id=2, trace_id=2).pack()
            await second_server.send(pong)
            assert await second_client.receive(max_packets=1, timeout_ms=10_000) == (pong,)
        finally:
            await second_client.close()
            await second_server.close()
    finally:
        await second_listener.close()


def test_packaged_native_role_batch_decodes_multiple_events_with_ffi_stride() -> None:
    with _open_native_role_loopback() as (_client, client_session, server_session):
        _drain_native_setup_events(client_session)
        submitted = client_session.submit_operation(
            SubmitRequest.token(
                TokenSubmitInput(
                    identity=SubmitIdentity(operation_id=101, frame_id=201),
                    policy=SubmitPolicy(),
                    chunks=(TokenChunk(b"request"),),
                )
            )
        )
        received = server_session.receive_submit(timeout_ms=5_000)

        server_session.send_progress(
            ProgressMetadata(101, 1, 2, 2_500, 7, 8),
            b"progress",
        )
        server_session.send_partial_result(
            PartialResultMetadata(101, 2, 301, 1, 7, 0),
            b"partial",
        )
        received.send_result(_native_token_result_metadata(), b"result")

        events = client_session.poll_events_batch(max_events=2, timeout_ms=5_000)
        assert all(isinstance(event, NativeRuntimeEvent) for event in events)
        frames = [event for event in events if isinstance(event, NativeRuntimeEvent)]
        assert [event.header.message_type for event in frames] == [
            MessageType.PROGRESS,
            MessageType.PARTIAL_RESULT,
        ]
        assert [event.tail.body for event in frames] == [b"progress", b"partial"]

        result = client_session.poll_result(submitted, max_events=2, timeout_ms=5_000)
        assert result.body == b"result"


@pytest.mark.asyncio
async def test_registered_native_runtime_shutdown_invalidates_handles_and_restarts() -> None:
    tcp = load_native_transport_binding("tcp")
    listener = await tcp.listen("tcp://127.0.0.1:0", timeout_ms=10_000)

    _shutdown_registered_native_runtimes()
    with pytest.raises(NativeInvalidHandleError):
        await listener.close()

    restarted = await tcp.listen("tcp://127.0.0.1:0", timeout_ms=10_000)
    await restarted.close()
