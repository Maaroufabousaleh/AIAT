# OpenHands v1.43.0 morning certification preparation

**Repository-local refresh:** 2026-08-24, current certification-path
hardening at `65237f3` (including `f3f4050`); the branch also
contains the earlier candidate/evidence history. The next live run must freeze
the exact SHA actually selected by the operator.

This is a manual preparation guide for the inactive candidate. It does not
activate OpenHands, approve the steward record, or dispatch a workflow. The
workflow is `workflow_dispatch` only and must be dispatched once against an
explicit frozen commit after the preflight passes.

The local static release ledger remains 63/63 passing with two pending
evidence items and `NO-RELEASE`. The OpenHands gate matrix remains
`BLOCKED_INCOMPLETE_MANDATORY_GATES` until a provider-backed run supplies live
task and lifecycle evidence. No workflow was dispatched during this refresh.
The previously prepared `1fcaf6cb62c6583efdf0ea1396e3d52329453fc3` SHA is an
ancestor of the reviewed branch tip and is not the current dispatch candidate;
the safe preflight correctly rejects it unless the checked-out repository tip
matches the requested SHA. The current reviewed tip includes `cc48cca` and
`a2c886e` auto-route/gate-wiring hardening, `a39788a` evidence-retention
validation, `a377dab` scalar provider attribution for successful `auto/coding`
evidence, and `1c4a426` Agent Server health-blocker output diagnostics. Freeze
and preflight that exact tip before any deliberate run. The current tip also
contains `f3f4050`, which hardens the run-scoped skill/plugin boundary described
below, and `65237f3` records that capability boundary in the maintained
status surface.
The clean-candidate static certificate was refreshed from a fresh clone at
`69f5fb4`; it remains valid for reviewed descendants because the validator now
treats the retained candidate SHA as authoritative by default while preserving
an explicit strict tip-match mode. It retains the same 63/63 static result,
two pending OpenCode evidence items, and `NO-RELEASE`.
The current workflow also verifies LiteLLM/OmniRoute source-archive hashes,
rejects host-bound gateway targets, validates the sanitized evidence tree, and
records run-scoped profile disposal through Agent Server container absence.
Cleanup additionally removes generated AIAT/tool/gateway secret entries from
the runner's `GITHUB_ENV` file and records that scalar absence assertion. The
normal OpenHands transport factory now refuses inline approval mappings and
loads only the committed canonical interface-verification report; its MCP
grant issuer also rejects tool names outside the bounded repository/test
coding surface before creating a run grant. Approved reports additionally
require an explicit approval-record identifier. The API image now packages
only this canonical candidate provenance directory and resolves the
source-style manifest ref under its `/app/docs` evidence root, so activation
does not depend on a repository checkout being present in the deployed image.

The overnight dependency and full local commit reconciliation is recorded in
[`overnight-reconciliation.json`](./overnight-reconciliation.json). It contains
only exact commit identifiers, scalar wiring, and secret-free boundary state.

The workflow auth-boundary step is compatible with an explicitly frozen
candidate checkout: `488a593` detects whether the candidate helper supports
native transport-retry flags and otherwise uses a bounded outer retry, while
retaining only scalar probe mode/count evidence. The governed provider-pool
specification is recorded in `gateway-provenance.json` and implemented by
`ecc1b48`; CI remains Groq-only (`GROQ_API_KEY`), while Gemini/Cerebras are
documented as future explicit allowlist options and no credentials are
discovered or added automatically.

The current disposable gateway keeps two independent observations: the exact
`groq/openai/gpt-oss-120b` baseline must pass before it can support a live
certification decision, while the LiteLLM `omniroute-coding` request exercises
OmniRoute `openai/auto/coding`. A successful auto-route record retains only the
bounded single-provider attribution (`groq`, the baseline model, and route
basis); it does not retain provider credentials, connection IDs, or response
payloads.

The pinned Agent Server profile now handles v1.43.0's skill deny-list semantics
explicitly: it materializes the server-discovered public/user catalog, persists
those names as disabled, and verifies a second materialization resolves zero
skills. The certification container uses an isolated home, disables VS Code,
VNC, and registered marketplaces, and rejects project workspaces containing
OpenHands skill/plugin sources before the coding wave. This is certification
scoping around the immutable upstream image; it does not modify or weaken the
candidate runtime.

### Historical workflow implementation failure

