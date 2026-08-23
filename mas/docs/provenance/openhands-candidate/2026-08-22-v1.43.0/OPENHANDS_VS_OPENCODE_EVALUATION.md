# OpenHands Agent Server candidate evaluation

Status: parallel candidate only. OpenCode remains unchanged and remains the
current adapter baseline. The OpenHands worker is inactive because this record
contains interface and fixture evidence, not a completed governed
certification.

## Candidate pin

| Field | Value |
| --- | --- |
| Repository | `https://github.com/OpenHands/software-agent-sdk.git` |
| Release/tag | `v1.43.0` |
| Full source commit | `4c1237f391fe394e9f67505fe3a0bd2d81f84188` |
| Packages | `openhands-sdk`, `openhands-tools`, `openhands-workspace`, `openhands-agent-server` all `1.43.0` |
| OCI image | `ghcr.io/openhands/agent-server:1.43.0-python` |
| OCI index digest | `sha256:36f847d1dfbbbdce90052437b06a3c6e76b8a54683228182eaf73085f03fcd97` |
| OCI amd64 digest | `sha256:c826bcfa6455267d8f99fe277d97d00806bc0f90bf263b94268cab29fa7be529` |
| Lockfile SHA-256 | `553caab1d6936ef86559f93eb7e6cc464a84810df832bc6070a5c456052df57f` |
| Interface record | [interface-verification.json](./interface-verification.json) |

The release and source are pinned from the official SDK repository and release
record. The adapter does not accept a floating tag or image. It also verifies
that a server-reported package version matches the selected release and that
the selected model profile returns the AIAT-resolved exact model ID.

Official interface references:

