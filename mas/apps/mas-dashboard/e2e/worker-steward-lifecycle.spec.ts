import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

import { authenticate } from "./auth";

const WORKER_ID = "steward-lifecycle-worker";
const CANDIDATE_ID = "candidate-lifecycle-001";

function makeCandidate(status = "DISCOVERED") {
  return {
    candidate_id: CANDIDATE_ID,
    worker_id: WORKER_ID,
    intake_status: status,
    bundle: {
      bundle_id: "bundle-lifecycle-001",
      semantic_version: "2.1.0",
      upstream_compatibility_range: "^2.0.0",
      status: status === "APPROVED" ? "APPROVED" : "DRAFT",
      migration_notes: ["Keep the current tool grant set"],
    },
    adapter: {
      adapter_id: "adapter-lifecycle-001",
      version: "2.1.0",
      content_hash: "a".repeat(64),
      transport_type: "process",
      entrypoint: "adapter:run",
      implementation_ref: "worker-adapter@immutable-ref",
      status: status === "APPROVED" ? "APPROVED" : "candidate",
    },
    source_provenance: {
      canonical_source_repository: "https://example.invalid/aiat/worker",
      exact_release: "v2.0.0",
      commit_sha: "b".repeat(40),
      package_version: "2.0.0",
      transport_type: "process",
      adapter_version: "2.0.0",
      security_scan_status: "pending",
    },
    evidence: {},
    certification_id: null,
    approval_record_id: null,
  };
}

type CandidateFixture = Omit<ReturnType<typeof makeCandidate>, "certification_id" | "approval_record_id"> & {
  certification_id: string | null;
  approval_record_id: string | null;
};

function makeSteward() {
  return {
    steward_id: "steward-lifecycle-001",
    worker_id: WORKER_ID,
    status: "READY",
    active_bundle_id: "bundle-active-001",
    active_adapter_id: "adapter-active-001",
    pending_rollouts: 0,
    provenance: {
      canonical_source_repository: "https://example.invalid/aiat/worker",
      exact_release: "v2.0.0",
      commit_sha: "b".repeat(40),
      package_version: "2.0.0",
      dependency_lock_hash: "c".repeat(64),
      protocol_api_version: "aiat.worker.v1",
      adapter_version: "2.0.0",
      transport_type: "process",
      security_scan_status: "pending",
    },
  };
}

async function installGovernanceReads(page: Page) {
  await page.route("**/api/governance/model-profiles", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route("**/api/governance/model-profiles/catalogue", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: "steward-ui-v1",
        registry_model_count: 0,
        profile_count: 0,
        profile_version_count: 0,
        covered_profile_version_count: 0,
        profile_pending_model_count: 0,
      }),
    });
  });
  await page.route("**/api/governance/runs?limit=50", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route("**/api/governance/stewards**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([{ ...makeSteward(), candidate_count: 0, monitoring_cadence: "daily" }]),
    });
  });
}

