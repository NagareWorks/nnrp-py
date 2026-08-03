import asyncio
import inspect

import pytest

import nnrp.client as client_module
from nnrp import (
    RUNTIME_CONTROL_FEATURE_FLAGS,
    RUNTIME_OBJECT_FEATURE_FLAGS,
    CacheLeaseDescriptor,
    CacheLeaseOwnerScope,
    CacheObjectIdentity,
    NativeLifecycleEvent,
    NativeLifecycleEventCallback,
    NativePolledEvent,
    NativePolledEventCallback,
    NativeRuntimeClient,
    NativeRuntimeEvent,
    NativeRuntimeEventCallback,
    NativeRuntimeFeatureFlag,
    NativeRuntimeServer,
    NativeRuntimeServerOperation,
    NativeRuntimeServerSession,
    SchemaDescriptorHeader,
    SchemaRegistryCatalog,
    SessionRecoveryReport,
    StandardProfile,
    StreamSemantics,
    native_runtime_feature_flag_names,
    native_runtime_feature_flags_available,
    should_replay_frame_after_migration,
    token_delta_payload_descriptor,
    token_delta_schema_descriptor,
    validate_migration_recovery,
    validate_session_recovery_ack,
    validate_session_recovery_request,
)
from nnrp.client import (
    ClientControlBootstrapSession,
    ClientDialPolicy,
    ClientProfile,
    ClientSession,
    ClientTransportBootstrap,
    ClientTransportPlan,
    MigrationOutcome,
    MigrationTriggerMonitor,
    MigrationTriggerPolicy,
    MigrationTriggerSnapshot,
    NativeClientConnection,
    NativeClientOptions,
    NativeClientProviderRoute,
    NativeClientSessionOptions,
    NativeSessionRecoveryTicket,
    PathHealthSample,
    Result,
    ResultRouter,
    SubmitHeaderContext,
    SubmitIdentity,
    SubmitObjectReferences,
    SubmitPolicy,
    SubmitRequest,
    TensorSubmitInput,
    TokenChunk,
    TokenSubmitInput,
    TransportProbeResult,
    TransportProbeSelection,
    TransportProbeSummary,
    TypedPayload,
    TypedPayloadInputFrame,
    TypedPayloadSubmitInput,
    bootstrap_client_transport,
    build_client_hello_packet,
    connect_client_control,
    connect_client_control_with_probe,
    connect_native_client_connection,
    plan_client_transport,
    probe_client_transport,
    resolve_client_hello_transport_policy,
    select_client_native_backend,
)
from nnrp.client.transport import connect_client_session, connect_client_session_with_probe
from nnrp.core import (
    BODY_REGION_PRELUDE_LENGTH,
    CLIENT_HELLO_LOSS_TOLERANCE_EXTENSION,
    CLIENT_HELLO_PAYLOAD_CAPABILITIES_EXTENSION,
    CLIENT_HELLO_TRANSPORT_POLICY_EXTENSION,
    EXTENSION_FRAME_DESCRIPTOR_LENGTH,
    FRAME_SUBMIT_METADATA_LENGTH,
    INLINE_OBJECT_BLOCK_HEADER_LENGTH,
    OBJECT_REFERENCE_BLOCK_LENGTH,
    RESULT_PUSH_METADATA_LENGTH,
    SERVER_HELLO_ACK_LOSS_TOLERANCE_EXTENSION,
    SERVER_HELLO_ACK_PAYLOAD_CAPABILITIES_EXTENSION,
    TENSOR_PROFILE_CACHE_OBJECT_BITMAP,
    TYPED_PAYLOAD_DESCRIPTOR_LENGTH,
    BodyRegionPrelude,
    BudgetPolicy,
    CacheInvalidateScope,
    CacheObjectKind,
    ClientHelloLossToleranceExtension,
    ClientHelloMetadata,
    ClientHelloPayloadCapabilitiesExtension,
    ClientHelloTransportPolicyExtension,
    ControlExtensionEntry,
    ControlExtensionFlags,
    ExtensionFrameDescriptor,
    ExtensionFrameFlags,
    FlowUpdateBackpressureLevel,
    FlowUpdateFlags,
    FlowUpdateMetadata,
    FlowUpdateReason,
    FlowUpdateScopeKind,
    FrameSubmitMetadata,
    InlineObjectBlockHeader,
    LossTolerance,
    ObjectReferenceBlock,
    PayloadKind,
    ResultClass,
    ResultHintBudgetPolicy,
    ResultHintCongestionState,
    ResultHintMetadata,
    ResultHintReason,
    ResultPushMetadata,
    ServerHelloAckLossToleranceExtension,
    ServerHelloAckPayloadCapabilitiesExtension,
    SessionMigrateAckMetadata,
    SessionMigrateMetadata,
    SubmitMode,
    TransportId,
    TransportPolicy,
    TransportProbeAckMetadata,
    TransportProbeMetadata,
    TypedPayloadDescriptor,
    build_client_hello_loss_tolerance_extension,
    build_client_hello_payload_capabilities_extension,
    build_degraded_result_push_packet,
    build_flow_update_packet,
    build_frame_cancel_packet,
    build_frame_submit_mixed_packet,
    build_frame_submit_packet,
    build_frame_submit_typed_payload_packet,
    build_partial_result_push_packet,
    build_ping_packet,
    build_pong_packet,
    build_result_drop_packet,
    build_result_hint_packet,
    build_result_push_mixed_packet,
    build_result_push_packet,
    build_result_push_typed_payload_packet,
    build_server_hello_ack_loss_tolerance_extension,
    build_server_hello_ack_payload_capabilities_extension,
    build_session_migrate_ack_packet,
    build_session_migrate_packet,
    build_stale_reuse_result_push_packet,
    build_structured_event_frame,
    build_tool_delta_frame,
    build_transport_probe_ack_packet,
    build_transport_probe_packet,
    pack_control_extension_block,
    parse_client_hello_transport_policy_extension,
    unpack_control_extension_block,
)
from nnrp.enums import ErrorCode, FrameClass, HeaderFlags, MessageType, WireFormat
from nnrp.header import HEADER_LENGTH, HEADER_MAGIC, NnrpHeader
from nnrp.server import (
    NativeServerAcceptOptions,
    NativeServerBootstrapOptions,
    NativeServerProviderRoute,
    NativeServerSessionOptions,
    NativeServerSessionPolicyDecision,
    ServerProfile,
    listen_native_server,
)


