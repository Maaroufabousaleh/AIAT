# Canonical Flow Template Status

**Updated:** 2026-08-11
**Roadmap:** [AIAT Roadmap](../../ROADMAP.md)
**Owning feature:** [Projects, Flows, Knowledge, and Evidence](FEATURE_PROJECTS_FLOWS_AND_EVIDENCE.md)
**Scope:** personal/internal AIAT instance

## Reviewed implementation

Commit `eb146bf` adds the canonical reusable-flow catalogue and its first
dashboard consumption path:

- six deterministic templates cover software delivery, research, hiring,
  incident response, integration rollout, and guarded self-improvement;
- every template carries the versioned flow schema and relevant evidence or
  lifecycle metadata, and is parsed and topology-validated before use;
- `GET /flow-templates` publishes the catalogue and
  `POST /flows/from-template` reuses the normal validated flow-creation path;
- the dashboard proxy at `app/api/flow-templates` consumes the control-plane
  catalogue; and
- the new-flow editor remaps template node IDs, parallel branch references,
  and switch targets on every application, preserves template metadata, and
  keeps a blank-canvas fallback when the catalogue is unavailable.

Resource licence or restriction notices remain metadata only. They do not
remove templates, prevent discovery, block creation, or change flow execution
authorization.

## Verification evidence

From `mas/`:

```bash
uv run --isolated pytest \
  packages/mas-core/tests/test_flow_templates.py \
  apps/orchestrator-api/tests/test_flow_templates_api.py -q
```

The core fixture proves deterministic ordering and validates every template
through the canonical parser/topology validator. The API fixture covers
catalogue discovery, unknown-template rejection, and creation through the
validated flow route. Focused dashboard ESLint passes for the editor and
proxy.

## Remaining gates

- run the dashboard template-selection/browser golden path against a live
  control-plane catalogue;
- verify persisted template-created definitions and immutable version history
  with live storage;
- complete live execution/recovery evidence for template graphs; and
- capture the live browser golden path after the project-evidence typecheck
  repair (`fc4f0fa`).
