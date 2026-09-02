import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const HISTORY_FIXTURE = {
  entries: [
    {
      entry_id: "ceo-states-001",
      envelope: JSON.stringify({
        message_type: "DIRECTIVE",
        sender_id: "ceo-states",
        project_id: "project-states",
        timestamp: "2026-08-01T00:00:00Z",
        payload: { summary: "retained directive" },
      }),
    },
  ],
};

test("CEO live feed retains messages when reconnect fails", async ({ page }) => {
  await page.addInitScript(() => {
    let attempt = 0;

    class FakeEventSource extends EventTarget {
      static readonly CONNECTING = 0;
      static readonly OPEN = 1;
      static readonly CLOSED = 2;
      readonly url: string;
      readyState = FakeEventSource.OPEN;
      onerror: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent<string>) => void) | null = null;

      constructor(url: string) {
        super();
        this.url = url;
        const currentAttempt = ++attempt;
        window.setTimeout(() => {
          if (currentAttempt === 2) {
            this.emit("error", JSON.stringify({ error: "CEO stream fixture unavailable" }));
            return;
          }
          this.emit("connected", JSON.stringify({ team: "exec_ceo" }));
          if (currentAttempt >= 3) {
            this.emit(
              "message",
              JSON.stringify({
                message_type: "REPORT",
                sender_id: "ceo-states",
                timestamp: "2026-08-01T00:00:01Z",
                payload: { summary: "recovered report" },
              }),
            );
          }
        }, 0);
      }

      close() {
        this.readyState = FakeEventSource.CLOSED;
      }

      private emit(type: string, data: string) {
        const event = new MessageEvent(type, { data });
        this.dispatchEvent(event);
        const handler = type === "message" ? this.onmessage : this.onerror;
        if (handler) handler(event);
      }
    }

    Object.defineProperty(window, "EventSource", {
      configurable: true,
      writable: true,
      value: FakeEventSource,
    });
  });

  await page.route("**/api/streams/exec_ceo**", async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("history") === "1") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(HISTORY_FIXTURE),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: "",
    });
  });

  await authenticate(page, "/ceo");
  await expect(page.getByRole("heading", { name: "CEO Live Feed" })).toBeVisible();
  const ceo = page.getByRole("main", { name: "CEO live feed" });
  await expect(ceo).toBeVisible();
  await expect(
    ceo.getByRole("region", { name: "CEO message composer" }),
  ).toBeVisible();
  await expect(
    ceo.getByRole("region", { name: "CEO feed summary" }),
  ).toBeVisible();
  await expect(
    ceo.getByRole("region", { name: "CEO feed filters" }),
  ).toBeVisible();
  await expect(
    ceo.getByRole("region", { name: "CEO message feed" }),
  ).toBeVisible();
  await expect(
    ceo.getByRole("region", { name: "CEO feed status" }),
  ).toBeVisible();
  for (const control of [
    ceo.getByRole("button", { name: "Group entries by think cycle" }),
    ceo.getByRole("button", { name: "Pause live feed" }),
    ceo.getByRole("button", { name: "Clear buffered messages" }),
    ceo.getByRole("button", { name: "Reconnect CEO feed" }),
    ceo.getByRole("textbox", { name: "Message to CEO" }),
    ceo.getByRole("button", { name: "Send message to CEO" }),
    ceo.getByRole("searchbox", { name: "Search CEO feed" }),
  ]) {
    await expect(control).toHaveCSS("min-height", "44px");
  }
  const typeFilters = ceo
    .getByRole("group", { name: "Filter by message type" })
    .getByRole("button");
  await expect(typeFilters).toHaveCount(10);
  for (const filter of await typeFilters.all()) {
    await expect(filter).toHaveCSS("min-height", "44px");
  }
  await expect(
    ceo.getByRole("button", { name: "Copy raw envelope to clipboard" }),
  ).toHaveCSS("min-height", "44px");
  await expect(page.getByText("DIRECTIVE", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "Reconnect CEO feed" }).click();
  await expect(page.getByText("Showing last known CEO feed")).toBeVisible();
  await expect(page.getByText(/CEO stream fixture unavailable/i)).toBeVisible();
  await expect(page.getByText("DIRECTIVE", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toHaveCSS(
    "min-height",
    "44px",
  );

  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("Showing last known CEO feed")).toHaveCount(0);
  await expect(page.getByText("REPORT", { exact: true }).first()).toBeVisible();
});

