# OpenHands v1.43.0 offline security triage

This note records an offline, applicability-aware review of the retained
certification artifact from GitHub Actions run `32594885180`. It does not
alter the raw artifact, activate the worker, accept a finding, or dispatch a
new certification run.

This is an immutable historical triage snapshot. Subsequent repository-local
hardening commits materialized the bridge/gateway and run-scoped profile path;
the current readiness state is maintained in `MORNING_PREFLIGHT.md` and the
release ledger. The historical counts and dispositions below remain unchanged.

| Field | Value |
| --- | --- |
| Run | `32594885180` |
| AIAT candidate | `9db4665f51f3677db0d2f8bb036912075d402847` |
| OpenHands candidate | v1.43.0, `4c1237f391fe394e9f67505fe3a0bd2d81f84188` |
| OCI image | `ghcr.io/openhands/agent-server:1.43.0-python@sha256:36f847d1dfbbbdce90052437b06a3c6e76b8a54683228182eaf73085f03fcd97` |
| Raw artifact | `openhands-candidate-certification-32594885180` |
| Raw evidence changed | No |
| Worker activation | Inactive |

## Normalized security state

The tools ran and their raw outputs are retained, but the security result is
not a clean complete scan. Semgrep has non-runtime parser/internal errors and
SkillSpector reports partial analysis. Raw matches are not exploitability
verdicts.

```text
SECURITY_SCAN_COMPLETE=NO (execution complete; evidence coverage is partial)
SECURITY_SCAN_COVERAGE_INCOMPLETE=YES
SECURITY_FINDINGS_UNTRIAGED=NO (all retained hits have a recorded applicability disposition; 76 remain unresolved candidates because analysis coverage is partial)
SECURITY_ACTIONABLE_FINDING=0 confirmed runtime findings
OPERATOR_REVIEW_REQUIRED=YES (candidate remains fail-closed and steward/configuration are pending)
```

## Semgrep

The retained output contains 173 findings, 97 errors, 93 coverage/parsing
errors, and 4 internal matching errors. Every affected path is CI/release,
documentation/example, or build/install code; the report identifies zero
runtime source paths with incomplete coverage.

```text
SEMGREP_RUNTIME_COVERAGE_COMPLETE=YES
SEMGREP_RUNTIME_PATHS_UNCOVERED=none identified in retained evidence
SEMGREP_ACTIONABLE_RUNTIME_FINDINGS=0 confirmed
```

The 93 partial-parsing errors are accounted for below. Counts are error
instances, not unique files.

| Error class | Count | Paths and classification | Alternate supported mode |
| --- | ---: | --- | --- |
| Embedded GitHub-expression/YAML snippets parsed as Bash | 16 | `.github/workflows/create-release.yml` (16); CI/release | YAML structural scan plus extracted Bash |
| Embedded GitHub-expression/YAML snippets parsed as Bash | 10 | `.github/workflows/version-bump-prs.yml` (10); CI/release | YAML structural scan plus extracted Bash |
| Embedded GitHub-expression/YAML snippets parsed as Bash | 10 | `.github/workflows/server.yml` (10); CI/release | YAML structural scan plus extracted Bash |
| Embedded GitHub-expression/YAML snippets parsed as Bash | 8 | `.github/workflows/integration-runner.yml` (8); CI/release | YAML structural scan plus extracted Bash |
| Embedded GitHub-expression/YAML snippets parsed as Bash | 6 | `.github/workflows/issue-readiness-check.yml` (6); CI/release | YAML structural scan plus extracted Bash |
| Embedded GitHub-expression/YAML snippets parsed as Bash | 6 | `.github/workflows/prepare-release.yml` (6); CI/release | YAML structural scan plus extracted Bash |
| Embedded GitHub-expression/YAML snippets parsed as Bash | 6 | `.github/workflows/pypi-release.yml` (6); CI/release | YAML structural scan plus extracted Bash |
| Embedded GitHub-expression/YAML snippets parsed as Bash | 6 | `.github/workflows/todo-management.yml` (6); CI/release | YAML structural scan plus extracted Bash |
| Embedded GitHub-expression/YAML snippets parsed as Bash | 6 | `examples/03_github_workflows/03_todo_management/workflow.yml` (6); documentation/example | YAML structural scan plus extracted Bash |
| Embedded GitHub-expression/YAML snippets parsed as Bash | 4 | `.github/workflows/release-binaries.yml` (4); CI/release | YAML structural scan plus extracted Bash |
| Embedded GitHub-expression/YAML snippets parsed as Bash | 4 | `.github/workflows/run-eval.yml` (4); CI/release | YAML structural scan plus extracted Bash |
| Embedded GitHub-expression/YAML snippets parsed as Bash | 4 | `.github/workflows/security-scan.yml` (4); CI/release | YAML structural scan plus extracted Bash |
| Embedded GitHub-expression/YAML snippets parsed as Bash | 4 | `.github/workflows/tests.yml` (4); CI/release | YAML structural scan plus extracted Bash |
| Embedded GitHub-expression/YAML snippets parsed as Bash | 2 | `.github/workflows/issue-duplicate-checker.yml` (2); CI/release | YAML structural scan plus extracted Bash |
| Source syntax unsupported by the selected mode | 1 | `openhands-agent-server/openhands/agent_server/docker/Dockerfile`; build/install | Dockerfile-specific Semgrep mode |