def test_client_profile_defaults_and_overrides() -> None:
    default_profile = ClientProfile()
    custom_profile = ClientProfile(
        max_views=2,
        enable_cache=False,
        max_cache_entries=32,
        max_cache_bytes=1024,
    )

    assert default_profile.max_views == 1
    assert default_profile.enable_cache is True
    assert default_profile.max_cache_entries == 256
    assert default_profile.max_cache_bytes == 8 * 1024 * 1024

    assert custom_profile.max_views == 2
    assert custom_profile.enable_cache is False
    assert custom_profile.max_cache_entries == 32
    assert custom_profile.max_cache_bytes == 1024


def test_client_dial_policy_maps_selected_and_forced_bindings() -> None:
    selected_policy = ClientDialPolicy(selected_transport_id=TransportId.TCP)
    forced_policy = ClientDialPolicy(forced_transport_id=TransportId.QUIC)

    assert selected_policy.to_client_hello_transport_policy() == ClientHelloTransportPolicyExtension(
        transport_policy=TransportPolicy.PREFER_TCP,
        preferred_transport_id=TransportId.TCP,
    )
    assert forced_policy.to_client_hello_transport_policy() == ClientHelloTransportPolicyExtension(
        transport_policy=TransportPolicy.FORCE_QUIC,
        preferred_transport_id=TransportId.QUIC,
    )


def test_client_dial_policy_rejects_conflicting_bindings() -> None:
    try:
        ClientDialPolicy(
            selected_transport_id=TransportId.QUIC,
            forced_transport_id=TransportId.TCP,
        ).to_client_hello_transport_policy()
    except ValueError as exc:
        assert str(exc) == "selected_transport_id and forced_transport_id must not conflict"
    else:
        raise AssertionError("expected conflicting client dial policy to fail")


def test_resolve_client_hello_transport_policy_helper() -> None:
    assert resolve_client_hello_transport_policy(
        selected_transport_id=TransportId.QUIC,
    ) == ClientHelloTransportPolicyExtension(
        transport_policy=TransportPolicy.PREFER_QUIC,
        preferred_transport_id=TransportId.QUIC,
    )


def test_client_transport_plan_builds_client_hello_packet() -> None:
    plan = plan_client_transport(selected_transport_id=TransportId.TCP)
    packet = plan.build_client_hello_packet(
        requested_session_id=31,
        auth_block=b"conv-lite",
    )

    metadata = ClientHelloMetadata.unpack(packet.metadata)
    extension_payload = packet.body[: -metadata.auth_bytes]
    decoded = unpack_control_extension_block(
        extension_payload,
        known_types={CLIENT_HELLO_TRANSPORT_POLICY_EXTENSION},
    )

    assert isinstance(plan, ClientTransportPlan)
    assert plan.selected_transport_id is TransportId.TCP
    assert packet.header.wire_format is WireFormat.CURRENT
    assert metadata.requested_session_id == 31
    assert metadata.supported_wire_format_bitmap == 0x0001
    assert metadata.auth_bytes == 9
    assert parse_client_hello_transport_policy_extension(decoded[0]) == ClientHelloTransportPolicyExtension(
        transport_policy=TransportPolicy.PREFER_TCP,
        preferred_transport_id=TransportId.TCP,
    )


