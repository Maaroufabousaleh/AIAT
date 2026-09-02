# AIAT personal internal-use policy

AIAT is a personal, single-operator, internal-use programme. It is not being
designed or documented as a product for sale, public distribution, managed
hosting, or third-party commercial deployment.

## Architecture direction

- Keep the AIAT-owned orchestrator, router, tool service, registries,
  credentials boundary, approvals, dashboard, company control plane, chiefs,
  managers, and evidence systems.
- Keep authority and project state inside AIAT.
- Implement specialist workers through AIAT shells and versioned adapters to
  external runtimes and tools rather than hardcoding every worker.
- Select resources primarily for capability, reliability, security, privacy,
  resource use, maintainability, and integration quality.
- Treat every external runtime as untrusted execution regardless of licence.

## Licence handling

Licence information is metadata only for this personal instance.

- Record the detected licence, source link, version, notices, and any stated
  use/modification/redistribution restrictions when they are known.
- Display unusual, missing, non-commercial, no-modification, copyleft, source
  disclosure, network-use, or other restrictions as operator notices.
- Do not use licence family, missing licence metadata, or an automated licence
  interpretation to block discovery, installation, hiring, activation,
  execution, updating, or normal internal use in AIAT.
- Do not maintain licence allowlists, prohibited-licence lists, or
  licence-based default-component bans for this programme.
- The `license_provenance_evaluator` is a metadata collector/reporter. It is
  not an approval authority and its result is not a mandatory activation gate.
- Security, authenticity, sandbox, compatibility, data-loss, privacy, budget,
  and human-approval gates remain enforceable; this policy changes only the
  treatment of licence metadata.

AIAT does not claim that recording metadata changes or waives a third party's
terms. The personal operator owns the decision to use a resource and any
obligations that may apply. If AIAT is later sold, distributed, hosted for
others, or used commercially, create a separate distribution review instead
of reusing this internal-only policy.

## Resource policy

All technically suitable resources may be used normally through the safest
appropriate integration mode. The choices below are preferences, not
licence-based bans.

| Capability | Preferred starting point | Other normal internal options |
| --- | --- | --- |
| General workers | LangGraph, CrewAI, Microsoft Agent Framework | AutoGen and other adapter-backed runtimes |
| Coding/testing | OpenCode, OpenHands core, Playwright, pytest | Other pinned coding/test adapters |
| Documents/specs | Docling, GitHub Spec Kit, Mermaid | Other document and diagram tools |
| Research/browser | Scrapling, guarded Playwright/browser-use | Other sandboxed fetch/browser tools |
| Code review | AIAT deterministic diff reviewer; optional pr-agent/open-code-review/stage-cli | Other exact-pinned review tools |
| Security | Semgrep, SkillSpector, TruffleHog, sandbox tests | Other scanners through bounded adapters |
| Sandboxing | gVisor; Firecracker for high risk | Other host-certified isolation profiles |
| DevOps | OpenTofu, GitHub Actions, Ansible | Other bounded infrastructure adapters |
| Planning/PM | ccpm, GitHub Issues, YouTrack | Plane, OpenProject, and other providers |
| Identity/secrets | AIAT identity/credentials, Stalwart, Resend | ZITADEL, Vault, OpenBao, cloud KMS |
| Monitoring | LiteLLM, OmniRoute, AIAT health/metrics | Prometheus-compatible tools and Grafana if useful |
| Data/memory | Postgres/pgvector, Redis, MinIO | SeaweedFS, Garage, Neo4j, Letta, Qdrant, Temporal |
| Protocols | MCP SDKs and individually configured servers | Other explicit API/CLI adapters |

Technical defaults may remain narrow to keep the personal stack simple. An
alternative is not prohibited merely because it is not enabled in the default
Compose profile.

## Metadata location

Third-party metadata belongs in:

- `mas/docs/provenance/third_party_components.yaml` for machine-readable
  version, source, integration, licence, notices, and restriction fields;
- `THIRD_PARTY_NOTICES.md` for the human-readable metadata policy and pointers;
- worker/runtime manifests for exact active version and source provenance.

Licence detail should not be repeated throughout feature specifications,
roadmaps, security gates, hiring flows, or normal operator workflows. Those
documents may link to the metadata catalogue when the detail is relevant.
