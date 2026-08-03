# AIAT third-party component policy

AIAT is proprietary. Third-party components are consumed as isolated
dependencies, external processes, or adapter-backed services; they do not
become AIAT authority code. The machine-readable inventory is
[`mas/docs/provenance/third_party_components.yaml`](mas/docs/provenance/third_party_components.yaml).

## Default-shipped policy

The default product may ship permissively licensed components such as
LangGraph, CrewAI, Microsoft Agent Framework (when its dependency set is
compatible with the locked MCP version), OpenCode, OpenHands core,
Playwright, pytest, Docling, GitHub Spec Kit, Mermaid, Scrapling, Semgrep,
gVisor, Firecracker, OpenTofu, ccpm/GitHub Issues adapters, Letta, Qdrant,
Temporal, LiteLLM, OmniRoute, and MCP SDKs. Each release must record the exact
version, source URL, license evidence, and whether the component is embedded,
executed as a subprocess, or reached through an external adapter.

The default image must not embed AGPL/GPL/BUSL or otherwise restricted
components without an explicit legal review. TruffleHog, Plane, ZITADEL, Vault,
Ansible, OpenProject, Neo4j Community, and unrestricted browser-use are
optional external/user-installed integrations, not default AIAT modules.

## Release evidence

Every release pipeline must produce an SPDX or CycloneDX SBOM, preserve source
and license evidence, run the prohibited-license check, and publish the
artifact with the image digest. A missing or ambiguous license fails the
release gate; it is never silently classified as permissive.

This file is an engineering control, not legal advice. Re-run the provenance
review when a dependency, image, adapter, or license changes.
