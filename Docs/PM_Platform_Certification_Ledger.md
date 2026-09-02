# PM platform certification ledger

This ledger is intentionally explicit about what local tests can and cannot
prove. Add one row per provider/adapter version and attach immutable evidence.

| Gate | Local contract | Staging evidence | Status |
| --- | --- | --- | --- |
| Provider URL/secret redaction | Fake + validation tests | N/A | PASS locally |
| Raw webhook HMAC/token and replay dedupe | Fake/GitHub/YouTrack tests | Signed provider delivery replay | PASS locally; staging pending |
| Canonical transaction + outbox retry/lease | Storage tests | Crash/outage rehearsal | PASS locally; staging pending |
| Scope, CAS, echo, drift conflicts | Orchestrator tests | Human edit and provider drift | PASS locally; staging pending |
| GitHub App token scope/expiry | Broker/unit tests | Disposable repository/App | PASS locally; staging pending |
| YouTrack least privilege/custom fields | Adapter tests: Observer + Project Creator/Create Project, existing Project Admin, automatic created-project ownership, forbidden global-admin/delete probes | Restricted account plus create/own/archive denial rehearsal | PASS locally; staging pending |
| Cutover/rollback | State-machine tests | Two-provider shadow run | PASS locally; staging pending |
| Alembic upgrade/rollback | Static migration review | Real database backup/restore | Pending deployment |

Do not mark a row `PASS` from a fixture alone. Record adapter commit, lockfile
hash, database revision, exact command, output location, operator, timestamp,
and any external account or repository scope used.

## 2026-07-29 live YouTrack certification

This run used connection `1b699f09-06c7-4a16-a4f3-9c1aaf69d6e2`, the existing
YouTrack project `0-1` (`AIAT`), and canonical project
`68345b4f-c52b-48d0-8e2b-6daf958fd941`. No external project was created or
deleted. Database migration `0025_pm_project_provisioning_lifecycle` was at
head. The gateway health check returned `ok=true` for
`https://pm-gateway.aiat.ca/health`.

| Evidence | Result |
| --- | --- |
| Missing `X-YouTrack-Token` | HTTP 401; rejected |
| Invalid `X-YouTrack-Token` | HTTP 401; rejected |
| Managed-secret issue delivery `cert-live-issue-20260729` | HTTP 202; verified, normalized `work_item`; actor `certification-actor`; inbox `f34ee482-0fb6-490b-85e4-d39285abe9fa` |
| Managed-secret comment delivery `cert-live-comment-20260729` | HTTP 202; verified, normalized `work_item`; actor `certification-actor`; inbox `ff0f5411-f109-4f86-a1ed-5ecce0e7c1d2` |
| Exact duplicate of issue delivery | HTTP 202 `duplicate`; no second inbox row (2 rows, 2 distinct delivery IDs) |
| Provider mappings | work item `3-19` -> canonical issue `fe747862-5d79-4325-bb4c-4fc7c8dc6061`; comment `7-1` -> canonical comment `fc7abf7a-7e22-4f57-a9e1-794933cd36f8` |
| Outbox | Two post-fix deliveries succeeded on attempt 1 (issue and comment); one historical pre-fix event remains `DEAD_LETTER` after 5 validation failures and is retained as evidence |
| Reconciliation run `3ca96c46-2b50-494e-b606-7e443f0e9417` | `COMPLETED`, audit mode; seen 1, mapped 1, conflicts 0, drift 0, scope conflicts 0; cursor `1785337673750` |
| Cursor-resume run `f474aa12-b8f6-4539-8a7f-8bcfa00a5a89` | `COMPLETED` from the durable binding cursor; seen 0, conflicts 0, drift 0; cursor remained `1785337673750` |

The binding `8c8a3b38-b57b-40d5-ae20-c46eb5654966` remains `SHADOW` with
`direction=both`. Projection and reconciliation evidence are recorded, but
webhook activation evidence is intentionally not promoted while the binding
is SHADOW. The two authenticated loopback deliveries are recorded as
`out_of_scope` conflicts because inbound application is correctly blocked
before READ_ONLY/ACTIVE. No provider-originated delivery IDs from the user's
YouTrack UI test were present in the inbox during this run; a fresh UI
issue-edit and comment while the stack is running is required to close that
external-delivery evidence gap. Certification therefore remains blocked from
READ_ONLY/ACTIVE.

## 2026-07-29 live UI delivery follow-up (16:09 UTC)

This immutable follow-up was recorded after a fresh live inspection of the
public gateway, database inbox, and the restricted YouTrack adapter. The
provider-originated UI evidence requested for
`CERT-UI-WEBHOOK-20260729` was not present: the gateway access log contained no
new provider POST, the inbox still contained only the two synthetic deliveries
above, and a server-side read-only YouTrack listing contained only `AIAT-1`
(`3-19`). No issue/comment ID, human actor, timestamp, or provider payload hash
for the requested UI actions can therefore be certified.

