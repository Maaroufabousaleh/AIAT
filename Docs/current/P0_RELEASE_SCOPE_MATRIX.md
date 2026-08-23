# P0 Release-Scope and External-Prerequisite Matrix

**Updated:** 2026-08-23
**Decision owner:** personal operator
**Authority:** [AIAT target programme](../../AIAT_TARGET_PROGRAMME.md), [roadmap](../../ROADMAP.md), [P0 status](P0_RELEASE_INTEGRITY_STATUS.md), and [P0 plan](plans/P0_RELEASE_INTEGRITY_PLAN.md)

This is the operator decision boundary for the current P0 release. It turns
external and conditional prerequisites into an explicit scope without
inventing live evidence. It is a documentation-only change: it does not alter
the release ledger, activate a capability, change deployment state, or waive a
third-party obligation.

## Frozen state

```text
CURRENT_STATE=BLOCKED_EXTERNAL_OPERATOR_STATE
CODE_BASELINE=CURRENT_AND_PASSING
FURTHER_IDENTICAL_AUDITS=NOT_REQUIRED
GLOBAL_RELEASE_DECISION=NO-RELEASE
```

The latest host-safe local Compose ledger remains a scalar summary of 79/85
passes, 0 failures, 6 blocked checks, and 4 pending items. The retained
release evidence is [`release_ledger_live_compose_local_current.json`](../../mas/docs/provenance/release_ledger_live_compose_local_current.json).
The aggregate is intentionally not reinterpreted by this matrix; a later
ledger commit may reclassify rows only after the operator signs the scope
choices below.

The corrected MinIO/SeaweedFS resource wave is complete: both providers pass
the identical bounded workload, checksum/read-back, multipart, abort, scalar
resource sampling, and zero-residue cleanup assertions. The nonexistent
network-alias attempt is `TEST-HARNESS/EXECUTION INVALID` and is excluded from
provider evidence and provider verdicts. Retained scalar evidence is in
[`object_store_resource_profile_provider_diverse_evidence.json`](../../mas/docs/provenance/object_store_resource_profile_provider_diverse_evidence.json),
[`object_store_multipart_provider_diverse_evidence.json`](../../mas/docs/provenance/object_store_multipart_provider_diverse_evidence.json),
and [`object_store_provider_outage_live_evidence.json`](../../mas/docs/provenance/object_store_provider_outage_live_evidence.json).

The software-side certification boundaries added in this wave are explicitly
fail-closed. The manual
[`native-linux-gvisor-certification.yml`](../../.github/workflows/native-linux-gvisor-certification.yml)
workflow is eligible for the canonical native-Linux interpretation because the
target programme does not require a persistent host; retained run `32541110299`
proves native Linux, `runsc` registration, digest-pinned smoke, sandbox,
cleanup, and zero residue. Hardening `d012ab9` now rejects mutable image inputs
and binds the checked-out commit to the requested candidate SHA before probes.
The separate WSL/native release-host preflight and broader native release
checks remain open. The OCI adapter's deterministic contract
passes through [`check_object_store_oci_sse_kms.py`](../../mas/scripts/check_object_store_oci_sse_kms.py)
and the release ledger; live configuration now fails closed unless
`OBJECT_STORE_ENCRYPTION_MODE=SSE_KMS` is explicit. The live OCI target is
still operator-owned. The
historical OpenCode 1.17.13 scan is retained only as
`FAILED_UNREPRODUCIBLE`; the fresh v1.18.21 candidate has immutable source and
image provenance plus a passing AIAT boundary regression, but its local run is
blocked by missing scanner/SBOM tools. The certification workflow now
provisions the exact Semgrep 1.168.0, TruffleHog 3.97.0, pinned SkillSpector,
and Syft 1.51.0 inputs with checksum/provenance records and explicit
tool-installation, scanner-execution, finding, and SBOM failure classes. Its evidence is
[`candidate-certification.json`](../../mas/docs/provenance/opencode-candidate/2026-08-21-v1.18.21/candidate-certification.json)
and its manual workflow is
[`opencode-candidate-certification.yml`](../../.github/workflows/opencode-candidate-certification.yml).