def test_build_client_hello_packet_applies_client_profile_cache_settings() -> None:
    packet = build_client_hello_packet(
        requested_session_id=33,
        client_profile=ClientProfile(
            max_views=2,
            enable_cache=False,
            max_cache_entries=64,
            max_cache_bytes=2048,
        ),
    )

    metadata = ClientHelloMetadata.unpack(packet.metadata)

    assert packet.header.wire_format is WireFormat.CURRENT
    assert metadata.requested_session_id == 33
    assert metadata.supported_wire_format_bitmap == 0x0001
    assert metadata.max_lane_count == 2
    assert metadata.cache_namespace_count == 0
    assert metadata.max_cache_entries == 0
    assert metadata.max_cache_bytes == 0


def test_build_client_hello_packet_uses_current_wire_format() -> None:
    packet = build_client_hello_packet(
        requested_session_id=34,
        wire_format=WireFormat.CURRENT,
    )

    metadata = ClientHelloMetadata.unpack(packet.metadata)

    assert packet.header.wire_format is WireFormat.CURRENT
    assert metadata.requested_session_id == 34
    assert metadata.supported_wire_format_bitmap == 0x0001


def test_bootstrap_client_transport_uses_probe_selection() -> None:
    selection = TransportProbeSelection(
        selected_transport_id=TransportId.TCP,
        tcp_summary=TransportProbeSummary(
            transport_id=TransportId.TCP,
            results=(
                TransportProbeResult(
                    transport_id=TransportId.TCP,
                    probe_id=7,
                    probe_payload_bytes=1024,
                    client_send_ts_us=100,
                    server_recv_ts_us=120,
                    ack_recv_ts_us=150,
                ),
            ),
        ),
    )

    bootstrap = bootstrap_client_transport(
        requested_session_id=44,
        auth_block=b"conv-lite",
        probe_selection=selection,
    )

    metadata = ClientHelloMetadata.unpack(bootstrap.hello_packet.metadata)
    extension_payload = bootstrap.hello_packet.body[: -metadata.auth_bytes]
    decoded = unpack_control_extension_block(
        extension_payload,
        known_types={CLIENT_HELLO_TRANSPORT_POLICY_EXTENSION},
    )

    assert isinstance(bootstrap, ClientTransportBootstrap)
    assert bootstrap.plan.selected_transport_id is TransportId.TCP
    assert bootstrap.probe_selection is selection
    assert metadata.requested_session_id == 44
    assert parse_client_hello_transport_policy_extension(decoded[0]) == ClientHelloTransportPolicyExtension(
        transport_policy=TransportPolicy.PREFER_TCP,
        preferred_transport_id=TransportId.TCP,
    )


def test_bootstrap_client_transport_forced_binding_overrides_probe_selection() -> None:
    selection = TransportProbeSelection(
        selected_transport_id=TransportId.TCP,
        tcp_summary=TransportProbeSummary(
            transport_id=TransportId.TCP,
            results=(
                TransportProbeResult(
                    transport_id=TransportId.TCP,
                    probe_id=8,
                    probe_payload_bytes=2048,
                    client_send_ts_us=200,
                    server_recv_ts_us=220,
                    ack_recv_ts_us=260,
                ),
            ),
        ),
    )

    bootstrap = bootstrap_client_transport(
        requested_session_id=45,
        probe_selection=selection,
        forced_transport_id=TransportId.QUIC,
    )

    assert bootstrap.plan.selected_transport_id is TransportId.QUIC
    assert bootstrap.plan.transport_policy_extension == ClientHelloTransportPolicyExtension(
        transport_policy=TransportPolicy.FORCE_QUIC,
        preferred_transport_id=TransportId.QUIC,
    )


def test_connect_client_control_is_exported() -> None:
    assert ClientControlBootstrapSession.__name__ == "ClientControlBootstrapSession"
    assert callable(connect_client_control)
    assert callable(connect_client_control_with_probe)
    assert callable(probe_client_transport)


