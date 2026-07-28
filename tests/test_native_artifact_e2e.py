from __future__ import annotations

import asyncio
import ipaddress
import os
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from nnrp.core import build_ping_packet, build_pong_packet
from nnrp.native import (
    NativeArtifactError,
    NativeTransportClientSecurity,
    NativeTransportServerSecurity,
    load_native_transport_binding,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("NNRP_NATIVE_E2E") != "1",
    reason="requires prepared preview4 native transport artifacts",
)


def _new_tcp_tls_material() -> tuple[bytes, bytes]:
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
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
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
