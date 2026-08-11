# PM Inbound Canary and Activation Status

**Updated:** 2026-08-11
**Roadmap:** [AIAT Roadmap](../../ROADMAP.md)
**Owning feature:** [Projects, Flows, Knowledge, and Evidence](FEATURE_PROJECTS_FLOWS_AND_EVIDENCE.md)
**Scope:** personal/internal AIAT instance

## Reviewed implementation

Commit `72e1aef` hardens the provider-neutral PM control plane at the inbound
mutation and lifecycle-activation boundaries:

- ACTIVE inbound issue updates persist sanitized actor, command, and projection
  evidence in the same transaction as the canonical issue CAS and outbox
  intents; idempotent evidence IDs and transaction IDs are returned on the
  inbox result, and the originating provider connection remains suppressed;
- canary validation compares the persisted provider mapping ID (not a provider
  display key), and the operator-authenticated
  `POST /integrations/inbound-canaries/{plan_id}/replay-verified-event` route
  replays only a verified, terminal-conflict `issueUpdated` inbox record after
  digest, arming, expiry, connection, and one-command checks;
- replay evidence is recorded before the normalized command is applied, the
  original inbox record remains the forensic source, and no caller-supplied
  webhook body or provider write API is accepted;
- PM lifecycle plans targeting `ACTIVE` snapshot trusted actor mappings,
  direct-command scope, binding/project blast radius, and the default-deny
  command policy; both plan creation and durable approval re-check active
  connection/binding readiness, while approval evidence and the APPROVED audit
  are written transactionally; and
- reconciliation treats explicitly evidence-only provider observations as
  non-blocking while unresolved actionable drift/conflict gates remain strict.

Resource licence/restriction values remain provenance metadata only. They do
not reject a provider connection, block a canary replay, or authorize an ACTIVE
binding.

## Verification evidence

From the repository root:

```bash
PYTHONPATH=mas/packages/mas-api-sdk ./.venv/bin/pytest -q \
  mas/apps/orchestrator-api/tests/test_pm_control_plane.py

PYTHONPATH=mas/packages/mas-api-sdk ./.venv/bin/pytest -q \
  mas/packages/mas-core/tests/test_pm_integrations.py \
  mas/packages/mas-core/tests/test_phase7_storage.py

./.venv/bin/python mas/scripts/check_api_contract.py --json
```

The focused PM/API and core storage groups pass, and the generated API
contract remains unchanged and green.

## Remaining gates

- exercise the replay route against an operator-owned verified inbox fixture;
- prove provider-specific live webhook delivery, ACTIVE canary, rollback, and
  restore behavior; and
- complete native-Linux/dashboard evidence for PM conflict, replay, and
  activation-denied states.
