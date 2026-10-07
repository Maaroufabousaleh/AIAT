# AIAT Post-Feature Certification Backlog

**Scope:** personal/internal AIAT instance  
**Status:** deferred while feature-complete alpha work is in progress  
**Authority:** this page tracks proof and operator work; it does not mark implemented product behavior as incomplete.

The alpha exit criteria are tracked separately from production certification.
These items return to the critical path after the major operator journeys are
feature-complete, unless investigation finds a current safety or correctness
defect in an active path.

| ID | Area | Deferred evidence/action | Owner | Release impact |
| --- | --- | --- | --- | --- |
| CERT-NATIVE-01 | Native Linux | Run the maintained release workflow on a clean native Ubuntu host, including Docker, gVisor/Kata selection, networking, recovery, and health checks. WSL is development evidence only. | Operator | Blocks native release certification |
| CERT-IMAGES-01 | Deployment images | Freeze production registry references, build/sign or otherwise record immutable OCI digests, and complete image SBOM and security disposition for every required image. | Operator / infrastructure | Blocks production image provenance |
| CERT-KMS-01 | Object storage | Configure the selected production object-store identity and provider-managed SSE-KMS with the required external key custody; run PUT/read/checksum/multipart/abort/delete/lifecycle and residue checks. | Operator / provider | Blocks provider KMS certification |
| CERT-DR-01 | Database and evidence recovery | Perform encrypted database and object-store backup/restore on a clean independent host; verify ownership, histories, worker/steward state, audit/evidence, and secret-safe handling. | Operator | Blocks independent-host DR certification |
| CERT-PROVIDER-01 | Model/provider | After explicit operator authorization, run the approved live OpenCode task and provider success/failure/fallback/recovery checks. Keep `AIAT_ALLOW_EXTERNAL_PROVIDER_DISPATCH` disabled until then. | Operator | Blocks provider-backed worker certification |
| CERT-SECURITY-01 | OpenCode candidate security | Complete human disposition for remaining scanner/security review rows against the exact active candidate. Automated results remain evidence, not a blanket waiver. | Human security reviewer | Blocks security acceptance/release gate |
| CERT-ROTATE-01 | Secret rotation | Rotate the dashboard signing secret and operator credential material using the normal procedure, then verify old sessions/tokens fail and new login succeeds without retaining secret values. | Operator | Blocks secret-exposure closure |
| CERT-INTEGRATIONS-01 | External collaboration | Complete live GitHub App installation/auth/webhook/permission/revocation matrix and any selected YouTrack ACTIVE command rehearsal. | Operator / provider | Blocks certification only for selected live integrations |
| CERT-LOAD-01 | Capacity and resilience | Run bounded deployment-sized load, soak, concurrency, queue-pressure, and chaos/recovery exercises with resource-growth evidence. | Engineering / operator | Blocks production capacity certification |
| CERT-RUNTIMES-01 | Optional runtimes | Certify only runtimes selected for activation; OpenHands remains inactive unless its existing AIAT governance and activation requirements are met. | Engineering / operator | Optional; does not block default OpenCode alpha |
| CERT-A11Y-01 | Accessibility | Complete the broader WCAG audit on the release deployment, beyond the existing accessibility contracts and focused keyboard checks. | Engineering / operator | Blocks accessibility certification only |

Do not use this list to defer a known exploitable defect, a required isolation
downgrade, an authority bypass, or a secret committed to source. Those are
development blockers and must be fixed immediately.
