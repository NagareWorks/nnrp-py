"""Minimal aioquic-based transport helpers for the current NNRP/1 wire format."""

from __future__ import annotations

import asyncio
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from aioquic.asyncio import connect, serve
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import (
    ConnectionTerminated,
    DatagramFrameReceived,
    HandshakeCompleted,
    QuicEvent,
    StopSendingReceived,
    StreamDataReceived,
    StreamReset,
)

from nnrp.core.enums import WireFormat
from nnrp.core.header import HEADER_LENGTH, NnrpHeader
from nnrp.core.packet import NnrpPacket

NNRP_CURRENT_ALPN = "nnrp/1"
NNRP_ALPN = NNRP_CURRENT_ALPN
_RESULT_STREAM_TYPES = frozenset({"RESULT_PUSH", "RESULT_DROP"})
_DEFAULT_IDLE_TIMEOUT_SECONDS = 120.0


def alpn_for_wire_format(wire_format: WireFormat) -> str:
    if wire_format is WireFormat.CURRENT:
        return NNRP_CURRENT_ALPN
    raise ValueError(f"unsupported wire format for ALPN: {wire_format}")


class NnrpQuicError(RuntimeError):
    """Base QUIC transport error for the current NNRP/1 wire format."""


class NnrpQuicConnectionClosedError(NnrpQuicError):
    """Raised when the peer terminates the QUIC connection or stream."""


class NnrpQuicProtocolError(NnrpQuicError):
    """Raised when a QUIC stream carries malformed NNRP packet bytes."""


def create_quic_client_configuration(
    *,
    wire_format: WireFormat = WireFormat.CURRENT,
    alpn_protocols: list[str] | None = None,
    verify_mode: ssl.VerifyMode = ssl.CERT_REQUIRED,
    insecure_skip_verify: bool = False,
    max_datagram_frame_size: int = 65536,
    idle_timeout: float = _DEFAULT_IDLE_TIMEOUT_SECONDS,
    cafile: str | Path | None = None,
    capath: str | Path | None = None,
    cadata: bytes | None = None,
) -> QuicConfiguration:
    resolved_verify_mode = ssl.CERT_NONE if insecure_skip_verify else verify_mode
    configuration = QuicConfiguration(
        alpn_protocols=alpn_protocols or [alpn_for_wire_format(wire_format)],
        is_client=True,
        max_datagram_frame_size=max_datagram_frame_size,
        idle_timeout=idle_timeout,
    )
    configuration.verify_mode = resolved_verify_mode
    if cafile is not None or capath is not None or cadata is not None:
        configuration.load_verify_locations(
            cafile=None if cafile is None else str(cafile),
            capath=None if capath is None else str(capath),
            cadata=cadata,
        )
    return configuration


def create_quic_server_configuration(
    certificate: str | Path,
    private_key: str | Path,
    *,
    wire_format: WireFormat = WireFormat.CURRENT,
    alpn_protocols: list[str] | None = None,
    max_datagram_frame_size: int = 65536,
    idle_timeout: float = _DEFAULT_IDLE_TIMEOUT_SECONDS,
) -> QuicConfiguration:
    configuration = QuicConfiguration(
        alpn_protocols=alpn_protocols or [alpn_for_wire_format(wire_format)],
        is_client=False,
        max_datagram_frame_size=max_datagram_frame_size,
        idle_timeout=idle_timeout,
    )
    configuration.load_cert_chain(str(certificate), str(private_key))
    return configuration


