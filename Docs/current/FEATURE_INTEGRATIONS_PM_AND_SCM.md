# PM and Source-Control Integrations Feature Specification

**Baseline:** 2026-08-10
**Status:** provider control plane implemented; YouTrack ACTIVE and GitHub production certification incomplete  
**Authority:** [AIAT Target Programme](../../AIAT_TARGET_PROGRAMME.md)

## Purpose

External project-management and source-control systems are governed collaboration surfaces. AIAT owns canonical projects, work items, workflows, actors, permissions, evidence, and revisions. Provider adapters translate commands and events without becoming a second control plane.

## Implemented now

- Provider-neutral `WorkManagementProvider` and `SourceControlProvider` contracts and registry.
- YouTrack and fake/test providers, plus GitHub source-control adapter paths.
- Connection lifecycle, capabilities, doctor, status revisions, lifecycle plans, approval/reject/apply, cutover, rollback, and audit.
- Project bindings, provider object mappings, provisioning plans, actor mappings, inbound canaries, webhook verification/replay, inbox/outbox, attempts/dispositions, conflicts, and reconciliation.
- Canonical issue create/get/update/comment/link APIs and tools.
- Source-control installation, branch, pull request, review comment, check, commit, run credential, and evidence APIs.
- Dedicated PM gateway and dashboard integration views.
- A reusable `aiat.provider-conformance.v1` fixture runner for disposable
  adapters. It exercises capability/health/configuration, canonical project and
  work-item projection, idempotent replay, cursor-bearing change listing,
  archive/deactivation, renamed-field webhook normalization, and shared
  rate-limit/stale-revision/outage/permission-loss classification.
- `scripts/check_provider_conformance.py` exposes that fixture as a reproducible
  JSON CLI. Its default in-memory mode passes without provider side effects;
  `--live` fails closed until an adapter-specific sandbox, HTTP mock, outage
  injection, and restore plan are supplied.
- `aiat.provider-adapter-declarations.v1` reconciles the actual built-in
  YouTrack and GitHub adapter classes without network access. It covers the
  YouTrack contract, GitHub `pm`/`delivery`/`checks` profiles, adapter/version
  identity, repository/ref/identifier path guards, and explicit zero-call/
  zero-mutation evidence. This is declaration/readiness evidence, not a live
  provider certificate.
- `aiat.provider-adapter-http-conformance.v1` drives the real YouTrack and
  GitHub methods through local mocked responses. It covers health/configuration,
  projection/read-back, list cursors, archive/deactivation, comments/links,
  GitHub branch/PR/check/review/commit/run-credential paths, actor/webhook
  normalization/signatures, and retryable/permanent provider failures. The
  fixture performs no external HTTP or provider mutation.
- Live YouTrack evidence ending in connection ACTIVE revision 2 and binding READ_ONLY revision 8.

## Code anchors

- Integration ports/contracts: [`mas/packages/mas-core/mas_core/integrations/`](../../mas/packages/mas-core/mas_core/integrations/)
- YouTrack provider: [`mas/packages/mas-core/mas_core/integrations/providers/youtrack.py`](../../mas/packages/mas-core/mas_core/integrations/providers/youtrack.py)
- GitHub provider: [`mas/packages/mas-core/mas_core/integrations/providers/github.py`](../../mas/packages/mas-core/mas_core/integrations/providers/github.py)
- PM gateway: [`mas/apps/pm-gateway/`](../../mas/apps/pm-gateway/)
- Current PM ledger: [`mas/docs/PM_ACTIVE_CERTIFICATION_LEDGER.md`](../../mas/docs/PM_ACTIVE_CERTIFICATION_LEDGER.md)
- Conformance runner: [`mas/scripts/check_provider_conformance.py`](../../mas/scripts/check_provider_conformance.py)
- Built-in declaration checker: [`mas/scripts/check_provider_adapter_declarations.py`](../../mas/scripts/check_provider_adapter_declarations.py)
- Built-in mocked HTTP conformance: [`mas/scripts/check_provider_adapter_http_conformance.py`](../../mas/scripts/check_provider_adapter_http_conformance.py)
- Adapter guide: [`Docs/PM_Platform_Adapter_Authoring.md`](../PM_Platform_Adapter_Authoring.md)

## Authority model

- AIAT canonical IDs and revisions remain stable if a provider is disabled or replaced.
- Connections and bindings progress through `DISABLED`, `SHADOW`, `READ_ONLY`, and narrowly scoped `ACTIVE` states.
- Changes are planned against expected revisions, hashed, approved where required, applied transactionally, and reversible.
- Inbound events require verified authenticity, replay protection, actor mapping, active canary/policy, canonical CAS, command evidence, and source-projection suppression.
- Outbound commands use a durable idempotent outbox and are reconciled with provider state.

## Default ACTIVE policy

Only explicitly mapped human actors may directly mutate an allowlisted safe field such as priority or a policy-approved status transition. Assignment, deletion, hierarchy, budget, approval, security, flow, worker, credential, policy, and unknown fields remain proposal/approval-only or AIAT-reserved.

Unknown actors fail closed. Integration identities cannot be mapped as humans to bypass that rule.

## Current YouTrack interpretation

The control plane, lifecycle, doctor, rollback, reconciliation, and READ_ONLY operation have live evidence. Two ACTIVE attempts rolled back safely because the required browser-mediated human action was unavailable or timed out. No synthetic event or API token was used as a substitute. The ACTIVE command path is therefore not certified.

## GitHub target

- GitHub App installation with repository-scoped permissions.
- Short-lived, run/project-scoped credentials instead of broad personal tokens.
- Governed branches, pull requests, reviews, checks, and commit evidence.
- Webhook signature/replay/actor mapping and source-suppression proof.
- Idempotent retries, rate-limit/backoff, dead-letter, reconciliation, and uninstall/revoke recovery.
- GitHub Issues and Actions as default projections/adapters while AIAT remains canonical.

## Remaining gaps

- Complete the one real mapped-human YouTrack ACTIVE command certification.
- Complete GitHub App live mutation and webhook certification.
- Run the conformance fixture against each provider's mocked HTTP adapter and
  retain the report as certification evidence; the shared fixture/CLI and
  status classifier, built-in declaration/readiness fixture, and deterministic
  YouTrack/GitHub mocked HTTP fixture are implemented, but live provider
  evidence remains.
- Finish dashboard drill-down for lifecycle plans, evidence, actor maps, canaries, dispositions, and reconciliation diffs.
- Reconcile the older `Docs/PM_Platform_*` ledger with the current `mas/docs/PM_ACTIVE_*` evidence without overwriting history.
- Certify a second work-management provider only after the provider conformance suite is stable.

## Acceptance criteria

- A provider outage or credential loss never corrupts canonical state.
- Duplicate webhook/outbox delivery performs one canonical mutation.
- Unmapped actors, disallowed fields, stale revisions, expired canaries, and missing evidence are denied.
- Lifecycle application and rollback preserve exact revisions, digests, approval evidence, and reconciliation proof.
- Provider-originated approved commands avoid echo loops.
- Disable/uninstall/revoke leaves AIAT usable and exposes all unsynchronised work.