The inactive OpenHands v1.43.0 candidate is a parallel certification effort,
not a replacement for OpenCode. Its self-contained LiteLLM v1.90.0 and
OmniRoute v3.8.38 route is digest-pinned to `omniroute-coding` → Groq
`llama-3.3-70b-versatile`; the internal gateway URL is fixed to
`http://litellm:4000`. The manual workflow now preflights both the single
operator-owned `GROQ_API_KEY` and the non-secret candidate bindings before
starting any gateway/runtime stages, creates all internal
credentials/profile/MCP objects run-scoped, and evaluates all 20 mandatory
gates fail-closed. Workflow hardening `1154a5f` keeps failure evidence
scalar-only without retaining container logs; `9184d56` records candidate
preflight blockers separately from provider blockers; `3e57123` preserves
gateway health/route failure stages; `593475b` prevents a partial live wave
from reporting PASS; and `6718d9b` enforces the native Ubuntu runner and
source-commit pin statically. Offline fixture evidence is not live
certification evidence.
OpenHands remains
inactive until a deliberate run, complete evidence, and independent steward
activation approval.

## Classification rules

| Classification | Meaning for this release |
| --- | --- |
| `REQUIRED_FOR_RELEASE` | The capability is in the active release scope. A valid live or operator-owned certification and an explicit disposition are required before release. |
| `OPTIONAL_UNVERIFIED` | The capability is disabled or not selected for this release. No support claim is made; promotion to release scope requires a new decision and evidence. |
| `DEFERRED` | The capability is explicitly outside this release. It must remain disabled or unused and must not be represented as certified. |

## Decision matrix

