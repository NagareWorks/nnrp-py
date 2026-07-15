from __future__ import annotations

import ctypes
from pathlib import Path

import pytest

from nnrp.native import (
    FFI_STATUS_OK,
    HANDLE_KIND_BUFFER,
    HANDLE_KIND_TRANSPORT_CONNECTION,
    HANDLE_KIND_TRANSPORT_LISTENER,
    HANDLE_KIND_TRANSPORT_SECURITY_CONFIG,
    NativeArtifactError,
    NativeHandleError,
    NativeInvalidStateError,
    NativeTransportBinding,
    NativeTransportClientSecurity,
    NativeTransportProvider,
    NativeTransportProviderCost,
    NativeTransportProviderLimitation,
    NativeTransportProviderLimits,
    NativeTransportProviderMetadata,
    NativeTransportServerSecurity,
    _NativeTransportEntrypoints,
    _NnrpBufferView,
    _NnrpFfiStatus,
    _NnrpHandle,
)


class FakeFunction:
    def __init__(self, handler):
        self.handler = handler
        self.calls: list[tuple[object, ...]] = []
        self.restype = None
        self.argtypes = None

    def __call__(self, *args):
        self.calls.append(args)
        return self.handler(*args)


class FakeTransportLibrary:
    def __init__(self) -> None:
        self.buffers: list[ctypes.Array] = []
        self.closed: list[tuple[int, int]] = []
        self.released: list[int] = []
        self.sent: list[tuple[bytes, ...]] = []
        self.received = (b"first-packet", b"second-packet")
        self.nnrp_transport_client_security_config_create = FakeFunction(self._client_security)
        self.nnrp_transport_server_security_config_create = FakeFunction(self._server_security)
        self.nnrp_transport_probe = FakeFunction(self._probe)
        self.nnrp_transport_connect = FakeFunction(self._connect)
        self.nnrp_transport_listen = FakeFunction(self._listen)
        self.nnrp_transport_accept = FakeFunction(self._accept)
        self.nnrp_transport_listener_endpoint = FakeFunction(self._listener_endpoint)
        self.nnrp_transport_write_batch = FakeFunction(self._write_batch)
        self.nnrp_transport_read_batch = FakeFunction(self._read_batch)
        self.nnrp_transport_close = FakeFunction(self._close)
        self.nnrp_buffer_release = FakeFunction(self._buffer_release)

    @staticmethod
    def _ok() -> _NnrpFfiStatus:
        return _NnrpFfiStatus(FFI_STATUS_OK, 0, 0, 0)

    def _client_security(self, _request, output) -> _NnrpFfiStatus:
        _write_handle(output, _NnrpHandle(HANDLE_KIND_TRANSPORT_SECURITY_CONFIG, 40, 1, 0))
        return self._ok()

    def _server_security(self, _request, output) -> _NnrpFfiStatus:
        _write_handle(output, _NnrpHandle(HANDLE_KIND_TRANSPORT_SECURITY_CONFIG, 41, 1, 0))
        return self._ok()

    def _probe(self, _request, output) -> _NnrpFfiStatus:
        target = output._obj
        target.sample_count = 3
        target.success_count = 3
        target.median_throughput_bytes_per_second = 8_000_000
        target.median_rtt_microseconds = 250
        return self._ok()

    def _connect(self, _request, output) -> _NnrpFfiStatus:
        _write_handle(output, _NnrpHandle(HANDLE_KIND_TRANSPORT_CONNECTION, 10, 1, 0))
        return self._ok()

    def _listen(self, _request, output) -> _NnrpFfiStatus:
        _write_handle(output, _NnrpHandle(HANDLE_KIND_TRANSPORT_LISTENER, 20, 1, 0))
        return self._ok()

    def _accept(self, _request, output) -> _NnrpFfiStatus:
        _write_handle(output, _NnrpHandle(HANDLE_KIND_TRANSPORT_CONNECTION, 11, 1, 0))
        return self._ok()

    def _listener_endpoint(self, _listener, owner, endpoint) -> _NnrpFfiStatus:
        payload = b"ws://127.0.0.1:43123/nnrp"
        buffer = ctypes.create_string_buffer(payload, len(payload))
        self.buffers.append(buffer)
        _write_handle(owner, _NnrpHandle(HANDLE_KIND_BUFFER, 60, 1, 0))
        endpoint._obj.ptr = ctypes.cast(buffer, ctypes.c_void_p)
        endpoint._obj.len = len(payload)
        return self._ok()

    def _write_batch(self, request) -> _NnrpFfiStatus:
        self.sent.append(
            tuple(
                ctypes.string_at(request.frames[index].ptr, request.frames[index].len)
                for index in range(request.frame_count)
            )
        )
        return self._ok()

    def _read_batch(self, _request, output) -> _NnrpFfiStatus:
        payload = b"".join(len(packet).to_bytes(4, "little") + packet for packet in self.received)
        buffer = ctypes.create_string_buffer(payload, len(payload))
        self.buffers.append(buffer)
        target = output._obj
        target.payload_owner = _NnrpHandle(HANDLE_KIND_BUFFER, 61, 1, 0)
        target.payload = _NnrpBufferView(ctypes.cast(buffer, ctypes.c_void_p), len(payload))
        target.frame_count = len(self.received)
        target.reserved0 = 0
        return self._ok()

    def _close(self, handle) -> _NnrpFfiStatus:
        self.closed.append((int(handle.kind), int(handle.id)))
        return self._ok()

    def _buffer_release(self, handle) -> _NnrpFfiStatus:
        self.released.append(int(handle.id))
        return self._ok()