def test_connect_client_session_is_exported() -> None:
    assert ClientSession.__name__ == "ClientSession"
    assert "connect_client_session" not in client_module.__all__
    assert "connect_client_session_with_probe" not in client_module.__all__
    assert not hasattr(client_module, "connect_client_session")
    assert not hasattr(client_module, "connect_client_session_with_probe")
    assert callable(connect_client_session)
    assert callable(connect_client_session_with_probe)
    assert "packet transport smoke/tooling" in (connect_client_session.__doc__ or "")
    assert "packet transport smoke/tooling" in (connect_client_session_with_probe.__doc__ or "")
    assert NativeClientConnection.__name__ == "NativeClientConnection"
    assert NativeClientOptions.__name__ == "NativeClientOptions"
    assert NativeClientProviderRoute.__name__ == "NativeClientProviderRoute"
    assert NativeClientSessionOptions.__name__ == "NativeClientSessionOptions"
    assert NativeSessionRecoveryTicket.__name__ == "NativeSessionRecoveryTicket"
    assert callable(connect_native_client_connection)
    assert callable(select_client_native_backend)
    assert SessionRecoveryReport.__name__ == "SessionRecoveryReport"
    assert callable(validate_session_recovery_request)
    assert callable(validate_session_recovery_ack)
    assert callable(validate_migration_recovery)
    assert callable(should_replay_frame_after_migration)


def test_preview4_host_runtime_api_keeps_request_options_off_packet_helpers() -> None:
    assert callable(NativeRuntimeClient.bind_server)
    assert callable(NativeRuntimeServer.accept_session)
    assert callable(NativeRuntimeServerSession.receive_submit)
    assert callable(NativeRuntimeServerOperation.send_result)
    assert NativeRuntimeEvent is not None
    assert NativeLifecycleEvent is not None
    assert NativePolledEvent is not None
    assert NativeRuntimeEventCallback is not None
    assert NativeLifecycleEventCallback is not None
    assert NativePolledEventCallback is not None

    assert set(NativeClientOptions.__annotations__) == {
        "endpoint",
        "provider_routes",
        "transport_policy",
        "session_defaults",
    }
    assert set(NativeClientProviderRoute.__annotations__) == {"provider_endpoint", "security"}
    assert set(NativeServerProviderRoute.__annotations__) == {"provider_endpoint", "security"}
    client_signature = inspect.signature(connect_native_client_connection)
    server_signature = inspect.signature(listen_native_server)
    assert tuple(client_signature.parameters) == (
        "options",
        "_transports",
        "_artifact_path",
        "_root",
        "_native_platform",
        "_library",
        "_fallback",
    )
    assert tuple(server_signature.parameters) == ("options", "_transports")
    assert inspect.iscoroutinefunction(NativeClientConnection.open_session)
    assert inspect.iscoroutinefunction(NativeClientConnection.resume_session)
    for removed_name in ("provider_endpoint", "transport", "security"):
        assert removed_name not in client_signature.parameters
        assert removed_name not in server_signature.parameters
    assert set(NativeClientSessionOptions.__annotations__) == {
        "requested_session_id",
        "profile_id",
        "schema_id",
        "schema_version",
        "priority_class",
        "default_deadline_ms",
        "max_in_flight_operations",
        "lease_ttl_hint_ms",
        "allow_resume",
        "resume_token_bytes",
        "cache_hints",
    }
    assert set(NativeServerBootstrapOptions.__annotations__) == {
        "endpoint",
        "provider_routes",
        "transport_policy",
        "session_defaults",
    }
    assert set(NativeServerSessionOptions.__annotations__) == {
        "supported_profiles",
        "supported_cache_objects",
        "max_cache_objects",
        "max_cache_object_bytes",
        "schema_registry",
        "resume_token_bytes",
        "max_in_flight_operations",
        "granted_operation_credit",
        "lease_ttl_ms",
        "resume_window_ms",
        "application_policy",
    }
    assert set(NativeServerAcceptOptions.__annotations__) == {"timeout_ms"}
    assert "deadline_unix_ms" not in SubmitRequest.__dataclass_fields__
    assert "priority_class" not in SubmitRequest.__dataclass_fields__

    priority_signature = inspect.signature(NativeClientConnection.update_runtime_priority)
    deadline_signature = inspect.signature(NativeClientConnection.update_runtime_deadline)
    expire_signature = inspect.signature(NativeClientConnection.expire_runtime_operation_at)

    assert "priority_class" in priority_signature.parameters
    assert "deadline_unix_ms" in deadline_signature.parameters
    assert "expire_at_unix_ms" in expire_signature.parameters


def test_contract_v9_role_option_defaults_are_exact() -> None:
    client = NativeClientSessionOptions()
    assert (
        client.requested_session_id,
        client.profile_id,
        client.schema_id,
        client.schema_version,
        client.priority_class.name,
        client.default_deadline_ms,
        client.max_in_flight_operations,
        client.lease_ttl_hint_ms,
        client.allow_resume,
        client.resume_token_bytes,
        client.cache_hints,
    ) == (0, 2, 0x00001001, 3, "BALANCED", 500, 4, 30_000, False, 0, ())

    server = NativeServerSessionOptions()
    assert (
        server.supported_profiles,
        server.supported_cache_objects,
        server.max_cache_objects,
        server.max_cache_object_bytes,
        server.resume_token_bytes,
        server.max_in_flight_operations,
        server.granted_operation_credit,
        server.lease_ttl_ms,
        server.resume_window_ms,
    ) == ((2,), (), 0, 0, 24, 4, 2, 30_000, 120_000)
    assert asyncio.run(server.application_policy.evaluate(None)) == NativeServerSessionPolicyDecision.accept()