| Evidence | Result |
| --- | --- |
| Public gateway health | `ok=true`; route reachable |
| Missing token probe | HTTP 401; rejected |
| Invalid token probe | HTTP 401; rejected |
| YouTrack adapter read-only discovery | One visible project (`0-1`/`AIAT`); target UI issue not present |
| Durable reconciliation `92a01e11-c161-4bae-8b98-ec2b47f522d1`, fresh post-resolution run `98e5fe97-72df-491c-adb1-6f4215018374` | Both `COMPLETED`, audit; fresh run seen 0, mapped 0, conflicts 0, drift 0, scope conflicts 0; cursor `1785337673750` |
| Existing SHADOW conflicts | `e027a641-6c97-4144-9de8-cb032242bcd7` and `9e6d35fa-f956-496e-9e04-26cf66955618` resolved as `expected_shadow_policy_denial`; snapshots retained |
| Historical dead-letter | `f0cecf8c-6b3a-4672-af38-b3920274e761` retained as `DEAD_LETTER`, marked `SUPERSEDED` with links to successful projections `1c660024-2356-4f55-8e3c-ab204eac290b` and `5c1a59a0-5da0-4271-ab43-6299814563b4`; five failed attempts retained |
| Rollback rehearsal | `POST /integrations/rollbacks` with `confirm=false` returned HTTP 400 (`rollback requires confirm=true`); binding remained `SHADOW`, proving the explicit-approval guard without mutating state |

The binding remains `SHADOW`; no READ_ONLY plan was generated because the
provider-originated UI delivery, actor, mapping, duplicate-replay, and rollback
gates are not evidenced. No connection, binding, project, webhook, or external
provider resource was promoted or deleted.

## 2026-07-29 live UI certification rerun (18:56 UTC)

The requested issue is now visible through the restricted YouTrack adapter as
`AIAT-2` (`3-21`) in project `0-1`, with summary
`CERT-UI-WEBHOOK-20260729`. Its provider timestamps are created
`2026-07-29T18:54:11.948Z` and updated `2026-07-29T18:54:29.735Z`; the provider
reports reporter `admin` (`2-1`), distinct from the integration identity
`AIAT_Agents` (`2-3`). The comments endpoint currently returns an empty list.

The gateway request counter remained `14` and its access log contained no new
provider POST. The inbox still contains only the two synthetic deliveries;
therefore no real webhook actor, delivery ID/fallback hash, comment ID, or
inbox row can be correlated for the UI actions. The user-reported four events
are not evidenced by the running system.

| Evidence | Result |
| --- | --- |
| Provider read-only issue lookup | `AIAT-2` / `3-21` found; reporter `admin`; comment list empty |
| Gateway health and counters | Healthy; no new provider webhook requests; failures `0` |
| Reconciliation `35b660cd-0cf2-4bdb-8421-181578c21bb6` | `COMPLETED`, audit; seen `1`, mapped `0`, conflicts `1` (`unknown_mapping` for `3-21`), drift `0`, scope conflicts `0` |
| New conflict | `04d40f83-8cf7-4eb5-8965-afbc9455d25a`, retained as evidence of an unmapped provider-created item under SHADOW policy |
| Doctor | `ready=false`; webhook activation evidence remains the blocker |

No exact real payload was available for replay, so real-delivery duplicate
deduplication and the four-event actor/hash correlation remain unverified. No
READ_ONLY transition plan was generated and no state promotion was attempted.
At the final propagation check (`2026-07-29T19:02Z`), the comment endpoint was
still empty and the gateway counter remained `14`.

## 2026-07-29 provider-originated YouTrack delivery certification (20:34 UTC)

Four new HTTP `202` requests were observed in the `pm-gateway` access log
between 20:31 and 20:33 UTC and persisted in the inbox for connection
`1b699f09-06c7-4a16-a4f3-9c1aaf69d6e2`. They are provider-originated: each
payload contains the YouTrack project/issue shape, the real human actor
`admin` (`maaroufabousaleh@hotmail.com`), and a provider timestamp. The
separate `credential-rotation-probe-20260729-v4` row is excluded as synthetic
because its payload is an explicit `credential.rotation.probe`, has no actor,
and was sent by the verification script.

YouTrack did not provide a native delivery header for these four requests. The
adapter therefore used the documented fallback: each `provider_delivery_id`
is the SHA-256 of the raw body and equals the persisted `payload_hash`. The
read-only provider API lookup confirmed issue readable key `AIAT-2` maps to
external issue ID `3-21`; comment IDs were correlated by the comment-created
timestamps to provider comments `7-2` and `7-3`.