The four `SCANNER_EXECUTION_FAILURE` instances are internal matching errors
for the `curl-eval` and `gha-curl-pipe-shell` rules: two in
`.github/workflows/run-examples.yml` and two in
`.github/workflows/version-bump-prs.yml`. They are CI/release-only. No MDX,
generated-artifact, corrupted/unreadable-source, or other error class was
identified. The alternate modes above were not executed in this retained run,
so the global Semgrep result remains coverage-incomplete; this does not leave
a shipped OpenHands runtime path uncovered.

## TruffleHog

There are 56 detections, all unverified: 46 test fixtures and 10 runtime
credential-like matches. No raw value is retained or printed. All ten runtime
paths are source-only and absent from the pinned Agent Server image.

| Path | Lines | Detector | Classification | Image value |
| --- | --- | --- | --- | --- |
| `openhands-sdk/openhands/sdk/plugin/types.py` | 131 | URI | FALSE_POSITIVE (security-note prose) | absent |
| `openhands-sdk/openhands/sdk/plugin/types.py` | 133 | URI | SYNTHETIC_OR_PLACEHOLDER (`TOKEN` example) | absent |
| `openhands-agent-server/openhands/agent_server/file_router.py` | 195 | URI | FALSE_POSITIVE (cache-directory name) | absent |
| `openhands-agent-server/openhands/agent_server/file_router.py` | 205 | URI | SYNTHETIC_OR_PLACEHOLDER (tokenized clone URI) | absent |
| `openhands-sdk/openhands/sdk/utils/redact.py` | 136 | URI | FALSE_POSITIVE (exception/body-length code) | absent |
| `openhands-sdk/openhands/sdk/utils/redact.py` | 142 | URI | SYNTHETIC_OR_PLACEHOLDER (generic docstring example) | absent |
| `openhands-sdk/openhands/sdk/utils/redact.py` | 148 | URI | NON_SECRET_IDENTIFIER (`${VAR}` reference) | absent |
| `openhands-sdk/openhands/sdk/utils/redact.py` | 154 | URI | SYNTHETIC_OR_PLACEHOLDER (`SECRET` example) | absent |
| `openhands-sdk/openhands/sdk/utils/redact.py` | 178 | URI | FALSE_POSITIVE (credential-matching regex) | absent |
| `openhands-sdk/openhands/sdk/utils/redact.py` | 186 | URI | SYNTHETIC_OR_PLACEHOLDER (`SECRET` example) | absent |

```text
TRUFFLEHOG_VERIFIED_SECRETS=0
TRUFFLEHOG_RUNTIME_ACTIONABLE=0 confirmed
```