def test_preview4_submit_api_matches_frozen_builder_contract() -> None:
    assert tuple(SubmitHeaderContext.__dataclass_fields__) == ("flags", "view_id", "route_id", "trace_id")
    assert tuple(SubmitIdentity.__dataclass_fields__) == ("operation_id", "frame_id", "header")
    assert tuple(SubmitPolicy.__dataclass_fields__) == (
        "frame_class",
        "latency_budget_ms",
        "target_fps_x100",
        "retry_of_frame",
        "budget_policy",
        "loss_tolerance_policy",
        "dependency_frame_id",
    )
    assert tuple(SubmitObjectReferences.__dataclass_fields__) == (
        "camera",
        "tile_index",
        "tensor_section_table",
    )
    assert tuple(TensorSubmitInput.__dataclass_fields__) == (
        "identity",
        "policy",
        "src_width",
        "src_height",
        "tile_width",
        "tile_height",
        "tile_ids",
        "sections",
        "camera_block",
        "input_profile",
        "tile_index_mode",
        "tile_base_id",
        "references",
    )
    assert tuple(TokenChunk.__dataclass_fields__) == ("payload", "descriptor_flags")
    assert tuple(TokenSubmitInput.__dataclass_fields__) == ("identity", "policy", "chunks")
    assert tuple(TypedPayloadInputFrame.__dataclass_fields__) == (
        "profile_id",
        "payload_kind",
        "payload",
        "descriptor_flags",
        "schema_id",
        "schema_version",
        "stream_semantics",
    )
    assert tuple(TypedPayloadSubmitInput.__dataclass_fields__) == ("identity", "policy", "frames")
    assert tuple(SubmitRequest.__dataclass_fields__) == (
        "operation_id",
        "frame_id",
        "header",
        "metadata",
        "body",
    )
    assert tuple(inspect.signature(SubmitRequest.tensor).parameters) == ("value",)
    assert tuple(inspect.signature(SubmitRequest.token).parameters) == ("value",)
    assert tuple(inspect.signature(SubmitRequest.typed_payload).parameters) == ("value",)


def test_legacy_packet_session_helpers_warn_as_tooling_only() -> None:
    async def enter_legacy_session() -> None:
        async with connect_client_session("127.0.0.1"):
            pass

    with pytest.warns(RuntimeWarning, match="packet transport smoke/tooling helpers"):
        with pytest.raises(ValueError, match="selected transport id is unspecified"):
            asyncio.run(enter_legacy_session())


def test_current_typed_models_are_exported() -> None:
    assert SubmitRequest.__name__ == "SubmitRequest"
    assert TypedPayload.__name__ == "TypedPayload"
    assert MigrationOutcome.__name__ == "MigrationOutcome"
    assert PathHealthSample.__name__ == "PathHealthSample"
    assert MigrationTriggerPolicy.__name__ == "MigrationTriggerPolicy"
    assert MigrationTriggerSnapshot.__name__ == "MigrationTriggerSnapshot"
    assert MigrationTriggerMonitor.__name__ == "MigrationTriggerMonitor"
    assert Result.__name__ == "Result"
    assert ResultRouter.__name__ == "ResultRouter"
    assert CacheObjectIdentity.__name__ == "CacheObjectIdentity"
    assert CacheLeaseDescriptor.__name__ == "CacheLeaseDescriptor"
    assert CacheLeaseOwnerScope.SESSION == 1
    assert SchemaDescriptorHeader.__name__ == "SchemaDescriptorHeader"
    assert SchemaRegistryCatalog.__name__ == "SchemaRegistryCatalog"
    assert TypedPayloadDescriptor.__name__ == "TypedPayloadDescriptor"
    assert StandardProfile.TOKEN == 2
    assert StreamSemantics.APPEND == 2
    assert token_delta_schema_descriptor().profile_id is StandardProfile.TOKEN
    assert token_delta_payload_descriptor(offset=0, length=1).profile_id is StandardProfile.TOKEN


def test_preview4_native_feature_flag_helpers_are_exported() -> None:
    feature_flags = NativeRuntimeFeatureFlag.CLIENT_API | NativeRuntimeFeatureFlag.CACHE_SCHEMA

    assert native_runtime_feature_flag_names(feature_flags) == ("client_api", "cache_schema")
    assert native_runtime_feature_flag_names(feature_flags, mask=RUNTIME_CONTROL_FEATURE_FLAGS) == ("client_api",)
    assert native_runtime_feature_flag_names(feature_flags, mask=RUNTIME_OBJECT_FEATURE_FLAGS) == ("cache_schema",)
    assert native_runtime_feature_flags_available(feature_flags, NativeRuntimeFeatureFlag.CLIENT_API) is True
    assert native_runtime_feature_flags_available(feature_flags, RUNTIME_CONTROL_FEATURE_FLAGS) is False


