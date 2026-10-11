# OpenCode Level A provider fixture

The provider fixture serves the pinned local `fixture-model` response used by
the isolated OpenCode Level A runtime harness. Its default behavior remains a
single deterministic text reply: `AIAT_LEVEL_A_FIXTURE_REPLY`.

For boundary-contract tests only, a request containing the exact user message
`AIAT_LEVEL_A_WORKSPACE_TOOL_LOOP` selects a fixed tool-call sequence. The
fixture requires exactly one advertised run-scoped `aiat-<32 hex>_aiat_tool`
function and emits fixed tool-call requests to write a CSV category-total function,
`test_expenses.py`, and `README.md`, followed by a call to
`opencode.workspace_pytest`. The fixed final
message makes no claim that the calls were executed or that tests passed. No
tool arguments or command structure are derived from other prompt content.

Run the pure deterministic fixture contract tests with:

```sh
node --test mas/scripts/opencode_level_a/fixture.test.mjs
```

This scenario prepares only the local model-provider boundary. The fixture and
its pure tests do not start OpenCode, invoke the AIAT MCP bridge, execute
workspace tools, create a canonical WorkerRun, or persist artifacts. The
response contract alone does not certify a complete AIAT WorkerRun lifecycle;
it is not provider-backed or production evidence.
