# PM integration runbook

1. Run doctor, cursor-reset reconciliation, inbox/outbox/dead-letter checks,
   TLS checks, and management-authentication checks.
2. Confirm the expected connection and binding revisions, trusted actor mapping,
   exact scope, canonical revision, and provider priority.
3. Generate a new lifecycle plan. Independently recompute its digest, approve
   it, apply it, verify immutable evidence, and verify idempotent replay.
4. Print the exact one-field human instruction. Accept only a verified webhook
   from immutable actor `2-1` for AIAT-3 and `issue.priority`.
5. After success, verify canonical CAS, revision increment, command/actor/
   suppression evidence, no duplicate replay, and clean reconciliation.
6. If the 20-minute window expires or any mutation fails, apply a governed
   ACTIVE → READ_ONLY plan. Never edit lifecycle state directly in SQL.

The current stable state is connection ACTIVE revision 2 and binding READ_ONLY
revision 8. No live command was accepted in the latest attempt.