def test_current_result_helpers_expose_payload_kinds_without_tensor_coverage() -> None:
    packet = build_result_push_typed_payload_packet(
        session_id=7,
        frame_id=88,
        frames=(
            build_structured_event_frame(b'{"event":"ready"}'),
            build_tool_delta_frame(b'{"tool":"render"}'),
        ),
        result_class=ResultClass.COMPLETE,
    )
    result = Result(packet=packet, metadata=ResultPushMetadata.unpack(packet.metadata))

    assert result.payload_kinds == (PayloadKind.STRUCTURED_EVENT, PayloadKind.TOOL_DELTA)
    assert result.has_payload_kind(PayloadKind.STRUCTURED_EVENT) is True
    assert result.has_payload_kind(PayloadKind.TENSOR) is False
    assert result.payload_frame_count == 2
    assert result.has_tensor_coverage is False
    assert result.tensor_covered_tile_count is None
    assert result.tensor_dropped_tile_count is None
    assert result.tile_ids == ()
    assert result.sections == ()


def test_current_migration_trigger_monitor_requires_consecutive_degraded_windows() -> None:
    monitor = MigrationTriggerMonitor(
        active_transport_id=TransportId.QUIC,
        policy=MigrationTriggerPolicy(
            window_size=3,
            consecutive_degraded_windows=2,
            max_timeout_rate=0.2,
            min_median_throughput_bytes_per_sec=1160.0,
            max_median_round_trip_us=115,
            max_jitter_us=40,
        ),
    )

    warmup = monitor.observe(PathHealthSample(round_trip_us=100, effective_throughput_bytes_per_sec=1200.0))
    assert warmup.should_trigger is False
    assert warmup.sample_count == 1

    monitor.observe(PathHealthSample(round_trip_us=110, effective_throughput_bytes_per_sec=1180.0))
    healthy = monitor.observe(PathHealthSample(round_trip_us=120, effective_throughput_bytes_per_sec=1150.0))
    assert healthy.is_degraded is False
    assert healthy.should_trigger is False
    assert healthy.consecutive_degraded_windows == 0

    first_degraded = monitor.observe(PathHealthSample(round_trip_us=260, effective_throughput_bytes_per_sec=700.0))
    assert first_degraded.is_degraded is True
    assert set(first_degraded.degraded_reasons) == {"throughput", "round_trip", "jitter"}
    assert first_degraded.should_trigger is False
    assert first_degraded.consecutive_degraded_windows == 1

    second_degraded = monitor.observe(PathHealthSample(timed_out=True))
    assert second_degraded.is_degraded is True
    assert "timeout_rate" in second_degraded.degraded_reasons
    assert second_degraded.should_trigger is True
    assert second_degraded.consecutive_degraded_windows == 2

    still_degraded = monitor.observe(PathHealthSample(round_trip_us=105, effective_throughput_bytes_per_sec=1300.0))
    assert still_degraded.is_degraded is True

    monitor.observe(PathHealthSample(round_trip_us=102, effective_throughput_bytes_per_sec=1280.0))
    recovered = monitor.observe(PathHealthSample(round_trip_us=101, effective_throughput_bytes_per_sec=1270.0))
    assert recovered.is_degraded is False
    assert recovered.should_trigger is False
    assert recovered.consecutive_degraded_windows == 0


def test_server_profile_defaults_and_overrides() -> None:
    default_profile = ServerProfile()
    custom_profile = ServerProfile(
        max_concurrent_frames=4,
        enable_cache=False,
        max_sections=8,
        max_body_bytes=4096,
    )

    assert default_profile.max_concurrent_frames == 1
    assert default_profile.enable_cache is True
    assert default_profile.max_sections == 16
    assert default_profile.max_body_bytes == 32 * 1024 * 1024

    assert custom_profile.max_concurrent_frames == 4
    assert custom_profile.enable_cache is False
    assert custom_profile.max_sections == 8
    assert custom_profile.max_body_bytes == 4096


