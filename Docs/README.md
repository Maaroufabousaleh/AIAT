# AIAT documentation hub

AIAT is a large internal programme, so the repository keeps a small normative
spine and labels the rest as implementation, evidence, or historical material.
Use this page when you need the shortest path to the right document.

> [!IMPORTANT]
> The target programme and roadmap describe what AIAT owns. The provenance
> catalogue records external resources and licence metadata. Neither replaces
> the security, sandbox, compatibility, privacy, budget, recovery, or human
> approval checks enforced by the implementation.

## Source-of-truth order

1. [`AIAT_TARGET_PROGRAMME.md`](../AIAT_TARGET_PROGRAMME.md) — normative product, architecture, and scope target.
2. [`ROADMAP.md`](../ROADMAP.md) — ordered implementation and documentation index.
3. [`current/`](current/) — maintained feature specifications, plans, and status notes.
4. [`../mas/docs/`](../mas/docs/) — focused implementation, deployment, and evidence references.
5. [`archive/`](archive/) — historical research, prompts, and review drafts;
   useful inputs that do not override the maintained set.

## Start here

| Question | Document |
| --- | --- |
| What is AIAT meant to be? | [`AIAT_TARGET_PROGRAMME.md`](../AIAT_TARGET_PROGRAMME.md) |
| What should be built next? | [`ROADMAP.md`](../ROADMAP.md) |
| Which documents are authoritative? | [`current/DOCUMENTATION_AUTHORITY_STATUS.md`](current/DOCUMENTATION_AUTHORITY_STATUS.md) |
| How does the system fit together? | [`../mas/docs/ARCHITECTURE.md`](../mas/docs/ARCHITECTURE.md) |
| How do I run the MAS workspace? | [`../mas/README.md`](../mas/README.md) |
| How do I deploy or operate an integration? | [`PM_Platform_Deployment.md`](PM_Platform_Deployment.md), [`PM_Platform_Integration_Runbook.md`](PM_Platform_Integration_Runbook.md) |
| Where is release evidence kept? | [`../mas/docs/AIAT_CURRENT_RELEASE_LEDGER.md`](../mas/docs/AIAT_CURRENT_RELEASE_LEDGER.md), [`../mas/docs/provenance/`](../mas/docs/provenance/) |
| Where are licence and source notices? | [`../THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md), [`../mas/docs/provenance/third_party_components.yaml`](../mas/docs/provenance/third_party_components.yaml) |
| How should the repository look and communicate? | [`DESIGN.md`](../DESIGN.md), [`docs/assets/`](../docs/assets/) |

## Maintained feature specifications

These documents define the current feature boundaries and implementation
expectations. Each links to the relevant plan, evidence, and code where
available.

- [`FEATURE_CONTROL_PLANE_AND_COMPANY.md`](current/FEATURE_CONTROL_PLANE_AND_COMPANY.md)
- [`FEATURE_PROJECTS_FLOWS_AND_EVIDENCE.md`](current/FEATURE_PROJECTS_FLOWS_AND_EVIDENCE.md)
- [`FEATURE_WORKERS_STEWARDS_AND_MODELS.md`](current/FEATURE_WORKERS_STEWARDS_AND_MODELS.md)
- [`FEATURE_WORKER_HOST_EXECUTION.md`](current/FEATURE_WORKER_HOST_EXECUTION.md)
- [`FEATURE_DASHBOARD_AND_OPERATOR_UX.md`](current/FEATURE_DASHBOARD_AND_OPERATOR_UX.md)
- [`FEATURE_DATA_STORAGE_AND_MEMORY.md`](current/FEATURE_DATA_STORAGE_AND_MEMORY.md)
- [`FEATURE_IDENTITY_MAIL_AND_CREDENTIALS.md`](current/FEATURE_IDENTITY_MAIL_AND_CREDENTIALS.md)
- [`FEATURE_INTEGRATIONS_PM_AND_SCM.md`](current/FEATURE_INTEGRATIONS_PM_AND_SCM.md)
- [`FEATURE_SECURITY_OBSERVABILITY_AND_OPERATIONS.md`](current/FEATURE_SECURITY_OBSERVABILITY_AND_OPERATIONS.md)
- [`FEATURE_SLO_CAPACITY_AND_OPERATIONS.md`](current/FEATURE_SLO_CAPACITY_AND_OPERATIONS.md)
- [`FEATURE_TRACE_EVIDENCE_AND_RETENTION.md`](current/FEATURE_TRACE_EVIDENCE_AND_RETENTION.md)
- [`FEATURE_MAIL_EDGE_OBSERVABILITY.md`](current/FEATURE_MAIL_EDGE_OBSERVABILITY.md)
- [`FEATURE_OBJECT_STORE_MIGRATION_STATUS.md`](current/FEATURE_OBJECT_STORE_MIGRATION_STATUS.md)

## Ordered plans and status

- [`P0_RELEASE_INTEGRITY_PLAN.md`](current/plans/P0_RELEASE_INTEGRITY_PLAN.md)
- [`P1_DEFAULT_PRODUCT_COMPLETION_PLAN.md`](current/plans/P1_DEFAULT_PRODUCT_COMPLETION_PLAN.md)
- [`P2_SCALE_STORAGE_AND_AUTONOMY_PLAN.md`](current/plans/P2_SCALE_STORAGE_AND_AUTONOMY_PLAN.md)
- [`P0_RELEASE_INTEGRITY_STATUS.md`](current/P0_RELEASE_INTEGRITY_STATUS.md)
- [`P0_RELEASE_SCOPE_MATRIX.md`](current/P0_RELEASE_SCOPE_MATRIX.md)
- [`FLOW_DEFINITION_PORTABILITY_STATUS.md`](current/FLOW_DEFINITION_PORTABILITY_STATUS.md)
- [`FLOW_EXECUTION_SEMANTICS_STATUS.md`](current/FLOW_EXECUTION_SEMANTICS_STATUS.md)
- [`FLOW_INSTANCE_RECOVERY_STATUS.md`](current/FLOW_INSTANCE_RECOVERY_STATUS.md)
- [`WORKFLOW_WATCHDOG_RECOVERY_STATUS.md`](current/WORKFLOW_WATCHDOG_RECOVERY_STATUS.md)

## Focused implementation references

The `mas/docs/` directory holds practical notes that are too close to a
service, deployment, or evidence flow to belong in the normative feature set:

- [`ARCHITECTURE.md`](../mas/docs/ARCHITECTURE.md)
- [`OMNIROUTE.md`](../mas/docs/OMNIROUTE.md)
- [`P0_NATIVE_LINUX_EXIT_RUNBOOK.md`](../mas/docs/P0_NATIVE_LINUX_EXIT_RUNBOOK.md)
- [`PM_ACTIVE_READINESS.md`](../mas/docs/PM_ACTIVE_READINESS.md)
- [`PM_ACTIVE_DEPLOYMENT.md`](../mas/docs/PM_ACTIVE_DEPLOYMENT.md)
- [`PM_ACTIVE_DASHBOARD.md`](../mas/docs/PM_ACTIVE_DASHBOARD.md)
- [`PM_ACTIVE_CERTIFICATION_LEDGER.md`](../mas/docs/PM_ACTIVE_CERTIFICATION_LEDGER.md)
- [`provenance/third_party_components.yaml`](../mas/docs/provenance/third_party_components.yaml)
- [`provenance/operator_pins.yaml`](../mas/docs/provenance/operator_pins.yaml)
- [`provenance/release_ledger.yaml`](../mas/docs/provenance/release_ledger.yaml)

## Evidence and terminology

- **Fixture** means deterministic local evidence with no external provider or
  deployment claim.
- **Operator-observed** means a human-run boundary check with its own scope and
  limits; it is not automatically a release approval.
- **Live/provider** means an explicitly requested external or deployed check and
  must retain its environment, source, and cleanup boundary.
- **Blocked** means the evidence or environment is unavailable; it is not a
  passing substitute for the missing proof.
- **Licence metadata** is recorded for provenance and operator awareness. For
  this internal programme it is not an activation or discovery gate.

## Updating documentation

When implementation changes:

1. Update the closest maintained feature or plan document.
2. Update the relevant implementation reference or release ledger.
3. Record external component/version/source/licence facts only in
   [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md) and
   [`mas/docs/provenance/third_party_components.yaml`](../mas/docs/provenance/third_party_components.yaml).
4. Run the repository checks from `mas/`:

   ```bash
   uv run python scripts/check_docs_index.py --json
   uv run python scripts/check_provenance.py
   uv run pytest
   ```

Avoid copying historical claims into current status prose. If a historical
document remains useful, link it and label its authority explicitly.
