import { expect, test } from "@playwright/test";
import { authenticate } from "./auth";

test("CEO chat merges live responses with delayed history and normalizes operator messages", async ({
  page,
}) => {
  const operatorEnvelope = JSON.stringify({
    message_id: "operator-history-1",
    msg_type: "TASK",
    sender_id: "human_operator",
    payload: {
      action: "HUMAN_DIRECTIVE",
      instruction: "Historical operator directive",
    },
    timestamp: "2026-06-21T12:00:00Z",
  });
  const liveEnvelope = JSON.stringify({
    message_id: "ceo-live-1",
    msg_type: "RESPONSE",
    sender_id: "ceo",
    payload: { response: "Live CEO response" },
    timestamp: "2026-06-21T12:00:01Z",
  });
  const departmentEnvelope = JSON.stringify({
    message_id: "department-live-1",
    msg_type: "RESPONSE",
    sender_id: "coo",
    sender_team: "exec_coo",
    payload: { response: "Department workflow update" },
    timestamp: "2026-06-21T12:00:02Z",
  });

  await page.route("**/api/streams/exec_ceo*", async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("history") === "1") {
      await new Promise((resolve) => setTimeout(resolve, 250));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        json: { entries: [{ entry_id: "1-0", envelope: operatorEnvelope }] },
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: `event: connected\ndata: {"team":"exec_ceo"}\n\ndata: ${liveEnvelope}\n\ndata: ${departmentEnvelope}\n\n`,
    });
  });

  await authenticate(page, "/ceo/chat");

  await expect(page.getByText("Live CEO response")).toBeVisible();
  await expect(page.getByText("Historical operator directive")).toBeVisible();
  await expect(page.getByText("Department workflow update")).toHaveCount(0);

  const outboundFilter = page.getByRole("button", { name: /OUTBOUND\s+1/i });
  await expect(outboundFilter).toBeVisible();
  await outboundFilter.click();
  await expect(page.getByText("Historical operator directive")).toBeVisible();
  await expect(page.getByText("Live CEO response")).toHaveCount(0);
});