This classification is not a risk acceptance; a future source/image change
must repeat the image cross-check.

## SkillSpector

SkillSpector produced 363 raw findings. Its retained execution was successful
but analysis completeness was `partial` (1,477 components inspected with
0.0% complete-analysis coverage). The applicability classifier recorded 287
non-runtime/not-applicable findings and 76 runtime candidates:

```text
RUNTIME_CRITICAL=1
RUNTIME_HIGH=10
RUNTIME_MEDIUM=41
RUNTIME_OTHER=24
```

For every row below, the retained image cross-check says
`SOURCE_ONLY`, `IMAGE_PRESENT_REACHABLE=NO`, and no concrete exploit path was
demonstrated by the retained evidence. A profile-disabled component is marked
as such; a source-only core component is not being called mitigated merely
because a sandbox would exist in a future deployment. Every row therefore has
the review disposition `NON_ACTIONABLE_WITH_EVIDENCE` for this candidate, not
`ACCEPTED` or `PASS`.

| Rule | Affected path | Severity | Occurrences / lines | Current boundary/disposition |
| --- | --- | --- | --- | --- |
| AST7 | `openhands-agent-server/openhands/agent_server/_secrets_exposure.py` | LOW | 1 / 97 | Source-only; no current path; review-only |
| AST4 | `openhands-agent-server/openhands/agent_server/desktop_service.py` | MEDIUM | 1 / 190-196 | Desktop disabled; source-only; review-only |
| AST4 | `openhands-agent-server/openhands/agent_server/docker/build.py` | MEDIUM | 1 / 172-179 | Build path source-only; review-only |
| TT2 | `openhands-agent-server/openhands/agent_server/docker/build.py` | MEDIUM | 2 / 1088,1154 | Build path source-only; review-only |
| AST7 | `openhands-agent-server/openhands/agent_server/env_parser.py` | LOW | 2 / 160,332 | Source-only; no current path; review-only |
| E2 | `openhands-agent-server/openhands/agent_server/env_parser.py` | HIGH | 1 / 134 | Source-only; no current path; review-only |
| E4 | `openhands-agent-server/openhands/agent_server/event_service.py` | HIGH | 1 / 1217 | Source-only; no current path; review-only |
| AST4 | `openhands-agent-server/openhands/agent_server/file_router.py` | MEDIUM | 3 / 386-393,394-401,405-413 | Source-only; no current path; review-only |
| E2 | `openhands-agent-server/openhands/agent_server/file_router.py` | HIGH | 1 / 379 | Source-only; no current path; review-only |
| E3 | `openhands-agent-server/openhands/agent_server/file_router.py` | MEDIUM | 1 / 723 | Source-only; no current path; review-only |
| AST7 | `openhands-agent-server/openhands/agent_server/provider_connections_router.py` | LOW | 2 / 73,219 | Provider connections unavailable; source-only; review-only |
| AST4 | `openhands-agent-server/openhands/agent_server/skills_service.py` | MEDIUM | 1 / 145-158 | Public skills disabled; source-only; review-only |
| AR1 | `openhands-sdk/openhands/sdk/agent/acp_agent.py` | HIGH | 1 / 2634 | ACP/sub-agent path disabled; source-only; review-only |
| AST7 | `openhands-sdk/openhands/sdk/agent/acp_agent.py` | LOW | 1 / 4199 | ACP/sub-agent path disabled; source-only; review-only |
| AST7 | `openhands-sdk/openhands/sdk/agent/acp_models.py` | LOW | 1 / 64 | ACP/sub-agent path disabled; source-only; review-only |
| AST7 | `openhands-sdk/openhands/sdk/agent/base.py` | LOW | 2 / 786,797 | Source-only; no current path; review-only |
| AST7 | `openhands-sdk/openhands/sdk/conversation/state.py` | LOW | 1 / 593 | Source-only; no current path; review-only |
| AST7 | `openhands-sdk/openhands/sdk/event/acp_tool_call.py` | LOW | 1 / 40 | ACP/sub-agent path disabled; source-only; review-only |
| AST4 | `openhands-sdk/openhands/sdk/git/utils.py` | MEDIUM | 1 / 29-37 | Source-only; scoped workspace required; review-only |
| AST7 | `openhands-sdk/openhands/sdk/hooks/config.py` | LOW | 2 / 330,387 | Source-only; no current path; review-only |
| AST4 | `openhands-sdk/openhands/sdk/hooks/executor.py` | MEDIUM | 3 / 102-107,506-516,545-554 | Source-only; scoped worker boundary required; review-only |
| E1 | `openhands-sdk/openhands/sdk/llm/auth/openai.py` | MEDIUM | 1 / 227 | Direct provider credentials disabled; source-only; review-only |
| E1 | `openhands-sdk/openhands/sdk/llm/llm.py` | MEDIUM | 1 / 728 | AIAT model governance required; source-only; review-only |
| E2 | `openhands-sdk/openhands/sdk/llm/llm.py` | HIGH | 1 / 3157 | Model switching disabled; source-only; review-only |
| AST7 | `openhands-sdk/openhands/sdk/llm/message.py` | LOW | 1 / 560 | Source-only; no current path; review-only |
| AST7 | `openhands-sdk/openhands/sdk/llm/mixins/non_native_fc.py` | LOW | 1 / 101 | Source-only; no current path; review-only |
| AST7 | `openhands-sdk/openhands/sdk/llm/router/base.py` | LOW | 1 / 117 | AIAT model routing required; source-only; review-only |
| AST7 | `openhands-sdk/openhands/sdk/marketplace/__init__.py` | LOW | 2 / 59,61 | Marketplace disabled; source-only; review-only |
| AST7 | `openhands-sdk/openhands/sdk/mcp/__init__.py` | LOW | 3 / 28,32,36 | Arbitrary MCP disabled; fixed AIAT bridge only; review-only |
| AST7 | `openhands-sdk/openhands/sdk/observability/laminar.py` | LOW | 1 / 499 | Source-only; no current path; review-only |
| AS2 | `openhands-sdk/openhands/sdk/plugin/format/claude_code.py` | HIGH | 2 / 107,138 | Plugins disabled; source-only; review-only |
| E1 | `openhands-sdk/openhands/sdk/security/grayswan/analyzer.py` | MEDIUM | 1 / 66 | Source-only; no current path; review-only |
| AST7 | `openhands-sdk/openhands/sdk/settings/__init__.py` | LOW | 1 / 136 | Source-only; no current path; review-only |
| AST7 | `openhands-sdk/openhands/sdk/settings/model.py` | LOW | 1 / 2167 | Model controls remain AIAT-owned; source-only; review-only |
| RP1 | `openhands-sdk/openhands/sdk/settings/model.py` | MEDIUM | 3 / 1209,1766,1794 | Model controls remain AIAT-owned; source-only; review-only |
| AST4 | `openhands-sdk/openhands/sdk/skills/execute.py` | MEDIUM | 1 / 66-73 | Public skills disabled; source-only; review-only |
| AS2 | `openhands-sdk/openhands/sdk/skills/skill.py` | HIGH | 1 / 423 | Public skills disabled; source-only; review-only |
| AS3 | `openhands-sdk/openhands/sdk/skills/skill.py` | MEDIUM | 1 / 855 | Public skills disabled; source-only; review-only |
| AST4 | `openhands-sdk/openhands/sdk/skills/utils.py` | MEDIUM | 1 / 367-381 | Public skills disabled; source-only; review-only |
| AST4 | `openhands-sdk/openhands/sdk/utils/command.py` | MEDIUM | 1 / 82-91 | Scoped terminal only; source-only; review-only |
| E2 | `openhands-sdk/openhands/sdk/utils/command.py` | HIGH | 1 / 42 | Scoped terminal only; source-only; review-only |
| AST4 | `openhands-sdk/openhands/sdk/workspace/repo.py` | MEDIUM | 2 / 352-357,383-385 | AIAT workspace grant required; source-only; review-only |
| RP1 | `openhands-tools/openhands/tools/browser_use/impl.py` | MEDIUM | 1 / 231 | Browser disabled; source-only; review-only |
| AST4 | `openhands-tools/openhands/tools/file_editor/utils/shell.py` | MEDIUM | 2 / 35-42,69-75 | Scoped file editor only; source-only; review-only |
| AST4 | `openhands-tools/openhands/tools/glob/impl.py` | MEDIUM | 1 / 162-169 | Scoped workspace required; source-only; review-only |
| AST4 | `openhands-tools/openhands/tools/grep/impl.py` | MEDIUM | 2 / 241-248,260-267 | Scoped workspace required; source-only; review-only |
| AST4 | `openhands-tools/openhands/tools/terminal/terminal/factory.py` | MEDIUM | 2 / 20-26,44-50 | Scoped terminal only; source-only; review-only |
| AST4 | `openhands-tools/openhands/tools/terminal/terminal/subprocess_terminal.py` | MEDIUM | 1 / 161-172 | gVisor/scoped terminal required; source-only; review-only |
| AST4 | `openhands-tools/openhands/tools/terminal/terminal/windows_terminal.py` | MEDIUM | 2 / 109-120,386-394 | Non-native path; source-only; review-only |
| AST4 | `openhands-tools/openhands/tools/utils/__init__.py` | MEDIUM | 1 / 24-30 | Source-only; no current path; review-only |
| AST1 | `openhands-tools/openhands/tools/workflow/impl.py` | HIGH | 1 / 432 | Workflow tool not granted; source-only; review-only |
| AST6 | `openhands-tools/openhands/tools/workflow/impl.py` | MEDIUM | 1 / 432 | Workflow tool not granted; source-only; review-only |
| AST8 | `openhands-tools/openhands/tools/workflow/impl.py` | CRITICAL | 1 / 432 | Workflow tool not granted; source-only; review-only |
| AST4 | `openhands-workspace/openhands/workspace/apptainer/workspace.py` | MEDIUM | 1 / 318-324 | Apptainer workspace unavailable; source-only; review-only |
| AST4 | `openhands-workspace/openhands/workspace/docker/workspace.py` | MEDIUM | 1 / 292-297 | AIAT supplies gVisor workspace; source-only; review-only |
| RP1 | `openhands-workspace/openhands/workspace/docker/workspace.py` | MEDIUM | 1 / 212 | AIAT supplies gVisor workspace; source-only; review-only |