class NnrpQuicConnection(QuicConnectionProtocol):
    """Minimal QUIC transport wrapper for NNRP packets.

    The first bidirectional stream opened by the client is treated as the
    control stream. The wrapper parses packet boundaries from the NNRP header,
    so multiple control packets can share the same QUIC stream.
    """

    def __init__(
        self,
        *args,
        connection_ready_queue: asyncio.Queue[NnrpQuicConnection] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._connection_ready_queue = connection_ready_queue
        self._connected = asyncio.Event()
        self._terminated = asyncio.Event()
        self._incoming_control_packets: asyncio.Queue[NnrpPacket] = asyncio.Queue()
        self._incoming_submit_packets: asyncio.Queue[NnrpPacket] = asyncio.Queue()
        self._incoming_result_packets: asyncio.Queue[NnrpPacket] = asyncio.Queue()
        self._incoming_datagrams: asyncio.Queue[bytes] = asyncio.Queue()
        self._stream_buffers: dict[int, bytearray] = {}
        self._control_stream_id: int | None = None
        self._last_submit_stream_id: int | None = None
        self._last_result_stream_id: int | None = None
        self._terminal_error: NnrpQuicError | None = None
        self._queued_on_ready = False

    @property
    def control_stream_id(self) -> int | None:
        return self._control_stream_id

    @property
    def last_submit_stream_id(self) -> int | None:
        return self._last_submit_stream_id

    @property
    def last_result_stream_id(self) -> int | None:
        return self._last_result_stream_id

    async def wait_connected(self, timeout: float | None = None) -> None:
        if self._connected.is_set():
            self._raise_if_terminal_error()
            return

        connected = await _wait_for_first(self._connected.wait(), self._terminated.wait(), timeout)
        if not connected:
            self._raise_if_terminal_error()

    async def ensure_control_stream(self) -> int:
        if self._control_stream_id is None:
            self._control_stream_id = self._quic.get_next_available_stream_id(is_unidirectional=False)
        return self._control_stream_id

    async def send_control_packet(self, packet: NnrpPacket) -> None:
        stream_id = await self.ensure_control_stream()
        self._quic.send_stream_data(stream_id, packet.pack(), end_stream=False)
        self.transmit()

    async def receive_control_packet(self, timeout: float | None = None) -> NnrpPacket:
        return await self._receive_from_queue(self._incoming_control_packets, timeout)

    async def send_submit_packet(self, packet: NnrpPacket) -> int:
        if packet.header.msg_type.name != "FRAME_SUBMIT":
            raise ValueError(f"expected FRAME_SUBMIT packet, got {packet.header.msg_type.name}")
        return self._send_unidirectional_packet(packet)

    async def receive_submit_packet(self, timeout: float | None = None) -> NnrpPacket:
        return await self._receive_from_queue(self._incoming_submit_packets, timeout)

    async def send_result_packet(self, packet: NnrpPacket) -> int:
        if packet.header.msg_type.name not in _RESULT_STREAM_TYPES:
            raise ValueError(f"expected RESULT_PUSH or RESULT_DROP packet, got {packet.header.msg_type.name}")
        return self._send_unidirectional_packet(packet)

    async def receive_result_packet(self, timeout: float | None = None) -> NnrpPacket:
        return await self._receive_from_queue(self._incoming_result_packets, timeout)

    def send_datagram(self, payload: bytes) -> None:
        self._quic.send_datagram_frame(payload)
        self.transmit()

    async def receive_datagram(self, timeout: float | None = None) -> bytes:
        return await self._receive_from_queue(self._incoming_datagrams, timeout)

    def quic_event_received(self, event: QuicEvent) -> None:
        if isinstance(event, HandshakeCompleted):
            self._connected.set()
            if self._connection_ready_queue is not None and not self._queued_on_ready:
                self._connection_ready_queue.put_nowait(self)
                self._queued_on_ready = True
            return

        if isinstance(event, ConnectionTerminated):
            self._fail_connection(
                NnrpQuicConnectionClosedError(
                    "connection terminated "
                    f"(error_code={event.error_code}, frame_type={event.frame_type}, "
                    f"reason={event.reason_phrase or 'peer closed'})"
                )
            )
            return

        if isinstance(event, StreamReset):
            self._stream_buffers.pop(event.stream_id, None)
            self._fail_connection(
                NnrpQuicConnectionClosedError(f"stream {event.stream_id} reset by peer (error_code={event.error_code})")
            )
            return

        if isinstance(event, StopSendingReceived):
            self._stream_buffers.pop(event.stream_id, None)
            return

        if isinstance(event, StreamDataReceived):
            self._handle_stream_data(event.stream_id, event.data, end_of_stream=event.end_stream)
            return

        if isinstance(event, DatagramFrameReceived):
            self._incoming_datagrams.put_nowait(bytes(event.data))

    def _handle_stream_data(self, stream_id: int, data: bytes, *, end_of_stream: bool) -> None:
        if self._control_stream_id is None and not _is_unidirectional_stream(stream_id):
            self._control_stream_id = stream_id

        buffer = self._stream_buffers.setdefault(stream_id, bytearray())
        buffer.extend(data)

        try:
            while True:
                packet = _try_unpack_packet(buffer)
                if packet is None:
                    break
                self._route_packet(stream_id, packet)
        except Exception as exc:
            self._stream_buffers.pop(stream_id, None)
            self._fail_connection(NnrpQuicProtocolError(f"failed to decode packet on stream {stream_id}: {exc}"))
            return

        if not end_of_stream:
            return

        self._stream_buffers.pop(stream_id, None)
        if buffer:
            self._fail_connection(
                NnrpQuicProtocolError(
                    f"stream {stream_id} ended with {len(buffer)} trailing bytes of an incomplete packet"
                )
            )

    def _route_packet(self, stream_id: int, packet: NnrpPacket) -> None:
        if stream_id == self._control_stream_id:
            self._incoming_control_packets.put_nowait(packet)
            return

        if packet.header.msg_type.name == "FRAME_SUBMIT":
            self._last_submit_stream_id = stream_id
            self._incoming_submit_packets.put_nowait(packet)
            return

        if packet.header.msg_type.name in _RESULT_STREAM_TYPES:
            self._last_result_stream_id = stream_id
            self._incoming_result_packets.put_nowait(packet)
            return

        self._incoming_control_packets.put_nowait(packet)

    def _send_unidirectional_packet(self, packet: NnrpPacket) -> int:
        stream_id = self._quic.get_next_available_stream_id(is_unidirectional=True)
        self._quic.send_stream_data(stream_id, packet.pack(), end_stream=True)
        self.transmit()
        return stream_id

    async def _receive_from_queue(self, queue: asyncio.Queue, timeout: float | None):
        self._raise_if_terminal_error()

        received, item = await _wait_for_queue_or_event(queue, self._terminated, timeout)
        if not received:
            self._raise_if_terminal_error()
        return item

    def _fail_connection(self, error: NnrpQuicError) -> None:
        if self._terminal_error is not None:
            return

        self._terminal_error = error
        self._terminated.set()

    def _raise_if_terminal_error(self) -> None:
        if self._terminal_error is not None:
            raise self._terminal_error


@dataclass(slots=True)
class NnrpQuicListener:
    """Handle for an aioquic listener and its accepted connections."""

    host: str
    port: int
    _server: object
    _connections: asyncio.Queue[NnrpQuicConnection] = field(repr=False)
    _accepted_connections: list[NnrpQuicConnection] = field(default_factory=list, repr=False)

    async def accept(self, timeout: float | None = None) -> NnrpQuicConnection:
        connection = await _wait_for(self._connections.get(), timeout)
        self._accepted_connections.append(connection)
        return connection

    async def _drain_accepted_connections(self, timeout: float = 1.0) -> None:
        async def drain(connection: NnrpQuicConnection) -> None:
            try:
                await asyncio.wait_for(connection.ping(), timeout=timeout)
            except (ConnectionError, TimeoutError):
                pass

        await asyncio.gather(*(drain(connection) for connection in self._accepted_connections))

    def close(self) -> None:
        close = getattr(self._server, "close", None)
        if callable(close):
            close()


@asynccontextmanager
async def serve_quic(
    host: str,
    port: int,
    *,
    configuration: QuicConfiguration,
) -> AsyncIterator[NnrpQuicListener]:
    ready_connections: asyncio.Queue[NnrpQuicConnection] = asyncio.Queue()

    def _create_protocol(*args, **kwargs) -> NnrpQuicConnection:
        return NnrpQuicConnection(*args, connection_ready_queue=ready_connections, **kwargs)

    server = await serve(
        host=host,
        port=port,
        configuration=configuration,
        create_protocol=_create_protocol,
    )
    listener = NnrpQuicListener(host=host, port=port, _server=server, _connections=ready_connections)
    try:
        yield listener
    finally:
        try:
            await listener._drain_accepted_connections()
        finally:
            listener.close()


@asynccontextmanager
async def connect_quic(
    host: str,
    port: int,
    *,
    configuration: QuicConfiguration | None = None,
) -> AsyncIterator[NnrpQuicConnection]:
    client_configuration = configuration or create_quic_client_configuration()
    async with connect(
        host=host,
        port=port,
        configuration=client_configuration,
        create_protocol=NnrpQuicConnection,
    ) as connection:
        protocol = cast(NnrpQuicConnection, connection)
        await protocol.wait_connected(timeout=5.0)
        yield protocol


async def _wait_for(awaitable, timeout: float | None):
    if timeout is None:
        return await awaitable
    return await asyncio.wait_for(awaitable, timeout=timeout)


async def _wait_for_first(primary_awaitable, secondary_awaitable, timeout: float | None) -> bool:
    primary_task = asyncio.create_task(primary_awaitable)
    secondary_task = asyncio.create_task(secondary_awaitable)
    done: set[asyncio.Task] = set()

    try:
        done, pending = await asyncio.wait(
            {primary_task, secondary_task},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        for task in (primary_task, secondary_task):
            if task not in done and not task.done():
                task.cancel()

    if not done:
        raise TimeoutError()

    return primary_task in done


async def _wait_for_queue_or_event(queue: asyncio.Queue, event: asyncio.Event, timeout: float | None):
    queue_task = asyncio.create_task(queue.get())
    event_task = asyncio.create_task(event.wait())
    done: set[asyncio.Task] = set()

    try:
        done, pending = await asyncio.wait(
            {queue_task, event_task},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        for task in (queue_task, event_task):
            if task not in done and not task.done():
                task.cancel()

    if not done:
        raise TimeoutError()

    if queue_task in done:
        return True, queue_task.result()

    return False, None


def _try_unpack_packet(buffer: bytearray) -> NnrpPacket | None:
    if len(buffer) < HEADER_LENGTH:
        return None

    header = NnrpHeader.unpack(buffer[:HEADER_LENGTH])
    packet_length = header.header_len + header.meta_len + header.body_len
    if len(buffer) < packet_length:
        return None

    payload = bytes(buffer[:packet_length])
    del buffer[:packet_length]
    return NnrpPacket.unpack(payload)


def _is_unidirectional_stream(stream_id: int) -> bool:
    return bool(stream_id & 0x02)