def _write_handle(pointer, handle: _NnrpHandle) -> None:
    target = getattr(pointer, "_obj", None)
    if target is not None:
        target.kind = handle.kind
        target.id = handle.id
        target.generation = handle.generation
        target.flags = handle.flags
        return
    ctypes.cast(pointer, ctypes.POINTER(_NnrpHandle)).contents = handle


def _provider(name: str = "websocket") -> NativeTransportProvider:
    return NativeTransportProvider(
        name=name,
        artifact_path=Path("native") / name / "nnrp_ffi.dll",
        manifest_path=Path("native") / name / "manifest.json",
        transport_slots=(name,),
        enabled_features=(f"transport-{name}",),
        package=f"nnrp-ffi-transport-{name}",
        transport_scope=name,
        platform_tag="windows-x86_64",
        metadata=NativeTransportProviderMetadata(
            id=f"org.nnrp.transport.{name}",
            cost=NativeTransportProviderCost(model_id=1, units=1),
            preference_rank=1,
            limits=NativeTransportProviderLimits(max_frame_bytes=64 * 1024 * 1024),
            limitations=(NativeTransportProviderLimitation.NATIVE_HOST_ONLY,),
        ),
    )


@pytest.mark.asyncio
async def test_binding_exchanges_complete_packet_batches_and_releases_native_buffers() -> None:
    library = FakeTransportLibrary()
    binding = NativeTransportBinding(_NativeTransportEntrypoints(library), _provider())

    listener = await binding.listen("ws://127.0.0.1:0/nnrp")
    client = await binding.connect(listener.endpoint)
    server = await listener.accept(timeout_ms=5_000)
    await client.send((b"packet-a", b"packet-b"))

    assert listener.endpoint.uri == "ws://127.0.0.1:43123/nnrp"
    assert library.sent == [(b"packet-a", b"packet-b")]
    assert await server.receive(max_packets=2, timeout_ms=5_000) == library.received
    assert library.released == [60, 61]

    await client.close()
    await client.close()
    await server.close()
    await listener.close()
    assert library.closed.count((HANDLE_KIND_TRANSPORT_CONNECTION, 10)) == 1
    assert not client.connected
    assert not listener.listening

    with pytest.raises(NativeInvalidStateError, match="connection is closed"):
        await client.send(b"late")


@pytest.mark.asyncio
async def test_binding_reports_probe_metrics() -> None:
    library = FakeTransportLibrary()
    binding = NativeTransportBinding(_NativeTransportEntrypoints(library), _provider())

    metrics = await binding.probe(
        "ws://127.0.0.1:43123/nnrp",
        sample_count=3,
        security=NativeTransportClientSecurity("localhost", b"certificate"),
    )

    assert metrics.sample_count == 3
    assert metrics.success_count == 3
    assert metrics.median_throughput_bytes_per_sec == 8_000_000
    assert metrics.median_rtt_us == 250
    assert (HANDLE_KIND_TRANSPORT_SECURITY_CONFIG, 40) in library.closed


@pytest.mark.asyncio
async def test_binding_rejects_cross_provider_endpoint_and_malformed_batch() -> None:
    library = FakeTransportLibrary()
    binding = NativeTransportBinding(_NativeTransportEntrypoints(library), _provider())

    with pytest.raises(NativeArtifactError, match="cannot open 'ipc' endpoint"):
        await binding.connect("npipe://./pipe/nnrp")

    connection = await binding.connect("ws://127.0.0.1:43123/nnrp")
    library.received = (b"packet",)
    original = library._read_batch

    def malformed(request, output):
        status = original(request, output)
        output._obj.frame_count = 2
        return status

    library.nnrp_transport_read_batch.handler = malformed
    with pytest.raises(NativeHandleError, match="declared 2 packets"):
        await connection.receive()
    assert library.released == [61]


@pytest.mark.asyncio
async def test_binding_does_not_release_non_buffer_transport_owners() -> None:
    library = FakeTransportLibrary()
    binding = NativeTransportBinding(_NativeTransportEntrypoints(library), _provider())

    original_endpoint = library._listener_endpoint

    def endpoint_with_wrong_owner(listener, owner, endpoint):
        status = original_endpoint(listener, owner, endpoint)
        _write_handle(owner, _NnrpHandle(HANDLE_KIND_TRANSPORT_CONNECTION, 60, 1, 0))
        return status

    library.nnrp_transport_listener_endpoint.handler = endpoint_with_wrong_owner
    with pytest.raises(NativeHandleError, match="expected native handle kind"):
        await binding.listen("ws://127.0.0.1:0/nnrp")
    assert library.released == []
    assert (HANDLE_KIND_TRANSPORT_LISTENER, 20) in library.closed

    library.nnrp_transport_listener_endpoint.handler = original_endpoint
    connection = await binding.connect("ws://127.0.0.1:43123/nnrp")
    original_read = library._read_batch

    def read_with_wrong_owner(request, output):
        status = original_read(request, output)
        output._obj.payload_owner = _NnrpHandle(HANDLE_KIND_TRANSPORT_CONNECTION, 61, 1, 0)
        return status

    library.nnrp_transport_read_batch.handler = read_with_wrong_owner
    with pytest.raises(NativeHandleError, match="expected native handle kind"):
        await connection.receive()
    assert library.released == []


def test_transport_security_values_are_non_empty_and_typed() -> None:
    with pytest.raises(ValueError, match="server_name"):
        NativeTransportClientSecurity("", b"certificate")
    with pytest.raises(ValueError, match="trusted_certificate_der"):
        NativeTransportClientSecurity("localhost", b"")
    with pytest.raises(ValueError, match="certificate_der"):
        NativeTransportServerSecurity(b"", b"key")
    with pytest.raises(ValueError, match="private_key_pkcs8_der"):
        NativeTransportServerSecurity(b"certificate", b"")