| Inbox ID | Provider event | Normalized type | External issue | External comment | Actor | Provider timestamp | Payload SHA-256 | Auth |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `a81596e4-56b4-4038-b649-aa495cabf043` | `commentUpdated` | `work_item` (comment operation) | `AIAT-2` / `3-21` | `7-2` | `admin` | `2026-07-29T20:31:53.712Z` | `713b50f318b7f2b4a4f3af1f78c3b8c7204868d41f473d7c61fdde8815a37e72` | verified=true |
| `c30d5292-70af-41a7-9245-bc55c6692669` | `issueUpdated` | `work_item` | `AIAT-2` / `3-21` | — | `admin` | `2026-07-29T20:32:42.570Z` | `e8f631e1e352e3abff9a475da2dd21955a7d9b2793a3d0b0b239376ac7343174` | verified=true |
| `52876582-e199-4c5d-91d1-d7ce3dba4107` | `commentAdded` | `work_item` (comment operation) | `AIAT-2` / `3-21` | `7-3` | `admin` | `2026-07-29T20:32:59.782Z` | `b482fbc9a621ceccc16c804f57d0c054bb2b15e1f53a134c505ca43a65f08251` | verified=true |
| `9ec5d1dd-ba33-4554-bd0c-02ea9f9eef44` | `commentUpdated` | `work_item` (comment operation) | `AIAT-2` / `3-21` | `7-3` | `admin` | `2026-07-29T20:33:08.633Z` | `061d70fac72e1efbb4f576fd200a2bdacaed1cefc7478addf6563b83a27ce369` | verified=true |

The current YouTrack normalizer records these webhook shapes as
`normalized_type=work_item` and derives the comment operation from the event
name; it does not receive a comment ID directly in the webhook payload. The
comment IDs above are therefore read-only provider correlations, not invented
IDs from canonical state.

Fresh audit reconciliation run `6dbfe4e0-5c7c-4a51-b41e-54b017568f22`
completed with `seen=1`, `mapped=0`, `drift=0`, `conflicts=1`, and
`scope_conflicts=0`; the conflict is the existing SHADOW-policy
`unknown_mapping` for `AIAT-2` / `3-21`. The PM outbox has `3` `SYNCED` rows
and `1` retained `DEAD_LETTER` (`f0cecf8c-6b3a-4672-af38-b3920274e761`, five
attempts, historical validation failure). The binding remains
`8c8a3b38-b57b-40d5-ae20-c46eb5654966` in `SHADOW`/`both` mode. No READ_ONLY
plan was generated or applied because create-event and complete canonical
projection/mapping gates are not yet satisfied.

## 2026-07-29 provider webhook authentication boundary repair (20:16 UTC)

The historical YouTrack `403 Forbidden` could not be reproduced at the current
origin. Before the repair, the orchestrator's global control-plane middleware
required an AIAT API key before the connection-specific provider verifier ran;
the public gateway does not and must not expose that internal key to YouTrack.
The gateway source had no provider-specific 403 branch. Current Cloudflared
metrics show `cloudflared_tunnel_request_errors=0` and response codes only
`200`, `202`, `401`, `404`, and `500`, with no `403`. The available Cloudflare
API credential was verified active but did not have permission to retrieve the
historical zone/security-log records, so the old edge decision cannot be
independently attributed to WAF/Access/Bot protection from this workspace.

The repair is deliberately narrow:

* only `POST /integrations/webhooks/{UUID}` (including the `/api/v1` alias)
  bypasses the global AIAT API-key middleware;
* the route still requires the connection's managed provider secret, resolved
  server-side from `youtrack-webhook-current`, and invalid/missing tokens return
  `401` before inbox persistence;
* the gateway accepts the public provider request without `PM_GATEWAY_API_KEY`,
  preserves the raw body and safe YouTrack headers, and uses the internal key
  only on its private hop to the orchestrator;
* management, operator, health-sensitive, and internal routes remain behind
  their existing authenticated principal checks.

| Evidence | Result |
| --- | --- |
| Regression suite | `python -m pytest --import-mode=importlib packages/mas-core/tests/test_pm_integrations.py apps/pm-gateway/tests/test_gateway.py apps/orchestrator-api/tests/test_pm_control_plane.py -q` — passed (92 tests) |
| Rebuild/restart | `docker compose -f mas/infra/compose/docker-compose.yml build pm-gateway`; `docker compose -f mas/infra/compose/docker-compose.yml build orchestrator-api`; then `docker compose -f mas/infra/compose/docker-compose.yml up -d --no-deps orchestrator-api pm-gateway` — both healthy; unrelated services were not recreated |
| Valid managed provider probe | Public gateway returned `202`; inbox `6adc4db2-5bb1-4c1a-a010-8793d68a1d9d`, delivery `auth-fix-probe-20260729`, verified `true`, status `PROCESSED`, normalized `none`, payload SHA-256 `866795769e69723d4bd61b695776b7d0981fd1eabe4278a3ac80944f0196771b` |
| Missing/invalid provider token | Both public probes returned `401`; no inbox row was inserted |
| Internal-key independence | Gateway regression test passed with only `X-YouTrack-Token`; no incoming `X-API-Key` was required |
| Management protection | Unauthenticated `GET /integrations/connections` returned `401` |
| Gateway health | `https://pm-gateway.aiat.ca/health` returned `ok=true` |
| Live AIAT-2 watch | Five polling windows (about 44 seconds) observed no new provider POST, inbox row, issue/comment event, or reconciliation change; the requested human actor/IDs/timestamps/hashes therefore remain unverified |

The binding remains `SHADOW`; no READ_ONLY or ACTIVE transition was generated
or applied. The previous synthetic/certification evidence remains unchanged,
and no provider resource was created, deleted, or widened.

