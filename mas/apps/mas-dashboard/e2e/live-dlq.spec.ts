import { expect, test } from "@playwright/test";
import { authenticate } from "./auth";

const liveLetterId = process.env.AIAT_LIVE_DLQ_ID;

test.describe("Live DLQ evidence", () => {
  test.skip(!liveLetterId, "Set AIAT_LIVE_DLQ_ID to run the destructive live replay probe");

  test("inspects and safely replays one letter with an audit record", async ({ page, request }) => {
    await authenticate(page, "/dlq");
    await expect(page.getByRole("heading", { name: "Dead Letter Queue" })).toBeVisible();

    const entry = page
      .getByRole("listitem")
      .filter({ hasText: "ttl_expired" });
    await expect(entry).toBeVisible();
    await expect(entry.getByText("ttl_expired")).toBeVisible();
    await entry.getByRole("button", { name: "Inspect envelope" }).click();
    await expect(entry.getByText("C-010-expired")).toBeVisible();

    const replayResponse = page.waitForResponse(
      (response) => response.url().endsWith(`/api/dlq/${liveLetterId}/replay`) && response.request().method() === "POST",
    );
    await entry.getByRole("button", { name: `Replay dead letter ${liveLetterId}` }).click();
    const response = await replayResponse;
    expect(response.ok()).toBeTruthy();
    const result = (await response.json()) as {
      status: string;
      new_message_id: string;
      entry_id: string;
      audit_task_id: string;
    };
    expect(result.status).toBe("replayed");
    expect(result.entry_id).toMatch(/^\d+-\d+$/);
    expect(result.audit_task_id).toBe(result.new_message_id);
    await expect(entry.getByRole("status")).toHaveText("Replayed");

    const audit = await request.get(`http://127.0.0.1:8000/tasks/${result.audit_task_id}`);
    expect(audit.ok()).toBeTruthy();
    const auditRow = await audit.json();
    expect(auditRow.status).toBe("DLQ_REPLAYED");
    expect(auditRow.input.dead_letter_id).toBe(Number(liveLetterId));
    expect(auditRow.output.retry_count).toBe(0);
    expect(auditRow.output.router_entry_id).toBe(result.entry_id);
  });
});
