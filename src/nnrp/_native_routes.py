"""Shared host-route validation for the public native role APIs."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import TypeVar

from nnrp.core import TransportPolicy
from nnrp.native import (
    NATIVE_TRANSPORT_ID_BY_NAME,
    NATIVE_TRANSPORT_SCOPES,
    NativeArtifactError,
    NativeTransportCandidateDiagnostic,
    NativeTransportProbeState,
    NativeTransportProviderCost,
    NativeTransportProviderLimitation,
    NativeTransportProviderLimits,
    NativeTransportProviderMetadata,
    NativeTransportRejectionReason,
    NativeTransportSelectionError,
    NativeTransportSelectionErrorCode,
)

_Route = TypeVar("_Route")


def normalize_transport_policy(policy: TransportPolicy | str | int) -> TransportPolicy:
    if isinstance(policy, TransportPolicy):
        return policy
    if isinstance(policy, int):
        return TransportPolicy(policy)
    normalized = policy.strip().lower().replace("-", "_")
    if normalized == "auto":
        return TransportPolicy.AUTO
    try:
        return TransportPolicy[normalized.upper()]
    except KeyError as error:
        raise NativeArtifactError(f"unsupported native transport policy: {policy}") from error


def forced_transport_name(policy: TransportPolicy) -> str | None:
    return {
        TransportPolicy.FORCE_QUIC: "quic",
        TransportPolicy.FORCE_TCP: "tcp",
        TransportPolicy.FORCE_IPC: "ipc",
        TransportPolicy.FORCE_WEBSOCKET: "websocket",
    }.get(policy)


def policy_allows(policy: TransportPolicy, transport_name: str) -> bool:
    forced = forced_transport_name(policy)
    return forced is None or forced == transport_name


def normalize_provider_routes(
    routes: Mapping[str, _Route] | None,
    route_type: type[_Route],
) -> Mapping[str, _Route]:
    if routes is None:
        return MappingProxyType({})
    normalized: dict[str, _Route] = {}
    for transport_name, route in routes.items():
        if transport_name not in NATIVE_TRANSPORT_SCOPES:
            raise NativeArtifactError(
                f"provider route key must be one of {', '.join(NATIVE_TRANSPORT_SCOPES)}: {transport_name!r}"
            )
        if not isinstance(route, route_type):
            raise TypeError(f"provider route {transport_name!r} must be {route_type.__name__}")
        normalized[transport_name] = route
    return MappingProxyType(normalized)


def official_provider_metadata(transport_name: str) -> NativeTransportProviderMetadata:
    limitations = {
        "tcp": (
            NativeTransportProviderLimitation.REQUIRES_TCP,
            NativeTransportProviderLimitation.NATIVE_HOST_ONLY,
        ),
        "quic": (
            NativeTransportProviderLimitation.REQUIRES_UDP,
            NativeTransportProviderLimitation.NATIVE_HOST_ONLY,
        ),
        "ipc": (
            NativeTransportProviderLimitation.LOCAL_HOST_ONLY,
            NativeTransportProviderLimitation.NATIVE_HOST_ONLY,
            NativeTransportProviderLimitation.WINDOWS_NAMED_PIPE
            if os.name == "nt"
            else NativeTransportProviderLimitation.UNIX_DOMAIN_SOCKET,
        ),
        "websocket": (
            NativeTransportProviderLimitation.REQUIRES_TCP,
            NativeTransportProviderLimitation.NATIVE_HOST_ONLY,
        ),
    }[transport_name]
    return NativeTransportProviderMetadata(
        id=f"nnrp.transport.{transport_name}.native",
        cost=NativeTransportProviderCost(model_id=0, units=0),
        preference_rank={"ipc": 0, "quic": 1, "tcp": 2, "websocket": 3}[transport_name],
        limits=NativeTransportProviderLimits(max_frame_bytes=67_108_864),
        limitations=limitations,
    )


def unavailable_candidate(transport_name: str) -> NativeTransportCandidateDiagnostic:
    return NativeTransportCandidateDiagnostic(
        transport_id=NATIVE_TRANSPORT_ID_BY_NAME[transport_name],
        provider=official_provider_metadata(transport_name),
        local_available=False,
        peer_supported=True,
        within_limits=True,
        probe_state=NativeTransportProbeState.NOT_RUN,
        rejection_reason=NativeTransportRejectionReason.LOCAL_UNAVAILABLE,
    )


def apply_host_rejection(
    candidate: NativeTransportCandidateDiagnostic,
    *,
    policy_allowed: bool,
    local_available: bool,
    peer_supported: bool,
    within_limits: bool,
    route_resolved: bool,
    security_satisfied: bool,
) -> NativeTransportCandidateDiagnostic:
    reason: NativeTransportRejectionReason | None
    if not policy_allowed:
        reason = NativeTransportRejectionReason.POLICY_DISALLOWED
    elif not local_available:
        reason = NativeTransportRejectionReason.LOCAL_UNAVAILABLE
    elif not peer_supported:
        reason = NativeTransportRejectionReason.PEER_UNSUPPORTED
    elif not within_limits:
        reason = NativeTransportRejectionReason.LIMIT_EXCEEDED
    elif not route_resolved:
        reason = NativeTransportRejectionReason.ROUTE_UNRESOLVED
    elif not security_satisfied:
        reason = NativeTransportRejectionReason.SECURITY_UNSATISFIED
    else:
        reason = candidate.rejection_reason
    return replace(
        candidate,
        local_available=local_available,
        peer_supported=peer_supported,
        within_limits=within_limits,
        selection_rank=None if reason is not None else candidate.selection_rank,
        rejection_reason=reason,
    )


def ordered_candidates(
    candidates: Mapping[str, NativeTransportCandidateDiagnostic],
) -> tuple[NativeTransportCandidateDiagnostic, ...]:
    return tuple(
        sorted(
            candidates.values(),
            key=lambda candidate: (
                candidate.selection_rank is None,
                candidate.selection_rank if candidate.selection_rank is not None else int(candidate.transport_id),
                candidate.provider.id.encode(),
            ),
        )
    )


def selection_error(
    policy: TransportPolicy,
    candidates: tuple[NativeTransportCandidateDiagnostic, ...],
) -> NativeTransportSelectionError:
    forced = forced_transport_name(policy)
    if forced is not None:
        candidate = next((value for value in candidates if value.transport_name == forced), None)
        if candidate is not None and candidate.rejection_reason is not None:
            return NativeTransportSelectionError(
                NativeTransportSelectionErrorCode.FORCED_TRANSPORT_UNAVAILABLE,
                f"forced native transport {forced} rejected: {candidate.rejection_reason.value}",
                policy=policy,
                candidates=candidates,
            )
        return NativeTransportSelectionError(
            NativeTransportSelectionErrorCode.FORCED_TRANSPORT_UNAVAILABLE,
            f"forced native transport is not available: {forced}",
            policy=policy,
            candidates=candidates,
        )
    return NativeTransportSelectionError(
        NativeTransportSelectionErrorCode.NO_VIABLE_TRANSPORT,
        "no viable native transport provider after applying host routes",
        policy=policy,
        candidates=candidates,
    )


def preferred_transport_name(policy: TransportPolicy) -> str | None:
    return {
        TransportPolicy.PREFER_QUIC: "quic",
        TransportPolicy.PREFER_TCP: "tcp",
        TransportPolicy.PREFER_IPC: "ipc",
        TransportPolicy.PREFER_WEBSOCKET: "websocket",
    }.get(policy)


def provider_order_key(
    transport_name: str,
    metadata: NativeTransportProviderMetadata,
    policy: TransportPolicy,
) -> tuple[int, int, int, bytes]:
    return (
        0 if preferred_transport_name(policy) == transport_name else 1,
        metadata.preference_rank,
        int(NATIVE_TRANSPORT_ID_BY_NAME[transport_name]),
        metadata.id.encode(),
    )