The final post-watch audit was reconciliation run
`938d27fa-c35a-48f3-9ecc-0bd5d8b68ebd`, completed in `audit` mode from the
durable binding cursor. It saw one provider work item, mapped zero, reported
`drift=0` and `scope_conflicts=0`, and retained one expected existing
`unknown_mapping` conflict for external work item `3-21` (`AIAT-2`) as
`04d40f83-8cf7-4eb5-8965-afbc9455d25a`. No new UI webhook event was observed
during the watch, so this conflict was not resolved or guessed into a mapping.

## 2026-07-29 canonical projection SHADOW gate (21:00 UTC)

The provider-originated `AIAT-2` / external `3-21` evidence remains preserved
as a human certification fixture. Its inbox rows, actor metadata, raw-body
SHA-256 fallback delivery identifiers, normalized payloads, and forensic
snapshots were retained. The five related `unknown_mapping`/scope fixtures
were resolved as `IGNORED` with explicit certification classifications; no
canonical mapping was fabricated and the YouTrack issue was not deleted. The
historical five-attempt outbox `DEAD_LETTER`
`f0cecf8c-6b3a-4672-af38-b3920274e761` remains retained, explicitly
superseded/resolved by the successful projection evidence, and excluded from
active blockers.

The generic canonical `issue.create` path created the following issue and
outbox event atomically for binding
`8c8a3b38-b57b-40d5-ae20-c46eb5654966`:

| Evidence | Result |
| --- | --- |
| Canonical issue | `c9aacb57-2717-44d2-b7ba-2c3649263876`, title `CERT-CANONICAL-PROJECTION-20260729`, revision `1` |
| Issue transaction timestamp | `2026-07-29 20:56:50.525893+00` |
| PM outbox | `02a5ae79-c677-431c-b20a-5ba82fd49ea6`, `upsert_work_item`, revision `1`, idempotency key `8c8a3b38-b57b-40d5-ae20-c46eb5654966:c9aacb57-2717-44d2-b7ba-2c3649263876:1:upsert` |
| Outbox transaction timestamp/status | `2026-07-29 20:56:50.547528+00`, `SYNCED` |
| Delivery attempt | ID `9`, attempt `1`, `SUCCEEDED`, `2026-07-29 20:56:52.869822+00`; provider response external ID `3-23` |
| PM mapping | `cebe9fc0-9203-4851-b8a8-4a9a33cd38d3`; external ID `3-23`; key `AIAT-3`; provider version `1785358614007`; exported revision `1` |

The projected YouTrack issue is `AIAT-3` in project `0-1`. A provider
`issueCreated` echo was accepted by the gateway with HTTP `202` and persisted
once as inbox `d72fb928-4f1c-46c9-ba0c-4238edbd5bd8`, payload/delivery hash
`5795a4b18b1eb06e4c2510f13e989d5110534db9db03ecb61d15a2a151fd501a`.
It was classified as an expected projection echo and ignored under SHADOW
policy after the readable-key fallback was installed. Replaying the exact
persisted body returned HTTP `202` with `status=duplicate`; the database still
contains one inbox row and one canonical issue for this event.

Read-only provider verification found the four stable fields on `AIAT-3`:

* `AIAT Object ID` = `c9aacb57-2717-44d2-b7ba-2c3649263876`
* `AIAT Object Type` = `work_item`
* `AIAT Revision` = `1`
* `AIAT Managed` = `true`

The focused PM/provider, orchestrator, and gateway regression suite passed
(`92` tests). Reconciliation run `b39dd543-2840-49f0-8dc1-7085b7aaf7a2`
recorded `seen=1`, `mapped=1`, `drift=0`, `conflicts=0`, and
`scope_conflicts=0` after projection. The binding remains `SHADOW`; human
priority update, comment add, and comment edit on mapped issue `AIAT-3` are
still required before the final mapping gate and before generating a separate
digest-bound READ_ONLY plan. No READ_ONLY or ACTIVE transition was generated
or applied.

## 2026-07-29 mapped human webhook certification (21:48 UTC)

The mapped YouTrack human comment actions were received through the public
gateway. Both provider deliveries returned HTTP `202 Accepted`, were
authenticated, and retained their raw-body SHA-256 fallback identifiers:

| Inbox ID | Event | Provider timestamp | Actor | External issue | Mapping | Payload SHA-256 |
| --- | --- | --- | --- | --- | --- | --- |
| `a4c41817-57a7-4647-b7c5-256c02a27629` | `commentAdded` | `2026-07-29T21:40:43.350Z` | `admin` (`maaroufabousaleh@hotmail.com`) | `AIAT-3` / `3-23` | `cebe9fc0-9203-4851-b8a8-4a9a33cd38d3` | `2d4db24cae9c427dbacdad7eab9507571d44ce7a0f8b2d819e375b89d513a5ca` |
| `6fa53056-41ad-400c-91a1-ca18c098fe73` | `commentUpdated` | `2026-07-29T21:40:53.974Z` | `admin` (`maaroufabousaleh@hotmail.com`) | `AIAT-3` / `3-23` | `cebe9fc0-9203-4851-b8a8-4a9a33cd38d3` | `7758da3ff91ff215e5be62acc1f364da34823a876478fd02fbb78fe2a9c0f40c` |

