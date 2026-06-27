Yes — the **tools** need the same split as the workers:

1. **AIAT authority tools** stay custom, because they mutate AIAT state, permissions, approvals, documents, projects, issues, or KPIs.
2. **OSS capability tools** sit *behind* AIAT wrappers, so AIAT still controls permission, audit, rate limits, sandboxing, and approvals. Your architecture already says the tool-service should be role-gated, expose 6 tool groups, enforce permissions, rate-limit calls, cache duplicate calls, and use circuit breakers. 

## 1. Tools you can ship in AIAT default/product

| Tool area              | AIAT tool names / capability                                                                          | Ship default backend                                                           | Final decision                                                                      |
| ---------------------- | ----------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------- |
| Tool-service shell     | `POST /tools/{tool_name}/run`, `GET /tools`, `GET /health`                                            | AIAT custom FastAPI tool-service                                               | ✅ Ship custom. This is the policy/audit/rate-limit boundary.                        |
| Workflow authority     | `project.create`, `project.status`, `project.transition`, `project.list`                              | AIAT orchestrator + deterministic workflow controller + Postgres               | ✅ Ship custom only. Do not outsource project state authority.                       |
| Human approval         | `approval.*`, human decision gates                                                                    | AIAT orchestrator + approval tables                                            | ✅ Ship custom only.                                                                 |
| Document lifecycle     | `document.create_draft`, `document.submit`, `document.revise`, `document.get_latest`, `document.list` | AIAT document tables + object storage + Docling/GitHub Spec Kit behind wrapper | ✅ Ship. AIAT owns lifecycle; OSS helps parse/write docs.                            |
| Review workflow        | `review.start_session`, `review.submit`, `review.aggregate`, `review.submit_veto`                     | AIAT review/session tables + C-Suite review agents                             | ✅ Ship custom. Review authority stays in AIAT.                                      |
| Sprint/issue authority | `sprint.create`, `sprint.activate`, `issue.create`, `issue.update_status`, `issue.list`               | AIAT Postgres + GitHub Issues/ccpm adapter                                     | ✅ Ship. Prefer GitHub Issues/ccpm over Plane/OpenProject by default.                |
| KPI/learning           | `kpi.compute`, `kpi.compute_project`, `kpi.query_history`, `kpi.update_agent_profile`                 | AIAT KPI tables + Postgres history + LangGraph worker logic                    | ✅ Ship custom wrapper.                                                              |
| Blob/artifacts         | `blob.upload`, `blob.download`, `blob.list`, `blob.delete`                                            | S3-compatible wrapper                                                          | ✅ Ship wrapper. Use a license-safe S3-compatible backend for external distribution. |
| Safe command boundary  | `command.run_safe`                                                                                    | AIAT-gated subprocess/sandbox wrapper                                          | ✅ Ship, but only with allowlists, budgets, logs, and sandboxing.                    |
| File/repo boundary     | `file.patch`, `repo.read`, `repo.search`                                                              | AIAT-gated wrappers over workspace/GitHub                                      | ✅ Ship. Never let workers access raw filesystem or GitHub unrestricted.             |
| Document/PDF ingestion | Doc parsing, OCR, Markdown/JSON extraction                                                            | Docling                                                                        | ✅ Ship default.                                                                     |
| Research/web fetch     | Search/fetch/scrape                                                                                   | Scrapling; normal web fetch adapter                                            | ✅ Ship Scrapling/fetch. Keep browser agents optional.                               |
| Coding/test execution  | Coding worker, test worker                                                                            | OpenCode/OpenHands core + pytest + Playwright                                  | ✅ Ship only after adapter certification.                                            |
| Code review            | PR/code review worker                                                                                 | pr-agent, open-code-review, stage-cli                                          | ✅ Ship, but pin exact repos because some names are generic.                         |
| Security scan          | Static/code/security checks                                                                           | Semgrep CLI + SkillSpector                                                     | ✅ Ship. Do **not** ship TruffleHog as default.                                      |
| QA/browser testing     | E2E tests, UI flows                                                                                   | Playwright                                                                     | ✅ Ship default.                                                                     |
| DevOps/IaC             | Infra provisioning, CI/CD                                                                             | OpenTofu + GitHub Actions adapter                                              | ✅ Ship. Keep Ansible as user-installed optional adapter.                            |
| Monitoring             | Metrics, health checks, SRE checks                                                                    | Prometheus + VictoriaMetrics + Playwright/API checks                           | ✅ Ship. Do not embed Grafana by default.                                            |
| Diagrams/docs          | Architecture diagrams, exports                                                                        | Mermaid                                                                        | ✅ Ship default.                                                                     |
| Visual UI tools        | Flow builder, org graph, capability graph                                                             | React Flow + Cytoscape.js + Mermaid                                            | ✅ Ship. Extend dashboard, do not replace it.                                        |
| Worker/tool protocol   | Tool interoperability                                                                                 | MCP SDKs/bridge                                                                | ✅ Ship as adapter mode, but verify each MCP server separately.                      |
| Sandbox                | Worker/tool isolation                                                                                 | gVisor default; Firecracker optional                                           | ✅ Ship gVisor. Firecracker can be optional high-risk mode.                          |

