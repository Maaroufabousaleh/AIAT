# P0 Release Integrity Status

**Updated:** 2026-08-19
- Current continuation refresh (`296d89b`, aggregate evidence `f6063e0`) runs the static ledger at 57/57
  pass and the corrected configured Compose aggregation at 76 pass/0 fail/5 blocked
  across 81 checks (`23:57:08Z`, canonical host-loopback), with four pending evidence items and
  `NO-RELEASE`. The local Postgres host-execution, multi-host, lease,
  host-fencing, and queued-run loss checks pass serially after their harnesses
  were isolated with fixture labels and deterministic priorities; their scalar
  evidence has zero residual rows. Native host/image/gVisor/Firecracker,
  outbound-mail, self-improvement, security-review, external KMS, clean-host,
  and disaster-recovery gates remain separate.
- The local image-provenance follow-up (`75f9be0`) reconciles all ten supplied
  immutable references with matching Docker `RepoDigests` on the current WSL2
  engine. The scalar certificate is [`image_provenance_local_identity.json`](../../mas/docs/provenance/image_provenance_local_identity.json);
  the bounded local SBOM observation (`662fb65`) is recorded in
  [`image_sbom_local_observation.json`](../../mas/docs/provenance/image_sbom_local_observation.json)
  with ten CycloneDX 1.4 summaries and 2,430 components. A deployment
  vulnerability scan still needs operator authentication or an offline scanner;
  source/lock, clean native build, and deployment identity evidence remain
  unproven, so the production image gate stays blocked and the global
  `NO-RELEASE` decision is unchanged.
- The first post-image aggregate attempt used `MAS_API_KEY` where the local
  deployment requires the distinct `AIAT_OPERATOR_API_KEY`; its 403 trace
  read-back is classified as harness/configuration invalid and excluded. The
  corrected operator-key rerun passes 76/81 checks with five explicit external
  blockers and zero failures.
- The 2026-08-18 continuation verification re-runs the static release ledger
  at 57/57 pass and confirms the API/protocol contract, documentation index,
  provenance inventory, docs-index scope guard, and host-owned restart-boundary
  regression. The policy-driven network boundary contract (`96fb71f`) now
  feeds the same deny/allow rows to static Compose validation and the live
  runner probe; the refreshed WSL2 matrix remains 11/11 pass while native
  release-host evidence remains open. The OpenCode Compose sandbox contract
  (`2c098f5`) now statically enforces its internal-only network, non-root/read-only
  execution, dropped capabilities, no-new-privileges, bounded resources, and
  noexec/nosuid tmpfs. The selected-model host certificate also passes with the
  production `GatewayWorkerAdapter` over a bounded local gateway fixture
  (`8ed53df`). The security review register/checker (`23e908e`) now proves
  complete rule-count coverage and owner/next-action metadata for the exact
  scan while retaining `technical_gate_status: blocked`. The release decision
  remains `NO-RELEASE` because live evidence, two pending worker security
  reviews, and a clean worktree are still absent. The workflow-control fixture
  extension (`9972b3b`) also passes deterministic cancellation, escalation,
  timeout, retry, and invalid-transition checks; it does not close native/live
  recovery.
- The supported repository regression suite is green after `dc8a19e` repaired
  stale expectations for the implemented worker-host tables and the current
  135-model/271-operation generated API contract. The focused storage,
  object-store, host-registry, SDK, and API contract slices pass; this is a
  test-contract repair and does not alter the live-gate blockers or
  `NO-RELEASE` decision.
- The mail-boundary regression slice is green after `3edff39` repaired the
  JMAP relay-verifier fixture, case-safe SMTP traceback provenance, and
  unprivileged security-artifact test modelling. The mail-edge suite passes
  11/11 and the SMTP-gateway suite passes; `35e52e1` records the status. This
  repairs local evidence/test boundaries only; external relay delivery and
  provider outage certification remain open.
- The optional Microsoft Agent Framework profile was freshly installed and
  re-certified in `9bde609`: the isolated MAF `1.13.0`/MCP `1.29.0` probe and
  focused adapter/compatibility/certification regressions pass, with one fake
  client call, bounded completion, health/shutdown verification, and no
  provider calls or mutation. The production workspace remains on MCP
  `1.23.3`; default-profile/provider activation is still blocked and this
  evidence does not advance live worker, sandbox, canary, or rollback gates.
- Commit `def4fe9` adds a bounded provider retry boundary to the production
  `GatewayWorkerAdapter`. Its configured live certificate
  [`gateway_worker_provider_recovery_live.json`](../../mas/docs/provenance/gateway_worker_provider_recovery_live.json)
  passes one injected transient `429`, one forwarded provider completion,
  durable dual-Postgres reopen, raw-ingress replay/conflict/tamper checks,
  payload-free generated-text redaction, and zero residual rows. This is
  retry-boundary evidence only; provider outage, external callback/delivery,
  independent-host, and sandbox evidence remain open.
- Commits `1679341` and `9c7e76d` extend the durable gateway/mail-edge checker
  with local provider-shaped recovery. The default fixture profile injects one
  transient `429`, retries once through the production adapter/controller, and
  passes dual-Postgres reopen, raw-ingress replay/conflict/tamper, payload-free
  projection, and scoped cleanup. Evidence is retained at
  [`gateway_worker_mail_edge_provider_recovery_postgres_evidence.json`](../../mas/docs/provenance/gateway_worker_mail_edge_provider_recovery_postgres_evidence.json).
  This is local retry evidence; external provider outage/restore, callback or
  delivery confirmation, independent host/process recovery, and sandbox
  evidence remain open.
- Commits `cec6558` and `520c6bf` add a local process-isolation certificate:
  two separate Python child processes reconnect to Postgres and settle two
  committed worker-host bindings through the production executor/controller;
  the parent verifies distinct process IDs, durable payload-free evidence,
  reopen, and zero-row cleanup. This is same-host process evidence only;
  independent deployed hosts, host-loss/split-brain, provider, and sandbox
  gates remain open. Evidence is retained at
  [`worker_independent_process_execution_postgres_evidence.json`](../../mas/docs/provenance/worker_independent_process_execution_postgres_evidence.json).
- Commit `91504dd` adds the AIAT-owned encrypted object-store backup envelope:
  AES-256-GCM ciphertext replication, opaque key-ID-only manifests,
  authenticated checksum/read-back, wrong-key and tamper rejection, clean-
  target refusal before mutation, and scoped cleanup pass in the deterministic
  fixture. Evidence is retained at
  [`object_store_encryption_evidence.json`](../../mas/docs/provenance/object_store_encryption_evidence.json).
  This is a provider-neutral envelope prerequisite only; provider-managed
-  SSE/KMS, external backend, key custody, clean-host/disaster-recovery, and
  outage gates remain open. Commit `b0f27f6` adds the local clean-process
  prerequisite: a ciphertext/scalar-manifest bundle is reopened by a distinct
  Python process with fresh adapters, verified through the production
  encrypted-restore helper, and removed. Payload-free evidence is retained at
  [`object_store_clean_environment_restore_evidence.json`](../../mas/docs/provenance/object_store_clean_environment_restore_evidence.json)
  (`59294c0`); the scalar read-back was refreshed at `f567979` and again at
  `554901c` (2026-08-18T23:24:22Z) with a distinct child process,
  ciphertext-only bundle, and zero fixture residue. This does not certify
  clean-host, provider-pair, KMS, outage, or disaster recovery.
- The 2026-08-18 storage continuation also retains a bounded provider-diverse
  adapter rehearsal and benchmark: Compose MinIO plus disposable SeaweedFS
  4.42 each pass checksum read-back/cleanup, while the pair checker proves
  secondary-only clean recovery after an adapter-boundary primary-loss probe.
  Scalar-only evidence is retained at
  [`object_store_provider_pair_provider_diverse_evidence.json`](../../mas/docs/provenance/object_store_provider_pair_provider_diverse_evidence.json)
  and
  [`object_store_provider_benchmark_evidence.json`](../../mas/docs/provenance/object_store_provider_benchmark_evidence.json).
  This remains local disposable comparison evidence; it does not authorize
  provider selection or claim provider durability, KMS, actual outage,
  clean-host, disaster recovery, or migration cutover.
- Commit `6794b9f` extends the same comparison with a bounded 1 MiB/8 MiB
  concurrent wave: four cases per size pass checksum read-back and scoped
  cleanup on both endpoints. Scalar evidence is retained at
  [`object_store_benchmark_advanced_provider_diverse_evidence.json`](../../mas/docs/provenance/object_store_benchmark_advanced_provider_diverse_evidence.json).
  It remains timing/comparison evidence only; resource, outage,
  provider-managed encryption/KMS, clean-host, and disaster-recovery gates
  remain open.
- Commit `a2f35de` adds the explicit multipart adapter boundary and checker.
  The bounded live run passes 8 MiB and 16 MiB payloads with 5 MiB parts on
  both MinIO and SeaweedFS, verifies checksum read-back and abort-without-
  object, and cleans both prefixes to zero. Evidence is retained at
  [`object_store_multipart_provider_diverse_evidence.json`](../../mas/docs/provenance/object_store_multipart_provider_diverse_evidence.json).
  Resource, provider-outage, provider-managed encryption/KMS, clean-host, and
  disaster-recovery gates remain open.
- Commit `3791b3f`, ledger registration `bb0fa8f`, and fresh evidence `c90fc13`
  add the bounded `aiat.object-store-resource-profile.v1` wave. Compose MinIO
  and disposable SeaweedFS 4.42 each pass eight 1 MiB/8 MiB checksum cases at
  concurrency four with identical procfs RSS/wall/CPU sampling, zero errors,
  and zero cleanup residue. The same fresh reserved namespace passes 8 MiB/16
  MiB multipart upload/read-back with 5 MiB parts and explicit abort cleanup.
  The earlier nonexistent-network-alias attempt is classified as invalid test
  harness/configuration execution and is excluded from provider evidence and
  verdicts. This is scalar comparison evidence only; production resource
  budgets/portability, KMS, clean-host, and disaster-recovery gates remain
  open; actual provider-process outage/recovery is certified separately below.
- Commit `d92b3dc` adds the guarded provider-process outage/recovery checker and
  fresh live evidence at
  [`object_store_provider_outage_live_evidence.json`](../../mas/docs/provenance/object_store_provider_outage_live_evidence.json).
  Fresh disposable MinIO and SeaweedFS containers on canonical `mas_internal`
  receive the same 64 KiB/1 MiB checksum workload, become unreachable during
  a controlled stop, restart with anonymous disposable volumes, pass checksum
  read-back, and clean to zero objects. Container/helper/volume removal is
  verified; earlier development attempts are excluded. This closes the local
  process-outage gate only, not KMS, independent-host, clean-host, or disaster
  recovery.
- The AIAT credentials-manager live certificate (`12ba7c7`, scalar evidence
  [`credentials_manager_live_evidence.json`](../../mas/docs/provenance/credentials_manager_live_evidence.json),
  refreshed at 2026-08-18T23:22:26Z against `23d93c9` with evidence commit
  `a90fda2`; release child `credentials_manager_live` registered in `d101901`) passes
  against Compose Postgres. Ciphertext-at-rest, metadata-only projection,
  policy denial, approved server-side resolution, one-use approval,
  rate-limit denial, audit persistence, and zero fixture residue are verified;
  no key material, values, payloads, or credentials are retained. This advances
  the AIAT-owned secret-management boundary only. Provider-managed SSE/KMS,
  external key custody/rotation, clean-host bootstrap, and disaster recovery
  remain independent operator gates.
- The security/adversarial-isolation rerun at 2026-08-18T23:26:40Z passes the
  secret-safe review-register contract (316 findings, 54 engine warnings,
  one explicitly open operator review), scanner aliases, 39-worker sandbox
  declaration, and the 11-runner live WSL2 network matrix; focused security,
  sandbox, and network regressions pass 29/29. The technical security gate
  remains blocked pending the operator's finding dispositions, and native
  `runsc`/host smoke evidence remains separate.
- The remaining live-gate probes were rerun after correcting the test harness:
  outbound mail is blocked only for operator-owned relay credentials and a
  safe recipient, while self-improvement candidate detection is blocked only
  for an operator-selected signal source/project scope. Their deterministic
  fixture checks and the corrected 56-test mail/self-improvement regression
  slice pass; the malformed identity-test collection command is excluded as a
  harness/configuration failure and no live provider evidence is claimed.
- The live verified-copy continuation inventories three reserved MinIO objects,
  copies them to SeaweedFS with matching checksums/sizes, preserves the source
  until explicit cleanup, and leaves zero source/target objects. Evidence is
  [`object_store_copy_provider_diverse_evidence.json`](../../mas/docs/provenance/object_store_copy_provider_diverse_evidence.json).
  This remains parity evidence only; retention, rollback, outage, clean-host,
  and disaster-recovery gates remain open.
- Commit `ecbef00` adds the guarded live `aiat.object-store-migration.v1`
  rehearsal. With a reserved project and explicit human cutover/rollback
  confirmations, it passes three-object inventory, provider copy/read-back,
  one dual write, AIAT-owned `CUTOVER` → `ROLLED_BACK` evidence, and scoped
  cleanup across disposable MinIO/SeaweedFS endpoints. Evidence is retained
  at [`object_store_migration_provider_diverse_evidence.json`](../../mas/docs/provenance/object_store_migration_provider_diverse_evidence.json).
  It does not change deployment routing or retention authority, so production
  cutover, provider outage, KMS, clean-host, and disaster-recovery gates remain
  open.
- Commit `00a468d` extends gateway fallback handling to all transport outages,
  and `48b32ef` adds the local `aiat.gateway-provider-recovery.v1` fixture.
  Its deterministic primary-outage → secondary-fallback → primary-recovery
  sequence verifies cooldown arming and clearance without external network,
  worker dispatch, or durable state. This is local routing evidence only;
  durable external-provider outage recovery remains open.
- Commit `5ed0a0b` adds the fail-closed Firecracker high-risk worker launch
  contract and read-only readiness checker. Static validation passes for
  immutable kernel/rootfs digests, bounded resources, read-only rootfs,
  deny-by-default egress, opaque secret references, artifact output, and
  cleanup; the current host certificate is live-blocked because the certified
  launcher and Firecracker binary are unavailable. No Docker/runc/gVisor
  fallback is allowed. Evidence is
  [`firecracker_worker_pool_readiness.json`](../../mas/docs/provenance/firecracker_worker_pool_readiness.json);
  real microVM, provider, recovery, and gVisor evidence remain open.
- Governance denial-state recovery (`888fde3`) is covered by source-built stale, first-load-denial, and post-read-denial fixture coverage 3/3; denied combined reads hide Refresh/Retry and executive action forms while preserving only last-known read context. Native/live ACL and WCAG evidence remain open.
- PM integrations denial-state recovery (`7373360`) is covered by source-built stale, first-load-denial, and post-read-denial fixture coverage 3/3; denied reads hide Refresh/Retry and lifecycle-plan mutations while preserving only last-known reconciliation context. Native/live ACL and provider evidence remain open.
- Hiring Board denial-state recovery (`553f196`) is covered by source-built stale, first-load-denial, and post-read-denial fixture coverage 3/3; denied worker reads hide Refresh/Retry and registration, evaluation, status, drain, and deletion controls while preserving only last-known rows. Native/live ACL and worker evidence remain open.
- Credentials denial-state recovery (`982c9c0`) is covered by source-built stale, first-load-denial, and post-read-denial fixture coverage 3/3; denied reads hide Refresh/Retry, creation, deletion, placeholder copy, selection, and audit navigation while preserving only previously loaded redacted metadata. Native/live ACL and credential evidence remain open.
- CEO Live Feed denial-state recovery (`a3cbd99`) is covered by source-built stale, first-load-denial, and post-read-denial fixture coverage 3/3; denied history/SSE/composer responses hide reconnect/retry, copy/clear/filter, and composer controls while preserving only previously loaded messages. Native/live ACL and Redis/router evidence remain open.
- Agent Streams denial-state recovery (`118ff18`) is covered by source-built stale, first-load-denial, and post-read-denial fixture coverage 3/3; denied history/SSE responses hide reconnect/retry, filter, pause, clear, and copy controls while preserving only previously loaded messages. Native/live ACL and Redis/router evidence remain open.
- Container Logs denial-state recovery (`156597c`) is covered by source-built stale, first-load-denial, and post-read-denial fixture coverage 3/3; denied SSE responses hide load/retry, filter, clear, copy, and download controls while preserving only previously loaded lines. Native/live ACL and container-log evidence remain open.
- Metrics denial-state recovery (`b64b15e`) is covered by source-built stale, first-load-denial, and post-read-denial fixture coverage 3/3; denied query-family responses hide refresh/retry, time-range, and reconnect controls while preserving only previously loaded series. Native/live ACL and Prometheus evidence remain open.
- Dead-letter queue denial-state recovery (`e6ab3a1`) is covered by source-built stale, first-load-denial, and post-read-denial fixture coverage 3/3; denied read/replay responses hide refresh/retry, filters, selection, and replay controls while preserving only previously loaded messages and read-only envelope inspection. Native/live ACL and DLQ evidence remain open.
- Tools catalogue denial-state recovery (`b418f8a`) is covered by source-built stale, first-load-denial, and post-read-denial fixture coverage 3/3; denied reads hide refresh/retry, search, grouping, expansion, and copy controls while preserving only previously loaded tool metadata and read-only tables/details. Native/live ACL and tool-service evidence remain open.
- Flows list denial-state recovery (`3108b02`) is covered by source-built stale, first-load-denial, retained-read-denial, and mutation-denial fixture coverage 4/4; denied reads/deletes hide refresh/retry, New Flow, search/status filters, selection, editing, and deletion controls while preserving only previously loaded definitions as read-only text. Native/live ACL and flow evidence remain open.
- Flow editor denial-state recovery (`392d264`) is covered by source-built stale, first-load-denial, retained-read-denial, and mutation-denial fixture coverage 4/4; denied reads/saves hide refresh/retry, palette, editing, undo/redo, and save controls while preserving only the last successfully loaded canvas as read-only. Native/live ACL and flow/runtime evidence remain open.
- New-flow builder denial-state recovery (`b07299b`) is covered by source-built initial-catalogue, create-denial, and readiness-validation-denial fixture coverage 3/3; denied reads/mutations expose a named access-status region, preserve the current draft canvas read-only, and hide templates, palette, node configuration, validation, activation, and creation controls. Native/live ACL and full WCAG/native-Linux evidence remain open.
- Mail-edge observation contracts/checker (`85369fe`), identity-service
  persistence/projection (`cfafe38`), and the Resend/Svix raw-body verifier plus
  provider-facing ingress (`2d21a2f`), projected-span checker classification
  (`29d4da5`), and optional signed identity read-back (`074ef8a`) pass the deterministic payload-free
  delivery/webhook/bounce fixture and focused service suites; configured
  provider callback certification, selected worker live read-back, and complete
  mail-span evidence remain open and non-gating.
