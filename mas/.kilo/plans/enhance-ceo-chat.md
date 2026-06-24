## Plan: Enhance CEO Chat

Goal: Upgrade `/(dashboard)/ceo/chat/page.tsx` from a plain conversation
view into a rich, typed-message experience that mirrors the CEO Live Feed
while staying specific to the operator–CEO conversation path.

### Non-goals
- Redesigning the CEO Live Feed (`/(dashboard)/ceo/page.tsx`).
- Changing the underlying message-router streams or team topology.
- Removing the existing dashboard SSE feed.

### Design decisions (locked)
1. **Shared frontend logic** — Extract `parseMessage`, `cleanChatText`,
   `payloadText`, `getTypeClass`, `getTypeBadgeClass`, `type-counting`,
   and `formatInTz` usage into shared hooks/components under
   `mas/apps/mas-dashboard/lib/ceo-feed/` so both the CEO page and the
   chat page consume the same code path.
2. **Chat page keeps its own stream** — It continues to use
   `/api/streams/exec_ceo` for history+live SSE, filtering to only
   operator↔CEO messages, but renders them with the richer bubble UI
   and cycle grouping (flat or grouped by project_id).
3. **Drop the fake LLM path** — Remove `_publish_ceo_response` from
   `POST /ceo/message` once the CEO agent runtime can reliably answer
   `HUMAN_DIRECTIVE` envelopes. Use an env-gated feature flag
   (`ENABLE_CEO_FAKE_RESPONSE=1`) so we can re-enable as a fallback if
   the agent runtime is silent for N seconds.
4. **Add CHAT action discriminator** — When the chat page sends, it
   adds `"action": "CHAT"` alongside `"action": "HUMAN_DIRECTIVE"` in
   the `TASK` payload. Team-runner’s `_choose_agent` is updated to
   route `TASK` envelopes with `action == "CHAT"` straight to the CEO
   admin agent (currently `TASK` → admin anyway, but this makes the
   intent explicit and safe).
5. **Tool registration** — `flow.*` tools are already present in
   `mas-tools-sdk/manifest.py`; ensure `exec_ceo.yaml` lists them so
   the manifest validation at runner startup passes. Add the missing
   flow tool implementations in `tool-service` if they do not yet
   exist.

### File changes

#### Frontend
- `mas/apps/mas-dashboard/lib/ceo-feed/` (new)
  - `types.ts` — shared envelope/feed-entry types.
  - `parsing.ts` — `parseMessage`, `cleanChatText`, `payloadText`,
    `parseFirstTimestamp`, `entryFromRaw`.
  - `styling.ts` — `getTypeClass`, `getTypeAccent`, `getTypeBadgeClass`,
    `getChipToneForType`, `TypeBadge`.
  - `use-ceo-stream.ts` — shared hook for loading recent history +
    subscribing to SSE; accepts a `filter` predicate.
- `mas/apps/mas-dashboard/app/(dashboard)/ceo/chat/page.tsx` (rewrite)
  - Use the shared hook; render bubbles with `TypeBadge`; reuse
    composer + error states from the live feed; add cycle-group toggle
    and message-type filter chips; use `formatInTz` for timestamps.
- `mas/apps/mas-dashboard/app/(dashboard)/ceo/page.tsx` (minor refactor)
  - Replace inline copies of parsing/styling with imports from
    `lib/ceo-feed/`.

#### Backend
- `mas/apps/orchestrator-api/orchestrator_api/main.py`
  - Gate `_publish_ceo_response` behind `os.getenv("ENABLE_CEO_FAKE_RESPONSE")`.
  - When the flag is off, return early without calling the LLM; the
    operator message is still published and the CEO runtime will reply.
- `mas/apps/team-runner/team_runner/main.py`
  - In `_choose_agent`, detect `payload.action == "CHAT"` on
    `TASK` envelopes and route to `admin_agent` explicitly (document
    why this is a no-op for exec_ceo while being safer for teams with
    workers).
- `mas/teams/exec_ceo.yaml`
  - Add `flow.list`, `flow.recommend`, `flow.assign`, `flow.status`,
    `flow.invoke`, `flow.advance` to the `admin.tools` list so the
    runner manifest validation passes once chat surfaces these tools.

### Verification
- TypeScript and Python lint must pass.
- Dashboard builds; `/ceo/chat` loads and streams without client
  errors.
- `team-runner` starts successfully for `exec_ceo` (tool manifest
  validation passes).
- `/api/ceo/messages` still round-trips; with the fake-response flag
  off the backend returns quickly and the live feed picks up the CEO
  runtime reply.