```text
SKILLSPECTOR_RUNTIME_REACHABLE=0 confirmed
SKILLSPECTOR_ACTIONABLE_HIGH=0 confirmed (11 critical/high candidates remain review-only)
SKILLSPECTOR_ACTIONABLE_MEDIUM=0 confirmed (41 candidates remain review-only)
SKILLSPECTOR_NON_ACTIONABLE_WITH_EVIDENCE=287 non-runtime + 24 low/runtime source-only candidates
```

The 76 rows are not silently discarded. If a future image contains any of
these paths, or the AIAT profile enables one of the disabled capabilities, the
candidate must be rescanned and the reachability decision revisited.

## Deployed-image cross-check

The detailed path cross-check survives this triage:

```text
SOURCE_ONLY=129
IMAGE_PRESENT_NOT_REACHABLE=2
IMAGE_PRESENT_REACHABLE=0
AIAT_WRAPPER_MITIGATED=0
```

The two image-present paths are common `AGENTS.md`/`Makefile` artifacts, not
OpenHands runtime source. This is a reachability result for the pinned digest,
not a claim that source-only matches are intrinsically safe.

## Required operator configuration

No fake identifiers or credentials were created.

The following table is retained as the offline triage record. Its historical
profile row is superseded for disposable certification by the current
run-scoped materialization documented in `agent-profile-spec.yaml` and
`MORNING_PREFLIGHT.md`; no portable profile UUID is required.

