"""Client-facing NNRP helpers."""

from dataclasses import dataclass

from nnrp.core import ClientHelloTransportPolicyExtension, TransportId, TransportPolicy


@dataclass(slots=True)
class ClientProfile:
    max_views: int = 1
    enable_cache: bool = True
    max_cache_entries: int = 256
    max_cache_bytes: int = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ClientDialPolicy:
    selected_transport_id: TransportId = TransportId.UNSPECIFIED
    forced_transport_id: TransportId = TransportId.UNSPECIFIED

    def to_client_hello_transport_policy(
        self,
    ) -> ClientHelloTransportPolicyExtension | None:
        selected_transport_id = TransportId(self.selected_transport_id)
        forced_transport_id = TransportId(self.forced_transport_id)

        if forced_transport_id is not TransportId.UNSPECIFIED:
            if (
                selected_transport_id is not TransportId.UNSPECIFIED
                and selected_transport_id is not forced_transport_id
            ):
                raise ValueError("selected_transport_id and forced_transport_id must not conflict")
            return ClientHelloTransportPolicyExtension(
                transport_policy=_forced_transport_policy_for_id(forced_transport_id),
                preferred_transport_id=forced_transport_id,
            )

        if selected_transport_id is TransportId.UNSPECIFIED:
            return None

        return ClientHelloTransportPolicyExtension(
            transport_policy=_preferred_transport_policy_for_id(selected_transport_id),
            preferred_transport_id=selected_transport_id,
        )


def resolve_client_hello_transport_policy(
    *,
    selected_transport_id: TransportId = TransportId.UNSPECIFIED,
    forced_transport_id: TransportId = TransportId.UNSPECIFIED,
) -> ClientHelloTransportPolicyExtension | None:
    return ClientDialPolicy(
        selected_transport_id=selected_transport_id,
        forced_transport_id=forced_transport_id,
    ).to_client_hello_transport_policy()


def _preferred_transport_policy_for_id(transport_id: TransportId) -> TransportPolicy:
    if transport_id is TransportId.QUIC:
        return TransportPolicy.PREFER_QUIC
    if transport_id is TransportId.TCP:
        return TransportPolicy.PREFER_TCP
    if transport_id is TransportId.IPC:
        return TransportPolicy.PREFER_IPC
    if transport_id is TransportId.WEBSOCKET:
        return TransportPolicy.PREFER_WEBSOCKET
    raise ValueError("selected transport id must be QUIC, TCP, IPC, or WEBSOCKET")


def _forced_transport_policy_for_id(transport_id: TransportId) -> TransportPolicy:
    if transport_id is TransportId.QUIC:
        return TransportPolicy.FORCE_QUIC
    if transport_id is TransportId.TCP:
        return TransportPolicy.FORCE_TCP
    if transport_id is TransportId.IPC:
        return TransportPolicy.FORCE_IPC
    if transport_id is TransportId.WEBSOCKET:
        return TransportPolicy.FORCE_WEBSOCKET
    raise ValueError("forced transport id must be QUIC, TCP, IPC, or WEBSOCKET")