- [v1.43.0 source tree](https://github.com/OpenHands/software-agent-sdk/tree/v1.43.0)
- [Agent Server conversation routes](https://github.com/OpenHands/software-agent-sdk/blob/v1.43.0/openhands-agent-server/openhands/agent_server/conversation_router.py)
- [SDK remote conversation client](https://github.com/OpenHands/software-agent-sdk/blob/v1.43.0/openhands-sdk/openhands/sdk/conversation/impl/remote_conversation.py)
- [v1.43.0 release](https://github.com/OpenHands/software-agent-sdk/releases/tag/v1.43.0)

## Exact OpenCode to OpenHands substitution map

| AIAT contract | Existing OpenCode shape | OpenHands v1.43.0 shape | Candidate adapter decision |
| --- | --- | --- | --- |
| Start task | OpenCode session create | `POST /api/conversations` with `agent_profile_id`, `LocalWorkspace`, and `initial_message` | AIAT supplies the profile and workspace; task input supplies only bounded prompt text |
| Execute | OpenCode prompt/session loop | `POST /api/conversations/{id}/run` | Start after conversation creation; poll status for terminal state |
| Status | OpenCode session/status API | `GET /api/conversations/{id}` and `execution_status` | Normalize only scalar status and metrics |
| Graceful cancellation | OpenCode abort/session control | `POST /api/conversations/{id}/pause` | Preserve resumable conversation; AIAT owns resulting run transition |
| Immediate cancellation | OpenCode abort | `POST /api/conversations/{id}/interrupt` | Interrupt in-flight LLM call; server leaves conversation paused/resumable |
| Resume | OpenCode session continuation | `POST /api/conversations/{id}/run` after pause | AIAT verifies conversation identity and workspace before resume |
| Result | OpenCode message/session result | `GET /api/conversations/{id}/agent_final_response` | Return final response plus AIAT-normalized usage and artifacts |
| Event stream | OpenCode SSE events | WebSocket `/sockets/events/{id}`, first-message session-key auth | Convert events to bounded progress/audit records; never retain raw payloads |
| Workspace | OpenCode directory query/session scope | `LocalWorkspace.working_dir` in conversation request | Workspace is taken only from `AdapterContext.workspace_path` |
| Artifacts | OpenCode diff plus local file reads | `GET /api/git/changes`, streamed `/api/file/download` | Hash changed files in memory and register `WorkerArtifact`; do not retain payloads |
| Health | OpenCode health endpoint/schema check | `GET /health` | Authenticated scalar health result |
| Readiness | OpenCode OpenAPI/config digest | `GET /ready` and `GET /server_info` | Check server readiness, package pin, profile/bridge/workspace/model bindings |

The official SDK has no separate `/resume` route in this release; the
documented resume operation is another `/run` call on a paused conversation.
The candidate does not infer undocumented endpoints.

## AIAT governance boundary

OpenHands remains below the AIAT control plane. AIAT retains authority over:

- activation, steward approval, immutable source/image pointers, and rollback;
- permissions, workspace grants, credentials, budgets, approvals, and audit
  persistence;
- exact model routing and the resolved model snapshot;
- sandbox profile, workspace path, network policy, cleanup, and artifact
  retention.

The candidate selects an AIAT-provisioned `agent_profile_id`; during disposable
certification this is a server-generated, run-scoped UUID materialized from the
governed profile specification. It does not send an `agent` object, API key,
model key, plugin list, or task-supplied path. The profile must reference the
approved AIAT MCP bridge. OpenHands built-ins
are limited to sandbox-local terminal/file editing/test capabilities. Browser,
subagent, plugin, public-skill, model-switching, direct-credential, and
arbitrary external MCP capabilities are not implicitly enabled; the operator
profile must explicitly report each of those controls disabled.

The official `ClientToolSpec` path is not used for privileged tools: its
documented executor acknowledges a client action rather than providing a
synchronous server-side authority path. Privileged operations therefore need a
separately certified AIAT MCP/custom-tool bridge with run-scoped grants. The
candidate uses only the documented Agent Server settings MCP route to add one
disposable `aiat-openhands-*` entry pointing at the fixed internal `/openhands`
bridge; an existing key is never overwritten and the entry is deleted during
cleanup. The bridge is required before readiness can pass.

AIAT `BudgetTracker` remains authoritative. OpenHands metrics are scalar
evidence only. The adapter emits normalized progress/audit events and excludes
prompts, tool arguments, file contents, credentials, and raw event payloads
from evidence.

## Workspace, sandbox, recovery, and cleanup

The intended deployment is a disposable gVisor container/volume with the AIAT
workspace mounted at the exact server-visible path, network restricted to the
approved gateway/tool-service allowlist, read-only root where supported, and
no host-path escape. The adapter rejects a missing or non-absolute workspace
and rejects artifact paths that resolve outside it.

Sandbox compatibility is not yet proven. OpenHands Agent Server requires its
Python/runtime and shell dependencies to execute inside the profile; the
candidate must pass a real gVisor run before any certification decision.

Graceful pause, immediate interrupt, and `/run` resume are interface-compatible
with a semantic caveat: OpenHands leaves an interrupted conversation paused,
while AIAT may make the worker run terminal. Recovery is therefore conditional
on a live restart/persistence test proving that the conversation and disposable
workspace can be reconciled without duplicate execution. AIAT owns retries,
timeouts, cancellation state, and final cleanup.

## Required certification gates

The candidate is `NOT_RUN` for the complete governed suite. All of the
following are required before activation:

1. SBOM with exact source/image provenance.
2. Security scans with tool versions, configuration, raw JSON/SARIF/logs,
   scanner errors, and exit statuses retained.
3. AIAT local governance-boundary tests.
4. Real gVisor execution.
5. Isolated workspace binding.
6. A real coding task.
7. Verified file modifications.
8. Test execution inside the governed workspace.
9. Artifact capture and hash/registration semantics.
10. Graceful pause.
11. Immediate interrupt.
12. Resume.
13. Forced runtime failure.
14. Recovery after failure/restart.
15. Adapter timeout.
16. AIAT budget enforcement.
17. Forbidden-tool attempt blocked by the AIAT bridge/profile.
18. Cross-workspace isolation.
19. Secret non-disclosure in output/events/artifacts/logs.
20. Zero-residue conversation/container/network/volume cleanup.

Any missing scanner coverage, parser error, unavailable host/runtime, failed
cleanup assertion, or unverified bridge remains a certification blocker. No
finding is accepted or waived by this evaluation.

## Comparison with the pinned OpenCode candidate

| Dimension | OpenHands candidate | OpenCode baseline | Evaluation state |
| --- | --- | --- | --- |
| Integration complexity | REST + WebSocket + workspace/file/git normalization; profile-based server setup | Existing session/event/permission/MCP adapter | OpenHands is a new adapter, not a drop-in |
| Code reuse | AIAT base adapter, controller, event normalization, artifact/audit/budget contracts remain reusable | Existing OpenCode-specific code is not reused for runtime calls | Approx. 65% of adapter architecture, not a measured LOC claim |
| Tool boundary | Requires AIAT MCP bridge in server profile; ClientTool is insufficient for authority | Existing run-scoped OpenCode MCP bridge | Both require a certified bridge |
| Cancellation | Pause and interrupt are explicit and resumable | OpenCode abort/session semantics already implemented | OpenHands mapping is promising but needs live timing tests |
| Recovery | Persisted conversation can be rerun after pause/error | Existing OpenCode session reconciliation | OpenHands recovery is unverified |
| Artifacts | Git changes plus streamed file download; easy to hash without retaining contents | Session diff plus local workspace reads | Different implementation, same AIAT artifact contract |
| Events | Typed WebSocket stream with unknown-event tolerance | OpenCode SSE event stream | Both normalize into AIAT events; raw payloads remain excluded |
| Sandbox | Requires live gVisor certification of Agent Server dependencies | Existing OpenCode gVisor evidence path | Not yet comparable from live evidence |
| Security | New source/image/SBOM/scan evidence required | OpenCode v1.18.21 evidence remains separate and currently not interpretable | No automatic security conclusion |
| Cost/latency | Must be measured with identical task/model/budget wave | Existing baseline measurements | Not run |
| Maintenance | Upstream SDK and Agent Server release cadence; profile/API pinning | Existing OpenCode adapter and runtime pin | OpenHands adds a second external surface |
| Update risk | Package set, image, profile schema, and WebSocket/REST contracts must move together | OpenCode endpoint/schema evidence | Both require steward re-certification |

## Decision fields

```text
OPENHANDS_INTEGRATION_FEASIBLE=CONDITIONALLY_FEASIBLE_PARALLEL_CANDIDATE
OPENHANDS_CANDIDATE=OpenHands Software Agent SDK + Agent Server v1.43.0 @ 4c1237f391fe394e9f67505fe3a0bd2d81f84188; OCI index sha256:36f847d1dfbbbdce90052437b06a3c6e76b8a54683228182eaf73085f03fcd97
OPENHANDS_LICENSE_SCOPE=MIT SDK/core/Agent Server metadata only; OpenHands Cloud and enterprise components excluded
OPENCODE_ADAPTER_CODE_REUSABLE_PERCENT=approximately 65% of adapter architecture (estimate, not LOC measurement)
NEW_FILES_REQUIRED=parallel adapter; adapter contract tests; pinned interface report; inactive candidate manifest; evaluation/comparison record
FILES_REQUIRING_CHANGE=only the new candidate files and third-party metadata entry; existing OpenCode adapter/manifests remain unchanged
TOOL_SERVICE_BRIDGE_REQUIRED=YES
SANDBOX_COMPATIBILITY=UNVERIFIED_GVISOR_REQUIRED
CANCELLATION_COMPATIBILITY=MAPPABLE_WITH_SEMANTIC_CAVEAT
RECOVERY_COMPATIBILITY=MAPPABLE_BUT_UNVERIFIED
CERTIFICATION_REQUIREMENTS=all 20 gates listed above, with retained structured evidence and no waived blockers
EXPECTED_MIGRATION_DIFFICULTY=HIGH
OPENHANDS_VS_OPENCODE_RECOMMENDATION=Keep OpenCode unchanged; continue OpenHands as an inactive parallel candidate and do not replace the default until the complete governed certification passes
```