| Name | Secret? | Who creates it | Storage | Expected format | AIAT object/reference | Validation |
| --- | --- | --- | --- | --- | --- | --- |
| `AIAT_TOOL_SECRET` | Yes | Operator through the AIAT secret boundary | Governed secret manager / GitHub Actions secret; never evidence | Opaque high-entropy token | Tool-service/OpenHands MCP bridge shared secret; never caller-supplied | Secret-boundary and bridge authentication test; never print the value |
| `OPENHANDS_AGENT_PROFILE_ID` | No | AIAT certification workflow materializes an OpenHands Agent Server profile | Run-scoped workflow output; never a persistent variable | Server-generated profile UUID | Governed profile name/pins resolve to the exact model, bridge, workspace, and disabled controls | Agent Server profile/readiness and exact-control check |
| `OPENHANDS_MCP_SETTINGS_KEY` | No | AIAT adapter/control plane creates one disposable entry after authorization | Run-scoped adapter metadata / environment variable; delete on close | `aiat-openhands-<run-scoped>` (must use the adapter-required prefix; no spaces) | Manifest `aiat_mcp_profile_ref`, pointing to fixed `http://tool-service:8002/openhands/mcp` | MCP settings POST/DELETE, collision refusal, and zero-residue test |
| `OPENHANDS_MODEL_ID` | No | AIAT model catalogue/steward | Governed model profile/version reference or environment variable | Exact registered model ID; not `auto`, `default`, `latest`, or a raw unmanaged provider choice | Approved AIAT model profile/snapshot resolved server-side | Catalogue readiness plus Agent Server profile/server-info exact-model check |