- The deterministic worker/mail-edge evidence join (`1d8aed5`) composes the
  independent worker source and payload-free mail-edge evaluators with
  explicit worker/trace scope; its fixture certificate passes without network,
  database, worker, or provider mutation. This is a cross-surface evidence
  contract only; live worker/provider/bounce evidence remains open.
- The bounded trace incident projection/checker (`c357fdf`), operator-only
  API/generated-contract/dashboard deep-link boundary (`b4b7cef`), and
  payload-free finding chronology (`869202c`) are covered by deterministic
  core/script/API/dashboard tests and remain a read-only descriptive summary;
  they do not turn partial coverage, incident findings, or licence metadata
  into an activation or release gate.
- The read-only retention-plan route/live checker and regenerated contract
  group (`f8829d6`, `b3fca97`, `9a80c6c`) are covered by focused API/core/script
  tests; they expose bounded native-span metadata and explicitly report
  `mutation_performed: false`. The planner’s explicit-boolean legal-hold marker
  is fail-safe and non-authoritative; destructive retention, authoritative
  holds, erasure, project narrowing, audit, and restore parity remain separate
  live/storage gates. The typed response model and generated count/candidate
  schemas are enforced by `b3fca97`/`9a80c6c`.
**Status:** in progress — metadata-only policy and worker evaluator/manifest group (`cbdcfa6`), governed model-profile/cooldown/catalogue/bootstrap group (`288996e`), section ACL contract, immutable image contract (`7d69fbd`), development image-wrapper defaults (`b9a77e9`), CycloneDX SBOM artifact validation (`42b03a3`), tool-service profile/budget contract (`b24ca0c`), runner control-plane storage/network boundary (`43bee16`), fail-closed sandbox runtime readiness contract (`a24c554`), reproducible default-runtime install and adapter-conformance contract (`9a10a4b`), worker readiness/default-binding/matrix contract (`4c5fd68`), deterministic worker-run lifecycle fixture (`fe6fb8d`), bounded runtime/adapter policy checks (`fc528a8`), prompt/tool/review contract (`20f0499`), bounded review/scanner/Git workspace implementation (`5b830e9`), provider conformance contracts (`7f6bfc5`), governed identity/mail lifecycle and trace projection (`f577675`), worker-registry grant/update-policy hardening (`d8cafbb`), fail-closed local image identity probe, bounded project-state metrics (contract `90a7d82`, runtime wiring `cbeb9db`, compatibility `541d6e0`), read-only persisted default-worker binding reconciliation, exact-locked LangGraph/CrewAI adapter conformance, deterministic flow traversal semantics, explicit evidence-policy scope resolution, external-account action-policy and lifecycle fixtures, outbound-mail approval/idempotency/retry/outage fixture, built-in YouTrack/GitHub adapter declaration and mocked HTTP conformance fixtures, asynchronous governed flow-task binding, evidence-preserving flow retry, watchdog/recovery fixture, WSL/DrvFS-safe project Git initialization, local dashboard UI golden paths (including shell focus, identity stale-record/retry, PM integration conflict/stale retry, project-detail stale/retry, system-visualization partial/offline retry, governance read-surface stale/retry recovery `52de581`, governance accessibility `f4ae7eb`, System Control stale/retry recovery `f445c17`, Projects list stale/retry recovery `d3482ab`, project evidence package stale/retry recovery `bc80ad5`, Tools catalogue stale/retry recovery `5f4b0eb`, dead-letter queue stale/retry recovery `823fa6d`, credentials metadata stale/retry recovery `970f09c`, credentials accessibility `93fdfbc`, identity-resource stale/retry recovery `46eccee`, identity table accessibility `651ad11`, shared identity-resource accessibility `a260e04`, Metrics partial/stale/retry recovery `85596b0`, and flow editor load/stale/retry recovery `b5098e7`), the CEO Command Center chat recovery group `beabb95`, the secret-safe release-environment/provenance input group committed as `64771b5`, the bounded release-ledger aggregator committed as `eff4eef`, and the native live-ledger gate committed as `4d7a495` implemented; model-profile bootstrap (`09bdd19`), flow schema/retry hardening (`234adfb`), team-runner boundary hardening (`22fc21a`), dashboard operation-selector hardening (`e378f40`), project-evidence typecheck/router fixes (`fc4f0fa`, `33e0384`), company-timezone propagation (`ee1361f`), and deterministic worker certification-matrix regression coverage (`a62ddb7`) are reflected in the maintained rows; native/live release exit gates, project-page composition, and live provider snapshot evidence remain open
- The AIAT-owned sandbox contract now also checks the OpenCode Compose runtime for internal-only networking, no host ports, non-root/read-only execution, cap-drop ALL, no-new-privileges, bounded CPU/memory/PIDs, and noexec/nosuid tmpfs (`2c098f5`); gVisor registration, smoke/network denial, canary, Firecracker, and upstream scan disposition remain separate gates.
- The maintained dashboard evidence now also includes Flows list stale/retry recovery (`a0faf5b`, source-built `flows-states.spec.ts` 1/1), flow-editor load/stale/retry recovery (`b5098e7`, source-built `flow-editor-states.spec.ts` 1/1), Project evidence package stale/retry recovery (`bc80ad5`, source-built `project-evidence-states.spec.ts` 1/1), Project evidence package canonical-read denial recovery (`00f81b5`, source-built `project-evidence-states.spec.ts` 3/3), Container Logs stale/retry recovery (`280d363`, source-built `logs-states.spec.ts` 1/1), Agent Streams reconnect/history recovery (`3e8a0ea`, source-built `streams-states.spec.ts` 1/1), Hiring Board stale/retry recovery (`7541b84`, source-built `workers-states.spec.ts` 1/1), operator sign-in landmark/status/toggle baseline (`d928834`, source-built `login-accessibility.spec.ts` 1/1), System Overview healthy/partial/offline source classification with bounded GET retry (`50cee61`, source-built `system-overview-recovery.spec.ts` 1/1 for explicit offline and partial fixtures), shared identity-resource route matrix across nine identity/mail pages (`485dfd2`, source-built `identity-resource-matrix.spec.ts` 9/9 with safe fixtures), identity-resource stale-to-recovered retry with obsolete-request cancellation (`46eccee`), semantic table/action controls (`651ad11`), named main/status/metadata/table regions with explicit busy state and decorative-icon suppression (`a260e04`), and the 403 access-denied identity state (`0974434`, source-built `identity-states.spec.ts` 2/2), System Control stale recovery plus first-load/post-read denial safety (`14968d4`, source-built `system-status-states.spec.ts` 3/3), CEO Live Feed reconnect/history recovery (`1761429`, source-built `ceo-states.spec.ts` 1/1), and CEO Command Center chat stream/history recovery (`beabb95`, source-built `ceo-chat-states.spec.ts` 1/1); native/live flow/project-evidence/log/stream/worker/CEO evidence remains separate from these preparatory P1 results.
- The project-detail page now also has explicit first-load unavailable/retry recovery (`f364763`, source-built `project-detail-states.spec.ts` 1/1), and its workspace sub-surface retains canonical data through failed workspace/repository refreshes with explicit stale/Retry recovery (`cb1c665`, source-built `project-workspace-states.spec.ts` 1/1). The nested Activity/Resources/Cost tabs now add semantic tab/tabpanel relationships, roving Arrow/Home/End navigation, and 44px targets (`fcb0f4b`); full project-page composition and live provider/worker evidence remain separate.
- The Projects list now also has an accessible table caption/column scopes, explicit description disclosure, responsive overflow, and 44px selection/filter/sort/link/action targets (`7828b48`, source-built `projects-states.spec.ts` 1/1); the Flows list now has the same focused table/accessibility baseline (`6b0413b`, source-built `flows-states.spec.ts` 1/1); the flow editor now has semantic landmarks and 44px editor/generated-form controls (`140af1c`, source-built `flow-editor-states.spec.ts` 1/1); full native-Linux/page-level visual certification remains separate.
- The project evidence package page now has named main/package sections, labeled 44px back/refresh actions, and a captioned evidence table with scoped column headers (`89091c1`, source-built `project-evidence-states.spec.ts` 1/1); full native-Linux/page-level visual certification remains separate.
- The Tools catalogue now has named main/search/group regions, captioned/scoped group tables, keyboard-visible tool expansion, and 44px refresh/group/search/copy/retry/empty-state targets (`83e39e6`, source-built `tools-states.spec.ts` 1/1); its 401/403 boundary exposes a named denial region, preserves only last-known tool metadata, and hides refresh/retry, search, grouping, expansion, and copy controls (`b418f8a`, source-built `tools-states.spec.ts` 3/3); full native-Linux/page-level visual certification remains separate.
- The dead-letter queue now has named main/summary/filter/list/disclosure regions, `aria-pressed` severity filters, keyboard-visible envelope inspection, and 44px recovery/selection/replay/inspection targets (`99a19a2`, source-built `dlq-states.spec.ts` 1/1); full native-Linux/page-level visual certification remains separate.
- The Credentials page now has named main/security/data regions, a captioned/scoped credentials table, a labeled creation dialog with explicit field associations, and 44px refresh/audit/selection/copy/delete/dialog targets (`93fdfbc`, source-built `credentials-states.spec.ts` 1/1). Its 401/403 denial state preserves only previously loaded redacted metadata and hides read/mutation controls (`982c9c0`, source-built `credentials-states.spec.ts` 3/3); full native-Linux/page-level visual certification remains separate.
- The Metrics page now has named main/summary/chart regions, a semantic time-range control, and 44px range/refresh/retry/empty-state targets (`da113af`, source-built `metrics-states.spec.ts` 1/1); its 401/403 query-family boundary exposes a named denial region, preserves only last-known series, and hides refresh/retry, time-range, and reconnect controls (`b64b15e`, source-built `metrics-states.spec.ts` 3/3); full native-Linux/page-level visual certification remains separate.
- The Flows list now has a named 401/403 denial boundary that preserves only last-known definitions as read-only text and hides refresh/retry, New Flow, search/status filters, selection, editing, and deletion controls (`3108b02`, source-built `flows-states.spec.ts` 4/4); full native-Linux/page-level visual certification remains separate.
- The Container Logs page now has named main/filter/legend/output/status regions, 44px stream/filter/recovery targets, and an `aria-busy` log output (`993b1cb`, source-built `logs-states.spec.ts` 1/1); its 401/403 SSE boundary exposes a named denial region, invalidates obsolete stream generations, preserves only last-known lines, and hides load/retry, filter, clear, copy, and download controls (`156597c`, source-built `logs-states.spec.ts` 3/3); full native-Linux/page-level visual certification remains separate.
- The Agent Streams page now has a named main/filter/feed/status structure, a captioned message table, keyboard-accessible expandable rows, 44px stream/filter/action targets, and an `aria-busy` feed state (`d320383`, source-built `streams-states.spec.ts` 1/1); its 401/403 history/SSE boundary exposes a named denial region, invalidates in-flight callbacks, preserves only last-known messages, and hides reconnect/retry, filter, pause, clear, and copy controls (`118ff18`, source-built `streams-states.spec.ts` 3/3); full native-Linux/page-level visual certification remains separate.
- The Hiring Board now has named main/policy/summary/filter/table regions, integration/runtime status landmarks, a captioned/scoped worker table, keyboard-expandable rows, associated registration-dialog fields, and 44px refresh/register/filter/selection/row-action/dialog targets (`826b4c5`, source-built `workers-states.spec.ts` 1/1); full native-Linux/page-level visual certification remains separate.
- The CEO Live Feed now has named main/composer/summary/filter/feed/status regions, 44px stream/composer/filter/recovery targets, a busy feed state, and keyboard-expandable messages (`1f947a9`, source-built `ceo-states.spec.ts` 1/1); full native-Linux/page-level visual certification remains separate.
- The CEO Live Feed now also exposes a named 401/403 access-denied state, preserves only previously loaded messages, invalidates in-flight stream callbacks, and hides reconnect/retry, copy/clear/filter, and composer controls (`a3cbd99`, source-built `ceo-states.spec.ts` 3/3); full native-Linux/page-level visual certification remains separate.
- The CEO Command Center chat now has a named main/workspace/transcript/composer structure, a live transcript log with busy state, 44px navigation/composer/quick-command/recovery targets, explicit chat guidance regions, and a mobile-safe accessible activity link (`8ffb5df`, source-built `ceo-chat-states.spec.ts` 1/1); full native-Linux/page-level visual certification remains separate.
- The Governance page now has a named main/read-surface structure, explicit executive/model-profile/WorkerRun/steward/catalogue regions, a captioned/scoped WorkerRun table, accessible catalogue status, and 44px refresh, retry, executive-form, and confirmation controls (`f4ae7eb`, source-built `governance-states.spec.ts` 1/1); full native-Linux/page-level visual certification remains separate.
- System Control now has a named main/loading state, explicit runtime-status/schedule/control/dialog regions, scheduled-event semantics, and 44px refresh, retry, shutdown/resume, schedule-input/save, and confirmation controls (`543f392`, source-built `system-status-states.spec.ts` 1/1); full native-Linux/page-level visual certification remains separate.
- Project Detail now has a named page/loading state, explicit project status, 44px refresh/retry/back and primary project-view tab targets, and semantic project/workspace tab-panel relationships (`40b87dd`, source-built `project-detail-states.spec.ts` 1/1); full project composition, WCAG/native-Linux visual certification, and live provider/worker evidence remain separate.
- Evidence Detail now has a named page/canonical-citation region, a semantic bounded-detail region with an explicit `aria-busy` refresh state, decorative-icon suppression, and 44px CEO-chat/canonical-link/Refresh targets (`32f3a76`, source-built `dashboard-evidence-detail.spec.ts` 9/9); full WCAG/native-Linux visual certification remains separate.
- System Visualisation now has named loading/error/ready page landmarks, horizontal visualization tabs with semantic tab/tabpanel links, and 44px breadcrumb, refresh, Mermaid-copy, path-trace, graph/detail, policy, retry, and back-link targets (`ed5e551`, source-built fixture-backed `app-operations.spec.ts` 1/1); full WCAG/native-Linux visual certification remains separate.
- PM Integrations now has a named busy main landmark, explicit summary/connections/reconciliation/lifecycle regions, labeled lifecycle inputs, and 44px refresh/retry/generation/approval/apply controls (`bbd6ba3`, source-built `app-operations.spec.ts` 1/1); full WCAG/native-Linux visual certification and provider-owned evidence remain separate.
- System Overview now has a named main and hero/status surface, explicit health/overview-metrics/first-run/company-project-state/Quick Links regions, decorative-icon suppression, and 44px graph/Quick Links/seed controls (`c07b4a6`). The source-built first-run test passes 1/1 for both `seeded` and `not_seeded` local deterministic orchestrator fixture runs; the live orchestrator was unavailable in this run, so full control-plane/WCAG/native-Linux evidence remains separate.

The secret-safe operational diagnostics group (`2860838`) and API-facing
operator wrapper (`380daf5`) are implemented and covered by focused
API/contract tests; both are bounded operational surfaces and do not turn
dependency or licence metadata into a release gate.
The communication-policy hardening group (`fb39128`) now validates declared
sender role/team coherence before any router enqueue, closing a worker-to-CEO
team-spoof path with static and mocked-router evidence.
The hierarchy visualization group (`8b7d9f1`) adds the corresponding dashboard
overlay for allowed/denied communication paths. A current `mas/dashboard:overlay`
image rebuilt from a clean explicit context passes the focused authenticated
Playwright flow 1/1 (`d5f596e`). The `mas.sh` wrapper now excludes all
disposable `.tmp*` paths and fails closed on incomplete staging (`45ee42c`);
direct unwrapped WSL Docker-context and release-image evidence remain open.

**Plan:** [P0 Release Integrity Plan](plans/P0_RELEASE_INTEGRITY_PLAN.md)  
**Roadmap:** [ROADMAP.md](../../ROADMAP.md)
**Ledger:** [AIAT Current Release Ledger](../../mas/docs/AIAT_CURRENT_RELEASE_LEDGER.md)
**Live exit procedure:** [P0 Native-Linux Exit Runbook](../../mas/docs/P0_NATIVE_LINUX_EXIT_RUNBOOK.md)

This is the current implementation status for the first roadmap phase. It is
an evidence index, not a release approval. The working tree also contains
pre-existing PM/integration changes, so a final release ledger must identify a
single frozen commit before production claims are made.

## Completed in this phase

### Licence metadata boundary

- `ExternalWorkerSteward` keeps `license_id` and `redistribution_status` in
  immutable provenance, but certification no longer derives a licence check.
- The API certification route filters licence/redistribution attestations out
  of blocking checks.
- The evaluator still records detected, missing, unclassified, or restricted
  licence metadata as an operator notice. Its diagnostic key is always present,
  has zero technical score weight, and cannot create a blocker or rejection.
- `LICENSE_REVIEW` remains a compatibility state and metadata-capture
  checkpoint; the normal source-review path can skip it, and the steward
  cannot transition from that label directly to `BLOCKED`, so licence metadata
  cannot become a standalone gate or delay normal use.
- `scripts/check_provenance.py` validates the personal/internal metadata policy
  and source/version inventory without an allowlist or prohibited-component
  decision (`cbdcfa6`). The evaluator's diagnostic licence result is retained
  for operator visibility, but has zero score weight and cannot create a
  blocker or rejection.
- Default worker manifests no longer carry licence-derived exclusions: the
  security evaluator advertises Semgrep, SkillSpector, and TruffleHog as normal
  bounded scanners, the
  planner exposes Plane/OpenProject provider adapters, and DevOps exposes
  Ansible through its normal CLI adapter. Small starting profiles remain
  technical packaging choices, not resource bans.
- Worker registry authority checks now constrain update-policy values and
  revalidate persisted capability grants on capability/team changes before
  mutation (`d8cafbb`); invalid policy values and forbidden grants fail closed,
  while licence/restriction metadata remains informational only.
