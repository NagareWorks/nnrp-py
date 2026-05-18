"""Minimal asyncio TCP transport skeleton for NNRP current stages."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field

from nnrp.core.header import HEADER_LENGTH, NnrpHeader
from nnrp.core.packet import NnrpPacket

_DEFAULT_IDLE_TIMEOUT_SECONDS = 120.0
_READ_CHUNK_SIZE = 65536


@dataclass(frozen=True, slots=True)
class NnrpTcpClientConfiguration:
    connect_timeout: float = 5.0
    idle_timeout: float = _DEFAULT_IDLE_TIMEOUT_SECONDS
    no_delay: bool = True


@dataclass(frozen=True, slots=True)
class NnrpTcpServerConfiguration:
    idle_timeout: float = _DEFAULT_IDLE_TIMEOUT_SECONDS
    no_delay: bool = True


class NnrpTcpError(RuntimeError):
    """Base TCP transport error for NNRP current stages."""


class NnrpTcpConnectionClosedError(NnrpTcpError):
    """Raised when the peer closes the TCP connection."""


class NnrpTcpProtocolError(NnrpTcpError):
    """Raised when the TCP control stream carries malformed NNRP bytes."""


class NnrpTcpUnsupportedOperationError(NnrpTcpError):
    """Raised when a QUIC-only transport primitive is used on TCP."""


def create_tcp_client_configuration(
    *,
    connect_timeout: float = 5.0,
    idle_timeout: float = _DEFAULT_IDLE_TIMEOUT_SECONDS,
    no_delay: bool = True,
) -> NnrpTcpClientConfiguration:
    return NnrpTcpClientConfiguration(
        connect_timeout=connect_timeout,
        idle_timeout=idle_timeout,
        no_delay=no_delay,
    )


def create_tcp_server_configuration(
    *,
    idle_timeout: float = _DEFAULT_IDLE_TIMEOUT_SECONDS,
    no_delay: bool = True,
) -> NnrpTcpServerConfiguration:
    return NnrpTcpServerConfiguration(
        idle_timeout=idle_timeout,
        no_delay=no_delay,
    )


class NnrpTcpConnection:
    """Ordered TCP wrapper for current packets.

    TCP carries control, submit, and result packets over one ordered byte
    stream. The wrapper keeps the single-socket model explicit while exposing
    synthetic stream ids for submit/result traffic so higher-level helpers can
    reuse the same transport-facing API as QUIC.
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        idle_timeout: float = _DEFAULT_IDLE_TIMEOUT_SECONDS,
        no_delay: bool = True,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._idle_timeout = idle_timeout
        self._incoming_control_packets: asyncio.Queue[NnrpPacket] = asyncio.Queue()
        self._incoming_submit_packets: asyncio.Queue[NnrpPacket] = asyncio.Queue()
        self._incoming_result_packets: asyncio.Queue[NnrpPacket] = asyncio.Queue()
        self._connected = asyncio.Event()
        self._terminated = asyncio.Event()
        self._terminal_error: NnrpTcpError | None = None
        self._last_submit_stream_id: int | None = None
        self._last_result_stream_id: int | None = None
        self._next_outgoing_submit_stream_id = 2
        self._next_incoming_submit_stream_id = 2
        self._next_outgoing_result_stream_id = 2
        self._next_incoming_result_stream_id = 2

        _configure_tcp_socket(writer, no_delay=no_delay)

        self._connected.set()
        self._reader_task = asyncio.create_task(self._read_packets())

    @property
    def control_stream_id(self) -> int:
        return 0

    @property
    def last_submit_stream_id(self) -> int | None:
        return self._last_submit_stream_id

    @property
    def last_result_stream_id(self) -> int | None:
        return self._last_result_stream_id

    async def wait_connected(self, timeout: float | None = None) -> None:
        await _wait_for(self._connected.wait(), timeout)
        self._raise_if_terminal_error()

    async def send_control_packet(self, packet: NnrpPacket) -> None:
        await self._send_packet(packet)

    async def receive_control_packet(self, timeout: float | None = None) -> NnrpPacket:
        return await self._receive_from_queue(self._incoming_control_packets, timeout)

    async def send_submit_packet(self, packet: NnrpPacket) -> int:
        if packet.header.msg_type.name != "FRAME_SUBMIT":
            raise ValueError(f"expected FRAME_SUBMIT packet, got {packet.header.msg_type.name}")
        stream_id = self._allocate_outgoing_submit_stream_id()
        await self._send_packet(packet)
        return stream_id

    async def receive_submit_packet(self, timeout: float | None = None) -> NnrpPacket:
        return await self._receive_from_queue(self._incoming_submit_packets, timeout)

    async def send_result_packet(self, packet: NnrpPacket) -> int:
        if packet.header.msg_type.name not in {"RESULT_PUSH", "RESULT_DROP"}:
            raise ValueError(f"expected RESULT_PUSH or RESULT_DROP packet, got {packet.header.msg_type.name}")
        stream_id = self._allocate_outgoing_result_stream_id()
        await self._send_packet(packet)
        return stream_id

    async def receive_result_packet(self, timeout: float | None = None) -> NnrpPacket:
        return await self._receive_from_queue(self._incoming_result_packets, timeout)

    async def _send_packet(self, packet: NnrpPacket) -> None:
        self._raise_if_terminal_error()
        self._writer.write(packet.pack())
        try:
            await self._writer.drain()
        except (ConnectionError, OSError) as exc:
            self._fail_connection(NnrpTcpConnectionClosedError(f"TCP send failed: {exc}"))
            self._raise_if_terminal_error()

    def send_datagram(self, payload: bytes) -> None:
        raise NnrpTcpUnsupportedOperationError("TCP adapter skeleton does not implement datagram transport")

    async def receive_datagram(self, timeout: float | None = None) -> bytes:
        raise NnrpTcpUnsupportedOperationError("TCP adapter skeleton does not implement datagram transport")

    def close(self) -> None:
        self._writer.close()

    async def wait_closed(self) -> None:
        await self._terminated.wait()
        with suppress(ConnectionError, RuntimeError):
            await self._writer.wait_closed()
        with suppress(asyncio.CancelledError):
            await self._reader_task

    async def _read_packets(self) -> None:
        buffer = bytearray()

        try:
            while True:
                chunk = await _read_with_optional_timeout(
                    self._reader,
                    _READ_CHUNK_SIZE,
                    timeout=self._idle_timeout,
                )
                if not chunk:
                    break
                buffer.extend(chunk)

                while True:
                    packet = _try_unpack_packet(buffer)
                    if packet is None:
                        break
                    self._route_packet(packet)
        except TimeoutError:
            self._fail_connection(
                NnrpTcpConnectionClosedError(f"TCP connection idle timeout expired after {self._idle_timeout:.1f}s")
            )
            return
        except Exception as exc:
            self._fail_connection(NnrpTcpProtocolError(f"failed to decode packet on TCP control stream: {exc}"))
            return

        if buffer:
            self._fail_connection(
                NnrpTcpProtocolError(
                    f"TCP control stream ended with {len(buffer)} trailing bytes of an incomplete packet"
                )
            )
            return

        self._fail_connection(NnrpTcpConnectionClosedError("TCP connection closed by peer"))

    async def _receive_from_queue(self, queue: asyncio.Queue[NnrpPacket], timeout: float | None) -> NnrpPacket:
        self._raise_if_terminal_error()

        queue_task = asyncio.create_task(queue.get())
        terminated_task = asyncio.create_task(self._terminated.wait())
        done: set[asyncio.Task[object]] = set()

        try:
            done, _ = await asyncio.wait(
                {queue_task, terminated_task},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in (queue_task, terminated_task):
                if task not in done and not task.done():
                    task.cancel()

        if not done:
            raise TimeoutError()

        if queue_task in done:
            return queue_task.result()

        self._raise_if_terminal_error()
        raise NnrpTcpConnectionClosedError("TCP connection terminated before a packet was received")

    def _route_packet(self, packet: NnrpPacket) -> None:
        if packet.header.msg_type.name == "FRAME_SUBMIT":
            self._last_submit_stream_id = self._allocate_incoming_submit_stream_id()
            self._incoming_submit_packets.put_nowait(packet)
            return

        if packet.header.msg_type.name in {"RESULT_PUSH", "RESULT_DROP"}:
            self._last_result_stream_id = self._allocate_incoming_result_stream_id()
            self._incoming_result_packets.put_nowait(packet)
            return

        self._incoming_control_packets.put_nowait(packet)

    def _allocate_outgoing_submit_stream_id(self) -> int:
        stream_id = self._next_outgoing_submit_stream_id
        self._next_outgoing_submit_stream_id += 4
        return stream_id

    def _allocate_incoming_submit_stream_id(self) -> int:
        stream_id = self._next_incoming_submit_stream_id
        self._next_incoming_submit_stream_id += 4
        return stream_id

    def _allocate_outgoing_result_stream_id(self) -> int:
        stream_id = self._next_outgoing_result_stream_id
        self._next_outgoing_result_stream_id += 4
        return stream_id

    def _allocate_incoming_result_stream_id(self) -> int:
        stream_id = self._next_incoming_result_stream_id
        self._next_incoming_result_stream_id += 4
        return stream_id

    def _fail_connection(self, error: NnrpTcpError) -> None:
        if self._terminal_error is not None:
            return

        self._terminal_error = error
        self._terminated.set()

    def _raise_if_terminal_error(self) -> None:
        if self._terminal_error is not None:
            raise self._terminal_error


@dataclass(slots=True)
class NnrpTcpListener:
    host: str
    port: int
    _server: asyncio.AbstractServer = field(repr=False)
    _connections: asyncio.Queue[NnrpTcpConnection] = field(repr=False)
    _live_connections: set[NnrpTcpConnection] = field(repr=False)

    async def accept(self, timeout: float | None = None) -> NnrpTcpConnection:
        return await _wait_for(self._connections.get(), timeout)

    def close(self) -> None:
        self._server.close()
        for connection in tuple(self._live_connections):
            connection.close()

    async def wait_closed(self) -> None:
        await self._server.wait_closed()
        if self._live_connections:
            await asyncio.gather(
                *(connection.wait_closed() for connection in tuple(self._live_connections)),
                return_exceptions=True,
            )


@asynccontextmanager
async def serve_tcp(
    host: str,
    port: int,
    *,
    configuration: NnrpTcpServerConfiguration | None = None,
) -> AsyncIterator[NnrpTcpListener]:
    server_configuration = configuration or create_tcp_server_configuration()
    ready_connections: asyncio.Queue[NnrpTcpConnection] = asyncio.Queue()
    live_connections: set[NnrpTcpConnection] = set()

    async def _handle_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connection = NnrpTcpConnection(
            reader,
            writer,
            idle_timeout=server_configuration.idle_timeout,
            no_delay=server_configuration.no_delay,
        )
        live_connections.add(connection)
        ready_connections.put_nowait(connection)
        try:
            await connection.wait_closed()
        finally:
            live_connections.discard(connection)

    server = await asyncio.start_server(_handle_connection, host=host, port=port)
    listener = NnrpTcpListener(
        host=host,
        port=port,
        _server=server,
        _connections=ready_connections,
        _live_connections=live_connections,
    )
    try:
        yield listener
    finally:
        listener.close()
        await listener.wait_closed()


@asynccontextmanager
async def connect_tcp(
    host: str,
    port: int,
    *,
    configuration: NnrpTcpClientConfiguration | None = None,
) -> AsyncIterator[NnrpTcpConnection]:
    client_configuration = configuration or create_tcp_client_configuration()
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host=host, port=port),
        timeout=client_configuration.connect_timeout,
    )
    connection = NnrpTcpConnection(
        reader,
        writer,
        idle_timeout=client_configuration.idle_timeout,
        no_delay=client_configuration.no_delay,
    )
    await connection.wait_connected(timeout=client_configuration.connect_timeout)
    try:
        yield connection
    finally:
        connection.close()
        await connection.wait_closed()


async def _wait_for(awaitable, timeout: float | None):
    if timeout is None:
        return await awaitable
    return await asyncio.wait_for(awaitable, timeout=timeout)


async def _read_with_optional_timeout(
    reader: asyncio.StreamReader,
    byte_count: int,
    *,
    timeout: float,
) -> bytes:
    if timeout <= 0:
        return await reader.read(byte_count)
    return await asyncio.wait_for(reader.read(byte_count), timeout=timeout)


def _configure_tcp_socket(writer: asyncio.StreamWriter, *, no_delay: bool) -> None:
    sock = writer.get_extra_info("socket")
    if sock is None:
        return
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1 if no_delay else 0)


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
