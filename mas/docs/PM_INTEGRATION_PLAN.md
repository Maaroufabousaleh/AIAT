# PM integration rollout plan

The governed rollout scope is one YouTrack connection
`1b699f09-06c7-4a16-a4f3-9c1aaf69d6e2` and one binding
`8c8a3b38-b57b-40d5-ae20-c46eb5654966`. The connection may remain ACTIVE while
the binding is READ_ONLY. Binding-wide ACTIVE is allowed only through a
persisted, digest-bound lifecycle plan with transactional approval and
application evidence.

The only currently trusted direct-command mapping is
`849fa0b8-84c0-4e9e-aeed-dfce71775470` → immutable provider actor `2-1`, with
scope `issue.priority`. Priority uses canonical CAS and stale/replay
protection. Other fields are approval-required, evidence-only, reserved, or
rejected according to [PM_ACTIVE_READINESS.md](PM_ACTIVE_READINESS.md).

The latest attempt timed out before the one manual AIAT-3 action and was rolled
back. See [PM_ACTIVE_CERTIFICATION_LEDGER.md](PM_ACTIVE_CERTIFICATION_LEDGER.md)
for immutable evidence and the current certification boundary.
