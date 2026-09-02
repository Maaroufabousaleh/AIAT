# Microsoft Agent Framework optional runtime profile

This profile is an isolated dependency set for the optional AIAT Microsoft
Agent Framework adapter. It is deliberately separate from the production
Compose images, whose tool-service MCP pin remains `1.23.3`.

Create an operator-owned virtual environment and install the exact profile:

```sh
uv venv .venv-maf
uv pip install --prerelease=allow --python .venv-maf/bin/python \
  -r mas/infra/runtime/maf/requirements.txt
```

Run the secret-safe deterministic certification probe against that interpreter:

```sh
uv run --isolated python mas/scripts/check_maf_runtime.py \
  --python .venv-maf/bin/python --json
```

The probe injects a local fake chat client and never contacts a model provider,
MCP server, project, tool, or credential store. A passing result certifies only
the pinned package imports, AIAT adapter construction, bounded task translation,
response normalization, health, and shutdown. Security scans, sandbox proof,
model-backed canaries, live worker runs, approvals, and rollback remain separate
gates.

The profile is optional. Its package versions and certification result are
recorded in [`mas/docs/provenance/runtime_compatibility.yaml`](../../../docs/provenance/runtime_compatibility.yaml)
and [`mas/docs/provenance/maf_runtime_certification.json`](../../../docs/provenance/maf_runtime_certification.json).