def test_public_exports_expose_core_symbols() -> None:
    header = NnrpHeader(
        version_major=1,
        wire_format=WireFormat.CURRENT,
        msg_type=MessageType.PING,
        flags=HeaderFlags.ACK_REQUIRED,
        meta_len=0,
        body_len=0,
        session_id=0,
        frame_id=0,
        view_id=0,
        route_id=0,
        trace_id=0,
    )

    assert HEADER_MAGIC == b"NNRP"
    assert HEADER_LENGTH == 40
    assert header.header_len == HEADER_LENGTH
    assert FrameClass.KEYFRAME == 0
    assert ErrorCode.UNSUPPORTED_VERSION == 0x0001
    assert build_ping_packet(session_id=1).header.msg_type is MessageType.PING
    assert build_pong_packet(session_id=1).header.msg_type is MessageType.PONG
    assert build_frame_cancel_packet(session_id=1, frame_id=2).header.msg_type is MessageType.FRAME_CANCEL
    assert FRAME_SUBMIT_METADATA_LENGTH == 72
    assert RESULT_PUSH_METADATA_LENGTH == 64
    assert BODY_REGION_PRELUDE_LENGTH == 32
    assert INLINE_OBJECT_BLOCK_HEADER_LENGTH == 16
    assert OBJECT_REFERENCE_BLOCK_LENGTH == 24
    assert TYPED_PAYLOAD_DESCRIPTOR_LENGTH == 24
    assert EXTENSION_FRAME_DESCRIPTOR_LENGTH == 16
    assert FrameSubmitMetadata.__name__ == "FrameSubmitMetadata"
    assert ResultPushMetadata.__name__ == "ResultPushMetadata"
    assert BodyRegionPrelude.__name__ == "BodyRegionPrelude"
    assert InlineObjectBlockHeader.__name__ == "InlineObjectBlockHeader"
    assert ObjectReferenceBlock.__name__ == "ObjectReferenceBlock"
    assert TypedPayloadDescriptor.__name__ == "TypedPayloadDescriptor"
    assert ExtensionFrameDescriptor.__name__ == "ExtensionFrameDescriptor"
    assert ExtensionFrameFlags.CRITICAL == 0x0001
    assert SubmitMode.MIXED == 2
    assert BudgetPolicy.ALLOW_DROP == 0x08
    assert ResultClass.DEGRADED == 3
    assert build_result_drop_packet(session_id=1, frame_id=2).header.msg_type is MessageType.RESULT_DROP
    assert callable(build_degraded_result_push_packet)
    assert callable(build_frame_submit_mixed_packet)
    assert callable(build_frame_submit_packet)
    assert callable(build_frame_submit_typed_payload_packet)
    assert callable(build_partial_result_push_packet)
    assert callable(build_result_push_mixed_packet)
    assert callable(build_result_push_packet)
    assert callable(build_stale_reuse_result_push_packet)
    assert callable(build_result_push_typed_payload_packet)
    flow_update = build_flow_update_packet(
        metadata=FlowUpdateMetadata(
            scope_kind=FlowUpdateScopeKind.SESSION,
            update_reason=FlowUpdateReason.CONGESTION,
            backpressure_level=FlowUpdateBackpressureLevel.SOFT,
            session_credit=2,
            retry_after_ms=25,
            credit_epoch=4,
            flags=FlowUpdateFlags.CREDIT_VALID | FlowUpdateFlags.RETRY_AFTER_VALID,
        ),
        session_id=1,
    )
    result_hint = build_result_hint_packet(
        metadata=ResultHintMetadata(
            applied_budget_policy=ResultHintBudgetPolicy.PARTIAL,
            congestion_state=ResultHintCongestionState.ELEVATED,
            reason=ResultHintReason.SERVER_BUSY,
            retry_after_ms=15,
        ),
        session_id=1,
        frame_id=2,
    )
    client_loss_tolerance = build_client_hello_loss_tolerance_extension(
        ClientHelloLossToleranceExtension(
            session_loss_tolerance=LossTolerance.LOW_LATENCY,
        )
    )
    server_loss_tolerance = build_server_hello_ack_loss_tolerance_extension(
        ServerHelloAckLossToleranceExtension(
            accepted_loss_tolerance=LossTolerance.BEST_EFFORT,
        )
    )
    client_payload_capabilities = build_client_hello_payload_capabilities_extension(
        ClientHelloPayloadCapabilitiesExtension(
            payload_kind_bitmap=PayloadKind.TENSOR | PayloadKind.TOOL_DELTA,
        )
    )
    server_payload_capabilities = build_server_hello_ack_payload_capabilities_extension(
        ServerHelloAckPayloadCapabilitiesExtension(
            accepted_payload_kind_bitmap=PayloadKind.TENSOR | PayloadKind.STRUCTURED_EVENT,
        )
    )
    probe = build_transport_probe_packet(
        metadata=TransportProbeMetadata(probe_id=1, probe_payload_bytes=4, client_send_ts_us=9),
        body=b"ping",
    )
    probe_ack = build_transport_probe_ack_packet(
        metadata=TransportProbeAckMetadata(probe_id=1, reserved=0, server_recv_ts_us=10)
    )
    migrate = build_session_migrate_packet(
        metadata=SessionMigrateMetadata(
            old_transport_id=TransportId.QUIC,
            new_transport_id=TransportId.TCP,
            last_result_frame_id=7,
            client_migrate_ts_us=11,
        ),
        session_id=1,
    )
    migrate_ack = build_session_migrate_ack_packet(
        metadata=SessionMigrateAckMetadata(
            accept_code=0,
            resume_from_frame_id=8,
            grace_window_ms=100,
            server_migrate_ts_us=12,
        ),
        session_id=1,
    )
    assert flow_update.header.msg_type is MessageType.FLOW_UPDATE
    assert result_hint.header.msg_type is MessageType.RESULT_HINT
    assert client_loss_tolerance.ext_type == CLIENT_HELLO_LOSS_TOLERANCE_EXTENSION
    assert server_loss_tolerance.ext_type == SERVER_HELLO_ACK_LOSS_TOLERANCE_EXTENSION
    assert client_payload_capabilities.ext_type == CLIENT_HELLO_PAYLOAD_CAPABILITIES_EXTENSION
    assert server_payload_capabilities.ext_type == SERVER_HELLO_ACK_PAYLOAD_CAPABILITIES_EXTENSION
    assert TransportId.UNSPECIFIED == 0
    assert TransportId.QUIC == 1
    assert TransportId.TCP == 2
    assert PayloadKind.TENSOR == 0x00000001
    assert PayloadKind.TOKEN_CHUNK == 0x00000002
    assert PayloadKind.AUDIO_CHUNK == 0x00000004
    assert PayloadKind.VIDEO_CHUNK == 0x00000008
    assert PayloadKind.STRUCTURED_EVENT == 0x00000010
    assert PayloadKind.TOOL_DELTA == 0x00000020
    assert PayloadKind.OPAQUE_BYTES == 0x00000040
    assert probe.header.msg_type is MessageType.TRANSPORT_PROBE
    assert probe_ack.header.msg_type is MessageType.TRANSPORT_PROBE_ACK
    assert migrate.header.msg_type is MessageType.SESSION_MIGRATE
    assert migrate_ack.header.msg_type is MessageType.SESSION_MIGRATE_ACK
    assert MessageType.CLIENT_HELLO == 0x01
    assert MessageType.SERVER_HELLO_ACK == 0x02
    assert MessageType.SESSION_PATCH == 0x03
    assert MessageType.SESSION_PATCH_ACK == 0x04
    assert MessageType.CLOSE == 0x05
    assert MessageType.ERROR == 0x06
    assert MessageType.FRAME_SUBMIT == 0x10
    assert MessageType.FRAME_CANCEL == 0x11
    assert MessageType.RESULT_PUSH == 0x12
    assert MessageType.RESULT_DROP == 0x13
    assert MessageType.CACHE_PUT == 0x14
    assert MessageType.CACHE_ACK == 0x15
    assert MessageType.CACHE_INVALIDATE == 0x16
    assert MessageType.FLOW_UPDATE == 0x17
    assert MessageType.RESULT_HINT == 0x18
    assert MessageType.TRANSPORT_PROBE == 0x19
    assert MessageType.TRANSPORT_PROBE_ACK == 0x1A
    assert MessageType.SESSION_MIGRATE == 0x1B
    assert MessageType.SESSION_MIGRATE_ACK == 0x1C
    assert MessageType.PING == 0x20
    assert MessageType.PONG == 0x21
    assert CacheObjectKind.CAMERA_BLOCK == 0x0001
    assert CacheObjectKind.TILE_INDEX_BLOCK == 0x0002
    assert CacheObjectKind.TENSOR_SECTION_TABLE == 0x0003
    assert CacheObjectKind.CODEC_TABLE == 0x0004
    assert CacheObjectKind.REUSABLE_RESULT_OBJECT == 0x0005
    assert CacheObjectKind.PAYLOAD_LAYOUT_TEMPLATE == 0x0006
    assert CacheObjectKind.PROMPT_SEGMENT == 0x0007
    assert CacheObjectKind.TOOL_SCHEMA == 0x0008
    assert CacheObjectKind.STRUCTURED_EVENT_SCHEMA == 0x0009
    assert TENSOR_PROFILE_CACHE_OBJECT_BITMAP == 0x0007
    assert CacheInvalidateScope.WHOLE_SESSION == 0
    assert CacheInvalidateScope.NAMESPACE == 1
    assert CacheInvalidateScope.OBJECT_KIND == 2
    assert CacheInvalidateScope.OBJECT_KEY == 3
    payload = pack_control_extension_block(
        (ControlExtensionEntry(ext_type=0x0001, ext_flags=ControlExtensionFlags.CRITICAL, payload=b"ok"),)
    )
    assert unpack_control_extension_block(payload)[0].payload == b"ok"