test("CEO live feed access denial on first load hides read and send controls", async ({ page }) => {
  await page.route("**/api/streams/exec_ceo**", async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("history") === "1") {
      await route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({ detail: "CEO feed access denied" }),
      });
      return;
    }
    await route.fulfill({
      status: 403,
      contentType: "text/event-stream",
      body: "",
    });
  });

  await authenticate(page, "/ceo");
  const ceo = page.getByRole("main", { name: "CEO live feed" });
  await expect(
    ceo.getByRole("region", { name: "CEO feed access status" }),
  ).toBeVisible();
  await expect(
    ceo.getByRole("heading", { name: "CEO feed access denied" }),
  ).toBeVisible();
  await expect(
    ceo.getByText("No live feed state is inferred or displayed."),
  ).toBeVisible();
  await expect(ceo.getByRole("region", { name: "CEO message composer" })).toHaveCount(0);
  await expect(ceo.getByRole("button", { name: "Reconnect CEO feed" })).toHaveCount(0);
  await expect(ceo.getByRole("button", { name: "Retry" })).toHaveCount(0);
  await expect(ceo.getByRole("searchbox", { name: "Search CEO feed" })).toHaveCount(0);
  await expect(ceo.getByRole("button", { name: "Copy raw envelope to clipboard" })).toHaveCount(0);
  await expect(
    ceo.getByText("No live CEO feed state is inferred while authorization is unavailable."),
  ).toBeVisible();
});

test("CEO live feed access denial after a successful read retains messages without controls", async ({ page }) => {
  await page.addInitScript(() => {
    class QuietEventSource extends EventTarget {
      static readonly CONNECTING = 0;
      static readonly OPEN = 1;
      static readonly CLOSED = 2;
      readonly url: string;
      readyState = QuietEventSource.OPEN;

      constructor(url: string) {
        super();
        this.url = url;
      }

      close() {
        this.readyState = QuietEventSource.CLOSED;
      }
    }

    Object.defineProperty(window, "EventSource", {
      configurable: true,
      writable: true,
      value: QuietEventSource,
    });
  });

  let historyReads = 0;
  await page.route("**/api/streams/exec_ceo**", async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("history") === "1") {
      historyReads += 1;
      if (historyReads > 1) {
        await route.fulfill({
          status: 401,
          contentType: "application/json",
          body: JSON.stringify({ detail: "CEO feed authorization expired" }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(HISTORY_FIXTURE),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: "",
    });
  });

  await authenticate(page, "/ceo");
  const ceo = page.getByRole("main", { name: "CEO live feed" });
  await expect(ceo.getByText("DIRECTIVE", { exact: true }).first()).toBeVisible();
  await ceo.getByRole("button", { name: "Reconnect CEO feed" }).click();

  await expect(
    ceo.getByRole("region", { name: "CEO feed access status" }),
  ).toBeVisible();
  await expect(ceo.getByText("DIRECTIVE", { exact: true }).first()).toBeVisible();
  await expect(ceo.getByText(/retained directive/)).toBeVisible();
  await expect(ceo.getByRole("region", { name: "CEO message composer" })).toHaveCount(0);
  await expect(ceo.getByRole("button", { name: "Reconnect CEO feed" })).toHaveCount(0);
  await expect(ceo.getByRole("button", { name: "Clear buffered messages" })).toHaveCount(0);
  await expect(ceo.getByRole("searchbox", { name: "Search CEO feed" })).toHaveCount(0);
  await expect(ceo.getByRole("button", { name: "Copy raw envelope to clipboard" })).toHaveCount(0);
});
