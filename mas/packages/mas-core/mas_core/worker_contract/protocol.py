"""Protocol compatibility negotiation for the universal worker contract."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .models import ADAPTER_API_VERSION, CONTRACT_VERSION, ProtocolVersion

CURRENT_PROTOCOL_VERSION = CONTRACT_VERSION
CURRENT_MAJOR = 1
SUPPORTED_MAJOR_VERSIONS = frozenset({CURRENT_MAJOR})


class ProtocolNegotiationError(ValueError):
    """Raised when a worker and AIAT cannot safely negotiate a protocol."""


@dataclass(frozen=True, slots=True)
class ProtocolNegotiationResult:
    accepted: bool
    contract_version: str
    schema_version: str
    adapter_api_version: str
    runtime_api_version: str | None
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "contract_version": self.contract_version,
            "schema_version": self.schema_version,
            "adapter_api_version": self.adapter_api_version,
            "runtime_api_version": self.runtime_api_version,
            "warnings": list(self.warnings),
        }


def _major(version: str) -> int:
    match = re.search(r"(?:\.v|[= ])(\d+)(?:\.|$)", version)
    if match:
        return int(match.group(1))
    # Accept conventional semantic versions too.
    match = re.match(r"^(\d+)", version)
    if match:
        return int(match.group(1))
    raise ProtocolNegotiationError(f"cannot determine protocol major version: {version!r}")


def negotiate_protocol(
    peer: ProtocolVersion,
    *,
    supported_contract_versions: frozenset[str] | set[str] = frozenset({CURRENT_PROTOCOL_VERSION}),
    supported_adapter_versions: frozenset[str] | set[str] = frozenset({ADAPTER_API_VERSION}),
    required_capabilities: set[str] | frozenset[str] = frozenset(),
    offered_capabilities: set[str] | frozenset[str] = frozenset(),
) -> ProtocolNegotiationResult:
    """Validate protocol metadata and capability requirements.

    Unknown optional fields are intentionally not inspected here. Required
    capability negotiation is explicit so a runtime cannot claim support from
    an unrecognized extension.
    """

    peer_major = _major(peer.contract_version)
    if peer.contract_version not in supported_contract_versions and peer_major not in SUPPORTED_MAJOR_VERSIONS:
        raise ProtocolNegotiationError(
            f"unsupported worker contract version {peer.contract_version!r}"
        )
    if peer.adapter_api_version not in supported_adapter_versions:
        raise ProtocolNegotiationError(
            f"unsupported adapter API version {peer.adapter_api_version!r}"
        )
    missing = sorted(set(required_capabilities) - set(offered_capabilities))
    if missing:
        raise ProtocolNegotiationError(
            "worker does not offer required capabilities: " + ", ".join(missing)
        )
    warnings: list[str] = []
    if peer.contract_version != CURRENT_PROTOCOL_VERSION:
        warnings.append(f"peer contract {peer.contract_version} negotiated with {CURRENT_PROTOCOL_VERSION}")
    if peer.schema_version != "1.0":
        warnings.append(f"peer schema version {peer.schema_version} is not the current 1.0")
    return ProtocolNegotiationResult(
        accepted=True,
        contract_version=peer.contract_version,
        schema_version=peer.schema_version,
        adapter_api_version=peer.adapter_api_version,
        runtime_api_version=peer.runtime_api_version,
        warnings=tuple(warnings),
    )