`OPENHANDS_SESSION_API_KEY` is generated per certification run by the Agent
Server adapter and was already present/working in run `32594885180`; it is not
an outstanding operator configuration object.

## Steward approval

The pending object is
`mas/docs/provenance/openhands-candidate/2026-08-22-v1.43.0/interface-verification.json`
(`report_id=openhands-agent-server-v1.43.0-2026-08-22`). It pins v1.43.0,
the upstream commit, packages, and image digest. Its current state is
`approval_status=PENDING`, `approved=false`.

The legitimate transition is `PENDING -> APPROVED` with `approved=true`,
using the steward/human decision record. The pending report still blocks
normal production construction, but it no longer blocks the isolated
certification path: the trusted certification controller issues a separate,
run-scoped authorization bound to the exact candidate pins, worker identity,
controller run, and gVisor/runsc claims. A passed certification remains
`CERTIFYING`/inactive; the pending interface report and independent steward
approval are reviewed before activation. The enforcing gates are
`OpenHandsAgentServerAdapter`'s production approval check, its trusted
`for_certification` factory, and the steward candidate/activation lifecycle.

The available governed API operations are the worker steward/candidate
read, generate, certify, approve, and stage endpoints in the orchestrator
OpenAPI (`/capabilities/workers/{worker_id}/steward/...`). The read-only
`mas/scripts/check_worker_steward_readiness.py` command can inspect the exact
worker/candidate IDs. No approval or mutation was performed during this
triage, and no UUID was invented.

## Dispatch readiness

```text
OPENHANDS_SECURITY_CONFIRMED_RUNTIME_FINDINGS=0
OPENHANDS_SECURITY_UNRESOLVED_RUNTIME_CANDIDATES=76
SEMGREP_RUNTIME_COVERAGE_COMPLETE=YES
BRIDGE_CONFIGURATION_READY=NO
STEWARD_APPROVAL_READY=NO
LOCAL_VALIDATION=PASS (existing adapter/bridge fixture tests)
MATERIAL_CHANGE_SINCE_RUN_32594885180=NO
READY_TO_DISPATCH=NO
WHY=The retained evidence has no unresolved artifact-processing defect that
requires an immediate rerun, but global scanner coverage is incomplete,
SkillSpector has 76 source-only runtime candidates without a complete
analysis, required bridge configuration is absent, and interface steward
approval is still pending. Per the execution policy, no GitHub Actions run is
triggered.
```