| Capability / gate | Current evidence and blocker class | Proposed current classification | Operator action before resuming release work |
| --- | --- | --- | --- |
| Native-Linux certification host | WSL2 remains the local environment. Retained native Ubuntu GitHub-hosted evidence from run `32541110299` proves the runsc sandbox path, but does not certify a persistent/operator-controlled release host or the remaining native release checks. **Infrastructure/environment.** | `REQUIRED_FOR_RELEASE` | Use the retained gVisor artifact as evidence, then run the remaining native release checks on an accepted host if the release rule requires one. `ubuntu-slim` is not acceptable; do not rerun unchanged gVisor CI evidence. |
| Default gVisor worker sandbox (`runsc`) | The target programme requires gVisor as the default external-worker sandbox; the retained native Ubuntu artifact [`native_gvisor_certification_live.json`](../../mas/docs/provenance/native_gvisor_certification_live.json) records `runsc` registration, digest-pinned smoke, sandbox, cleanup, and zero-residue PASS in run `32541110299`. WSL2 Docker Desktop still cannot register the WSL-installed runtime. **Infrastructure/runtime evidence.** | `REQUIRED_FOR_RELEASE` | Treat the retained native CI certificate as the current gVisor evidence. Keep the local WSL diagnostic and broader native-host checks separate; no silent `runc` fallback is permitted. |
| Firecracker high-risk isolation tier | The target programme calls Firecracker optional for high-risk work; the launch contract is statically valid, but launcher/binary/KVM evidence is absent. **Infrastructure/conditional.** | `OPTIONAL_UNVERIFIED` | Keep high-risk Firecracker workers disabled for this release. Promote to `REQUIRED_FOR_RELEASE` only if the operator includes that tier in scope, then provide KVM, launcher, microVM smoke/network, cleanup, and recovery evidence. |
| Deployment image identity, SBOM, scan, and clean native build | Local image observations exist, but deployment-supplied immutable refs, native build reconciliation, SBOM/scan artifacts, and vulnerability dispositions are not complete. **Infrastructure/operator evidence.** | `REQUIRED_FOR_RELEASE` | Provide the ten immutable deployment refs and matching SBOM/scan/disposition artifacts on the certification host. |
| Provider-managed object-store SSE/KMS and external key custody | The OCI-native adapter and deterministic mocked contract pass; no provider target, bucket customer-managed key, custody, rotation, or live read-back evidence is configured. **External configuration.** | `REQUIRED_FOR_RELEASE` | Supply the real OCI Object Storage + Vault/KMS target and governed auth reference, then run the live adapter for bucket/key identity, PUT/read/checksum, multipart/abort, provider metadata, delete, and zero residue. Do not claim the fixture as live evidence. |
| External mail relay and delivery | Stalwart/Resend contracts and local fixtures pass, but operator-owned relay credentials, DNS/PTR state, and a safe recipient for live delivery/outage evidence are absent. **External configuration.** | `REQUIRED_FOR_RELEASE` if email remains in this release | Supply the live Stalwart/Resend/DNS state and run the governed external-delivery/reply and outage/restore acceptance. Keep direct MX outbound disabled until that evidence exists. |
| Self-improvement live signal and worker/provider path | The guarded lifecycle, approvals, rollback, and local Postgres certificate pass; no operator-selected signal source/project scope is configured. **External configuration/governance.** | `DEFERRED` for the current release | Keep candidate detection and live self-improvement disabled. If enabled, constrain the signal source/project, retain the human kill switch and approval, and promote the full live path to `REQUIRED_FOR_RELEASE`. |
| OpenCode Program D candidate | The historical v1.17.13 scan is `FAILED_UNREPRODUCIBLE` because its raw findings/source snapshot were not retained. Fresh v1.18.21 source/image provenance and the AIAT boundary regression pass; the workflow now provisions Semgrep 1.168.0, TruffleHog 3.97.0, pinned SkillSpector, and Syft 1.51.0, while the local WSL run remains blocked because those tools have not been installed locally. **Security/tooling.** | `REQUIRED_FOR_RELEASE` | Run the pushed candidate workflow from a branch containing the workflow file. Retain the provisioning manifest, checksums, raw sanitized JSON/logs, SBOM, invocations, versions, and exit codes; keep coding/tester inactive until the technical scan passes and do not remediate historical ghost counts. |
| OpenHands v1.43.0 candidate | Exact source/image pins, OpenHands gVisor evidence from run `32594885180`, native CI gVisor prerequisite evidence from run `32541110299`, AIAT boundary tests, digest-pinned disposable gateway wiring, provider preflight, 20-gate matrix, coding-task fixture, and offline lifecycle/security fixtures pass repository-local checks. No new live model task, lifecycle, or provider route has been run; `GROQ_API_KEY` is absent. **Candidate/live-provider gate.** | `REQUIRED_FOR_RELEASE` only if selected; currently inactive/certifying | Set the one operator-owned `GROQ_API_KEY`, run the safe preflight, freeze an exact candidate SHA, and dispatch exactly one manual workflow. Review sanitized evidence and steward approval independently; do not activate on certification alone. |
| Security/adversarial findings | Exact-source historical findings are not accepted or used as patch instructions; a fresh reproducible candidate scan is now the technical source of truth. **Operator decision.** | `REQUIRED_FOR_RELEASE` | Review actual fresh findings by severity/applicability. Remediate AIAT wrapper/configuration findings in code; choose newer upstream or an operator-owned fork for genuine upstream findings. The coding agent must not auto-accept risk. |
| Protected memory files | `mas/packages/mas-core/mas_core/memory/checkpoints.py` and `mas/packages/mas-core/mas_core/memory/storage.py` are pre-existing dirty operator files. **Repository/operator hygiene.** | `REQUIRED_FOR_RELEASE` | Decide deliberately whether to commit them in an operator-owned commit, restore them, or record an approved dirty exception. Do not let a general cleanup or release commit modify, stage, discard, or normalize them. |
| Clean-host/bootstrap and disaster-recovery restore | Local encrypted/fresh-process and disposable-provider prerequisites pass; clean native-host bootstrap, external endpoint recovery, and disaster-recovery evidence remain absent. **Infrastructure/external configuration.** | `REQUIRED_FOR_RELEASE` | After the scope decisions and native host are available, run clean-host bootstrap and restore with only scalar evidence and verified zero residue. |
| MinIO/SeaweedFS resource and provider-functional wave | Both providers pass the identical bounded resource, multipart, checksum/read-back, abort, outage/read-back, and cleanup assertions; the invalid alias run is excluded. **No retained provider failure.** | `REQUIRED_FOR_RELEASE` gate is **closed/pass** | Preserve only the structured scalar evidence. Do not rerun the wave unless the provider fixture, workload, or external environment materially changes. |
| AIAT-owned credentials boundary | Compose live certificate passes all 12 checks with zero fixture rows; provider-managed KMS remains separate. **Functional pass; external configuration still open.** | `REQUIRED_FOR_RELEASE` gate is **closed/pass** | No further identical probe is needed. Reopen only if the credentials implementation or configured external custody changes. |

