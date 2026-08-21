# P0 Release-Scope and External-Prerequisite Matrix

**Updated:** 2026-08-21
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

## Classification rules

| Classification | Meaning for this release |
| --- | --- |
| `REQUIRED_FOR_RELEASE` | The capability is in the active release scope. A valid live or operator-owned certification and an explicit disposition are required before release. |
| `OPTIONAL_UNVERIFIED` | The capability is disabled or not selected for this release. No support claim is made; promotion to release scope requires a new decision and evidence. |
| `DEFERRED` | The capability is explicitly outside this release. It must remain disabled or unused and must not be represented as certified. |

## Decision matrix

| Capability / gate | Current evidence and blocker class | Proposed current classification | Operator action before resuming release work |
| --- | --- | --- | --- |
| Native-Linux certification host | WSL2 is the current environment; native host, clean-host checks, and native network/sandbox evidence are absent. **Infrastructure/environment.** | `REQUIRED_FOR_RELEASE` | Provision one disposable or dedicated native-Linux validation host, install Docker/Compose, provide immutable deployment image references, and run the native exit runbook once. |
| Default gVisor worker sandbox (`runsc`) | The target programme requires gVisor as the default external-worker sandbox; `runsc` is not registered on the current host. **Infrastructure/environment.** | `REQUIRED_FOR_RELEASE` | Install/configure gVisor on the native host and run the governed smoke, network-denial, cleanup, and attribution suite. No silent `runc` fallback is permitted. |
| Firecracker high-risk isolation tier | The target programme calls Firecracker optional for high-risk work; the launch contract is statically valid, but launcher/binary/KVM evidence is absent. **Infrastructure/conditional.** | `OPTIONAL_UNVERIFIED` | Keep high-risk Firecracker workers disabled for this release. Promote to `REQUIRED_FOR_RELEASE` only if the operator includes that tier in scope, then provide KVM, launcher, microVM smoke/network, cleanup, and recovery evidence. |
| Deployment image identity, SBOM, scan, and clean native build | Local image observations exist, but deployment-supplied immutable refs, native build reconciliation, SBOM/scan artifacts, and vulnerability dispositions are not complete. **Infrastructure/operator evidence.** | `REQUIRED_FOR_RELEASE` | Provide the ten immutable deployment refs and matching SBOM/scan/disposition artifacts on the certification host. |
| Provider-managed object-store SSE/KMS and external key custody | AIAT-owned encrypted envelope and fresh-process restore pass locally; no provider-managed KMS/SSE target or custody/rotation evidence is configured. **External configuration.** | `DEFERRED` for the current MinIO plus AIAT-envelope profile | Keep provider-managed encryption disabled and make no provider-KMS claim. If provider-backed encrypted restore is part of the intended release, promote this row to `REQUIRED_FOR_RELEASE` and supply a real supported target, rotation, custody, and restore evidence. |
| External mail relay and delivery | Stalwart/Resend contracts and local fixtures pass, but operator-owned relay credentials, DNS/PTR state, and a safe recipient for live delivery/outage evidence are absent. **External configuration.** | `DEFERRED` for the current internal release | Keep external delivery disabled and make no live-delivery claim. If email is in release scope, promote to `REQUIRED_FOR_RELEASE` and provide the relay, domain, safe-recipient, queue/restore, and callback evidence. |
| Self-improvement live signal and worker/provider path | The guarded lifecycle, approvals, rollback, and local Postgres certificate pass; no operator-selected signal source/project scope is configured. **External configuration/governance.** | `DEFERRED` for the current release | Keep candidate detection and live self-improvement disabled. If enabled, constrain the signal source/project, retain the human kill switch and approval, and promote the full live path to `REQUIRED_FOR_RELEASE`. |
| Security/adversarial findings | Exact-source review records 316 findings and parser/engine errors; the review register is structurally valid but awaits operator dispositions. **Operator decision.** | `REQUIRED_FOR_RELEASE` | For every finding group, record `ACCEPT`, `REMEDIATE`, or `DEFER`, with rationale, owner, expiry/compensating controls, and follow-up evidence. The coding agent must not auto-accept findings. |
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

The proposed external identifiers are:

```text
OBJECT_STORE_PROVIDER=oci
OCI_REGION=<operator value>
OCI_NAMESPACE=<operator value>
OCI_BUCKET=<operator value>
OCI_KMS_KEY_ID=<operator key identifier>
OBJECT_STORE_ENCRYPTION_MODE=SSE_KMS
```

The current S3-compatible `BlobClient` and object-store checkers accept an
endpoint, access key, secret key, bucket, and region, but do not yet expose an
OCI-native bucket-encryption read-back or a provider-managed SSE/KMS evidence
field. Therefore the OCI target is not live-ready merely because these names
are supplied: an implementation/evidence adapter must first be added, then a
real put/read/checksum/multipart/delete run must verify the bucket encryption
state. Do not place credentials or key material in the matrix.

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

The 316 findings and scanner/parser errors are from the exact external
OpenCode source revision recorded in
[`security_scan_evidence.yaml`](../../mas/docs/provenance/security_scan_evidence.yaml),
not from a retained AIAT source tree. AIAT's local workspace/grant/sandbox
boundary regression already passes. A safe code remediation requires either a
new upstream revision, an operator-owned fork/patch source, or a narrowed
replacement runtime; without one of those, the coding agent cannot honestly
rewrite upstream findings from the scalar register. No finding is accepted by
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
- **External configuration/governance:** KMS/SSE, mail relay, and
  self-improvement require an intentional scope choice and operator-owned
  configuration; they must not be simulated.
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
