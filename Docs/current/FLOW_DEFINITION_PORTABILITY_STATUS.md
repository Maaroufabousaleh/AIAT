# Flow Definition Portability Status

**Updated:** 2026-08-11
**Roadmap:** [AIAT Roadmap](../../ROADMAP.md)
**Owning feature:** [Projects, Flows, Knowledge, and Evidence](FEATURE_PROJECTS_FLOWS_AND_EVIDENCE.md)
**Scope:** personal/internal AIAT instance

## Reviewed implementation

Commit `a219092` adds deterministic flow-definition portability controls:

- `mas_core.workflow.definition_tools` computes a stable SHA-256 definition
  hash and a deterministic node/edge/metadata diff;
- `GET /flows/{flow_id}/export` returns a versioned `aiat.flow-export.v1`
  envelope with the source flow and definition hash;
- `POST /flows/diff` compares two persisted definitions without mutating
  either flow;
- `POST /flows/import` reuses the validated flow-creation path, so imported
  definitions receive the same schema/topology checks as authored definitions;
- `POST /flows/{flow_id}/publish` and `/deprecate` change selection state
  without deleting flow history; and
- `test_flow_definition_tools.py` and
  `test_flow_definition_lifecycle_api.py` cover deterministic hashes, diff
  output, validation reuse, export, import, publish, and deprecate behavior.

The portability envelope is an interchange/read-model surface. It does not
silently execute imported definitions, overwrite an existing version, or
rewrite running instances.

Resource licence or restriction notices remain metadata only. They can be
shown in provenance and operator views, but they do not remove an export,
reject an import, block discovery, or change execution authorization.

## Verification evidence

From `mas/`:

```bash
uv run --isolated pytest \
  packages/mas-core/tests/test_flow_definition_tools.py \
  apps/orchestrator-api/tests/test_flow_definition_lifecycle_api.py \
  apps/orchestrator-api/tests/test_flows.py -q
```

The committed portability tests pass in the focused API/core group. The
topology and generated-schema checks also remain green in the current flow
verification set.

## Remaining gates

- add a browser-facing import/export control and human confirmation path;
- validate persisted imported versions and publish/deprecate concurrency
  against live storage;
- add signed or operator-authenticated file transfer handling when flows move
  outside the local workspace; and
- connect export/diff evidence to the release ledger and recovery runbooks.
