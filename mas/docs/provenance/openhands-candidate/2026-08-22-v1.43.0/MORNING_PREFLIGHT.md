# OpenHands v1.43.0 morning certification preparation

This is a manual preparation guide for the inactive candidate. It does not
activate OpenHands, approve the steward record, or dispatch a workflow. The
workflow is `workflow_dispatch` only and must be dispatched once against an
explicit frozen commit after the preflight passes.

The overnight dependency and full local commit reconciliation is recorded in
[`overnight-reconciliation.json`](./overnight-reconciliation.json). It contains
only exact commit identifiers, scalar wiring, and secret-free boundary state.

## Operator sequence

1. Set or verify the two non-secret repository variables. No profile UUID,
   internal tool secret, or internal model-gateway secret is a persistent
   GitHub input.

   ```bash
   gh variable set OPENHANDS_MODEL_ID --body omniroute-coding
   gh variable set OPENHANDS_MCP_SETTINGS_KEY --body aiat-openhands-v1-43-0-coding
   ```

2. Set the one external provider secret interactively. The value is read from
   stdin and is never embedded in shell history or documentation.

   ```bash
   gh secret set GROQ_API_KEY
   ```

3. Freeze and inspect the exact candidate commit. Do not use an implicit
   `HEAD` in the dispatch command.

   ```bash
   CANDIDATE_SHA="$(git rev-parse HEAD)"
   git show -s --format='%H %s' "$CANDIDATE_SHA"
   ```

4. Run the safe read-only preflight. It checks secret/variable presence only,
   never retrieves a secret value, and runs the bounded deterministic tests.

   ```bash
   python3 mas/scripts/check_openhands_dispatch_preflight.py \
     --candidate-sha "$CANDIDATE_SHA" \
     --github-repo OWNER/REPOSITORY \
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
     --ref "$CANDIDATE_SHA" \
     -f candidate_sha="$CANDIDATE_SHA"
   ```

6. Watch and inspect the single run. Do not automatically rerun a blocked or
   failed certification; first identify a material prerequisite change.

   ```bash
   RUN_ID="$(gh run list --workflow openhands-candidate-certification.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
   gh run watch "$RUN_ID" --exit-status
   gh run view "$RUN_ID" --log-failed
   gh run download "$RUN_ID" \
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
None of these local checks is live provider evidence.

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