## External input inventory

This inventory names required inputs without recording values. Empty or missing
inputs are configuration blockers, not provider failures. Values belong in the
operator secret store or host configuration and must never be copied into
evidence, commits, logs, or this document.

### Native host and optional Firecracker

The certification host must provide native Linux, Docker/Compose, registered
`runsc`, the immutable `*_IMAGE_REF` values checked by
`check_release_environment.py`, and a clean certification clone. Firecracker
is conditional: if promoted, the host must additionally provide `/dev/kvm`,
read/write KVM access, the certified `aiat-firecracker-launcher`, the
`firecracker` binary, immutable kernel/rootfs inputs, and the bounded artifact
directory expected by `check_firecracker_worker_pool.py`. No Firecracker value
is currently configured in the repository.

### OCI Object Storage plus Vault/KMS proposal

The adapter is implemented and its fixture contract is registered as a
non-live ledger check. The proposed external identifiers are:

```text
OBJECT_STORE_PROVIDER=oci
OCI_REGION=<operator value>
OCI_NAMESPACE=<operator value>
OCI_BUCKET=<operator value>
OCI_KMS_KEY_ID=<operator key identifier>
OBJECT_STORE_ENCRYPTION_MODE=SSE_KMS
```

The live command is:

```text
uv run --package mas-core --extra oci python scripts/check_object_store_oci_sse_kms.py --live --json
```

The target is not live-ready merely because these names are supplied: the
operator must provide the real bucket/key and governed OCI config or instance
principal, then the command must verify bucket identity, provider encryption
metadata, checksum/read-back, multipart/abort, deletion, and zero residue.
Do not place credentials or key material in the matrix.

### Stalwart/Resend mail acceptance

Production identity configuration currently requires these names (values stay
in the secret environment):

```text
MAS_ENVIRONMENT=production
IDENTITY_PROFILE=production
IDENTITY_DATABASE_PASSWORD or IDENTITY_DATABASE_DSN
IDENTITY_SERVICE_SECRET
IDENTITY_CONTENT_ENCRYPTION_KEY
STALWART_API_KEY
STALWART_JMAP_SERVICE_TOKEN
RESEND_API_KEY
IDENTITY_CLIENT_PUBLIC_KEYS_JSON
IDENTITY_CLIENT_SCOPES_JSON
AGENT_MAIL_DOMAIN=agents.aiat.ca
MAIL_HOSTNAME=mail.aiat.ca
OUTBOUND_RELAY_PROVIDER=resend
OUTBOUND_RELAY_HOST=smtp.resend.com
OUTBOUND_RELAY_PORT=465 or 587
OUTBOUND_RELAY_TLS_MODE=implicit or starttls
OUTBOUND_RELAY_CERTIFIED=true       # only after live certification
DIRECT_MX_OUTBOUND_ENABLED=false
DEFAULT_OUTBOUND_ENABLED=false
```

The opt-in governed acceptance additionally requires these names:

```text
AIAT_RUN_LIVE_IDENTITY_TESTS=1
LIVE_IDENTITY_SERVICE_URL
LIVE_MAIL_HOST
LIVE_SMTP_PORT
LIVE_SMTP_ENVELOPE_FROM
LIVE_IDENTITY_OUTBOUND_RECIPIENT
LIVE_IDENTITY_REQUIRE_REPLY=1
LIVE_IDENTITY_SUSPEND_WORKER_B=1
LIVE_IDENTITY_COMPANY_ID
LIVE_IDENTITY_WORKER_A_ID
LIVE_IDENTITY_WORKER_B_ID
LIVE_IDENTITY_OPERATOR_CLIENT_ID
LIVE_IDENTITY_OPERATOR_PRIVATE_KEY
LIVE_IDENTITY_WORKER_A_CLIENT_ID
LIVE_IDENTITY_WORKER_A_PRIVATE_KEY
LIVE_IDENTITY_WORKER_B_CLIENT_ID
LIVE_IDENTITY_WORKER_B_PRIVATE_KEY
LIVE_IDENTITY_REPLY_TIMEOUT_SECONDS
```