- The tools SDK/manifest group `965ba38` now routes the `semgrep`,
  `skillspector`, and `trufflehog`
  compatibility aliases through `security.scan`; each executes only via the
  configured sandbox adapter with bounded output and the existing audit, grant,
  rate, and approval boundaries. SkillSpector may be supplied through
  `TOOL_SKILLSPECTOR_COMMAND`.

### Shared operational predicate

- `operational_promotion_checks()` is shared by the in-process steward, the
  orchestrator certification route, and rollout promotion for immutable
  provenance and security scan checks. Activation/status changes also require
  persisted external provenance with a passed security scan. Documentation and
  capability snapshots remain additional technical evidence at the API boundary.
- Missing or pending security scans continue to fail certification regardless of
  licence metadata.

### Worker manifest truth

- `coding_worker` and `tester` now declare `evaluation_status: pending` and
  `certification_status: pending` while their exact OpenCode source scan is
  recorded as `findings_review_required` (316 findings, 54 engine warnings).
  The scan summary is linked from both manifests; their OpenCode interface
  evidence is not treated as a passed security scan substitute.
- `scripts/check_worker_reconciliation.py` (runtime catalogue/checker group
  `80e0ca3`) validates all 39 manifests against
  the shared runtime catalogue, transport/isolation contract, default company
  references, external source/version/provenance records, OpenCode Compose
  service/version, production image inventory, and the metadata-only notices
  policy. Its read-only `--live` mode now reconciles the checked-in defaults
  against persisted `/capabilities/workers` adapter, sandbox, model,
  source-pin, capability, and active immutable-record bindings. It reports
  pending security evidence without converting licence data into a gate; its
  package-availability field is advisory and it does not claim live runtime
  certification.
- All 39 team-runner agent declarations now carry exact `worker_manifest_ref`
  values; `scripts/check_team_worker_manifest_refs.py` passes 11 teams/39
  agents without registration or activation side effects. Runtime registration
  remains a separate gate.
- Production team-runner startup repeats that read-only reconciliation against
  the mounted worker directory and carries each reference into `AgentConfig` and
  health metadata (`569231f`). Missing or mismatched references fail closed;
  startup does not register or activate workers.
- `scripts/check_runtime_install_profile.py` reconciles the default
  LangGraph/CrewAI extra, `uv.lock` versions, runtime-catalogue imports, and
  production orchestrator Dockerfile install command. This is reproducible
  packaging evidence only; imports, sandbox, security, canary, and live-run
  evidence remain open.
- `scripts/check_operator_pins.py --json` (checker/test group `dd857ae`)
  reconciles the exact production
  `uv`, Docker CLI, MCP, Semgrep, Docling, Playwright, Mermaid, OpenTofu, and
  OpenCode declarations. Microsoft Agent Framework remains unavailable in the
  default workspace but now has a separately pinned/certified isolated profile
  (`b937a89`); gVisor, Firecracker, SkillSpector, ccpm, LiteLLM, and OmniRoute
  remain explicitly unavailable until their operator/package/host/image
  identities are supplied. No licence field is consulted by this technical
  check.
- The isolated MAF certificate at
  [`provenance/maf_runtime_certification.json`](../../mas/docs/provenance/maf_runtime_certification.json)
  proves package imports and the AIAT adapter's bounded fake-client task,
  response normalization, health, and shutdown. It does not prove provider
  configuration, model-backed canary, sandbox, live worker execution, or
  rollback; the default production MCP pin remains `1.23.3`.
- `mas/docs/provenance/security_scan_evidence.yaml` records the exact
  OpenCode `v1.17.13` commit and Semgrep `1.168.0` result. The evidence is
  deliberately non-passing because it contains 19 `ERROR` findings and 54
  engine warnings; activation remains fail-closed until technical triage is
  complete.
- `mas/docs/provenance/security_scan_review.yaml` and
  `scripts/check_security_scan_review.py` (`23e908e`) provide the bounded
  triage contract: one personal-operator review row, all 15 Semgrep rule
  groups mapped to exactly 316 findings, and a separate follow-up for 54
  engine warnings. The checker reports a coherent register as static `pass`
  but preserves `technical_gate_status: blocked`; it never waives findings or
  consults licence metadata as a gate.
- `scripts/check_worker_steward_contract.py` runs the actual steward domain for
  each externally sourced default worker through immutable candidate,
  compatibility-matrix, shadow/read-only-canary promotion, regression blocking,
  and pre-activation rollback transitions. The rollback fixture proves that a
  rejected replacement preserves the previously active immutable pointers. It
  is deterministic domain evidence only and does not turn synthetic security
  or canary observations into live certification; the retained report is
  [`worker_steward_contract.json`](../../mas/docs/provenance/worker_steward_contract.json).
- The certification route now writes the compatibility matrix through the
  canonical storage owner and links its ID into certification/candidate
  evidence. The same-process steward cache records the row immediately, and
  restart rehydration restores persisted matrix rows with
  profile/capability-shape normalization; production database reconciliation
  and live canary evidence are still required.
- API steward rehydration now restores durable active bundle/adapter pointers
  before another rollout and fails closed when a persisted pointer is unknown;
  this preserves restart-time rollback state without claiming live worker
  certification.
- `scripts/check_worker_steward_readiness.py` and its
  `aiat.worker-steward-readiness.v1` evaluator provide a read-only,
  explicitly selected worker/candidate preflight. Fixture mode passes; the
  authenticated local coding-worker probe is blocked by `PROVISIONING` steward
  state, a pending technical scan, and no candidate. It never generates,
  certifies, approves, activates, rolls out, or dispatches, and licence
  metadata remains informational only.

### Metrics

- AIAT Prometheus families no longer use raw `project_id` labels. Project-state
  uses the bounded `state` label, while review/infra metrics are aggregate and
  project drill-down remains in structured workflow/audit records. A
  2,000-series platform budget plus per-family budgets are exposed through
  `metric_series_budget_status()`/`metric_label_inventory()` and covered by the
  metrics test suite. `metric_label_policy_inventory()` now classifies every
  declared AIAT label by its bounded source (protocol enum, active registry,
  catalogue, or declared histogram buckets), and the checker rejects unknown
  or non-bounded labels; the live local scrape now folds Prometheus' synthetic
  histogram `_created` sample into its declared family instead of reporting a
  false undeclared-family failure. The runtime records bounded aggregate
  transitions for project creation, workflow transitions, decisions, retries,
  watchdog recovery, and archive operations, then reconciles the aggregate from
  persisted rows during resume/startup; the synthetic 10,000-project
  bounded-label test passes; the durable local many-project scrape now also
  passes, while clean native-Linux release-host scale evidence remains open.

### Secret-safe operational diagnostics

- `GET /system/diagnostics` provides a read-only control-plane health summary
  across the database, message router, tool service, and optional object store
  (`2860838`). The database probe executes only `SELECT 1`; HTTP probes consume
  `/health`; the object-store probe uses only `head_bucket` and closes its
  client. The response contains bounded status/latency/connection facts and
  exception type, never credentials, URLs, raw dependency payloads, or error
  text.
- Healthy, degraded, object-store-unconfigured, no-storage, and dependency
  payload-redaction behavior is covered by `test_test10_ops_scripts.py`. A
  dependency failure is returned as an explicit aggregate `degraded` report;
  missing storage remains a 503 boundary. This route is diagnostic only and
  does not activate workers, mutate state, or consult licence/restriction
  metadata as a gate.

### Operator control CLI

- `scripts/mas-ctl` wraps the authenticated control-plane API with
  `status`, `diagnostics`, and fail-closed `bootstrap` commands, plus explicit
  `resume` and `shutdown` POST commands (`380daf5`; executable mode `f8df50e`). It does not invoke Docker
  or Compose lifecycle operations, accepts the operator key only from an
  argument/environment, and suppresses upstream error bodies.
- Six deterministic transport cases cover API-key forwarding, base-URL
  normalization, ready/degraded bootstrap, HTTP-error redaction, and explicit
  POST methods. The operational API suite now verifies that the executable
  wrapper is present. A focused boundary regression (`2360e07`) verifies that
  per-service restart remains owned by the Compose/systemd host wrappers rather
  than gaining Docker authority inside the API.

### Communication-policy boundary

- The message router now rejects non-CEO envelopes whose declared sender team
  is not owned by the declared trust tier (`fb39128`). Workers can run under
  department/C-suite parent teams and sub-agents under any known parent team,
  but a worker cannot claim `exec_ceo` to make a direct or intra-team message
  appear authorised. Rejection occurs before Redis dedupe/enqueue.
- `test_policy.py`, `test_phase3.py`, and `test_test12_comms_policy.py` cover
  valid worker/admin/sub-agent paths, spoofed worker/admin teams, role-specific
  message types, and mocked-router HTTP 403 behavior. The remaining hierarchy
  graph item is now implemented by the `HierarchyViz` sender-role overlay and
  a checked-in source-built E2E spec (`8b7d9f1`); its execution and live Compose image evidence remain
  separate because the current image predates the change.

### Runner network and control-plane storage boundary

- `43bee16` routes deployed runner checkpoints, usage events, documents, and
  COO review records through the authenticated orchestrator storage endpoint.
  The operation set is allow-listed, UUID/datetime payloads are normalized at
  the API boundary, and checkpoint reads/deletes are team-scoped.
- Compose runners receive only their worker or CEO identity key and gateway
  configuration; PgBouncer, Postgres, MinIO, and OpenCode remain off the
  `workers` network. Runner startup performs a storage-health request and
  fails closed when the control-plane path is unavailable.
- `mas/docs/provenance/network_boundary_policy.yaml` (`96fb71f`) is now the
  machine-readable `aiat.network-boundary-policy.v1` source for protected
  services, allowed gateways, internal-network flags, identity variables,
  forbidden mounts/env names, and the external-denial target. Both static and
  live checker paths consume this policy; static coverage also rejects runner
  host-port publication and a public `workers` network.
- The static/live boundary checker records no runner data-plane credentials,
  Docker sockets, or unapproved egress. The refreshed WSL2 matrix passes all
  11 runners; native-Linux release-host denial/allow evidence remains open.

### Trace propagation

- The pure trace-context/native-span/trace-evidence core is reviewed and
  committed as `77d5494`; the bounded API-observation schema/migrations are
  committed as `9c39919`; tool-service HTTP and usage-writer integration is
  committed as `53d38fc`, while broader API/storage writer integration remains a separate
  review group. Request-level trace propagation is now verified for the orchestrator API,
  message router, and tool service. Bounded `X-AIAT-Trace-ID` and W3C
  `traceparent` values are accepted, invalid values are replaced with a fresh
  root trace, orchestrator/SDK callers forward the bound trace, responses
  return `X-AIAT-Trace-ID`, agent message dispatch binds envelope context, and
  async context is cleared after each request/handler. Router/agent forwarding
  and envelope cleanup are committed as `5bc0aae`. The operator-only
  `aiat.trace-evidence.v1` query now joins task logs, project-usage events,
  worker-run transitions, durable API request observations, direct
  trace-correlated model-usage/worker-artifact/integration-evidence metadata
  with legacy run fallback, and PM-inbound correlations with safe source
  coverage and company trace-retention metadata. The native span contract now
  persists payload-free transport/model/tool/audit/worker/integration spans;
  the identity service now persists safe outbound delivery-attempt trace/span
  metadata and the signed client projects matching mail spans without content
  or provider fields. The refreshed local orchestrator is at migration
  `0036_native_trace_spans`; a bounded live `/health` request and operator
  trace read observe one API-request row plus one native transport span in the
  fresh 2026-08-11 local run,
  retained at [`mas/docs/provenance/trace_observability_live.json`](../../mas/docs/provenance/trace_observability_live.json)
  and reproducible through [`mas/scripts/check_live_trace_observability.py`](../../mas/scripts/check_live_trace_observability.py) (`eac83ae`).
  The rebuilt tool-service usage writer also passes a bounded `time_now` probe:
  one project-usage row plus one `tool_service` native span are retained at
  [`mas/docs/provenance/tool_trace_live.json`](../../mas/docs/provenance/tool_trace_live.json)
  and reproducible through [`mas/scripts/check_live_tool_trace.py`](../../mas/scripts/check_live_tool_trace.py) (`eac83ae`, refreshed 2026-08-11).
  The host-side checker now resolves the Compose-only `tool-service:8002`
  alias to the published loopback port only when the orchestrator is local;
  the aggregate live ledger therefore records both trace children as passing.
  Commit `24c2e35` adds explicit native model/worker source-category coverage
  and a fail-closed selected-worker checker; its fixture passes, but no live
  worker dispatch is claimed. Provider mail-edge, representative live worker,
  audit/integration, and provider-backed retention evidence remain outside this
  bounded local slice. Commits `f8829d6`, `b3fca97`, and `9a80c6c` add the read-only
  retention-plan route/checker, typed response, and fail-safe legal-hold
  metadata guard; all validate bounded policy/candidate metadata and prove
  `mutation_performed: false` without applying retention. The follow-on
  `01996c9` execution contract/rehearsal, hardened by typed parity evidence in
  `57e13cb`, a typed authoritative hold snapshot in `15054ba`, the typed
  bounded audit envelope in `5d71309`, and the provider-neutral registry read
  adapter in `67f5eae`. The Postgres-backed local adapter and reserved-fixture
  certificate (`96f5fc0`) now pass one trace-scoped delete with database-local
  backup/read-back parity, two held rows, and scoped cleanup; evidence is
  [`mas/docs/provenance/trace_retention_execution_live.json`](../../mas/docs/provenance/trace_retention_execution_live.json).
  Production hold authority, durable audit, erasure, archive, provider,
  and restore evidence remain separate gates.

### SLO and capacity read models

- Versioned `aiat.slo-policy.v1`, `aiat.slo-report.v1`, and
  `aiat.capacity-forecast.v1` contracts now cover descriptive API, queue,
  worker, tool, model-routing, PM/SCM, mail, and recovery targets. Durable
  usage aggregates provide bounded cost/token forecasts, confidence, and
  budget headroom; the payload-free API request ledger now supplies native
  platform request observations, and the signed identity-service delivery
  projection supplies bounded mail-attempt observations when configured.
  Missing native mail-edge/full-span sources remain explicit `no_data` or
  `insufficient_data` and never block execution.

### Model routing and profile evidence

- Commit `288996e` adds one explicit transient-status vocabulary across normal,
  streaming, and fallback gateway paths; permanent client/credential errors are
  not blindly retried. Model and provider cooldown state is bounded and
  persisted, SmartRouter/fallback selection excludes active cooldowns, and a
  successful request clears the affected scope.
- The same commit adds deterministic `aiat.model-profile-catalogue.v1`
  reconciliation, a fail-closed `--live` verifier, an idempotent conflict-
  preserving profile bootstrap, and the internal `omniroute-coding` LiteLLM
  alias. Current 2026-08-18 local evidence retains 93 registered models, 93
  persisted versions, 93 approved covered entries, and no pending registered
  model or reconciliation finding after the exact unreferenced local smoke
  fixture was removed following a zero-reference preflight. The explicit
  `aiat/omniroute-coding` profile identity now reconciles to the canonical
  `litellm/omniroute-coding` registry entry. The same read-only run sees nine `/v1/models`
  entries and all five AIAT aliases; the repeatable route-only checker is
  `f6ed16f` with evidence at
  [`model_gateway_readiness_live.json`](../../mas/docs/provenance/model_gateway_readiness_live.json),
  while the profile report is refreshed in `04521e7` at
  [`model_profile_catalogue_live.json`](../../mas/docs/provenance/model_profile_catalogue_live.json).
  These are operator-visible reconciliation findings, not licence/resource
  restrictions; no dispatch or provider call was performed and provider
  outage/recovery evidence remains open.

### Metric-series evidence boundary

- `scripts/check_metric_series_budget.py --json` now exercises the bounded
  metric registry with 10,000 synthetic projects, enforces the 2,000-total and
  per-family ceilings, rejects any `project_id` label, and emits the complete
  label inventory plus bounded classification policy. Its `--live` mode parses
  only AIAT-owned `mas_*` families from the orchestrator scrape, folds the
  client's synthetic histogram timestamp sample into the declared histogram
  family, and passes the current local scrape at 31 series. It still returns
  `blocked` without a configured endpoint and never emits metric payloads,
  credentials, or unbounded label values. The durable local many-project
  certificate inserts/read-backs 10,000 Postgres project rows, observes 31
  bounded series with all 18 workflow-state values and no `project_id` label,
  removes only its reserved namespace, and verifies baseline restoration; see
  [`metric_series_many_projects.json`](../../mas/docs/provenance/metric_series_many_projects.json).
  Clean native-Linux release-host scale evidence remains open.

### Machine-readable release ledger

- `scripts/check_release_ledger.py --json` (base aggregator `eff4eef`, native live-ledger gate `4d7a495`) now
  aggregates the checked-in verifier inventory into `aiat.release-ledger.v1`.
  The latest static run reports 57/57 configured
  fixture/contract/documentation/release-environment/operator-pin/governance
  checks passing, two worker security findings-review evidence items, and
  `NO-RELEASE` because the worktree is dirty and live evidence was not
  included. Child-check timeouts and live unavailability remain explicit
  blocked evidence rather than passes.
- `scripts/check_release_environment.py --json` (inputs committed as
  `64771b5`) emits the current source revision, branch/dirty state, hashes for
  thirteen release inputs, available tool identities, configured-input
  presence flags, and a deterministic per-revision
  `aiat.release-environment.v1` digest without printing values or credentials.
  The current WSL manifest passes its static identity check;
  `--require-clean` remains appropriately open until a frozen release worktree
  exists.
- `scripts/check_docs_index.py --json` passes the canonical target, thirteen current
  feature specifications, three ordered plans, maintained local links, roadmap
  references, and the personal/internal metadata-only policy markers.
- The current unconfigured local 2026-08-18 77-check profile records 58
  passes, zero failures, 19 externally blocked probes, and four pending
  evidence items with a bounded 60-second child-check timeout. The native
  release-host preflight is now the `release_environment:live` child and
  reports WSL2, missing `runsc`, dirty worktree, and absent immutable image
  refs as safe blockers; the new `object_store_provider_outage:live` child is
  also blocked until disposable provider configuration is supplied. The current summary is retained at
  [`provenance/release_ledger_live_current.json`](../../mas/docs/provenance/release_ledger_live_current.json).
  The configured 81-check profile remains retained at
  [`provenance/release_ledger_live.json`](../../mas/docs/provenance/release_ledger_live.json)
  with 76 passes, five blocked probes, and four pending evidence items; both profiles yield
  `NO-RELEASE`. These are evidence records, not a release pass.