The payloads contain the human comment text and edited text respectively;
`verified=true` and `normalized_type=work_item` are persisted on both inbox
rows. No `unknown_mapping` conflict was created for these events. The two
expected SHADOW-policy `out_of_scope` conflicts
(`cf7a4f56-d03b-4387-bcf9-5b33011e0a13` and
`6c6a364a-9ea6-41c7-8932-c339573ba4fd`) were resolved as
`IGNORED/ignored_mapped_human_shadow_policy`, retaining their snapshots and
actor/hash evidence.

The mapping remains complete for canonical issue
`c9aacb57-2717-44d2-b7ba-2c3649263876`; its provider version was advanced to
`1785361245580` from the authenticated provider observation without changing
the canonical revision or applying an inbound canonical mutation while the
binding is SHADOW. The stale pre-correction drift observation
`3040bbce-f8fe-4fe0-afab-0758f9af31e5` was retained and marked
`IGNORED/ignored_superseded_drift_observation` after the corrected run.

Fresh reconciliation run `db5d3bd9-5909-4946-9b5a-022d658be27d` completed with
`seen=1`, `mapped=1`, `drift=0`, `conflicts=0`, and `scope_conflicts=0`.
The canonical issue still has exactly one `SYNCED` projection outbox event;
the historical five-attempt dead letter
`f0cecf8c-6b3a-4672-af38-b3920274e761` remains retained and superseded, not an
active retry blocker. The prior exact-body projection replay remains
deduplicated as one inbox row and one canonical issue.

The gateway log contains three provider POSTs for this issue family (the
projection `issueCreated` echo plus the two human comment deliveries), all
returning HTTP `202`. No provider `issueUpdated` request or inbox row was
observed for the claimed priority change. Consequently, issue-update coverage
and the persisted binding `webhook_events` set remain incomplete (`comment`
only), so a READ_ONLY transition plan was not generated. The binding remains
`SHADOW`; no READ_ONLY or ACTIVE transition was applied.

## 2026-07-29 mapped human issueUpdated certification (22:07 UTC)

The fresh provider-originated AIAT-3 description and priority edits were
received through the public gateway and persisted in the PM inbox. Both
deliveries returned HTTP `202 Accepted`, passed the configured YouTrack token
verification, and were attributed to the real human YouTrack account `admin`
(`maaroufabousaleh@hotmail.com`), not a certification actor or AIAT agent.

| Gateway received | Inbox ID | Event | Provider timestamp | External issue/comment | Actor | Payload SHA-256 | Processing |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `2026-07-29T22:06:43.015956835Z` / `202` | `0248ef98-d2eb-460a-8b52-c76858374e8a` | `issueUpdated` | `2026-07-29T22:06:43.787Z` | issue `AIAT-3` / `3-23`; no comment | `admin` (`maaroufabousaleh@hotmail.com`) | `4eca2994a44c9c5f64d0e75bf5ddb81888fd7197d45edf471eea576aac97e469` | authenticated, normalized, mapped; SHADOW policy `out_of_scope` retained as ignored conflict |
| `2026-07-29T22:07:10.322062499Z` / `202` | `08d72d95-6af7-4739-8905-806ec3148b3b` | `issueUpdated` | `2026-07-29T22:07:10.862Z` | issue `AIAT-3` / `3-23`; no comment | `admin` (`maaroufabousaleh@hotmail.com`) | `70948ee9ab59e63120db64502bed44828c47ca7c5d867257c624e259d475948c` | authenticated, normalized, mapped; SHADOW policy `out_of_scope` retained as ignored conflict |

The first payload records the description edit and the second records the
priority change (`Normal` to `Critical`), proving that both UI actions produce
provider `issueUpdated` events. The events resolve through mapping
`cebe9fc0-9203-4851-b8a8-4a9a33cd38d3` to canonical issue
`c9aacb57-2717-44d2-b7ba-2c3649263876`. The corresponding SHADOW-policy
conflicts `129efa63-d700-4857-9bdf-1dd4481531fe` and
`93ed192f-93bc-429d-b04c-257ab52412cc` were resolved as
`IGNORED/ignored_mapped_human_shadow_policy`; inbox rows, actor metadata,
payload hashes, and forensic snapshots remain retained.

The recent gateway window contained only these two webhook POSTs, both `202`;
no `400` or `401` occurred in the current test window. Older `400`/`401`
entries are historical and were excluded from this certification result.

The provider TLS trust path was corrected without disabling certificate
verification. `ProviderHTTP` now uses a `CERT_REQUIRED` context with hostname
checking and the configured/system CA bundle. The rebuilt orchestrator verified
the provider over TLS (`HTTP 200`), and the normal production reconciliation
endpoint completed successfully after the restart. Focused PM, orchestrator,
and gateway tests passed, including the certificate-verification regression.

Fresh production reconciliation run
`3e6619e5-2ee6-4d15-8693-8705f9fac74f` completed in audit mode with:

* `seen=1`, `mapped=1`, `drift=0`
* `conflicts=0`, `scope_conflicts=0`, `version_mismatches=0`,
  `hash_mismatches=0`
