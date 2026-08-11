# PM ACTIVE certification ledger

## 2026-08-05 UTC — HUMAN_SESSION_UNAVAILABLE

Scope was limited to connection
`1b699f09-06c7-4a16-a4f3-9c1aaf69d6e2`, binding
`8c8a3b38-b57b-40d5-ae20-c46eb5654966`, canonical issue
`c9aacb57-2717-44d2-b7ba-2c3649263876`, provider actor `2-1`, and
`issue.priority`.

The connection lifecycle plan
`3da5c641-dfeb-44f1-a2cb-8cd346323ca5` applied SHADOW revision 1 to ACTIVE
revision 2. Its digest was
`48b021fea69e0cb20b6f0c97bc1538c8dc9675798cbe386cee8aa8cdc6278369`.

The binding lifecycle plan
`384ff1b5-ce24-4acc-815c-609220190c11` applied READ_ONLY revision 4 to ACTIVE
revision 5. Its digest was
`5b39b4c25a4cd57d83feedbf4789b2b64ae5800558a93fa1feb214c871877993`.

No provider action was performed. No issue field was changed. No provider
credentials, cookies, tokens, or session exports were accessed or retained.

Because no headed browser session could be presented, governed rollback plan
`940b0654-a5f3-4f7d-9a1d-a6796f8478db` applied ACTIVE revision 5 to READ_ONLY
revision 6. Its digest was
`a0d5f24d920bc8d361fc383cbb545ed837f2078de1fa679c3c3e35268dc0aa99`.

Rollback evidence:

- Approval evidence: `9c2e3424-22aa-40c8-a5ac-541028cd0c44`
- Application audit: `dcf594a8-2f4d-4dad-ae72-cba3b6c43f32`
- Application transaction: `36675171-6475-4dc3-99de-1b2ca9f9d698`
- Fresh post-rollback reconciliation: `1b87719c-c611-4196-8ef5-f4390574a967`
- Final state: connection ACTIVE revision 2; binding READ_ONLY revision 6

Final gates were clean: doctor ready, reconciliation drift/conflicts 0,
active dead letters 0, pending/processing/failed projections 0, open
conflicts 0, TLS certificate and hostname verification enabled, and management
authentication enforced. One historical dead letter remains governed and
superseded; it is not an active blocker.

Certification status: **not certified**. The required single browser-mediated
human action remains outstanding and must not be substituted with an API token,
integration identity, or synthetic event.

## 2026-08-05 UTC — HUMAN_ACTION_TIMEOUT

After the previous rollback, a new four-hour binding activation was authorized
for the same exact scope. The persisted plan
`50afc48c-7429-452f-a9e0-f1aa3ac6c318` had digest
`3e14f167637398da2690efa88cccc67cf0517c3d15a29683e863b1f255ef571f` and
expired at `2026-08-05T05:33:55.132382Z`. It applied READ_ONLY revision 6 to
ACTIVE revision 7 while retaining connection ACTIVE revision 2.

Activation evidence:

- Approval audit: `e6f51ff7-db97-4c57-a2c5-d82a348cb190`
- Approval evidence: `296b93fe-d2c0-4480-a40b-15609d8a39da`
- Application audit: `8854e67f-a59c-48f3-8874-47b4500bfe47`
- Application transaction: `8aded540-8e43-4fc2-bf5f-376ded06995d`
- Immediate reconciliation: `715b2ecb-4b38-4182-89b2-3be5b884c130`
- Final pre-action reconciliation: `b05e720b-2f8e-4d9e-977d-ce319c1f88fe`

The exact manual instruction was issued: change AIAT-3 priority `high` to
`critical`, with no other field or comment. No matching authenticated webhook
arrived during the authorized 20-minute window. No post-activation inbox event,
command evidence, actor-resolution evidence, canonical mutation, or provider
projection was created. Canonical state remained `high`, revision 3.

The governed timeout rollback plan
`3f45530e-44c8-4d27-b76a-86ac2992ac7b` had digest
`915074bde2ca7afd0430eba06c08775986b178d143c26d3cabc1ccdfe3893383` and
applied ACTIVE revision 7 to READ_ONLY revision 8.

Rollback evidence:

- Approval audit: `2f0652da-82c6-493a-9c44-74edac8a2356`
- Approval evidence: `d465b858-6627-4119-94ee-8912b8575c45`
- Application audit: `1d2e891d-8e38-499c-9c71-dda364f02245`
- Application transaction: `20e706f2-653e-4041-a353-9563b9ea2767`
- Final reconciliation: `7e22c620-c421-4e49-be9a-ecb689dd9ff1`

Final stable state is connection ACTIVE revision 2 and binding READ_ONLY
revision 8. Doctor is ready; drift, conflicts, hash/version/scope mismatches,
open conflicts, active dead letters, pending/processing/failed projections,
and post-activation inbox events are all zero. The one historical dead letter
remains governed and superseded.

The lifecycle plan generator now snapshots trusted actor mappings, direct
policy, default-deny fields, blast radius, and governed kill-switch metadata.
Normal ACTIVE commands now persist actor, command, and source-projection
suppression evidence in the same transaction as canonical CAS and outbox
creation; evidence-write failure rolls back the state change. These changes
were covered by focused and aggregate tests, but the live command path remains
uncertified because the human action did not arrive.

Certification status: **not certified — HUMAN_ACTION_TIMEOUT**.
