# OpenCode event fixtures

`live-event-summary.json` is emitted only by the successful Phase 0B live
certifier. It captures normalized event-type compatibility evidence without
storing SSE payloads, prompts, workspace content, headers, or credentials.
Its SHA-256 is bound into `live-certification-evidence.json` and checked by
the verifier before the interface report can be approved.