* no pending, processing, failed, or active dead-letter projections

The historical five-attempt dead letter
`f0cecf8c-6b3a-4672-af38-b3920274e761` remains retained and explicitly
superseded/resolved; it is excluded from active blockers. The binding remains
`SHADOW`.

All SHADOW gates now pass. A separate digest-bound READ_ONLY transition plan
was generated but not applied:

* Plan ID: `49251fcf-2477-49ae-a500-121426a1ecff`
* Exact digest: `d229c3be20faf0ef3e912b66c8cc81da20efcc9cf0e316a71b6181d6d850770d`
* Proposed action: transition binding
  `8c8a3b38-b57b-40d5-ae20-c46eb5654966` from `SHADOW` to `READ_ONLY` while
  keeping the connection `SHADOW`, retaining credentials, mappings, inbox,
  outbox, and rollback evidence.

No READ_ONLY or ACTIVE transition was applied.

## 2026-07-29 persisted lifecycle-plan correction

The earlier READ_ONLY preview (`49251fcf-2477-49ae-a500-121426a1ecff`, digest
`d229c3be20faf0ef3e912b66c8cc81da20efcc9cf0e316a71b6181d6d850770d`) was a
non-actionable certification-ledger entry. It was never persisted in the
control-plane database, so the attempted apply correctly failed closed. It is
retained here as historical evidence and must not be reconstructed, backfilled,
or applied.

Migration `0026_pm_lifecycle_transition_plans` now persists every connection
or binding transition plan in `pm_lifecycle_plans` before returning it, with a
canonical digest, immutable operations, expected revisions, gate/evidence
references, expiry, and rollback operations. Operator approval and exact
digest apply are recorded atomically with the target state and an immutable
`pm_lifecycle_audits` row. Legacy direct cutover/rollback aliases now return
`lifecycle_plan_required` and cannot bypass this path. The current connection
and binding remain `SHADOW`; no plan from this correction was approved or
applied during implementation.

## 2026-07-29 exact persisted READ_ONLY transition applied

The newly persisted lifecycle plan was independently read back and verified
before approval. Plan
`28013e3b-d472-42e7-b4d0-b7b46bfb449f` remained `PLANNED`, unexpired, and its
persisted and independently recomputed digest was
`9d8d00e328f50ca9e68cb877537c1a44b7b2358f291676a6896c9c38466286db`. Its
single operation was binding
`8c8a3b38-b57b-40d5-ae20-c46eb5654966` `SHADOW` -> `READ_ONLY`; the only
rollback operation was `READ_ONLY` -> `SHADOW`. No connection mutation was
present. The pre-approval doctor was ready with no blockers, and audit
reconciliation run `88ddecec-2d78-45cb-a905-d2f0a767c49c` was clean
(`seen=0`, `mapped=0`, `drift=0`, `conflicts=0`).

Authenticated operator approval was persisted at
`2026-07-29T23:52:48.299965Z` with reason
`Operator approval after completed SHADOW live-provider certification.` The
exact approved digest-bound apply completed at
`2026-07-29T23:53:01.531348Z` under the authenticated `operator` principal.
The immutable cutover audit (and transition-history record) is
`3c5358de-ec74-489c-b69e-ee5cfe32f326`, with transaction/correlation ID
`befa7000-97db-456f-88e9-6db058b5b4c4`:

* before: connection `SHADOW` revision `1`, binding `SHADOW` revision `3`
* after: connection `SHADOW` revision `1`, binding `READ_ONLY` revision `4`
* rollback: binding `READ_ONLY` -> `SHADOW`

An exact repeated apply returned `idempotent=true` and the same audit and
transaction IDs; the audit count remained one. The plan is `APPLIED` and its
digest remains valid. There is no separate transition-history table in this
schema; the immutable `pm_lifecycle_audits` ID above is the transition-history
record.

Post-transition doctor completed successfully at
`2026-07-29T23:55:16.0931893Z`-`2026-07-29T23:55:17.0944695Z`, with
`ready=true` and no blockers. The doctor endpoint does not issue a native run
ID, so this UTC interval and endpoint/connection tuple are the immutable
evidence reference. Production audit reconciliation run
`6ec0d63a-0c46-48a0-8da6-f0eb2343b306` completed at
`2026-07-29T23:55:28.1703976Z` with `seen=0`, `mapped=0`, `drift=0`,
`conflicts=0`, `scope_conflicts=0`, `version_mismatches=0`, and
`hash_mismatches=0`. No open conflicts, pending/processing/failed outbox
projections, or active dead letters were present; TLS certificate verification
remained enabled. The effective runtime policy test confirms outbound
projection remains permitted, authenticated inbound events remain evidence,
and inbound events cannot mutate canonical state while the connection is
`SHADOW`.

The binding is operating in `READ_ONLY`. No `ACTIVE` plan was generated,
approved, or applied.

## 2026-07-30 READ_ONLY full-scan and outbound projection evidence