The acceptance must prove Stalwart submission, Resend relay, external receipt,
reply return through Stalwart, and the required revocation case. Submission
alone is not delivery evidence; direct MX outbound remains disabled.

### Self-improvement staging

No self-improvement live secret variable is currently defined. The current
`check_self_improvement_candidates.py --live` path intentionally returns
blocked until an operator supplies and scopes:

- one signal source and adapter (`defect`, `metric`, `upstream_update`, `cost`,
  or explicit `operator_goal`);
- company/project scope, owner, budget, evidence policy, and source revision;
- an approved worker/provider/model profile and isolated branch/workspace;
- immutable change, provenance, SBOM, migration, and rollback artifact
  references; and
- human approval, kill-switch, shadow/canary thresholds, and rollback policy.

The existing local Postgres certificate proves the lifecycle writer only. This
is an implementation/governance boundary, not a missing credential that the
operator should guess. Because this capability is currently `DEFERRED`, no
live self-improvement run should be attempted until the operator promotes it.

### Security finding remediation boundary

The historical 316 findings and scanner/parser errors are from the exact
external OpenCode source revision recorded in
[`security_scan_evidence.yaml`](../../mas/docs/provenance/security_scan_evidence.yaml),
not from a retained AIAT source tree, and is classified
`FAILED_UNREPRODUCIBLE` rather than accepted. The fresh v1.18.21 candidate
retains an immutable source commit/archive reference and image digest and
passes the AIAT workspace/grant/sandbox boundary regression; its current
scanner/SBOM run is blocked because those tools are unavailable in WSL. A
safe code remediation requires actual fresh findings, a newer safe upstream
revision, or an operator-owned fork/patch source. No finding is accepted by
this document, and coding/tester activation remains blocked.

### Explicit failure taxonomy

- **Harness/configuration failure:** the earlier nonexistent Docker network alias
  and the malformed identity-test collection are excluded from provider or
  release evidence. They are not provider failures.
- **Provider functional failure:** none is retained for the corrected MinIO or
  SeaweedFS resource wave; multipart, checksum/read-back, abort, and cleanup
  pass for both.
- **Provider resource-limit failure:** none is observed in the bounded scalar
  profile. The local profile is not a production budget or portability
  certificate.
- **Infrastructure/environment failure:** WSL2/no native Linux, unavailable
  `runsc`, missing Firecracker host capability, and missing deployment image
  artifacts remain external host prerequisites.
- **External configuration/governance:** the required OCI KMS/SSE target and
  mail relay require operator-owned configuration; deferred self-improvement
  must remain disabled and must not be simulated.
- **Operator decision/hygiene:** security findings and protected memory-file
  state require a human disposition and are not silently normalized by the
  implementation agent.

## Operator decision record

Complete this record before changing any conditional row or rerunning a live
release gate:

| Capability | Decision (`REQUIRED_FOR_RELEASE` / `OPTIONAL_UNVERIFIED` / `DEFERRED`) | Rationale and compensating controls | Owner/date | Evidence reference |
| --- | --- | --- | --- | --- |
| Firecracker |  |  |  |  |
| Provider-managed KMS/SSE |  |  |  |  |
| External mail relay |  |  |  |  |
| Self-improvement live path |  |  |  |  |
| Security findings |  |  |  |  |
| Protected memory files |  |  |  |  |

## Resume condition and prohibited shortcuts

Resume the release-gate sequence only after at least one previously missing
prerequisite has materially changed: a native host/runtime is available, a
required external service is configured, an operator decision is recorded, or
the protected-file state is deliberately resolved. Then run the narrow failing
check first, its regression slice, and the relevant broader release checks;
commit implementation, evidence, and ledger changes separately when practical.

Until then:

- do not bypass sandbox requirements under WSL;
- do not fabricate provider, KMS, mail, self-improvement, or native-host evidence;
- do not repeat identical audits against unchanged external state;
- do not auto-accept security findings; and
- do not alter the protected memory files.

Licence and restriction information remains metadata-only in
[`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md) and
[`third_party_components.yaml`](../../mas/docs/provenance/third_party_components.yaml).
It is not a release-scope classifier or an activation gate.