Run `32645055499` (job `97207942478`) is preserved as
[`github-run-32645055499-failure.json`](./github-run-32645055499-failure.json).
The provider preflight and all three pinned image pulls passed; no live gate
wave ran. The run is classified as `FAILED_CERTIFICATION_IMPLEMENTATION` with
reason `MALFORMED_WORKFLOW_HEREDOC`, not as a provider, image, gVisor, or
OpenHands runtime failure. The workflow fix is `a02899b`, which restores both
missing `PY` terminators in the image/provenance step and makes the validator
reject unclosed or marker-count-mismatched heredocs. The next candidate SHA
must be frozen after this fix and verified by the safe preflight.

Run `32648660093` (job `97216753646`) is preserved as
[`github-run-32648660093-failure.json`](./github-run-32648660093-failure.json).
It is a separate `FAILED_CERTIFICATION_IMPLEMENTATION` record: provider and
candidate preflights, image pulls, platform verification, and the immutable
gateway pin verifier all passed, but the old inline provenance check queried
only `refs/tags/v1.90.0^{}`. LiteLLM v1.90.0 is a lightweight tag, so that
peeled-ref lookup was empty and the live gate wave never ran. The corrected
workflow delegates all six checks to a diagnostic helper that accepts exact
annotated or lightweight tags and writes per-check scalar evidence before it
fails. This record is not provider, image, gVisor, runtime, or security
evidence.

Run `32651712645` (job `97224247350`) is preserved as
[`github-run-32651712645-failure.json`](./github-run-32651712645-failure.json).
The provenance, image pulls, network creation, and OmniRoute container/Docker
health passed, but the pinned release's `/api/health/ping` returned HTTP 401
after startup. The run remains a blocked runtime-readiness record, not Groq,
provenance, gVisor, or OpenHands evidence. The corrected harness uses the
release's public read-only `/api/monitoring/health` endpoint for application
readiness, keeps dashboard/management on port 20128, uses the
OpenAI-compatible API bridge on port 20129, and separately verifies its API-key
boundary.

Run `32662156390` (job `97249868393`) is preserved as
[`github-run-32662156390-failure.json`](./github-run-32662156390-failure.json).
It passed OmniRoute readiness/authentication, exactly-one Groq route
provisioning, LiteLLM startup, gateway provenance, and cleanup, then stopped at
the provider model request with `PROVIDER_MODEL_NOT_FOUND`. The old
`llama-3.3-70b-versatile` harness choice was retired by Groq on 2026-08-16;
the next candidate uses a live-discovered `openai/gpt-oss-120b` baseline before
the governed `auto/coding` route. `omniroute-coding` remains the only model id
visible to OpenHands and no additional provider secrets are required for the
one-provider CI pool. The workflow retains separate scalar evidence for the
baseline (`gateway/provider-baseline.json`) and auto-router route
(`gateway/auto-routing.json` plus the compatibility `route-probe.json`).

Run `32670107128` (job `97269525240`) is preserved as
[`github-run-32670107128-failure.json`](./github-run-32670107128-failure.json).
The exact pinned images, provenance, network, OmniRoute container, and public
management health all passed. The pre-fix single-attempt API-auth probe
observed transport failures because OmniRoute's OpenAI-compatible bridge binds
after its dashboard listener; no provider route, LiteLLM, tool-service, or
OpenHands gate ran. The historical artifact ended
`BLOCKED_INCOMPLETE_MANDATORY_GATES`; the normalized blocker is
`BLOCKED_MODEL_GATEWAY` / `MODEL_GATEWAY_TRANSPORT_FAILURE`, not provider or
credential evidence. The follow-up bounded-retry hardening is in `f941d70`;
the gate evaluator and summary now retain and report this narrower class.

Run `32673150585` (job `97276954958`) is preserved as
[`github-run-32673150585-failure.json`](./github-run-32673150585-failure.json).
The exact candidate checkout, native gVisor, pinned image/provenance checks,
OmniRoute management health/API authentication, exactly-one Groq route, and
zero-residue cleanup passed. The first live deterministic
`groq/openai/gpt-oss-120b` request returned HTTP 502 and was recorded as a
retryable `PROVIDER_SERVER_ERROR`; LiteLLM, tool-service, OpenHands, and the
live gate wave were not run. This is not evidence of image, gVisor, or
OpenHands failure, and it is not a reason to rerun without a material provider
state change.