The immediate post-transition reconciliation with the durable cursor returned
`seen=0` because the YouTrack incremental query used binding cursor
`1785362831175`; no provider issue had an `updated` value greater than that
cursor at that instant. This was an incremental-window result, not proof that
the binding had no mapped objects. A cursor-reset full audit (`cursor=""`)
enumerated three issues in the configured `AIAT` project. The retained AIAT-2
fixture (`3-21`) is intentionally unmapped and was previously forensic-only;
its duplicate open reconciliation row
`476100d9-4806-40d1-92b6-cf527ef1d193` was marked `IGNORED` with an explicit
certification-fixture resolution, preserving its snapshots. Reconciliation
now loads historical conflict states so ignored/resolved fixtures do not
reopen or block later full scans.

Full audit run `8c199f47-035f-4a1e-a75d-f29797ae2bd0` completed with
`seen=3`, `mapped=2`, `drift=0`, and `conflicts=0`. The canonical AIAT-3
mapping is complete and remains mapping
`cebe9fc0-9203-4851-b8a8-4a9a33cd38d3` -> YouTrack `3-23` / `AIAT-3`.

A controlled canonical description update advanced issue
`c9aacb57-2717-44d2-b7ba-2c3649263876` from revision `1` to `2`, adding the
marker `READ_ONLY-CERT-20260730-0001`. Exactly one new outbox event was
committed:
`69bd6a90-c8e0-45e8-b465-7cf90ed3057b`, idempotency key
`8c8a3b38-b57b-40d5-ae20-c46eb5654966:c9aacb57-2717-44d2-b7ba-2c3649263876:2:upsert`.
It reached `SYNCED` with one successful delivery attempt (`pm_delivery_attempts`
ID `10`), provider issue `3-23` / `AIAT-3`, and provider version
`1785370223232`. The stable fields were read back from YouTrack and matched:
AIAT Object ID = canonical issue UUID, Object Type = `work_item`, AIAT Revision
= `2`, and AIAT Managed = `true`.

The provider echo was authenticated and retained as inbox evidence
(`e48019e5-7e97-47e2-a075-dc71e8b5e5c4`, `issueUpdated`, payload SHA-256
`3ddaaa190ce93eb7cc4ea85ad82e255c19e2f32c143276bb7978180573bee66c`). Its
previous READ_ONLY policy conflict
`1e499211-3d07-4df5-a0c0-9cf556940630` was explicitly marked `IGNORED` as a
projection echo, retaining the forensic record. The implementation now
preserves YouTrack `AIAT Revision` changed-field markers and acknowledges a
matching canonical projection echo before the READ_ONLY inbound-mutation
gate; no canonical mutation or second outbox event is created. A replay with
the same provider idempotency key returned `SYNCED` for the same external
issue and did not create another outbox event or mapping.

Post-fix full audit run `50e30dd9-6ea4-4887-8790-0afccc37e7bf` remained clean
(`seen=3`, `mapped=2`, `drift=0`, `conflicts=0`). Doctor evidence was ready
with no blockers at `2026-07-30T00:15:19.7602218Z`-
`2026-07-30T00:15:21.0886495Z`. The historical five-attempt dead letter
`f0cecf8c-6b3a-4672-af38-b3920274e761` remains retained and superseded; active
dead letters, pending/failed projections, and open conflicts are zero.

## 2026-07-30 READ_ONLY inbound non-mutation evidence

The requested YouTrack UI actions produced two authenticated HTTP `202`
deliveries through the gateway:

| Gateway received | Inbox ID | Event | Provider payload hash | Actor in payload | Mapping/result |
| --- | --- | --- | --- | --- | --- |
| `2026-07-30T00:34:29.035830887Z` | `add34b32-b44b-4c5c-addf-402b01e5dce5` | `issueUpdated` | `9db6140d72e87b12db09cfe8291e0f6642efdd85421cc349fab8aa2b318950f0` | `AIAT_Agents` | mapped to `cebe9fc0-9203-4851-b8a8-4a9a33cd38d3`, READ_ONLY denial retained as ignored conflict `2ff9060c-ff23-46d1-972a-84b59f39c89c` |
| `2026-07-30T00:34:41.472178745Z` | `daf5ad9d-8503-4ce2-b6ff-a4fc41db1852` | `commentAdded` | `e26854d26e10993a2bb2bb4ba8058071d5740a1984acd114986bcd511b27b9bb` | `admin` / `maaroufabousaleh@hotmail.com` | mapped to `cebe9fc0-9203-4851-b8a8-4a9a33cd38d3`, READ_ONLY denial retained as ignored conflict `8270a07c-0ac8-4483-bb7e-388f004c0646` |

Both inbox rows were authenticated and retained. No `unknown_mapping` conflict
was created. The issue and comment policy conflicts were explicitly marked
`IGNORED` with their forensic snapshots. The comment's YouTrack activity record
(`7-5.0-0`, timestamp `1785371681891`) and webhook actor are the real human
`admin`. The issueUpdated webhook payload is a delayed projection echo: its
provider timestamp is `1785370223232`, its payload actor is `AIAT_Agents`, and
YouTrack activity record `0-0.14-97` independently records the human
description edit at timestamp `1785371670841` by `admin`. This discrepancy is
retained explicitly; the webhook payload is not relabeled as human evidence.