### CEO/service dashboard boundary

- Compose requires distinct `AIAT_CEO_API_KEY` and `AIAT_WORKER_API_KEY`
  principals; only the CEO runner receives the dedicated CEO key, while other
  runners receive the worker key. The team-runner constructor retains a
  fallback only for non-Compose unit fixtures.
- `system_config.dashboard.section_acl.v1` stores the validated section ACL.
  The human operator is always retained as the repair principal; automation
  principals receive only their bounded default sections.
- Dashboard API proxies send `X-AIAT-Dashboard-Section`; the API enforces the
  persisted ACL, and operator-only `PUT /dashboard/sections/{section}/acl`
  updates it. Positive and negative human/CEO/service/worker tests pass.

### Team-runner data-plane boundary

- Deployed team runners no longer receive `PGBOUNCER_DSN`, MinIO credentials,
  or the shared `MAS_API_KEY` in the Compose team environment. PgBouncer and
  MinIO are internal-only services, and the OpenCode runtime is no longer
  attached to the runner network; runners use the authenticated,
  operation-allowlisted `/internal/team-runners/{team_id}/storage` control
  plane for checkpoints, usage events, documents, and COO review durability.
- `ControlPlaneStorageClient` preserves the small `CheckpointStore` and
  `AgentStorage` method surfaces required by `AgentBase`/`ExecutiveAgent`, so
  resume and review persistence remain functional without direct SQL or S3
  access. Runners fail startup if the durable control-plane storage health
  check is unavailable. Static Compose and API boundary tests pass; native
  DNS/TCP/HTTP denial and positive-path evidence remains open.
- `scripts/check_network_boundary.py` now codifies the Compose credential,
  network, gateway, Docker-socket, and OpenCode isolation contract for CI and
  provides a non-secret native-Docker probe mode. The static report passes;
  live execution remains externally dependent on Docker Engine.

### Local object-store evidence

- The persisted MinIO `mas_agent` IAM secret was reconciled safely after local
  environment rotation without touching object data. The private-network
  conformance probe now passes all 8/8 scoped cases, and the same-provider
  backup/restore rehearsal passes two disposable objects with manifest parity
  and cleanup. Evidence was refreshed 2026-08-11 in `22c736d` and is retained at
  [`object_store_live_conformance.json`](../../mas/docs/provenance/object_store_live_conformance.json)
  and
  [`object_store_backup_restore_live.json`](../../mas/docs/provenance/object_store_backup_restore_live.json).
  The pinned, credential-safe reconciliation helper is committed as `5558f3c`.
  Restore-copy safety hardening `93bf755` now rejects a non-empty target prefix
  before mutation and records `clean_target_verified` in restore evidence.
  The bounded provider-pair group `351444a` now dual-writes three objects from
  the Compose MinIO endpoint to a disposable MinIO endpoint, rejects a
  simulated unavailable primary at the AIAT adapter boundary, restores from
  the secondary into a clean bucket, and cleans all prefixes to zero; evidence
  is [`object_store_provider_pair_evidence.json`](../../mas/docs/provenance/object_store_provider_pair_evidence.json).
  Both endpoints are MinIO, so provider-diverse durability, actual provider
  process/network outage, provider-managed encryption, clean-host, and
  disaster-recovery evidence remain open; the AIAT-owned encrypted envelope
  and local fresh-process restore prerequisite are retained separately above.

### Immutable release inputs and image profiles

- Production Compose no longer contains mutable application image defaults.
  Fixed infrastructure images and all Dockerfile bases carry OCI digests;
  application/gateway images require digest-bearing `*_IMAGE_REF` values.
- `scripts/check_image_provenance.py` (committed as `7d69fbd`) passes the source-level production
  contract. Its `--live --json` mode compares deployment-supplied immutable
  refs with local Docker `RepoDigests`, returns exit 2 when Docker or refs are
  unavailable, and never emits image refs or credentials. The live scope is
  local identity only; it does not claim SBOM, scan, build, or clean-room
  evidence. `production-image-lock.example.env` documents the deployment
  inputs without inventing local OCI digests; its complete Compose-variable
  coverage regression is committed as `1d373ee`; `b9a77e9` keeps development-only
  `:dev` defaults in the wrapper instead of weakening direct production
  Compose. Runtime-wrapper hardening is committed as `fd41874`: local
  validation requires distinct CEO/worker principals, propagates the company
  timezone to runner/tool/dashboard environments, pins wrapper `uv` bootstrap
  versions, and documents the identity migration head without supplying
  production image identities or release credentials.
- `Dockerfile.tool-service` now builds a lightweight `core` profile. Browser,
  Docling, Semgrep, and Mermaid/Node payloads are installed only by the
  `extensions` profile; `infra/docker/image-budgets.yaml` defines the live
  ceilings and `scripts/check_image_budgets.py --json` validates supplied size,
  startup, and memory measurements (contract committed as `b24ca0c`). The
  development-only `mas.sh` wrapper supplies local `:dev` image names when
  deployment refs are absent; direct production Compose still requires real
  immutable references.
- `document.ingest` remains usable in the lightweight profile: it invokes
  Docling when installed and returns source text with an explicit degraded
  `plain_text_fallback` backend when the optional binary is absent. This is
  local fallback evidence only; it does not certify the external Docling
  runtime.

### Contract export (preparatory P1 work)

- `schemas/http/orchestrator.openapi.json` is generated from the FastAPI
  application using canonical JSON ordering and recorded in
  `docs/provenance/api_contract.yaml` with its path count and SHA-256 hash.
- The same checker compares the checked-in `aiat.v1` protocol schema, generated
  dashboard TypeScript surface, and generated Python SDK surface with
  runtime/OpenAPI sources. CI fails on API, protocol, or client type drift
  unless artifacts and provenance are deliberately regenerated together.
- Commit `8f46ed1` reconciles the checked-in `WorkerManifest.transport` enum
  with the runtime `aiat_gateway` transport and updates the protocol SHA-256
  in `docs/provenance/api_contract.yaml`; `check_api_contract.py --json`
  passes with the API counts unchanged.
- `scripts/generate_typescript_api.py` turns the 135 OpenAPI component schemas
  and 271 operations into a checked-in dashboard type surface; CI checks that
  generated output and `npm run typecheck` remain green.
- `scripts/generate_python_api.py` emits the matching 135 Python `TypedDict`
  models and 271-operation metadata surface under `packages/mas-api-sdk`, and
  `OrchestratorClient` exercises it without a handwritten endpoint fork. The
  typed read-only retention-plan response is included in both generated
  clients.
- The company manifest now exposes typed optional timezone, retention (including
  trace days/sample rate), privacy, evidence, model, and deployment policy
  blocks; legacy manifests without those blocks still compile, while the
  default manifest records the current values.
- The manifest timezone is consumed by team-runner prompt headers, the
  `time_now` tool, orchestrator scheduler defaults, dashboard display helpers,
  and Compose/development defaults. Prompt headers and clock results are
  operator-facing; persistence remains UTC.
- This does not claim the P1 modular-control-plane exit; P1 remains gated on
  the still-open native P0 release evidence.

## Verified evidence