The repository-local follow-up keeps this evidence immutable while hardening
the baseline probe: it makes the Groq GPT-OSS request contract explicit,
allows at most one retry for a transient server/transport boundary, and
retains only scalar attempt status plus bounded provider error code/type fields.
Persistent 5xx, authentication, rate-limit, model, or response-shape failures
still block closed; no live result is claimed by this change.

## Operator sequence

1. Set or verify the two non-secret repository variables. No profile UUID,
   internal tool secret, or internal model-gateway secret is a persistent
   GitHub input.

   ```bash
   REPO="OWNER/REPOSITORY"
   gh variable set OPENHANDS_MODEL_ID --repo "$REPO" --body omniroute-coding
   gh variable set OPENHANDS_MCP_SETTINGS_KEY --repo "$REPO" --body aiat-openhands-v1-43-0-coding
   ```

2. Set the one external provider secret interactively. The value is read from
   stdin and is never embedded in shell history or documentation.

   ```bash
   gh secret set GROQ_API_KEY --repo "$REPO"
   ```

3. Freeze and inspect the exact candidate commit. Do not use an implicit
   `HEAD` in the dispatch command.

   ```bash
   WORKFLOW_REF="agent/fix-review-p1"
   CANDIDATE_SHA="$(git rev-parse HEAD)"
   git show -s --format='%H %s' "$CANDIDATE_SHA"
   ```

4. Run the safe read-only preflight. It checks secret/variable presence only,
   never retrieves a secret value, and runs the bounded deterministic tests.

   ```bash
   python3 mas/scripts/check_openhands_dispatch_preflight.py \
     --repo . \
     --candidate-sha "$CANDIDATE_SHA" \
     --github-repo "$REPO" \
     --output /tmp/aiat-openhands-dispatch-preflight.json
   ```

   Continue only when it reports `READY_TO_DISPATCH`/`ready_to_dispatch=true`.
   The preflight runs the bounded OpenHands suite through `uv run
   --isolated pytest`; its `--skip-tests` diagnostic mode always remains
   non-ready and must not be used for dispatch preparation. The workflow also
   gates all model/runtime stages on its non-secret candidate preflight, so a
   variable, pin, or binding mismatch is recorded as configuration evidence
   and cannot reach the expensive stages.
   A dirty but understood local worktree does not change the exact SHA used by
   GitHub; do not stage or normalize the protected memory files.

5. Dispatch exactly one run against that same SHA. Do not dispatch OpenCode.

   ```bash
   gh workflow run openhands-candidate-certification.yml \
     --repo "$REPO" \
     --ref "$WORKFLOW_REF" \
     -f candidate_sha="$CANDIDATE_SHA"
   ```

6. Watch and inspect the single run. Do not automatically rerun a blocked or
   failed certification; first identify a material prerequisite change.

   ```bash
   RUN_ID="$(gh run list --repo "$REPO" --workflow openhands-candidate-certification.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
   gh run watch "$RUN_ID" --repo "$REPO" --exit-status || true
   gh run view "$RUN_ID" --repo "$REPO" --log-failed
   gh run download "$RUN_ID" \
     --repo "$REPO" \
     --name "openhands-candidate-certification-${RUN_ID}" \
     --dir "/tmp/aiat-openhands-evidence-${RUN_ID}"
   python3 -m json.tool "/tmp/aiat-openhands-evidence-${RUN_ID}/certification/gate-evaluation.json"
   ```

The only expected persistent secret for this candidate workflow is
`GROQ_API_KEY`. `AIAT_TOOL_SECRET`, `OPENHANDS_SESSION_API_KEY`, and
`OPENHANDS_MODEL_GATEWAY_API_KEY` are generated, masked, and cleaned up inside
the run. The profile UUID and MCP registration are also run-scoped. A passing
certification does not activate the worker; evidence registration and steward
activation approval remain independent. The current local candidate baseline
includes exact disposable-network topology assertions, candidate-worker-bound
certification authorization, a bounded 20-iteration/300-second adapter budget,
strict MCP allowlist/delete read-back, and repository-relative interface-report
resolution, and zero-residue verification for the workspace and tool image.
The current candidate hardening (`732ab3d`, `2cf8591`) also re-runs the fixed
task test after model execution, compares the exact workspace diff to the
task contract, requires returned artifact hashes, and scans transient events
for secret disclosure. `7bc9f63` additionally exercises bounded pause/resume,
interrupt, and timeout controls, requires an observed remote pause transition,
and scans lifecycle events without retaining payloads. The gateway/provisioning
wave now gates LiteLLM, the tool bridge, Agent Server, and live model stages on
successful OmniRoute route validation and the runsc-to-LiteLLM probe; a failed
control-plane/provider stage cannot fall through into model execution. The
post-run verifier also scans disposable workspace files for secret canaries
using hashes/counts only. A model response alone cannot advance those gates.
The subsequent repository-local group (`88eeb6d`, `55930ad`, `3e31206`,
`b25abb3`, `fd50dc9`, `784ce47`, `fbbb63c`, and `4223507`) validates exact
gateway network aliases, pins the governed MCP key, gates every expensive
stage on provider/gateway/runtime readiness, classifies control-plane failures,
scans workspace evidence for secret canaries, emits an explicit fail-closed
summary, and publishes runtime materialization readiness to downstream steps.
None of these local checks is live provider evidence.