Persisted canonical snapshots before and after both inbound events are equal:
canonical issue
`c9aacb57-2717-44d2-b7ba-2c3649263876`, title unchanged,
description remains `SHADOW canonical projection certification` plus the
canonical marker only, priority remains `medium`, status remains `backlog`,
revision remains `2`, and canonical comments remain `0`. No inbound command or
comment projection outbox event was created. Mapping remains
`cebe9fc0-9203-4851-b8a8-4a9a33cd38d3` -> `3-23` / `AIAT-3`.

An inbound replay of the comment raw body through the gateway returned HTTP
`202` with `status=duplicate`, the original delivery ID, and the original inbox
ID `daf5ad9d-8503-4ce2-b6ff-a4fc41db1852`; the inbox count remained one. The
outbound replay reused the revision-2 idempotency key and returned `SYNCED`
for `3-23` / `AIAT-3`; the outbox count remained two total (revisions 1 and 2),
with one mapping and no duplicate canonical issue.

Final full audit reconciliation run
`e01fd6da-febc-4aeb-ab26-de4e158d3d59` completed with `seen=3`, `mapped=2`,
`drift=0`, `conflicts=0`, `version_mismatches=0`, and
`scope_conflicts=0`. Provider-version observation advanced to `1785371681890`
without changing canonical revision. Final doctor evidence was ready with no
blockers at `2026-07-30T00:46:35.5995590Z`-
`2026-07-30T00:46:36.7781634Z`. The binding remains `READ_ONLY`, the
connection remains `SHADOW`, active dead letters are `0`, and pending/failed
projections are `0`. No ACTIVE transition was generated, approved, or applied.

## 2026-07-30 — ACTIVE inbound-command boundary prepared (not activated)

Scope: binding `8c8a3b38-b57b-40d5-ae20-c46eb5654966`, canonical project
`68345b4f-c52b-48d0-8e2b-6daf958fd941`, mapped issue
`c9aacb57-2717-44d2-b7ba-2c3649263876` / YouTrack `AIAT-3` / external `3-23`,
mapping `cebe9fc0-9203-4851-b8a8-4a9a33cd38d3`. Binding remains `READ_ONLY`;
connection remains `SHADOW`; no ACTIVE lifecycle plan was generated,
approved, or applied.

The implemented provider-neutral ACTIVE policy is default-deny. Direct inbound
commands are limited to allowlisted priority values and non-destructive
statuses (`backlog`, `in_progress`, `review`, `blocked`). Title, description,
assignee/reassignment, closing, deletion, escalation, `done`, and `cancelled`
are approval-required. Ordinary, edited, and deleted comments are evidence-
only; structured `AIAT-COMMAND: {...}` comments require an AIAT approval and
invalid command syntax is rejected. AIAT Object ID/Object Type/Revision/
Managed, project identity/ownership, governance, credentials, binding, and
lifecycle fields are reserved and rejected.

ACTIVE requires an authenticated provider actor mapped through
`external_actor_mappings` to an authorized human/operator AIAT identity.
Provider integration users, `AIAT_Agents`, synthetic certification actors,
unknown actors, and projection echoes cannot authorize commands. Evidence
records retain provider actor and resolved AIAT identity, inbox ID, payload
hash, provider version, mapping revision, origin, and idempotency key.

Revision safety is enforced with an explicit `expected_canonical_revision` or
the latest durable mapping observation. Missing/stale revisions become
conflicts; canonical update and PM outbox enqueue remain one CAS/row-lock
transaction. Duplicate, reordered, delayed, and projection-echo events use
mapping/provider-version/revision/origin/content/idempotency evidence and do
not create a second canonical mutation. The existing governed lifecycle plan
flow is the kill switch/rollback to `READ_ONLY`; direct status writes remain
blocked.

Evidence: focused PM control-plane and provider-contract suites passed after
adding allowlist, actor, revision/CAS, comment-policy, reserved-field, and
echo regression coverage. Doctor and full reconciliation evidence remain the
required pre-ACTIVE gates; the current live binding is intentionally not
eligible for ACTIVE until an operator supplies actor mappings and separately
approves/runs the bounded canary.

## 2026-07-30 — durable actor mapping and unarmed priority canary

The prior configuration-only actor allowlist is not an authorization source.
Migration `0027_pm_actor_mappings_inbound_canary` adds tenant- and
connection-scoped immutable provider actor mappings with creation/approval
audits, revocation, exact-digest canary plans, and an atomic one-command claim.
For providers whose webhook payload lacks a user ID, AIAT resolves the actor
through the authenticated provider API, requires a unique matching result, and
stores only that returned immutable ID as the authorization key; login/email
are correlation evidence only.

The bounded canary leaves the connection `SHADOW` and binding `READ_ONLY`.
It may be separately approved and armed only for one mapped issue, one trusted
actor mapping, one exact canonical revision, one priority transition, and a
short expiry. Any scope escape, stale revision, duplicate, reordered event, or
post-claim failure is fail-closed. Disarming retains evidence and keeps the
binding READ_ONLY; no general ACTIVE enablement is implied.