| Check | Result | Evidence |
|---|---|---|
| Governance denial-state recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `888fde3`; targeted ESLint, `npm run typecheck`, `npm run build`, the Impeccable detector (`[]`), and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4151 npx playwright test e2e/governance-states.spec.ts --workers=1 --reporter=line` pass 3/3 for stale recovery, first-load 403 denial, and authorization loss after a successful read. Denied combined reads hide Refresh/Retry and all executive action forms while preserving only last-known read context; native/live ACL and WCAG evidence remain open |
| PM integrations denial-state recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `7373360`; targeted ESLint, `npm run typecheck`, `npm run build`, the Impeccable detector (`[]`), and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4152 npx playwright test e2e/app-operations.spec.ts --workers=1 --grep "PM integrations" --reporter=line` pass 3/3 for stale recovery, first-load 403 denial, and authorization loss after a successful read. Denied reads hide Refresh/Retry and lifecycle-plan generation/review/approval/apply controls while preserving only last-known reconciliation context; native/live ACL and provider evidence remain open |
| Hiring Board denial-state recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `553f196`; targeted ESLint, `npm run typecheck`, `npm run build`, the Impeccable detector (`[]`), and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4155 npx playwright test e2e/workers-states.spec.ts --workers=1 --reporter=line` pass 3/3 for stale recovery, first-load 403 denial, and authorization loss after a successful read. Denied worker reads hide Refresh/Retry and registration, evaluation, activation/deactivation, drain, and deletion controls while preserving only last-known worker rows; native/live ACL and worker evidence remain open |
| Credentials denial-state recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `982c9c0`; targeted ESLint, `npm run typecheck`, `npm run build`, the Impeccable detector (`[]`), and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4156 npx playwright test e2e/credentials-states.spec.ts --workers=1 --reporter=line` pass 3/3 for stale recovery, first-load 403 denial, and authorization loss after a successful read. Denied reads preserve only previously loaded redacted metadata and hide Refresh/Retry, creation, deletion, placeholder copy, selection, and audit navigation; creation and bulk mutations fail closed on authorization loss. Native/live ACL and credential evidence remain open |
| Worker/steward/evaluator regression suite | PASS | `uv run --isolated pytest packages/mas-core/tests/test_worker_governance.py packages/mas-core/tests/test_worker_steward_contract.py packages/mas-core/tests/test_compatibility_matrix_persistence.py packages/mas-core/tests/test_default_shipped_agents.py apps/orchestrator-api/tests/test_workers_test5_lifecycle.py apps/orchestrator-api/tests/test_steward_rehydration.py -q`; the steward fixture also passes `uv run --isolated python scripts/check_worker_steward_contract.py --json`, including regression blocking, pre-activation pointer preservation, compatibility-matrix shape normalization, and restart-safe rehydration coverage |
| Worker registry grant/update policy | PASS (static/API fixture) | Commit `d8cafbb`; `uv run --isolated pytest apps/orchestrator-api/tests/test_workers_test4_config.py -q` passes 66 focused cases, while adjacent capability, lifecycle, and policy suites pass. Registration and partial updates constrain `manual`/`auto-patch`/`auto-minor`/`auto-all`; persisted capability `required_tools` are rechecked on capability/team changes and forbidden grants fail before storage mutation. Licence metadata is not a gate |
| Document ingest fallback contract | PASS | `uv run --isolated pytest apps/tool-service/tests/test_default_shipped_tool_catalog.py -q`; Docling execution and explicit degraded plain-text fallback are covered without claiming the optional binary is installed |
| Backend and team-runner regression suite | PASS | `uv run --isolated pytest packages/mas-core/tests apps/orchestrator-api/tests apps/tool-service/tests apps/team-runner/tests -q` |
| Broader worker/observability regression suite | PASS | `uv run --isolated pytest packages/mas-core/tests/test_worker_*.py packages/mas-core/tests/test_observability.py apps/orchestrator-api/tests/test_metrics.py -q` |
| Metrics API and label-policy suite | PASS (static + refreshed Compose live + durable many-project scrape) | Contract `90a7d82`, runtime wiring `cbeb9db`, bounded legacy-storage reconciliation fallback `541d6e0`, and native scrape/evidence group `eefe08f`; `uv run --isolated pytest packages/mas-core/tests/test_metric_series_budget.py apps/orchestrator-api/tests/test_metrics.py apps/orchestrator-api/tests/test_projects.py scripts/tests/test_check_metric_series_many_projects.py -q`; the static report includes every AIAT label policy, the synthetic 10,000-project fixture, and the Prometheus histogram `_created` normalization regression test; the refreshed 2026-08-18 Compose scrape and the authenticated durable 10,000-project scrape each pass at 31 bounded series with no `project_id` label, retained at [`metric_series_live.json`](../../mas/docs/provenance/metric_series_live.json) and [`metric_series_many_projects.json`](../../mas/docs/provenance/metric_series_many_projects.json); clean native-Linux release-host scale evidence remains open |
| HTTP/message/trace-evidence suite | PASS (committed core; operator incident chronology/deep link; fresh Compose transport + tool read-back; broader sources open) | Core commit `77d5494`; router/agent propagation `5bc0aae`; worker source coverage contract `24c2e35`; bounded incident projection/checker `c357fdf`; operator incident API/generated contracts/dashboard deep link `b4b7cef`; payload-free finding chronology `869202c`; focused API/core/dashboard trace, build/typecheck, and Playwright tests pass; `uv run --isolated pytest packages/mas-core/tests/test_tracing.py packages/mas-core/tests/test_trace_evidence.py packages/mas-core/tests/test_native_trace_spans.py packages/mas-core/tests/test_trace_incident.py packages/mas-core/tests/test_phase4_5.py apps/message-router/tests/test_trace_propagation.py scripts/tests/test_check_trace_incident.py -q`; `uv run --isolated python scripts/check_trace_evidence.py --json`, `scripts/check_native_trace_spans.py --json`, `scripts/check_trace_incident.py --json`, and `scripts/check_worker_trace_coverage.py --json --require-integration` pass deterministic contracts. The configured 2026-08-18 Compose probes pass and are retained in [`trace_observability_live.json`](../../mas/docs/provenance/trace_observability_live.json) and [`tool_trace_live.json`](../../mas/docs/provenance/tool_trace_live.json); selected live model-backed worker, mail-edge, retention, and richer incident chronology probes remain separate evidence boundaries |
| Trace retention execution | PASS (deterministic fixture + local Postgres reserved-fixture certificate) | `96f5fc0`; `uv run --isolated pytest packages/mas-core/tests/test_postgres_retention.py packages/mas-core/tests/test_retention_execution.py scripts/tests/test_check_trace_retention_execution.py -q` and focused Ruff pass. `check_trace_retention_execution.py --live --json` reaches migration `0036_native_trace_spans`, keeps preview non-mutating, verifies database-local backup/read-back parity, applies one trace-scoped delete after human confirmation, preserves two held rows, and cleans the reserved namespace to zero; evidence is [`trace_retention_execution_live.json`](../../mas/docs/provenance/trace_retention_execution_live.json). Production hold authority, durable audit, erasure, archive, provider-diverse recovery, and restore rollback remain open |
| API observation ledger | PASS (bounded static/unit/API) | `uv run --isolated pytest packages/mas-core/tests/test_api_observations.py apps/orchestrator-api/tests/test_trace_propagation.py -q`; `uv run --isolated python scripts/check_api_observability.py --json` passes the payload-free normalized-route fixture; migrations `0034_api_request_observations`, `0035_trace_correlation_evidence`, and `0036_native_trace_spans`, durable table/readers, and trace/SLO projections are implemented |
| Secret-safe system diagnostics | PASS (static/unit/API) | Commit `2860838`; `uv run --isolated pytest apps/orchestrator-api/tests/test_system.py apps/orchestrator-api/tests/test_test10_ops_scripts.py -q` covers database, router, tool-service, optional object-store, degraded aggregation, no-storage 503, and dependency-payload redaction. The route is read-only and returns only bounded status/latency/connection facts or exception type |
| Operator control CLI | PASS (static/unit) | Commits `380daf5`, `f8df50e`, and `2360e07`; `uv run --isolated pytest scripts/tests/test_mas_ctl.py -q` passes six deterministic cases, and `test_test10_ops_scripts.py` verifies the executable `scripts/mas-ctl` wrapper plus the host-owned Compose/systemd per-service restart boundary. `bootstrap` requires healthy `/health` plus `ok` diagnostics; error bodies are never returned |
| Communication-policy sender identity | PASS (static/unit/mocked router) | Commit `fb39128`; `uv run --isolated pytest packages/mas-core/tests/test_policy.py apps/message-router/tests/test_phase3.py apps/message-router/tests/test_publish_auth.py apps/orchestrator-api/tests/test_test12_comms_policy.py -q` covers sender role/team coherence, spoofed worker-to-CEO/admin paths, role-specific message types, and HTTP 403 before enqueue. Live external-router and dashboard hierarchy evidence remain separate |
| Hierarchy communication-policy overlay | PASS (source type/lint/build + focused live E2E) | Implementation `8b7d9f1`; evidence-test wording cleanup `3dc61ad`; selector hardening `d5f596e`; fail-closed staged-context handling `45ee42c`; `npm run typecheck`, focused ESLint, and `npm run build` pass for the dashboard. The focused authenticated `npm run test:e2e -- --workers=1 --grep "system visualization exposes hierarchy"` passes 1/1 against a current `mas/dashboard:overlay` image built from a clean explicit context. The `mas.sh` wrapper excludes all disposable `.tmp*` paths and rejects incomplete staging; direct unwrapped WSL Docker-context and native/release-image evidence remain separate, and the API-only hierarchy suites retain two explicit live-evidence skips |
| SLO/capacity suite | PASS (bounded; live report attention) | `uv run --isolated pytest packages/mas-core/tests/test_slo.py apps/orchestrator-api/tests/test_slo_capacity.py -q`; the 2026-08-18 authenticated Compose `--live --json` read-back is schema-valid with five observed services, 46 events, `capacity_status: clear`, and `slo_status: attention`/`SLO_SOURCES_NOT_OBSERVED`; scalar evidence is [`slo_capacity_live.json`](../../mas/docs/provenance/slo_capacity_live.json). Native model/tool/mail-edge, load/soak/chaos, and disaster-recovery sources remain open |
| Provenance inventory | PASS | `uv run --isolated python scripts/check_provenance.py` — 21 components, including the metadata-only operator-supplied SkillSpector record |
| Python compilation | PASS | isolated `compileall` for changed runtime, API, policy, image-contract, and provenance paths |
| CEO/service/dashboard ACL API suite | PASS (unit + authenticated local API matrix; native UI matrix open) | `uv run --isolated pytest apps/orchestrator-api/tests/test_auth_boundary.py -q`; the refreshed local `/dashboard/access` and `/dashboard/sections/{section}` matrix passes for operator/CEO/service/worker identities, with evidence at [`mas/docs/provenance/dashboard_acl_live.json`](../../mas/docs/provenance/dashboard_acl_live.json) |
| Team-runner storage boundary | PASS (static/API; local live matrix) | Commit `43bee16` with policy contract `96fb71f`; `uv run --isolated pytest apps/orchestrator-api/tests/test_team_runner_storage_boundary.py apps/team-runner/tests/test_storage_client.py packages/mas-core/tests/test_network_boundary.py -q` passes 6 focused boundary tests; `uv run --isolated python scripts/check_network_boundary.py --json` passes the policy-backed static contract and `--live --json` passes all 11 current WSL2 runners with storage-health read-back; runners have no Compose DB/object-storage credentials and the API exposes only allow-listed operations |
| Dashboard ACL policy unit suite | PASS | Core policy/test group `d405ccb`; `uv run --isolated pytest packages/mas-core/tests/test_dashboard_access.py -q` covers finite sections, deny-by-default unknowns, deterministic persistence, and operator recovery invariants |
| Dashboard operator proxy/type contract | PASS | `npm run typecheck` in `apps/mas-dashboard` |
| Operator sign-in accessibility baseline | PASS (static/build/source-built Playwright; preparatory P1) | Commit `d928834`; targeted ESLint, `npm run typecheck`, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4143 npx playwright test login-accessibility.spec.ts --workers=1 --reporter=line` pass 1/1. The unauthenticated route exposes a named main/operator-sign-in structure, explicit busy/status announcements, labeled credential fields, password-visibility state, and 44px password/sign-in targets; the Impeccable detector returned no warnings for the changed page/test. Full WCAG/native-Linux visual certification remains open |
| Shared identity-resource route matrix | PASS (source-built Playwright; preparatory P1) | Commit `485dfd2`; `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4145 npx playwright test identity-resource-matrix.spec.ts --workers=1 --reporter=line` passes 9/9 with safe metadata-only fixtures across identities, approvals, audit, sessions, external accounts, domains, relay, mailboxes, and outbound mail; provider/live identity certification remains open |
| Shared identity-resource denial-state recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `0974434`; targeted ESLint, `npm run typecheck`, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4148 npx playwright test e2e/identity-states.spec.ts --workers=1 --reporter=line` pass 2/2 for stale/retry preservation and a 403 first-load denial. The shared page removes Refresh/Retry while denied, exposes a named access-status region and 44px dashboard return link, and preserves already loaded metadata-only rows if authorization is lost after a successful read; provider/live identity certification remains open |
| System Overview source-status recovery | PASS (static/build/source-built Playwright with deterministic fixtures; preparatory P1) | Commits `50cee61`, `24be4ba`; targeted ESLint, `npm run typecheck`, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4144 EXPECTED_OVERVIEW_STATE=offline npx playwright test system-overview-recovery.spec.ts --workers=1 --reporter=line` plus the same test with `EXPECTED_OVERVIEW_STATE=partial` pass 1/1 each. The page classifies seven independent control-plane/metrics reads as healthy, partial, or offline, names failed sources, avoids inferring unavailable values, exposes a 44px GET retry, and hides decorative EmptyState icons from assistive technology; full retained stale history, WCAG, native-Linux, and live control-plane evidence remain open |
| System Overview access-denied recovery | PASS (static/build/source-built Playwright with local 403 fixture; preparatory P1) | Commit `b0ab779`; targeted ESLint, `npm run typecheck`, `npm run build`, the Impeccable detector (`[]`), and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4173 EXPECTED_OVERVIEW_STATE=denied npx playwright test e2e/system-overview-recovery.spec.ts --workers=1 --reporter=line` pass 1/1 against an isolated 403 orchestrator/Prometheus fixture. Denied sources expose a named access-status region, retain available overview values as read-only context, and hide retry plus first-run seed actions; native/live ACL, retained live history, and full WCAG/native-Linux evidence remain open |
| Governance read-surface stale/retry recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `52de581`; `npm run typecheck`, `npm run lint`, `npm run build`, and the source-built `governance-states.spec.ts` pass 1/1. The combined model-profile, runtime catalogue, WorkerRun, and external-steward reads retain last-known data after a failed refresh, label the page stale, expose header Refresh and banner Retry controls, and clear the warning after successful recovery. Provider/live governance evidence remains open |
| Governance accessibility baseline | PASS (static/build/source-built Playwright; preparatory P1) | Commit `f4ae7eb`; targeted ESLint, `npm run typecheck`, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4133 npx playwright test governance-states.spec.ts --workers=1 --reporter=line` pass 1/1. The page exposes a named main/read-surface structure, explicit executive/model-profile/WorkerRun/steward/catalogue regions, a captioned/scoped WorkerRun table, accessible catalogue status, and 44px refresh, retry, executive-form, and confirmation controls; the Impeccable detector returned no warnings for the changed page, action panel, or test. This is a focused page-level baseline; full WCAG/native-Linux visual certification and live governance evidence remain open |
| System Control status stale/retry recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `f445c17`; `npm run typecheck`, `npm run lint`, `npm run build`, and the source-built `system-status-states.spec.ts` pass 1/1. The canonical `/api/system/status` read retains last-known runtime status after refresh failure, labels the state stale, exposes header Refresh and banner Retry, and keeps initial loading explicit; native/live runtime evidence remains open |
| System Control accessibility baseline | PASS (static/build/source-built Playwright; preparatory P1) | Commit `543f392`; targeted ESLint, `npm run typecheck`, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4134 npx playwright test system-status-states.spec.ts --workers=1 --reporter=line` pass 1/1. The page exposes a named main/loading state, explicit runtime-status/schedule/control/dialog regions, scheduled-event semantics, and 44px refresh, retry, shutdown/resume, schedule-input/save, and confirmation controls; the Impeccable detector returned no warnings for the changed page or test. This is a focused page-level baseline; full WCAG/native-Linux visual certification and live System Control evidence remain open |
| System Control denial-state recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `14968d4`; targeted ESLint, `npm run typecheck`, `npm run build`, the Impeccable detector (`[]`), and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4150 npx playwright test e2e/system-status-states.spec.ts --workers=1 --reporter=line` pass 3/3 for stale recovery, first-load 403 denial, and authorization loss after a successful read. Denied status reads hide Refresh/Retry and shutdown/resume/schedule mutations while preserving only last-known read context; native/live ACL and WCAG evidence remain open |
| Projects list stale/retry and accessibility baseline | PASS (static/build/source-built Playwright; preparatory P1) | Commits `d3482ab`, `7828b48`; `npm run typecheck`, targeted ESLint, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4115 npx playwright test projects-states.spec.ts --workers=1 --reporter=line` pass 1/1. The paired project/active-flow read retains last-known data through refresh failure; the table exposes a caption, scoped headers, explicit description disclosure, responsive overflow, and 44px selection/filter/sort/link/action targets. Native/live project evidence remains open |
| Projects list denial-state recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `17d25b0`; `npm run lint` (two unrelated hook warnings), `npm run typecheck`, `npm run build`, the Impeccable detector (`[]`), and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4166 npx playwright test e2e/projects-states.spec.ts --workers=1 --reporter=line` pass 4/4 for stale recovery, first-load 403 denial, retained-read 401 denial, and deletion 403 denial. Denied paired reads and mutations expose a named access-status region, preserve only previously loaded project/flow definitions as read-only text, clear selection, and hide Refresh/Retry, New Project, filters, sorting, archive, and delete controls; native/live ACL and project evidence remain open |
| Flows list stale/retry and accessibility baseline | PASS (static/build/source-built Playwright; preparatory P1) | Commits `a0faf5b`, `6b0413b`; `npm run typecheck`, targeted ESLint, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4115 npx playwright test flows-states.spec.ts --workers=1 --reporter=line` pass 1/1. The `/api/flows` read retains last-known definitions through refresh failure and exposes stale/retry recovery; the table now has an accessible name/caption, scoped headers, responsive overflow, and 44px refresh/create/search/filter/selection/link/delete targets. Native/live flow evidence remains open |
| Flows list denial-state recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `3108b02`; targeted ESLint, `npm run typecheck`, `npm run build`, the Impeccable detector (`[]`), and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4164 npx playwright test e2e/flows-states.spec.ts --workers=1 --reporter=line` pass 4/4 for stale retention, first-load 403 denial, retained-read 401 denial, and deletion 403 denial. Denied reads/deletes preserve only previously loaded definitions as read-only text and hide refresh/retry, New Flow, search/status filters, selection, editing, and deletion controls; native/live ACL and flow evidence remain open |
| Tools catalogue denial-state recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `b418f8a`; targeted ESLint, `npm run typecheck`, `npm run build`, the Impeccable detector (`[]`), and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4163 npx playwright test e2e/tools-states.spec.ts --workers=1 --reporter=line` pass 3/3 for stale recovery, first-load 403 denial, and authorization loss after a successful read. Denied reads expose a named access-status region, preserve only previously loaded tool metadata, hide refresh/retry, search, grouping, expansion, and copy controls, and retain read-only tables/details; native/live ACL and tool-service evidence remain open |
| Project detail first-load/retry recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `f364763`; `npm run typecheck`, targeted ESLint, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4112 npx playwright test project-detail-states.spec.ts --workers=1 --reporter=line` pass 1/1. A failed first project read now shows Project unavailable with backend error detail and keyboard-visible Retry; a successful retry renders the project workspace, while existing-project stale/retry recovery remains intact. Full project-page composition and live provider/worker generation remain open |
| Project detail accessibility baseline | PASS (static/build/source-built Playwright; preparatory P1) | Commit `40b87dd`; targeted ESLint, `npm run typecheck`, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4135 npx playwright test project-detail-states.spec.ts --workers=1 --reporter=line` pass 1/1. The page exposes a named page/loading state, explicit project status, 44px refresh/retry/back and primary project-view tab targets, semantic project/workspace tab-panel relationships, and an `aria-busy` workspace panel; the Impeccable detector returned no warnings for the changed page or test. This is a focused page-level baseline; full project composition, WCAG/native-Linux visual certification, and live provider/worker evidence remain open |
| Project detail denial-state recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `0671eaa`; `npm run lint` (two unrelated existing hook warnings), `npm run typecheck`, `npm run build`, the Impeccable detector (`[]`), and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4167 npx playwright test e2e/project-detail-states.spec.ts --workers=1 --reporter=line` pass 4/4 for transient recovery, initial 403 denial, retained-header 401 denial, and workflow-mutation 403 denial. Denied canonical reads/mutations expose a named access-status region, retain only the last-known project header as read-only context, clear pending interaction state, and hide refresh/retry, tabs, panels, and workspace/workflow/flow/context/evidence/repository/approval/transition controls; native/live ACL and project evidence remain open |
| Evidence detail accessibility baseline | PASS (static/build/source-built Playwright; preparatory P1) | Commits `32f3a76`, `23e2db9`; targeted ESLint, `npm run typecheck`, `npm run build`, the Impeccable detector (`[]`), and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4171 npx playwright test e2e/dashboard-evidence-detail.spec.ts --workers=1 --reporter=line` pass 11/11. The page exposes a named page/canonical-citation region, semantic bounded-detail region with an `aria-busy` refresh state, decorative-icon suppression, and 44px CEO-chat/canonical-link/Refresh targets; bounded scalar 401/403 reads expose a named denial region, retain any last-known scalar projection read-only, hide Refresh, and preserve citation identity/safe navigation. This is a focused page-level baseline; full WCAG/native-Linux visual certification and broader evidence-detail recovery remain open |
| System visualisation accessibility baseline | PASS (static/build/source-built Playwright; preparatory P1) | Commit `ed5e551`; targeted ESLint, `npm run typecheck`, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4138 npx playwright test app-operations.spec.ts --workers=1 --grep "system visualization exposes hierarchy, permissions, orchestration, and path tracing" --reporter=line` pass 1/1. The page exposes named loading/error/ready landmarks, horizontal visualization tabs with semantic tab/tabpanel links, and 44px breadcrumb, refresh, Mermaid-copy, path-trace, graph/detail, policy, retry, and back-link targets; the focused test uses deterministic fixtures for the four system-visualisation API reads, and the detector pass identified contrast classes that were corrected before commit. This is a focused page-level baseline; full WCAG/native-Linux visual certification and live control-plane evidence remain open |
| System visualisation denial-state recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `db898e7`; targeted ESLint, `npm run typecheck`, `npm run build`, the Impeccable detector (`[]`), and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4147 npx playwright test e2e/app-operations.spec.ts --workers=1 --grep "system visualization" --reporter=line` pass 4/4 for healthy, partial, offline, and 403 hierarchy states. A denied hierarchy read renders an explicit named access-status region, operator guidance, and a 44px dashboard return link without Retry; partial notices identify each failed source. Full WCAG/native-Linux visual certification and live ACL evidence remain open |
| Shared ErrorBanner decorative-icon semantics | PASS (static/build/source-built Playwright; preparatory P1) | Commit `29b700c`; targeted ESLint, `npm run typecheck`, `npm run build`, the Impeccable detector (`[]`), and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4149 npx playwright test e2e/app-operations.spec.ts --workers=1 --grep "system visualization" --reporter=line` pass 4/4, including the partial-state assertion that the shared warning icon has `aria-hidden="true"`. Full WCAG/native-Linux visual certification remains open |
| PM integrations accessibility baseline | PASS (static/build/source-built Playwright; preparatory P1) | Commit `bbd6ba3`; targeted ESLint, `npm run typecheck`, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4139 npx playwright test app-operations.spec.ts --workers=1 --grep "PM integrations preserve conflicts and expose a stale refresh retry" --reporter=line` pass 1/1. The page exposes a named busy main landmark, explicit summary/connections/reconciliation/lifecycle regions, labeled lifecycle inputs, and 44px refresh/retry/generation/approval/apply controls; the Impeccable detector returned no warnings for the changed page or test. This is a focused page-level baseline; full WCAG/native-Linux visual certification and provider-owned evidence remain open |
| System Overview accessibility baseline | PASS (static/build/source-built Playwright with local fixtures; preparatory P1) | Commit `c07b4a6`; targeted ESLint, `npm run typecheck`, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4140 E2E_ORCHESTRATOR_URL=http://127.0.0.1:9999 npx playwright test first-run-states.spec.ts --workers=1 --reporter=line` plus the same test at port `4141` against a `not_seeded` fixture pass 1/1 each. The home page exposes a named main and hero/status surface, explicit health/overview-metrics/first-run/company-project-state/Quick Links regions, decorative-icon suppression, and 44px graph/Quick Links/seed controls; the Impeccable detector returned no warnings. The direct live backend was unavailable during this run, so full control-plane, WCAG, native-Linux, and visual certification remain open |
| Project workspace stale/retry recovery and nested tab semantics | PASS (static/build/source-built Playwright; preparatory P1) | Commits `cb1c665`, `fcb0f4b`; `npm run typecheck`, targeted ESLint, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4114 npx playwright test project-workspace-states.spec.ts --workers=1 --reporter=line` pass 1/1. A failed `/workspace` refresh retains activity/resources/cost data, preserves the last repository snapshot across the partial failure, labels the surface as showing last known workspace data, and recovers through a keyboard-visible Retry. The nested Activity/Resources/Cost tabs expose semantic relationships, roving `tabIndex`, Arrow/Home/End navigation, and 44px targets. First-load failure handling is explicit; live workspace/provider/worker generation remains open |
| Flow editor load/stale/retry recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `b5098e7`; `npm run typecheck`, full `npm run lint` (two unrelated hook warnings), `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4111 npx playwright test flow-editor-states.spec.ts --workers=1 --reporter=line` pass 1/1. Existing-flow first load exposes an explicit unavailable state with Retry; refresh failures retain the last-known flow/canvas, show stale labeling, preserve backend error detail, and recover through Retry. New-flow creation remains available without the refresh control. Native/live flow/runtime recovery remains open |
| Flow editor load/stale/retry and accessibility baseline | PASS (static/build/source-built Playwright; preparatory P1) | Commits `b5098e7`, `140af1c`; targeted ESLint, `npm run typecheck`, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4115 npx playwright test flow-editor-states.spec.ts --workers=1 --reporter=line` pass 1/1. Existing-flow first load remains explicit and retryable; refresh failures retain the last-known canvas, and the editor exposes semantic header/main/palette/canvas/config landmarks plus 44px toolbar/palette/config/generated-form targets. Native/live flow/runtime recovery remains open |
| Flow editor denial-state recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `392d264`; targeted ESLint, `npm run typecheck`, `npm run build`, the Impeccable detector (`[]`), and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4165 npx playwright test e2e/flow-editor-states.spec.ts --workers=1 --reporter=line` pass 4/4 for stale recovery, first-load 403 denial, retained-read 401 denial, and save 403 denial. Denied reads/saves preserve only the last successfully loaded canvas as read-only and hide Refresh/Retry, palette, editing, undo/redo, and save controls; native/live ACL and flow/runtime evidence remain open |
| Agent Streams stale/retry recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `3e8a0ea`; `npm run lint`, `npm run typecheck`, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4123 npx playwright test streams-states.spec.ts --reporter=line` pass (focused browser coverage 1/1). The stream page retains history/messages across failed reconnect or history refresh, guards obsolete generations, labels last-known data, and exposes Reconnect/Retry; native/live Redis/router stream evidence remains open |
| Agent Streams accessibility baseline | PASS (static/build/source-built Playwright; preparatory P1) | Commit `d320383`; targeted ESLint, `npm run typecheck`, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4129 npx playwright test streams-states.spec.ts --workers=1 --reporter=line` pass 1/1. The page exposes named main/filter/feed/status regions, a captioned message table, keyboard-accessible expandable rows, 44px stream/filter/action targets, and an `aria-busy` feed state; the Impeccable detector returned no warnings for the changed page and test. This is a focused page-level baseline; full WCAG/native-Linux visual certification and live stream evidence remain open |
| Agent Streams denial-state recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `118ff18`; targeted ESLint, `npm run typecheck`, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4159 npx playwright test e2e/streams-states.spec.ts --workers=1 --reporter=line` pass 3/3 for stale recovery, first-load 403 denial, and authorization loss via SSE after a successful read. Denied history/SSE responses expose a named access-status region, invalidate in-flight stream callbacks, preserve only previously loaded messages, and hide reconnect/retry, filter, pause, clear, and copy controls; native/live ACL and Redis/router evidence remain open |
| Hiring Board stale/retry recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `7541b84`; `npm run lint`, `npm run typecheck`, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4124 npx playwright test workers-states.spec.ts --reporter=line` pass (focused browser coverage 1/1). The worker catalogue retains its last successful rows after a failed refresh, labels the view as showing last-known workers, keeps rows visible, and exposes Retry; first-load failures show an unavailable state. Native/live worker certification evidence remains open |
| Hiring Board accessibility baseline | PASS (static/build/source-built Playwright; preparatory P1) | Commit `826b4c5`; targeted ESLint, `npm run typecheck`, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4131 npx playwright test workers-states.spec.ts --workers=1 --reporter=line` pass 1/1. The page exposes named main/policy/summary/filter/table regions, integration/runtime status landmarks, a captioned/scoped worker table, keyboard-expandable rows, associated registration-dialog fields, and 44px refresh/register/filter/selection/row-action/dialog targets; the Impeccable detector returned no warnings for the changed page and test. This is a focused page-level baseline; full WCAG/native-Linux visual certification and live worker evidence remain open |
| CEO Live Feed reconnect/history recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `1761429`; `npm run lint`, `npm run typecheck`, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4125 npx playwright test ceo-states.spec.ts --reporter=line` pass (focused browser coverage 1/1). The CEO feed retains bounded history/messages across failed reconnect or history refresh, guards obsolete generations, labels last-known data, and exposes Reconnect/Retry without changing the governed composer. Native/live Redis/router CEO evidence remains open |
| CEO Command Center chat stream/history recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `beabb95`; `npm run lint`, `npm run typecheck`, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4126 npx playwright test ceo-chat-states.spec.ts --reporter=line` pass (focused browser coverage 1/1). The chat retains bounded history through live-stream failure, guards obsolete history/live callbacks, keeps the transcript last-known, exposes a keyboard-visible Retry action, and keeps history and stream failures independent. Native/live Redis/router CEO evidence remains open |
| CEO Live Feed accessibility baseline | PASS (static/build/source-built Playwright; preparatory P1) | Commit `1f947a9`; targeted ESLint, `npm run typecheck`, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4130 npx playwright test ceo-states.spec.ts --workers=1 --reporter=line` pass 1/1. The page exposes named main/composer/summary/filter/feed/status regions, 44px stream/composer/filter/recovery targets, a busy feed state, and keyboard-expandable messages; the Impeccable detector returned no warnings for the changed page and test. This is a focused page-level baseline; full WCAG/native-Linux visual certification and live CEO feed evidence remain open |
| CEO Live Feed denial-state recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `a3cbd99`; targeted ESLint, `npm run typecheck`, `npm run build`, the Impeccable detector (`[]`), and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4158 npx playwright test e2e/ceo-states.spec.ts --workers=1 --reporter=line` pass 3/3 for stale recovery, first-load 403 denial, and authorization loss after a successful read. Denied history/SSE/composer responses preserve only previously loaded messages, invalidate in-flight stream callbacks, and hide reconnect/retry, copy/clear/filter, and composer controls; native/live ACL and Redis/router evidence remain open |
| CEO Command Center chat accessibility baseline | PASS (static/build/source-built Playwright; preparatory P1) | Commit `8ffb5df`; targeted ESLint, `npm run typecheck`, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4132 npx playwright test ceo-chat-states.spec.ts --workers=1 --reporter=line` pass 1/1. The page exposes named main/workspace/transcript/composer regions, a live transcript log with busy state, 44px navigation/composer/quick-command/recovery targets, explicit chat guidance regions, and a mobile-safe accessible activity link; the Impeccable detector returned no warnings for the changed page and test. This is a focused page-level baseline; full WCAG/native-Linux visual certification and live CEO chat evidence remain open |
| CEO Command Center chat access-denied recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `038d5f2`; targeted ESLint, `npm run typecheck`, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4175 npx playwright test e2e/ceo-chat-states.spec.ts --workers=1 --reporter=line` pass 3/3 for history 403 denial, message-submission 403 denial with retained transcript, and existing stream-failure recovery. Denied history/SSE/message responses expose a named access-status region, preserve loaded transcript context read-only, invalidate the shared stream hook's in-flight callbacks, and hide Clear/retry, quick commands, composer, and confirmation controls; native/live ACL, Redis/router, and full WCAG/native-Linux evidence remain open |
| Container Logs stale/retry recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `280d363`; `npm run lint`, `npm run typecheck`, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4122 npx playwright test logs-states.spec.ts --reporter=line` pass (focused browser coverage 1/1). The SSE route retains the last log buffer after an error payload, labels it as last known, exposes Retry, and replaces the retained buffer on the first successful event after retry; native/live container log evidence remains open |
| Container Logs denial-state recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `156597c`; targeted ESLint, `npm run typecheck`, `npm run build`, the Impeccable detector (`[]`), and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4160 npx playwright test e2e/logs-states.spec.ts --workers=1 --reporter=line` pass 3/3 for stale recovery, first-load 403 denial, and authorization loss after a successful read. Denied SSE responses expose a named access-status region, invalidate obsolete stream generations, preserve only previously loaded lines, and hide load/retry, filter, clear, copy, and download controls; native/live ACL and container-log evidence remain open |
| Metrics denial-state recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `b64b15e`; targeted ESLint, `npm run typecheck`, `npm run build`, the Impeccable detector (`[]`), and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4161 npx playwright test e2e/metrics-states.spec.ts --workers=1 --reporter=line` pass 3/3 for partial recovery, first-load 403 denial, and authorization loss after a successful read. Denied responses from any query family expose a named access-status region, preserve only previously loaded series, and hide refresh/retry, time-range, and reconnect controls; native/live ACL and Prometheus evidence remain open |
| Container Logs accessibility baseline | PASS (static/build/source-built Playwright; preparatory P1) | Commit `993b1cb`; targeted ESLint, `npm run typecheck`, `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4128 npx playwright test logs-states.spec.ts --workers=1 --reporter=line` pass 1/1. The page exposes named main/filter/legend/output/status regions, 44px stream/filter/recovery targets, and an `aria-busy` log output; the Impeccable detector returned no warnings for the changed page and test. This is a focused page-level baseline; full WCAG/native-Linux visual certification and live container-log evidence remain open |
| Project evidence package stale/retry recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `bc80ad5`; `npm run typecheck`, targeted ESLint, full `npm run lint` (two unrelated hook warnings), `npm run build`, and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4110 npx playwright test project-evidence-states.spec.ts --workers=1 --reporter=line` pass (focused browser coverage 1/1). The project evidence page reads with `cache: "no-store"`; after a successful package load, a failed refresh retains the last package, labels it as last known, and exposes a keyboard-visible Retry control that clears after successful recovery. Initial failures remain explicit; full project-page composition and live provider/worker evidence remain open |
| Project evidence package denial-state recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `00f81b5`; `npm run typecheck`, full `npm run lint`, `npm run build`, the Impeccable detector (`[]`), and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4169 npx playwright test e2e/project-evidence-states.spec.ts --workers=1 --reporter=line` pass 3/3 for stale recovery, initial 403 denial, and retained-package 401 denial. Denied package reads expose a named access-status region, retain a previously loaded package as read-only context, hide Refresh/Retry and package controls, and preserve safe Back to project navigation; native/live ACL and project evidence remain open |
| Dashboard local Compose E2E matrix | PASS WITH EXPLICIT SKIP (58/59) | `npm run test:e2e -- --workers=1` in `apps/mas-dashboard` passes 58/59; focused hierarchy/path-tracing and hiring-board evaluation flows pass after selector/state repairs (`d5f596e`, `514aeeb`). Focused shell and identity regressions also pass (`e2e/dashboard-shell-accessibility.spec.ts` 2/2, `e2e/identity-states.spec.ts` 1/1), the targeted system/PM resilience filter passes 4/4, the source-built governance, System Control, Projects list, Tools catalogue, dead-letter queue, credentials stale/denial, Metrics partial/stale, Flows stale/recovery, and Agent Streams stale/recovery tests pass 1/1 (`e2e/governance-states.spec.ts`, `e2e/system-status-states.spec.ts`, `e2e/projects-states.spec.ts`, `e2e/tools-states.spec.ts`, `e2e/dlq-states.spec.ts`, `e2e/credentials-states.spec.ts`, `e2e/metrics-states.spec.ts`, `e2e/flows-states.spec.ts`, `e2e/streams-states.spec.ts`), and the one-test flow-builder golden path passes after the project flow catalogue was made `cache: "no-store"`. Authenticated WSL2 Compose coverage includes operational UI, hierarchy communication-policy/path tracing, CEO chat/hiring, project workspace, schema-driven flow editing, all eight flow runtime scenarios, hiring-board evaluation details, identity stale-record/retry state, PM integration conflict/stale retry, project-detail stale/retry state, runtime status, shell skip-link/mobile focus recovery, and system-visualization partial/offline retry states. Secret-safe evidence is [`mas/docs/provenance/dashboard_e2e_live.json`](../../mas/docs/provenance/dashboard_e2e_live.json); the live DLQ replay case is skipped pending an operator-owned safe fixture, and native-Linux/mobile/WCAG evidence remains open |
| Dead-letter queue denial-state recovery | PASS (static/build/source-built Playwright; preparatory P1) | Commit `e6ab3a1`; targeted ESLint, `npm run typecheck`, `npm run build`, the Impeccable detector (`[]`), and `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4162 npx playwright test e2e/dlq-states.spec.ts --workers=1 --reporter=line` pass 3/3 for stale recovery, first-load 403 denial, and authorization loss after a successful read. Denied read/replay responses expose a named access-status region, preserve only previously loaded messages, clear selection/replay state, hide refresh/retry, filters, selection, and replay controls, and retain read-only envelope inspection; native/live ACL and DLQ evidence remain open |
| External-account action policy and mail correlation | PASS (static/API/unit, preparatory P1) | Commits `f577675`, `cfafe38`, `2d21a2f`, `aab6285`, and `2d04b30`; `PYTHONPATH=apps/identity-service uv run --isolated pytest apps/identity-service/tests/test_identity_service.py apps/identity-service/tests/test_provider_adapters.py -q`; versioned action taxonomy, closure human-approval/session-revocation path, safe delivery-attempt trace/span persistence, normalized provider-event migration `0003_mail_edge_observations`, exact raw-body Resend/Svix verification, provider-facing ingress, and local in-memory plus rebuilt Compose/Postgres ingress certificates pass; secret-safe evidence is [`../../mas/docs/provenance/mail_edge_postgres_ingress_certification.json`](../../mas/docs/provenance/mail_edge_postgres_ingress_certification.json). Configured external provider callback, deployed/live outage, selected worker, and complete mail-edge evidence remain open |
| Production image contract | PASS (static + SBOM schema); BLOCKED (live) | Commits `42b03a3` and `2804a9f` add CycloneDX structure, unique inventory/ref identity, checked-in build-recipe, bounded digest/lock, and repository-contained artifact-path validation; `uv run --isolated pytest packages/mas-core/tests/test_image_provenance_runner.py -q` (8 passed) and `uv run --isolated python scripts/check_image_provenance.py --json` pass the static contract. `--live --require-sbom --json` exits 2 because deployment-supplied immutable `*_IMAGE_REF` values and release artifacts are absent; no SBOM licence field is used as a gate |
| Operator runtime/CLI pin contract | PASS (static; host-only entries explicit unavailable) | `uv run --isolated python scripts/check_operator_pins.py --json` verifies exact production CLI/dependency declarations and records explicit reasons for unavailable host, optional, and deployment-supplied capabilities; no licence metadata is a gate |
| Worker manifest/runtime/provenance reconciliation | PASS (static + authenticated local live binding; technical findings remain open) | `uv run --isolated python scripts/check_worker_reconciliation.py --json` validates 39 manifests; the authenticated local `--live --json` run (evidence refreshed 2026-08-11 in `180f9e0`) matches all 39 persisted defaults with zero missing rows or binding mismatches, retained at [`provenance/worker_reconciliation_live.json`](../../mas/docs/provenance/worker_reconciliation_live.json). Coding/tester rows still link to exact Semgrep evidence with 316 findings and remain `findings_review_required`; host package availability is advisory and Compose import readiness is recorded separately |
| Team-runner manifest identity bindings | static/unit | PASS | Commit `d9b1262`; `uv run --isolated pytest packages/mas-core/tests/test_team_worker_manifest_refs.py apps/team-runner/tests/test_team_config.py -q` and `uv run --isolated python scripts/check_team_worker_manifest_refs.py --json` reconcile 11 team files and 39 exact agent→manifest IDs; no registration/activation mutation and licence metadata remains informational |
| Team-runner startup manifest enforcement | unit/startup contract | PASS | Commit `569231f`; `uv run --isolated pytest apps/team-runner/tests/test_team_config.py apps/team-runner/tests/test_shutdown.py -q` verifies production-style mounted-manifest reconciliation, fail-closed missing references, exact `AgentConfig` propagation, and health metadata; startup remains read-only and does not register or activate workers |
| Default worker implementation bindings | PASS (static); BLOCKED (live without operator environment) | `uv run --isolated pytest packages/mas-core/tests/test_default_worker_bindings.py -q`; `uv run --isolated python scripts/check_default_worker_bindings.py --json` reconciles all 15 documented default worker slots across department, runtime, transport, isolation, runtime-catalogue support, runtime/integration adapter entrypoints, capability, adapter configuration, and required tools. `--live --json` is fail-closed and does not mutate runtime state; licence metadata remains informational only |
| Worker-run lifecycle contract | PASS (deterministic fixture); BLOCKED (live without operator-selected run) | `uv run --isolated python scripts/check_worker_run_lifecycle.py --json` drives the real controller/native adapter through checkpoint persistence, pause/resume, cold cancellation, cold-crash failure normalization, lease-expiry requeue, and artifact/usage-before-terminal ordering. `--live --json` returns exit 2 without mutating a live run; database, sandbox, canary, and rollback certification remain open |
| Worker trace source coverage | PASS (fixture; live dispatch explicitly gated) | Commit `24c2e35`; `uv run --isolated pytest packages/mas-core/tests/test_worker_trace_coverage.py -q` and `uv run --isolated python scripts/check_worker_trace_coverage.py --json --require-integration` require model-usage, worker-artifact, native model, native worker, and optional integration source categories. Read-only live mode requires a selected trace; dispatch requires an active model-backed worker/project/profile and `--confirm-dispatch`, is bounded to a small deterministic task/budget, and emits no raw payloads or credentials. The worker/mail-edge join and certificate are implemented in `1d8aed5` with separate scope and no mutation. No live worker-run evidence is claimed yet; mail-edge, retention, sandbox, canary, and rollback remain open; licence metadata is informational only |
| Worker/mail-edge evidence join | PASS (deterministic fixture; live sources open) | Commit `1d8aed5`; `uv run --isolated python scripts/check_worker_mail_edge_coverage.py --json --require-integration` passes the `aiat.worker-mail-edge-coverage.v1` composition with worker usage/artifact/model/worker/integration sources, explicit worker/trace correlation, one verified delivery, and one verified bounce. The report is counts-only, payload-free, and non-mutating; external provider delivery, durable worker execution, and live bounce read-back remain open |
| Durable local worker-run evidence | PASS (local Compose Postgres; live worker/provider sources open) | Commit `acd3f06`; `uv run --isolated pytest packages/mas-core/tests/test_worker_trace_coverage.py packages/mas-core/tests/test_trace_evidence.py scripts/tests/test_check_worker_run_postgres_evidence.py -q` plus `uv run --isolated ruff check scripts/check_worker_run_postgres_evidence.py` pass. The checker drives the real `WorkerRunController`/`NativeWorkerAdapter`, persists one successful run with five transitions, two events, one usage row, one artifact row, and three native spans at migration `0036_native_trace_spans`, reopens Postgres, verifies payload-free trace coverage, and removes only the reserved fixture namespace. Evidence is [`worker_run_postgres_evidence.json`](../../mas/docs/provenance/worker_run_postgres_evidence.json); live model/provider, sandbox, canary/rollback, retention, and outage evidence remain open |
| Durable worker-run lease/recovery evidence | PASS (local Compose Postgres; production multi-host open) | Commit `a413997`; `uv run --isolated pytest scripts/tests/test_check_worker_lease_recovery_postgres.py -q` and focused Ruff pass. The checker drives the existing storage claim/heartbeat/recovery/transition methods against migration `0036_native_trace_spans`: owner-B is denied while owner-A's lease is live and cannot heartbeat it, one reserved lease is explicitly expired and requeued, owner-B reclaims at attempt two, terminal re-claim is denied, eight transitions and healthy worker metadata survive connection reopen, the projection is payload-free, and scoped cleanup leaves zero fixture rows. Evidence is [`worker_lease_recovery_postgres_evidence.json`](../../mas/docs/provenance/worker_lease_recovery_postgres_evidence.json). Live worker dispatch, real host-loss/split-brain, gVisor/Firecracker, live worker/provider, and licence metadata gates remain separate |
| Durable in-flight worker-version pinning | PASS (local Compose Postgres; complete governed pin set certified, independent-host open) | Commit `7c1ef74` extending `6a10b0e`; migration `0042_worker_run_host_binding`; focused checker test/Ruff and live checker pass. The checker creates shell-v1/v2, adapter-v1/v2, bundle-v1/v2, one AIAT steward row, worker source/version metadata, and model-profile versions/snapshots; keeps a version-one `RUNNING` run pinned to its original IDs/snapshot after registry pointers advance, creates a version-two queued run with the replacement shell/adapter/bundle/model snapshot, reopens Postgres, reads labels/states back, emits no payload, and cleans workers, runs, stewards, model profiles, profile versions, and snapshots to zero. Live worker dispatch, independent host/process recovery, provider recovery, gVisor/Firecracker, and licence metadata gates remain separate |
| Deterministic worker placement policy | PASS (static/unit/fixture; worker-plane isolation certified) | Commit `3fb15db` building on `db22e60`; `uv run --isolated ruff check packages/mas-core/mas_core/worker_registry/placement.py packages/mas-core/tests/test_worker_placement.py scripts/check_worker_placement.py scripts/tests/test_check_worker_placement.py` and `PYTHONPATH=packages/mas-core:scripts uv run --isolated pytest packages/mas-core/tests/test_worker_placement.py scripts/tests/test_check_worker_placement.py -q` pass. The fixture checker covers eligible worker-host selection, blocked capacity/constraint filtering, duplicate-host fail-closed behavior, and explicit rejection of control/tool/data planes with `host_plane_mismatch`; the predicate enforces readiness/lease, labels, capabilities, sandbox/isolation, and slot/memory/GPU capacity without mutation or dispatch. Scheduler selection/fallback is certified by `d9917f8`; host-loss/split-brain, gVisor/Firecracker, and licence metadata gates remain separate. Evidence is [`worker_placement_contract.json`](../../mas/docs/provenance/worker_placement_contract.json) |
| Durable authenticated worker-host registry | PASS (local Compose Postgres; worker-plane persistence and fencing certified) | Commit `3fb15db`; migrations `0037_worker_host_registry`, `0041_worker_host_planes`, and current fencing migration `0039_worker_host_fencing`, focused host-registry unit/checker tests, Ruff, and the live Compose checker pass. The certificate stores only a token digest, rejects wrong-token registration/heartbeat, renews an AIAT-owned lease, persists and reads back the worker host plane, projects redacted placement snapshots with a lease generation, survives connection reopen, exposes an expired lease as invalid, and cleans the reserved host to zero rows. Live worker dispatch, gVisor/Firecracker, live worker/provider, and licence metadata remain separate; capacity reservation/commit is certified by `232c0bb` and deterministic scheduler integration by `d9917f8` |
| Durable worker-host capacity reservations | PASS (local Compose Postgres; generation-aware) | Commit `232c0bb`; base migration `0038_worker_host_reservations` plus current fencing migration `0039_worker_host_fencing`, focused reservation unit/checker tests, Ruff, and the live Compose checker pass. The ledger requires a READY host lease, records the host lease generation, serializes per-host capacity checks, rejects over-capacity requests, replays an idempotent key, records commit/release/expiry transitions, reads scalar capacity after connection reopen, and cleans all reserved rows. Host fencing/recovery is certified by `72e59ec`; deterministic multi-host selection/fallback is certified separately by `d9917f8`. |
| Durable worker-host multi-host scheduler | PASS (local Compose Postgres; fencing separate) | Commit `d9917f8`; `host_scheduler.py`, focused scheduler unit/checker tests, Ruff, and the live Compose checker pass. The scheduler ranks registry snapshots, falls back after a row-locked preferred-host capacity rejection, replays a globally idempotent schedule key, filters draining/unleased hosts, reports blocked full capacity, survives connection reopen, and cleans its reserved fixtures without worker/provider dispatch. Host-loss/split-brain fencing and recovery are certified separately by `72e59ec`; live worker dispatch, gVisor/Firecracker, and licence metadata remain separate. Evidence is [`worker_host_scheduler_postgres_evidence.json`](../../mas/docs/provenance/worker_host_scheduler_postgres_evidence.json) |
| Durable worker-run host binding | PASS (local Compose Postgres; assignment authority only) | Commit `08f1610e`; migration `0042_worker_run_host_binding`; focused binding/checker tests, Ruff, and the live Compose checker pass. The checker binds two payload-free Worker Runs to the scheduler's worker-plane reservation, proves preferred-host fallback, host lease-generation persistence, assignment-key replay, owner-bound commit/release settlement, connection-reopen read-back, and scoped cleanup. External runtime/provider dispatch, sandbox execution, and licence metadata remain separate/not-gated. Evidence is [`worker_run_host_binding_postgres_evidence.json`](../../mas/docs/provenance/worker_run_host_binding_postgres_evidence.json) |
| Committed worker-plane host execution | PASS (local Compose Postgres; native fixture dispatch; selected model-backed runtime open) | Commit `73c0bda`; focused host-executor/checker tests, Ruff, and `check_worker_host_execution_postgres.py --json` pass. `aiat.worker-host-execution.v1` requires a committed binding/reservation, matching worker-plane host and lease generation, READY/current host lease, and a queued run claim before delegating to `WorkerRunController`; the certificate reads back `SUCCEEDED`, one usage row, one artifact row, three native spans, released binding/reservation, payload-free evidence, connection reopen, and zero fixture rows after cleanup. Deployed gVisor/Firecracker, provider/remote runtime, selected model-backed worker, and multi-host recovery remain separate; licence metadata is not a gate. Evidence is [`worker_host_execution_postgres_evidence.json`](../../mas/docs/provenance/worker_host_execution_postgres_evidence.json) |
| Concurrent multi-host native worker execution and duplicate-effect protection | PASS (local Compose Postgres; two-host native fixture; independent-host/runtime open) | Commits `f9c717b` and `d45e4dd`; focused checker tests, Ruff, and `check_worker_multi_host_execution_postgres.py --json` pass. Two distinct worker-plane host records receive committed reservations and execute two queued runs concurrently through `WorkerHostExecutor`; a raced second claim for one run is rejected with `worker_run_claim_failed`, exactly two adapter dispatches occur, terminal and alternate-run-ID idempotency replays return the canonical `SUCCEEDED` run without redispatch, host lease-generation/current-lease equality is read back, both bindings/reservations release, two usage rows, two artifacts, three native spans per trace, and payload-free coverage survive a Postgres reopen, and scoped cleanup leaves zero fixture rows. Independent deployed hosts, selected model-backed dispatch, gVisor/Firecracker, providers, host-loss/split-brain, and provider recovery remain separate; licence metadata is not a gate. Evidence is [`worker_multi_host_execution_postgres_evidence.json`](../../mas/docs/provenance/worker_multi_host_execution_postgres_evidence.json) |
| Fenced worker-host loss and queued-run recovery | PASS (local Compose Postgres; scoped host-loss/requeue/reassignment plus same-host soak; independent-host/runtime open) | Commits `893293a` and `424805c`; focused recovery/binding/executor/soak tests, Ruff, and the production checker plus three-iteration `check_worker_host_loss_queue_recovery_soak_postgres.py --json` pass. The host-filtered reconciliation fences one expired worker host and reservation, requeues the expired Worker Run lease, rejects the stale executor before dispatch, reassigns to host B for a native retry at attempt two, then repeats that path in three separate child processes. Each iteration reads back `SUCCEEDED`, released binding/reservation, one usage row, one artifact row, three native spans, payload-free coverage, Postgres reopen, and zero fixture rows. This is same-host local consistency evidence; independent deployed hosts, gVisor/Firecracker, providers, provider outage, load/chaos, and disaster recovery remain separate; licence metadata is not a gate. Evidence is [`worker_host_loss_queue_recovery_postgres_evidence.json`](../../mas/docs/provenance/worker_host_loss_queue_recovery_postgres_evidence.json) and [`worker_host_loss_queue_recovery_soak_postgres_evidence.json`](../../mas/docs/provenance/worker_host_loss_queue_recovery_soak_postgres_evidence.json) |
| Selected model-resolution worker-host execution | PASS (local Compose Postgres; production gateway adapter over bounded local fixture; external provider open) | Commits `6cef1b8`, `9a7db70`, and `8ed53df`; focused checker/executor/gateway tests, targeted Ruff, and `check_worker_host_model_resolution_postgres.py --json` pass. The certificate creates an approved Model Profile/version, resolves it through the deterministic resolver, persists the snapshot, rejects mismatched snapshot references before claim, registers and dispatches the production `GatewayWorkerAdapter` once through a bounded local gateway double, carries valid requested/resolved references through a committed worker-host run, and reads back `aiat_gateway` mode, exact provider/model usage, terminal evidence, released binding, payload-free trace coverage, Postgres reopen, and zero fixture rows. Provider/model identifiers are local fixtures; external provider calls, independent hosts, gVisor/Firecracker, provider-backed recovery, and licence metadata remain separate/not-gated. Evidence is [`worker_host_model_resolution_postgres_evidence.json`](../../mas/docs/provenance/worker_host_model_resolution_postgres_evidence.json) |
| Pre-terminal model usage attribution | PASS (fixture/unit; local worker controller; external provider identity open) | Commit `199eb5b`; focused controller/host/governance coverage (40 tests), Ruff, and `test_worker_model_attribution.py` prove successful results must match the immutable resolution snapshot before usage/terminal evidence persistence. Missing/incomplete snapshots and provider/model mismatches fail closed; legacy/native runs without a snapshot remain compatible. This is an AIAT-owned attribution boundary, not external provider or sandbox certification; licence metadata remains separate/not-gated. |
| Governed AIAT model-gateway worker adapter | PASS (deterministic fixture + loopback HTTP; external provider/sandbox open) | Commits `080ee18`, `f6baebc`, `cec1e4c`, and `cbbfe56`; `uv run --isolated pytest packages/mas-core/tests/test_gateway_worker_adapter.py scripts/tests/test_check_gateway_worker_adapter.py scripts/tests/test_check_gateway_worker_http_fixture.py -q`, focused reconciliation/provider tests, targeted Ruff, `check_gateway_worker_adapter.py --json`, and `check_gateway_worker_http_fixture.py --json` pass. `GatewayWorkerAdapter` requires an exact resolved model, starts/stops its owned AIAT gateway client, normalizes bounded prompt/generation input, rejects non-finite temperatures, routes through the gateway client, and emits exact provider/model usage into a successful controller result. The transport is declared in `WorkerRuntime`, the builtin runtime catalogue, and the static reconciliation checker. The new loopback certificate exercises the real `LLMGatewayClient` HTTP boundary, bearer-secret header, `/v1/chat/completions` payload, one transient retry, and terminal usage read-back through `httpx.MockTransport`. The evidence performs no external provider call, network mutation, or sandbox execution; licence metadata remains separate/not-gated. Evidence is [`gateway_worker_adapter_fixture.json`](../../mas/docs/provenance/gateway_worker_adapter_fixture.json) and [`gateway_worker_http_fixture.json`](../../mas/docs/provenance/gateway_worker_http_fixture.json) |
| Gateway worker/mail-edge local composition | PASS (local scalar + durable dual-Postgres composition + retained live worker/provider/mail evidence; external callback/recovery/sandbox open) | Commits `6ebb12c`, `fa42284`, `67f1599`, `0e0a76f`, and `17f6547`; the scalar fixture and `check_gateway_worker_mail_edge_postgres.py --json --identity-ingress` plus `--provider-ingress` pass with the real `GatewayWorkerAdapter`/`WorkerRunController`, exact fixture provider/model usage, normalized delivery/verified-webhook/bounce observations, signed and Resend/Svix raw-body replay/conflict/tamper checks, durable provider-message worker/trace correlation, independent worker/identity Postgres reopen, payload-free cross-store correlation, and scoped cleanup. The retained [`gateway_worker_provider_mail_edge_live.json`](../../mas/docs/provenance/gateway_worker_provider_mail_edge_live.json) (`17f6547`) additionally records one configured `llama-3.3-70b-versatile`/`litellm` call, `SUCCEEDED` settlement, dual-Postgres reopen, delivered/bounced raw-ingress statuses `200/200`, replay/conflict/tamper outcomes `200/409/401`, payload-free coverage, generated-text redaction, and zero residual rows. External-provider callback/delivery, provider-backed recovery, sandbox, and host-runtime evidence remain open. Licence metadata remains separate/not-gated. Evidence is [`gateway_worker_mail_edge_fixture.json`](../../mas/docs/provenance/gateway_worker_mail_edge_fixture.json), [`gateway_worker_mail_edge_postgres_evidence.json`](../../mas/docs/provenance/gateway_worker_mail_edge_postgres_evidence.json), [`gateway_worker_mail_edge_provider_postgres_evidence.json`](../../mas/docs/provenance/gateway_worker_mail_edge_provider_postgres_evidence.json), and [`gateway_worker_provider_mail_edge_live.json`](../../mas/docs/provenance/gateway_worker_provider_mail_edge_live.json). |
| Gateway worker failure classification | PASS (unit/fixture; live provider recovery open) | Commit `b2ae516`; focused gateway adapter tests and Ruff pass. `GatewayWorkerAdapter` emits terminal non-retryable validation errors for bounded input failures, retryable provider errors for known transient gateway statuses, and terminal provider rejections for permanent statuses; reports retain only status/cause type metadata and do not expose response text or credentials. Live provider recovery and sandbox evidence remain separate; licence metadata is not a gate. |
| Gateway worker host-executor composition | PASS (bounded in-memory fixture; durable host/provider/sandbox open) | Commit `38c99f4`; `check_gateway_worker_host_fixture.py --json`, its focused test, host-executor/controller/adapter tests, and targeted Ruff pass. The real `WorkerHostExecutor`, `WorkerRunController`, and `GatewayWorkerAdapter` prove committed worker-plane admission, queued-run claim, exact fixture model/usage attribution, terminal settlement, binding release, and payload-free scalar trace coverage. The artifact is a synthetic bounded report pointer; no generated payload, durable host, external provider, independent host, sandbox, or live recovery claim is made. Evidence is [`gateway_worker_host_fixture.json`](../../mas/docs/provenance/gateway_worker_host_fixture.json); licence metadata remains separate/not-gated. |
| Gateway worker host failure settlement | PASS (bounded in-memory fixture; live provider recovery open) | Commit `2abc02a`; `check_gateway_worker_host_failure_fixture.py --json`, its focused test, host-executor/controller/adapter tests, and targeted Ruff pass. The real host boundary settles both retryable `429` and permanent `401` gateway cases as `FAILED`, releases the committed binding/reservation, retains only status/cause metadata, and excludes injected provider detail from evidence. This does not claim automatic live retry, external provider recovery, durable host, or sandbox execution; licence metadata remains separate/not-gated. Evidence is [`gateway_worker_host_failure_fixture.json`](../../mas/docs/provenance/gateway_worker_host_failure_fixture.json). |
| Explicit live worker-plane provider runner | PASS (bounded live provider dispatch; durable/mail/sandbox/recovery open) | Commits `f999695` and `90c3e5d`; `check_gateway_worker_provider_live.py` and its focused tests pass targeted Ruff/pytest. With explicit operator opt-in, the runner reads the configured gateway's `/v1/models` listing, selects `llama-3.3-70b-versatile`, and drives one bounded completion through the real host executor/controller/adapter chain. The retained certificate records one successful provider call, `SUCCEEDED` settlement, binding/reservation release, scalar usage/status metadata only, and no generated text or credentials. The default invocation remains blocked; durable Postgres read-back, independent hosts, gVisor/Firecracker, provider recovery, and mail-edge callback/bounce evidence remain open. Licence metadata remains non-gating. Evidence is [`gateway_worker_provider_live.json`](../../mas/docs/provenance/gateway_worker_provider_live.json). |
| Durable worker-host fencing and expired-host recovery | PASS (local Compose Postgres) | Commit `72e59ec`; migration `0039_worker_host_fencing`, focused recovery unit/checker tests, Ruff, and the live Compose checker pass. Re-registration advances the host generation and expires reservations from the old incarnation; expired READY leases are fenced to OFFLINE, their active reservations expire atomically, stale heartbeats are rejected, placement excludes the recovered host, and connection-reopen read-back remains durable. Evidence is [`worker_host_recovery_postgres_evidence.json`](../../mas/docs/provenance/worker_host_recovery_postgres_evidence.json). Live worker dispatch, gVisor/Firecracker, provider-backed recovery, and licence metadata remain separate. |
| Selected worker-run readiness preflight | PASS (fixture + read-only live diagnostic; dispatch blocked) | Commit `5553b19`; `uv run --isolated pytest packages/mas-core/tests/test_worker_run_readiness.py -q` and `uv run --isolated python scripts/check_worker_run_readiness.py --json` pass the complete snapshot fixture. The authenticated live read requires explicit worker/project UUIDs and reports status, immutable shell/adapter/skill pointers, project/company/assignment state, approved profile/version, bounded budget headroom, declared sandbox, and health without selecting or mutating state. The current coding-worker/terminal-project selection exits 2 with inactive worker, missing immutable pointers, terminal project, and missing company assignment blockers; identity, sandbox runtime, canary, live-run, rollback, and licence metadata remain separate/not-gated |
| Selected steward certification readiness preflight | PASS (fixture + authenticated read-only live diagnostic; certification blocked) | Commit `adc7b26`; `uv run --isolated pytest packages/mas-core/tests/test_worker_steward_readiness.py -q` and `uv run --isolated python scripts/check_worker_steward_readiness.py --json` pass the complete candidate fixture. The authenticated live read requires explicit worker/candidate UUIDs and reads only worker, steward, and candidate models. The current coding-worker selection exits 2 with `steward_not_ready`, `security_scan_not_passed`, and `candidate_not_found`; it never generates/certifies/approves/activates/rolls out/dispatches, and licence metadata remains separate/not-gated |
| Selected worker-run readiness health guard | PASS (fixture + mocked read-only live diagnostic) | Commits `2eea80a` and `dac268c`; `test_check_worker_run_readiness.py` proves both a 503/unavailable and a 200 response with no usable `health_status` become `read_worker_health_unavailable` rather than an unqualified not-checked result. No activation, dispatch, reservation, or licence gate is added |
| Worker certification matrix | PASS (deterministic static/unit) | Commit `a62ddb7`; `uv run --isolated pytest packages/mas-core/tests/test_worker_certification_matrix.py -q` and `uv run --isolated python scripts/generate_worker_certification_matrix.py --check`; 39 rows record exact runtime imports, transports, adapter versions, and pending evidence without claiming live certification; the regression checks generated-artifact parity, exact manifest coverage, and metadata-only licence handling |
| Default runtime adapter conformance | PASS (adapter fixture + Compose package/lifecycle and exact lock-parity probe; worker certification open) | `uv run --isolated pytest packages/mas-core/tests/test_runtime_adapter_conformance.py -q`; `docker exec mas-orchestrator-api-1 python /app/scripts/check_runtime_adapter_conformance.py --live --json` passes actual LangGraph/CrewAI adapter classes with locked LangGraph `0.6.11` and CrewAI `1.6.1`, without model/tool/provider/project calls; evidence is [`mas/docs/provenance/runtime_adapter_conformance_live.json`](../../mas/docs/provenance/runtime_adapter_conformance_live.json). Sandbox, canary, live-run, and rollback remain open |
| Runtime benchmark readiness | PASS (fresh local dependency dry-run; certification boundary remains open) | Checker/test group `ad31793`; `uv run --isolated pytest packages/mas-core/tests/test_runtime_benchmarks.py -q` and static mode pass. The authenticated local LangGraph/CrewAI dependency dry-run was refreshed 2026-08-11 in `3f15e28` and is retained at [`mas/docs/provenance/runtime_benchmarks_live.json`](../../mas/docs/provenance/runtime_benchmarks_live.json). Unavailable API/package/validation paths still return `blocked`/exit 2. This remains package benchmark evidence only, not a worker canary, project run, sandbox proof, or rollback result |
| Network boundary contract | PASS (policy-backed static + refreshed local live); native release open | Contract `96fb71f`; `uv run --isolated python scripts/check_network_boundary.py --json` and `--live --json` consume `aiat.network-boundary-policy.v1`; all runners use only `workers`, protected data services and OpenCode are off that network, gateway reachability/storage health pass, and identity/forbidden-env/socket checks pass. Six focused tests also reject runner host-port publication and public `workers` networks. Secret-safe evidence is [`mas/docs/provenance/network_boundary_live.json`](../../mas/docs/provenance/network_boundary_live.json); native release-host denial/allow evidence remains open |
| Sandbox runtime readiness | PASS (static worker/Compose contract; live gVisor blocked) | Commits `a24c554` and `2c098f5`; `uv run --isolated pytest packages/mas-core/tests/test_sandbox_runtime_readiness.py -q` and `uv run --isolated python scripts/check_sandbox_runtime_readiness.py --json` reconcile 39 manifests, 10 hardened external workers, and the OpenCode Compose runtime’s internal-only network, no host ports, non-root/read-only execution, cap-drop ALL, no-new-privileges, bounded CPU/memory/PIDs, and noexec/nosuid tmpfs. `--live --json` remains exit 2 without registered gVisor `runsc`; no runc fallback, smoke, canary, Firecracker, or upstream scan pass is claimed |
| Tool-service image budget and dependency profile | PASS (static + local Linux probe); native release open | Contracts `b24ca0c` and `e6ee8b8`; `apps/tool-service/pyproject.toml` keeps Playwright in the opt-in `browser` extra, while `Dockerfile.tool-service` keeps browser/Docling/Semgrep in the separately budgeted `extensions` profile and pins uv `0.4.30`; `uv run --isolated pytest packages/mas-core/tests/test_image_budgets.py apps/tool-service/tests/test_default_shipped_tool_catalog.py -q` and `uv run --isolated python scripts/check_image_budgets.py --json` pass the checked-in ceilings. The local profile measurements remain in [`mas/docs/provenance/image_budgets_live.json`](../../mas/docs/provenance/image_budgets_live.json). Compressed archive, clean native-Linux build/pull, SBOM, and vulnerability evidence remain open |
| API/protocol contract export | PASS (static/preparatory) | Commits `2860838`, `f8829d6`, `b3fca97`, `9a80c6c`, and `8f46ed1` regenerate/reconcile the checked-in artifacts; `uv run --isolated python scripts/check_api_contract.py --json` — 238 OpenAPI paths/135 schemas and checked-in `aiat.v1` protocol schema match runtime/provenance hashes, including the read-only system diagnostics route, native trace-evidence source, SLO/capacity, evidence-package, self-improvement outcome-action, artifact-bundle/read-back-action, bounded artifact/usage evidence-read fields, and typed read-only retention-plan response with legal-hold fields; `8f46ed1` specifically reconciles the runtime and checked-in `aiat_gateway` transport enum |
| Python SDK contract generation | PASS (static/unit/preparatory) | Commits `2860838`, `f8829d6`, `b3fca97`, and `9a80c6c`; `uv run --isolated python scripts/generate_python_api.py --check`; `uv run --isolated pytest packages/mas-api-sdk/tests -q`; 135 models and 271 operations match OpenAPI, including the typed retention-plan legal-hold fields |
| Company timezone propagation | PASS (tool-service + dashboard/Compose/default-wrapper) | Commits `8bcff1a` and `ee1361f`; focused time/schedule tests and dashboard typecheck pass. The effective company IANA timezone propagates through schedule persistence, CEO schedule views, dashboard datetime fallback, and the development wrapper; invalid input still fails closed to UTC |
| Prompt/tool and review contract reconciliation | PASS (static/unit) | Contracts `20f0499` and `5b830e9`; `uv run --isolated python scripts/check_prompt_tool_reconciliation.py --json` plus the focused tool-service review/catalogue tests; 11 shipped prompts resolve to 114 concrete manifest tools, review adapters publish signed-context `REVIEW_RESPONSE` envelopes, scanner aliases remain bounded, and the CEO privileged-action tool targets the audited gate |
| Flow node-schema contract | PASS (static/unit, preparatory P1) | `uv run --isolated python scripts/generate_flow_node_schemas.py --check`; 9 node types at v1.0 match backend validation, JSON artifact, `/flows/node-schemas`, and generated dashboard metadata |
| Dashboard node-schema editor | PASS (static/typecheck, preparatory P1) | `54ad710` plus project-evidence typecheck repair `fc4f0fa`; both flow editors render the generated contract and editable typed form, including governed worker/profile selectors; deprecated `team_id`/`action` fields are primary-form hidden and retained in collapsed compatibility controls, while API dry-run and immutable saved-definition migration report deterministic alias findings and explicit worker mappings |
| Evidence-policy contract | PASS (static/unit, preparatory P1) | Evidence tests cover required artifact kinds; `/evidence-policies` publishes built-ins and policy dry-run validation includes worker-run/repository resources |
| Evidence-policy selection | PASS (static/API/unit, preparatory P1) | Company defaults persist through the active manifest; `PUT /projects/{project_id}/evidence-policy` persists project defaults and milestone overrides; `PUT /companies/{company_id}/evidence-policy` updates the company default; `resolve_evidence_policy_selection` and `check_evidence_policy_resolution.py --json` cover project-milestone → project → flow → company-milestone → company → manual precedence without using licence metadata as a gate; live transition/recovery proof remains open |
| Project evidence package | PASS (committed core/API/dashboard surfaces; project-page/live preparatory) | Commits `a44a1aa`, `d0472af`, `cbf00d9`, `33e0384`, `82bbaeb`, `1112d5e`, `fc4f0fa`, and `bc80ad5`; core and clean-checkout API tests pass, including `uv run --isolated pytest packages/mas-core/tests/test_evidence_package_runner.py packages/mas-core/tests/test_evidence_policy_resolution.py packages/mas-core/tests/test_workflow_scaffold.py apps/orchestrator-api/tests/test_project_evidence_routes.py apps/orchestrator-api/tests/test_projects.py -q`; `npm run typecheck` passes for the project evidence page, deep-link record page, and API proxies; `uv run --isolated python scripts/check_project_evidence_package.py --json` and `scripts/check_evidence_policy_resolution.py --json` pass with metadata-only notices and fail-closed live modes. The source-built project evidence page now retains its last successful package through a failed refresh and recovers through Retry (`project-evidence-states.spec.ts` 1/1); accessibility semantics and 44px action/table targets are covered by `89091c1`. Project-page composition, live durable snapshot/provider/worker generation, and native recovery remain open |
| Reusable flow templates | PASS (static/unit/API, preparatory P1) | Six canonical templates validate through `validate_flow`; `/flow-templates` and `/flows/from-template` are covered by `test_flow_templates.py` and `test_flows.py` |
| Dashboard canonical template consumption | PASS (static/typecheck, preparatory P1) | New-flow starter cards fetch `/api/flow-templates`, preserve canonical configs/evidence metadata, remap branch references, and retain a blank fallback; `npm run typecheck` passes |
| Flow definition lifecycle | PASS (static/API, preparatory P1) | Flow export/hash, deterministic diff, import, publish, deprecate, compatible migration, and explicitly mapped active-node graph-rewrite endpoints are covered by `apps/orchestrator-api/tests/test_flows.py`; live recovery remains open |
| Flow execution semantics contract | PASS (deterministic traversal fixture, preparatory P1) | `uv run --isolated pytest packages/mas-core/tests/test_flow_execution_semantics.py -q`; `uv run --isolated python scripts/check_flow_execution_semantics.py --json` drives real fan-out/join/switch traversal, prevents duplicate/completed join scheduling, and blocks unknown switch cases without worker or storage mutation; live execution/recovery remains open |
| Durable flow-instance recovery evidence | PASS (local Compose Postgres; native/live recovery open) | Commit `1f20132`; `uv run --isolated pytest scripts/tests/test_check_flow_instance_recovery_postgres.py -q` plus Ruff pass, and the reserved checker reaches migration `0042_worker_run_host_binding`, preserves superseded retry history, preserves switch context while resetting execution authority, retries a cancelled instance, reopens Postgres, and cleans three projects/two flows/three instances with zero remaining rows. Evidence is [`flow_instance_recovery_postgres_evidence.json`](../../mas/docs/provenance/flow_instance_recovery_postgres_evidence.json); native watchdog, worker/provider canary, durable audit authority, UI, and cold-crash recovery remain open |
| Governed asynchronous flow-task binding | PASS (deterministic contract fixture, preparatory P1) | `uv run --isolated pytest packages/mas-core/tests/test_flow_worker_binding.py packages/mas-core/tests/test_flow_retry_persistence.py apps/orchestrator-api/tests/test_flows.py -q`; `uv run --isolated python scripts/check_flow_worker_binding.py --json` proves queued/claimed/running Worker Runs keep their task active, terminal states settle, parallel bindings remain copy-on-write, safe retry re-enters governed dispatch, and unknown states fail closed; the API and no-safe-node storage fallback preserve prior executions as `SUPERSEDED`; live canary/recovery remains open |
| Watchdog and safe-retry recovery semantics | PASS (deterministic controller fixture, preparatory P1) | Commit `9972b3b`; `uv run --isolated pytest packages/mas-core/tests/test_workflow_watchdog_recovery.py -q`; `uv run --isolated python scripts/check_workflow_watchdog_recovery.py --json` proves boot grace, downtime-aware timeout, watchdog failure transition, recorded-safe-state retry, terminal-state exclusion, explicit human cancellation, review-circuit escalation, node-timeout failure, and invalid-transition rejection without storage/worker mutation; native watchdog, storage-backed transition history, and cold-recovery remain open |
| Docker/Compose live certification | PASS (refreshed local WSL2 runner matrix) / BLOCKED (native release host and broader service evidence) | The `43bee16` boundary plus policy contract `96fb71f` and current `check_network_boundary.py --live --json` run pass all 11 runners: named gateways are reachable, Redis/Postgres/PgBouncer/MinIO/OpenCode/unapproved egress are denied, control-plane storage health is true, no forbidden runner environment names or Docker sockets are present, and the result is retained at [`mas/docs/provenance/network_boundary_live.json`](../../mas/docs/provenance/network_boundary_live.json). Native-Linux identity/network/dashboard, provider-pair, backup/restore, and broader live probes remain open. |
| Evidence-policy scope resolution fixture | PASS (static/unit, preparatory P1) | `uv run --isolated pytest packages/mas-core/tests/test_evidence_policy_resolution.py -q`; `uv run --isolated python scripts/check_evidence_policy_resolution.py --json` passes seven precedence/fallback cases and explicitly reports `licence_metadata_is_gate: false`; `--live` remains blocked without an authenticated API scenario |
| External-account action policy fixture | PASS (static/unit, preparatory P1) | `uv run --isolated pytest packages/mas-core/tests/test_external_account_action_policy.py -q`; `uv run --isolated python scripts/check_external_account_action_policy.py --json` reconciles all five action rules, four category dispositions, and fail-closed unknown action/category behavior without mutating identity/provider state; `--live` remains blocked until a provider-specific sandbox/outage scenario exists |
| Built-in PM/SCM adapter declarations | PASS (static/unit/fixture; provider live open) | `uv run --isolated pytest packages/mas-core/tests/test_provider_adapter_declarations.py -q`; `uv run --isolated python scripts/check_provider_adapter_declarations.py --json` reconciles the real YouTrack adapter plus GitHub `pm`/`delivery`/`checks` profiles and bounded path guards with zero provider HTTP calls or mutations; provider-specific mock/live/outage/restore evidence remains open |
| Built-in PM/SCM mocked HTTP conformance | PASS (static/unit/fixture; provider live open) | `uv run --isolated pytest packages/mas-core/tests/test_provider_adapter_http_conformance.py -q`; `uv run --isolated python scripts/check_provider_adapter_http_conformance.py --json` drives eight real-adapter YouTrack/GitHub cases for health/configuration, projection/read-back, cursors, deactivation, comments/links, GitHub source-control paths, webhook handling, and retryable/permanent failures using local responses only |
| External-account lifecycle fixture | PASS (static/unit/fixture; provider live open) | `uv run --isolated pytest packages/mas-core/tests/test_external_account_lifecycle.py -q`; `uv run --isolated python scripts/check_external_account_lifecycle.py --json` drives the actual `IdentityService` through eight in-memory cases for category approval/idempotency, one-use browser leases, credential rotation/session revocation, closure approval, immediate suspension, fail-closed unknown categories, and secret-safe output without external account/provider calls |
| Outbound-mail lifecycle fixture | PASS (static/unit/fixture; relay live open) | `uv run --isolated pytest packages/mas-core/tests/test_outbound_mail_lifecycle.py -q`; `uv run --isolated python scripts/check_outbound_mail_lifecycle.py --json` drives the actual `IdentityService` through approval pause, request/submission idempotency, definitive provider-failure retry, ambiguous-outage reconciliation hold, and secret-safe output without external relay calls |
| Self-improvement candidate detection | PASS (static/unit/fixture; live signal sources open) | Commit `4d8dddf`; `uv run --isolated pytest packages/mas-core/tests/test_improvement_candidates.py -q`; `uv run --isolated python scripts/check_self_improvement_candidates.py --json` reconciles defect, metric, upstream-update, cost, and operator-goal signals with deterministic deduplication/risk/budget mapping, conflicting-ID rejection, secret-safe metadata, and zero project/budget/credential/deployment side effects |
| Self-improvement lifecycle persistence | PASS (local Compose Postgres certificate; live worker/provider boundary open) | Commit `10983c8`; `uv run --isolated pytest mas/scripts/tests/test_check_self_improvement_postgres_evidence.py -q` plus focused self-improvement/storage tests and Ruff pass. `docker exec mas-orchestrator-api-1 sh -lc 'python /tmp/check_self_improvement_postgres_evidence.py --json'` reaches migration `0036_native_trace_spans`, persists the canonical project and revisioned lifecycle through six technical gates, rejects a stale CAS snapshot, records human approval, verifies five checksum/size read-backs, promotes and exactly rolls back, persists a terminal outcome/history, reopens the durable row, and removes only the reserved project; evidence is [`provenance/self_improvement_postgres_evidence.json`](../../mas/docs/provenance/self_improvement_postgres_evidence.json). This is local control-plane evidence only; selected model-backed worker, provider callback, budget settlement, deployment, and live issue reconciliation remain open; licence metadata is informational only |
| Machine-readable release ledger | PASS (57/57 static aggregation; native live-ledger gate `4d7a495`); BLOCKED/NO-RELEASE (current and configured live profiles) | `uv run --isolated python scripts/check_release_ledger.py --json` reports 57/57 static checks passing, including the bounded multipart, resource-profile, provider-outage, credentials-manager, identity-provider, and flow-runtime children, with `NO-RELEASE`. The unconfigured 77-check `--live --json` snapshot records 58 pass/19 blocked/0 fail with four pending items and is retained at [`provenance/release_ledger_live_current.json`](../../mas/docs/provenance/release_ledger_live_current.json); the corrected configured Compose profile at 2026-08-18T23:57:08Z records 76 pass/5 blocked/0 fail across 81 checks and is retained at [`provenance/release_ledger_live.json`](../../mas/docs/provenance/release_ledger_live.json), with the model-profile catalogue at complete 93/93 coverage and local image/trace/tool-trace/SLO children passing. Native preflight, image SBOM/scan artifacts, Firecracker/gVisor, provider/KMS host configuration, outbound mail, self-improvement source, and security-review children remain explicit; the key-alias harness run that produced a 403 trace read-back is excluded, and the corrected operator-key rerun does not change `NO-RELEASE`; licence metadata remains non-gating. |
| Release environment manifest | PASS (secret-safe static identity; refreshed policy input) | `uv run --isolated python scripts/check_release_environment.py --json` emits `aiat.release-environment.v1` with fifteen input hashes, including the network-boundary policy, security review register, tool identities, environment-presence flags, and a deterministic per-revision manifest digest without printing values or credentials. The report records the current branch, revision, changed-path count, and dirty state; its digest must be captured again for the eventual frozen release commit. |
| Native release-host preflight | BLOCKED (current local WSL2 host) | The opt-in `uv run --isolated python scripts/check_release_environment.py --require-native-linux --json` check was refreshed at 2026-08-19T00:06:33Z (`995643d`) with all ten local immutable image refs supplied and digest-pinned. The current WSL2 result is retained at [`provenance/native_release_preflight.json`](../../mas/docs/provenance/native_release_preflight.json) with safe blockers for host identity, `runsc`, and dirty state. It is a prerequisite diagnostic only; native network, image/SBOM, scan, recovery, and provider evidence remain open, and licence metadata is non-gating. |

## Still open before P0 exit

1. Repeat the network denial/allow matrix on a clean native-Linux release host
   and close the historical `DEF-2026-07-14-036` record with that evidence
   using the `check_network_boundary.py --live --json` harness. The refreshed
   local WSL2 recreation and 11-runner matrix pass are retained, but they do
   not substitute for the native release-host result.
2. Run the native-Linux network and dashboard ACL matrix, including the actual
   provisioned CEO/worker keys.
3. Resolve application `*_IMAGE_REF` values to recorded OCI digests and
   generate the image/source/SBOM ledger from a clean pull/build; run the live
   image identity helper with `--require-sbom` first and retain its
   blocked/pass/fail result. The checked-in CycloneDX shape validator is not a
   substitute for clean artifact generation or scan reconciliation.
4. Repeat the durable many-project aggregate-state scrape and both tool-service
   profile measurements on a clean native-Linux release host; the local
   10,000-project certificate, local 31-series scrape, and both local
   image-budget probes already pass and are retained as descriptive evidence.
5. Certify each default runtime/adapter against the installed lock and live
   worker-run lifecycle. The bounded local LangGraph/CrewAI dependency
   benchmark now passes and is retained at
   [`mas/docs/provenance/runtime_benchmarks_live.json`](../../mas/docs/provenance/runtime_benchmarks_live.json),
   but it is not sandbox, canary, worker-run, or rollback evidence. The new
   read-only `check_worker_run_readiness.py` preflight reports the selected
   worker/project blockers individually; its current exit-2 result does not
   substitute for activation, identity, sandbox, canary, live-run, or rollback
   evidence. Commits `2eea80a` and `dac268c` also make unavailable or malformed
   selected-worker health responses, including successful empty payloads,
   explicit `read_worker_health_unavailable` blockers rather than silently
   treating health as not checked.
6. Freeze a clean release commit/environment manifest and publish the current
   release ledger with static, contract, integration, live, recovery, and
   externally blocked evidence labels; use `check_release_ledger.py` as the
   machine-readable aggregation boundary.

Until those items are evidenced, the programme remains **not release-certified**
even though the licence policy implementation is complete for personal,
internal use.
