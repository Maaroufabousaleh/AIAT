import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const HISTORY_FIXTURE = {
  entries: [
    {
      entry_id: "stream-e2e-001",
      envelope: JSON.stringify({
        message_type: "agent.started",
        sender_id: "streams-e2e",
        timestamp: "2026-08-01T00:00:00Z",
      }),
    },
  ],
};

test("stream monitor retains messages when reconnect fails", async ({ page }) => {
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
            this.emit("error", JSON.stringify({ error: "stream fixture unavailable" }));
            return;
          }
          this.emit("connected", JSON.stringify({ team: "exec_ceo" }));
          if (currentAttempt >= 3) {
            this.emit(
              "message",
              JSON.stringify({
                message_type: "agent.completed",
                sender_id: "streams-e2e",
                timestamp: "2026-08-01T00:00:01Z",
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

  await page.route("**/api/streams/**", async (route) => {
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

  await authenticate(page, "/streams");
  await expect(page.getByRole("heading", { name: "Agent Stream Monitor" })).toBeVisible();
  const streams = page.getByRole("main", { name: "Agent stream monitor" });
  await expect(streams).toBeVisible();
  await expect(
    streams.getByRole("region", { name: "Message type filters" }),
  ).toBeVisible();
  await expect(
    streams.getByRole("region", { name: "Live message feed" }),
  ).toBeVisible();
  await expect(
    streams.getByRole("region", { name: "Stream status" }),
  ).toBeVisible();
  await expect(
    streams.getByRole("table", { name: "Agent message stream" }),
  ).toBeVisible();
  for (const control of [
    streams.getByRole("searchbox", {
      name: "Filter messages by text, sender, or project",
    }),
    streams.getByRole("combobox", { name: "Select team stream" }),
    streams.getByRole("button", { name: "Reconnect stream" }),
    streams.getByRole("checkbox", { name: "Group by type" }),
    streams.getByRole("button", { name: "Pause live feed" }),
    streams.getByRole("button", { name: "Clear message history" }),
  ]) {
    await expect(control).toHaveCSS("min-height", "44px");
  }
  const typeFilters = streams
    .getByRole("region", { name: "Message type filters" })
    .getByRole("button");
  await expect(typeFilters).toHaveCount(2);
  for (const filter of await typeFilters.all()) {
    await expect(filter).toHaveCSS("min-height", "44px");
  }
  await expect(
    streams.getByRole("button", { name: "Copy message payload" }),
  ).toHaveCSS("min-height", "44px");
  await expect(page.getByText("agent.started", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "Reconnect stream" }).click();
  await expect(page.getByText("Showing last known stream data")).toBeVisible();
  await expect(page.getByText(/stream fixture unavailable/i)).toBeVisible();
  await expect(page.getByText("agent.started", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();

  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("Showing last known stream data")).toHaveCount(0);
  await expect(page.getByText("agent.completed", { exact: true }).first()).toBeVisible();
});

test("stream monitor access denial on first load hides stream controls", async ({ page }) => {
  await page.route("**/api/streams/**", async (route) => {
    const url = new URL(route.request().url());
    await route.fulfill({
      status: 403,
      contentType: url.searchParams.get("history") === "1"
        ? "application/json"
        : "text/event-stream",
      body: JSON.stringify({ detail: "stream access denied" }),
    });
  });

  await authenticate(page, "/streams");
  const streams = page.getByRole("main", { name: "Agent stream monitor" });
  await expect(
    streams.getByRole("region", { name: "Stream access status" }),
  ).toBeVisible();
  await expect(streams.getByText("Stream access denied", { exact: true })).toBeVisible();
  await expect(
    streams.getByText("No live stream data is available while authorization is unavailable."),
  ).toBeVisible();
  await expect(streams.getByRole("searchbox", { name: "Filter messages by text, sender, or project" })).toHaveCount(0);
  await expect(streams.getByRole("combobox", { name: "Select team stream" })).toHaveCount(0);
  await expect(streams.getByRole("button", { name: "Reconnect stream" })).toHaveCount(0);
  await expect(streams.getByRole("button", { name: "Retry" })).toHaveCount(0);
  await expect(streams.getByRole("button", { name: "Copy message payload" })).toHaveCount(0);
  await expect(
    streams.getByRole("region", { name: "Last known agent message feed" }),
  ).toBeVisible();
});

test("stream monitor access denial after a successful read retains messages without controls", async ({ page }) => {
  await page.addInitScript(() => {
    let attempt = 0;

    class FakeEventSource extends EventTarget {
      static readonly CONNECTING = 0;
      static readonly OPEN = 1;
      static readonly CLOSED = 2;
      readonly url: string;
      readyState = FakeEventSource.OPEN;

      constructor(url: string) {
        super();
        this.url = url;
        const currentAttempt = ++attempt;
        window.setTimeout(() => {
          if (currentAttempt === 2) {
            this.dispatchEvent(
              new MessageEvent("error", {
                data: JSON.stringify({ error: "stream authorization expired", status: 401 }),
              }),
            );
            return;
          }
          this.dispatchEvent(
            new MessageEvent("connected", { data: JSON.stringify({ team: "exec_ceo" }) }),
          );
        }, 0);
      }

      close() {
        this.readyState = FakeEventSource.CLOSED;
      }
    }

    Object.defineProperty(window, "EventSource", {
      configurable: true,
      writable: true,
      value: FakeEventSource,
    });
  });

  await page.route("**/api/streams/**", async (route) => {
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

  await authenticate(page, "/streams");
  const streams = page.getByRole("main", { name: "Agent stream monitor" });
  await expect(page.getByText("agent.started", { exact: true }).first()).toBeVisible();
  await streams.getByRole("button", { name: "Reconnect stream" }).click();

  await expect(
    streams.getByRole("region", { name: "Stream access status" }),
  ).toBeVisible();
  await expect(page.getByText("agent.started", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/Previously loaded messages remain visible/)).toBeVisible();
  await expect(streams.getByRole("searchbox", { name: "Filter messages by text, sender, or project" })).toHaveCount(0);
  await expect(streams.getByRole("combobox", { name: "Select team stream" })).toHaveCount(0);
  await expect(streams.getByRole("button", { name: "Reconnect stream" })).toHaveCount(0);
  await expect(streams.getByRole("button", { name: "Clear message history" })).toHaveCount(0);
  await expect(streams.getByRole("button", { name: "Copy message payload" })).toHaveCount(0);
});