The latest repository-local hardening commits are:

- `4890aaa2bd0ddbc2ef1e5b19ba075363de58167f`: verifies exact LiteLLM and
  OmniRoute release-tag targets (annotated-tag peeling or lightweight direct
  refs) and source archive SHA-256 values in the workflow;
- `82f053e8e9858c9c70d2fad058ae154675ff9ba5`: validates the retained JSON
  evidence tree and rejects sensitive-retention flags or an incomplete gate
  set;
- `3b264d485a3e82b8e25d040b8dda4b19d5da4667`: rejects non-loopback published
  ports and laptop/host-bound gateway targets in static workflow validation;
- `c7b84609e458cea7947bc34c44f35cd0ed66d2f0`: records run-scoped profile
  disposal as a scalar cleanup assertion tied to Agent Server container
  absence.
- `4b9ae29042a0655d74103fa2da25017ed11b6de6`: records disposable-network
  startup status, gates dependent stages on network readiness, and maps a
  failed Docker network create to `FAILED_INFRASTRUCTURE`.
- `cedcee2a1efd740439b7b54d45f17610fc6c8a71`: retains scalar startup failure
  classes for each disposable service and validates shell syntax for every
  workflow `run` block.

These changes are implementation/evidence hardening only. They do not claim a
provider call, live lifecycle result, steward approval, worker activation, or
release decision.

The latest provider-boundary commits are `a16d8e3` (separate the exact
live-discovered Groq baseline from the governed `auto/coding` route) and
`04717ce` (register the corresponding immutable run evidence and route
provenance). `9df2779` additionally hardens cleanup of run-scoped internal
secret environment entries and adds a static regression check; `69face5`
classifies unexpected readiness 4xx responses as application-health contract
failures rather than cold-start timeouts. `29d66e6` additionally keeps
production activation tied to the committed report and bounds the OpenHands
AIAT bridge grant surface. These changes do not dispatch a workflow or
activate OpenHands. `eced0cf` requires the committed approval-record
identifier whenever a report claims approved status, and `176055e` packages
the canonical report for the API-image runtime path.

The latest offline routing-fixture hardening is `7d06e4f`. It records bounded
rate-limit fallback semantics, rejects unbounded fixture failure labels, and
asserts that credential-like connection fields never appear in scalar route
evidence. This does not claim a live multi-provider run and does not dispatch
the candidate workflow.

The latest compatibility/provider-pool hardening is `488a593` plus `ecc1b48`.
These commits do not claim live provider, lifecycle, steward, activation, or
release evidence; a new candidate SHA must still be frozen and pass the
read-only preflight before one deliberate workflow dispatch.

The latest scalar provider-attribution and preflight-regression hardening is
`a377dab` plus `69f5fb4`; `ad511d9` makes stale-candidate blockers explicit and
`fa19b1c` keeps clean-candidate evidence pinned to its evaluated revision.
These commits do not dispatch a workflow or activate OpenHands.

After a future run is genuinely `PASSED`, validate the downloaded gate
artifact before any steward submission (this is read-only and does not approve
or activate the worker):

```bash
cd mas
uv run python scripts/check_openhands_steward_registration.py \
  --evidence "$RUNNER_TEMP/aiat-openhands-evidence/certification/gate-evaluation.json" \
  --candidate-sha "$CANDIDATE_SHA" \
  --output "$RUNNER_TEMP/openhands-steward-registration-preflight.json"
```

The validator requires all 20 mandatory gates to be `PASS`, exact candidate,
source, and image pins, and `payloads_retained=false`. It only returns
`READY_FOR_STEWARD_REGISTRATION`; the candidate remains `CERTIFYING`/inactive
and activation approval remains a separate operator decision.
