# OpenCode request/response fixtures

`live-interface-summary.json` is emitted only by the successful Phase 0B live
certifier. It records sanitized operation status and bridge behavior, never
request/response bodies, prompts, source files, unredacted headers, or
credentials. Its SHA-256 is bound into `live-certification-evidence.json` and
checked by the verifier before the interface report can be approved.