test("governance supports immutable candidate review and staged rollout using mocked canonical APIs", async ({ page }) => {
  let candidate: CandidateFixture | null = null;
  let rollout: Record<string, unknown> | null = null;
  const requestedActions: Array<{ path: string; body: Record<string, unknown> }> = [];

  await installGovernanceReads(page);
  await page.route(`**/api/governance/stewards/${WORKER_ID}/**`, async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ steward: makeSteward(), candidates: candidate ? [candidate] : [] }),
      });
      return;
    }
    const body = route.request().postDataJSON() as Record<string, unknown>;
    requestedActions.push({ path, body });

    if (path.endsWith("/candidates") && route.request().method() === "POST") {
      candidate = makeCandidate();
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(candidate) });
      return;
    }
    if (path.endsWith(`/candidates/${CANDIDATE_ID}/stage`)) {
      candidate = { ...candidate!, intake_status: String(body.target_status) };
      candidate.evidence = {
        ...candidate.evidence,
        intake_transitions: [{ from: "DISCOVERED", to: body.target_status, actor: body.actor, evidence: body.evidence }],
      };
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(candidate) });
      return;
    }
    if (path.endsWith(`/candidates/${CANDIDATE_ID}/certify`)) {
      candidate = {
        ...candidate!,
        evidence: {
          ...candidate!.evidence,
          certification: { certification_id: "cert-lifecycle-001", passed: true, checks: { provenance_pin: true, security: true }, failures: [] },
        },
        certification_id: "cert-lifecycle-001",
      };
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ certification_id: "cert-lifecycle-001", passed: true, checks: { provenance_pin: true, security: true }, failures: [] }),
      });
      return;
    }
    if (path.endsWith(`/candidates/${CANDIDATE_ID}/approve`)) {
      candidate = { ...candidate!, intake_status: "APPROVED", approval_record_id: "approval-lifecycle-001", bundle: { ...candidate!.bundle, status: "APPROVED" }, adapter: { ...candidate!.adapter, status: "APPROVED" } };
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(candidate) });
      return;
    }
    if (path.endsWith("/rollouts")) {
      rollout = {
        rollout_id: "rollout-lifecycle-001",
        candidate_id: CANDIDATE_ID,
        status: "PENDING",
        eligible_task_classes: body.eligible_task_classes,
        sample_count: 0,
        sample_targets: { shadow: 10, readonly_canary: 5, live_canary: 3 },
        rollback_thresholds: { regression_fraction: 0.1 },
      };
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(rollout) });
      return;
    }
    if (path.endsWith("/advance")) {
      if (body.target_status === "CANARY" && Number(body.sample_count) < 10) {
        await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ error: "shadow rollout requires at least 10 samples before canary" }) });
        return;
      }
      rollout = { ...rollout!, status: body.target_status, sample_count: body.sample_count, comparison_metrics: body.comparison_metrics };
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(rollout) });
      return;
    }
    if (path.endsWith("/rollback")) {
      rollout = { ...rollout!, status: "ROLLED_BACK", rollback_reason: body.reason };
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(rollout) });
      return;
    }
    await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: "unexpected steward action" }) });
  });
  await page.route(`**/api/governance/stewards/${WORKER_ID}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ steward: makeSteward(), candidates: candidate ? [candidate] : [] }),
    });
  });

  page.on("dialog", (dialog) => dialog.accept());
  await authenticate(page, "/governance");
  const governance = page.getByRole("main", { name: "Governance" });
  const lifecycle = governance.getByRole("region", { name: "Worker steward candidates and rollout" });
  await expect(lifecycle.getByText("https://example.invalid/aiat/worker", { exact: true })).toBeVisible();
  await expect(lifecycle.getByText("b".repeat(40), { exact: true })).toBeVisible();

  await expect(lifecycle.getByLabel("Candidate semantic version")).toBeVisible();
  await lifecycle.getByLabel("Candidate semantic version").fill("2.1.0");
  await lifecycle.getByLabel("Adapter version").fill("2.1.0");
  await lifecycle.getByLabel("Upstream compatibility range").fill("^2.0.0");
  await lifecycle.getByLabel("Adapter entrypoint").fill("adapter:run");
  await lifecycle.getByLabel("Implementation reference").fill("worker-adapter@immutable-ref");
  await lifecycle.getByLabel("Diff summary").fill("Bounded operator-reviewed update");
  await lifecycle.getByRole("button", { name: "Create candidate from current steward provenance" }).click();
  const candidateCard = lifecycle.getByRole("article", { name: `Candidate ${CANDIDATE_ID}` });
  await expect(candidateCard).toBeVisible();
  await expect(candidateCard.getByText("2.1.0", { exact: false }).first()).toBeVisible();
  await expect(candidateCard.getByText("a".repeat(64), { exact: true })).toBeVisible();

  for (const stage of ["SOURCE_REVIEW", "SECURITY_REVIEW", "INTERFACE_RESEARCH", "GENERATED", "CERTIFYING"]) {
    await candidateCard.getByRole("button", { name: `Advance to ${stage}` }).click();
    await expect(candidateCard.getByText(stage, { exact: false }).first()).toBeVisible();
  }
  await candidateCard.getByRole("button", { name: "Run server-owned certification" }).click();
  await expect(candidateCard.getByText("Server-owned certification: PASSED")).toBeVisible();
  await candidateCard.getByLabel("Independent approval reason").fill("Reviewed exact candidate evidence");
  await candidateCard.getByRole("button", { name: "Approve candidate" }).click();
  await expect(candidateCard.getByText("APPROVED", { exact: false }).first()).toBeVisible();

  await lifecycle.getByRole("button", { name: "Start rollout for the sole approved candidate" }).click();
  await expect(lifecycle.getByText("rollout-lifecycle-001", { exact: true })).toBeVisible();
  await lifecycle.getByRole("button", { name: "Advance to SHADOW" }).click();
  await lifecycle.getByLabel("Observed sample count").fill("1");
  await lifecycle.getByRole("button", { name: "Advance to CANARY" }).click();
  await expect(lifecycle.getByText(/shadow rollout requires at least 10 samples/i)).toBeVisible();
  await lifecycle.getByLabel("Observed sample count").fill("10");
  await lifecycle.getByRole("button", { name: "Advance to CANARY" }).click();
  await expect(lifecycle.getByText("CANARY", { exact: true })).toBeVisible();
  await lifecycle.getByLabel("Rollback reason").fill("Fixture rollback after review");
  await lifecycle.getByRole("button", { name: "Roll back rollout" }).click();
  await expect(lifecycle.getByText("ROLLED_BACK", { exact: true })).toBeVisible();
  await expect(lifecycle.getByText(/existing API has no rollout-history read endpoint/i)).toBeVisible();

  const creation = requestedActions.find((entry) => entry.path.endsWith("/candidates"));
  expect(creation?.body).toMatchObject({
    semantic_version: "2.1.0",
    adapter_version: "2.1.0",
    upstream_compatibility_range: "^2.0.0",
    implementation_ref: "worker-adapter@immutable-ref",
  });
  expect(requestedActions.some((entry) => entry.path.endsWith("/certify") && Object.keys(entry.body).length === 0)).toBe(true);
  expect(requestedActions.some((entry) => entry.path.endsWith("/approve") && entry.body.decided_by === "dashboard-operator")).toBe(true);
  expect(requestedActions.some((entry) => entry.path.endsWith("/rollback") && entry.body.reason === "Fixture rollback after review")).toBe(true);
});

test("worker-section denial hides steward actions and preserves the governance boundary", async ({ page }) => {
  await installGovernanceReads(page);
  await page.route(`**/api/governance/stewards/${WORKER_ID}`, async (route) => {
    await route.fulfill({ status: 403, contentType: "application/json", body: JSON.stringify({ error: "worker section access denied" }) });
  });

  await authenticate(page, "/governance");
  const lifecycle = page.getByRole("region", { name: "Worker steward candidates and rollout" });
  await expect(lifecycle.getByRole("region", { name: "Worker steward access denied" })).toBeVisible();
  await expect(lifecycle.getByText(/workers section did not authorize this request/i)).toBeVisible();
  await expect(lifecycle.getByRole("button", { name: "Create candidate from current steward provenance" })).toHaveCount(0);
  await expect(lifecycle.getByRole("button", { name: /Start rollout/ })).toHaveCount(0);
});