Your own pasted tool list already separates custom authority tools from OSS capability implementations: project/document/review/sprint/KPI/blob/policy tools stay custom, while Docling, Scrapling, OpenCode/OpenHands, pr-agent, Semgrep, Playwright, OpenTofu, ccpm, Mermaid, MCP, and sandboxes sit behind AIAT wrappers. 

## 2. Tools you can personally add after, but not ship as AIAT default

| Tool                      | Personal/local use |               AIAT default? | How to use safely                                                                                         |
| ------------------------- | -----------------: | --------------------------: | --------------------------------------------------------------------------------------------------------- |
| TruffleHog                |              ✅ Yes |                        ❌ No | Use as isolated optional scanner or commercial/license-cleared scanner. Not default because of AGPL risk. |
| Plane                     |              ✅ Yes |                        ❌ No | Use as separate self-hosted PM integration only. Do not embed as AIAT PM module.                          |
| OpenProject               |              ✅ Yes |            ⚠️ External only | Good personal/self-hosted PM platform, but keep separate because GPL risk.                                |
| Grafana                   |              ✅ Yes |                        ❌ No | Use as local/self-hosted monitoring dashboard. Do not embed as AIAT’s default dashboard.                  |
| ZITADEL                   |              ✅ Yes |                        ❌ No | Optional external identity provider after license review.                                                 |
| Vault                     |              ✅ Yes |                        ❌ No | Use personally or commercially cleared. For shipped default, prefer OpenBao/cloud KMS/secrets manager.    |
| Ansible                   |              ✅ Yes |             ⚠️ Adapter only | AIAT can call user-installed Ansible externally; do not bundle tightly.                                   |
| Neo4j Community           |              ✅ Yes | ⚠️ External/commercial only | Optional graph analytics read-model, not default embedded DB.                                             |
| browser-use unrestricted  |              ✅ Yes |         ⚠️ Guardrailed only | Use only with domain allowlists, no stealth/CAPTCHA/proxy abuse, full logs, and approval gates.           |
| AutoGen                   |              ✅ Yes |        ⚠️ Experimental only | License is okay, but Microsoft Agent Framework is better for new default Microsoft-style workers.         |
| OpenClaw                  |              ✅ Yes |        ⚠️ Experimental only | Do not use as CEO/default authority runtime. Optional isolated assistant only.                            |
| Firecracker advanced mode |              ✅ Yes |                  ✅ Optional | License is okay, but operationally more complex. Keep for high-risk workers.                              |
| n8n                       |              ✅ Yes |     ⚠️ Edge automation only | Use for external automations/webhooks, not as AIAT’s core workflow runtime.                               |
| Garage / SeaweedFS        |              ✅ Yes |     ⚠️ Later storage option | Good candidates if you replace MinIO/S3 backend later; audit before switching.                            |

The license-corrected stack from the previous review already says to remove TruffleHog from default security, remove Plane/OpenProject from default PM, remove Grafana from embedded monitoring, and make license/provenance checks mandatory. 

## 3. Final tool-service manifest I would implement

| Group        | Tools                                                                                                                                       | Allowed by default                                        |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| Workflow     | `project.create`, `project.status`, `project.transition`, `project.list`                                                                    | CEO/orchestrator; limited status access for chiefs        |
| Document     | `document.create_draft`, `document.submit`, `document.revise`, `document.get_latest`, `document.list`                                       | COO, C-Suite, PMs, workers with read-only limits          |
| Review       | `review.start_session`, `review.submit`, `review.aggregate`, `review.submit_veto`                                                           | COO starts/aggregates; C-Suite submits; CSO can veto      |
| Sprint/Issue | `sprint.create`, `sprint.activate`, `issue.create`, `issue.decompose`, `issue.update_status`, `issue.list`                                  | CTO creates/activates; PMs/workers update assigned issues |
| DevOps       | `infra.provision`, `cicd.configure`, `monitoring.setup`, `secrets.manage`, `infra.ready_signal`                                             | DevOps PM and DevOps workers only                         |
| KPI/Utility  | `kpi.compute`, `kpi.query_history`, `kpi.update_agent_profile`, `velocity.report`, `estimation.adjust`, `blob.*`, `web_search`, `web_fetch` | CTO for KPI; most roles for limited blob/web access       |

This matches the architecture plan’s 6 tool groups and role-gated tool-service design. Workers should be blocked from dangerous authority tools like `project.transition`, `approval.*`, `review.start_session`, `sprint.create`, and `sprint.activate`. 

## Bottom line

**Ship AIAT’s tool-service and authority tools custom.**
**Ship safe OSS capability tools behind wrappers.**
**Keep AGPL/GPL/BUSL or high-risk tools personal/optional/external.**

The clean default tool stack is:

**Docling, GitHub Spec Kit, Scrapling, OpenCode/OpenHands after certification, pr-agent, open-code-review/stage-cli after repo pinning, Semgrep CLI, SkillSpector, Playwright, OpenTofu, GitHub Actions, ccpm, GitHub Issues, Mermaid, React Flow, Cytoscape.js, MCP, Prometheus, VictoriaMetrics, gVisor, optional Firecracker.**
