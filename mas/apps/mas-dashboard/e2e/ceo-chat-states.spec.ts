import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const HISTORY_FIXTURE = {
  entries: [
    {
      entry_id: "ceo-chat-states-001",
      envelope: JSON.stringify({
        message_type: "RESPONSE",
        sender_id: "ceo",
        timestamp: "2026-08-01T00:00:00Z",
        payload: { response: "retained CEO response" },
      }),
    },
  ],
};

test("CEO chat retains history through a stream failure and recovers on retry", async ({
  page,
}) => {
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
          this.emit("connected", JSON.stringify({ team: "exec_ceo" }));
          if (currentAttempt === 1) {
            window.setTimeout(() => {
              this.emit("error", JSON.stringify({ error: "CEO chat fixture unavailable" }));
            }, 20);
            return;
          }
          if (currentAttempt >= 2) {
            this.emit(
              "message",
              JSON.stringify({
                message_type: "RESPONSE",
                sender_id: "ceo",
                timestamp: "2026-08-01T00:00:01Z",
                payload: { response: "recovered CEO response" },
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

  await authenticate(page, "/ceo/chat");
  await expect(page.getByRole("heading", { name: "CEO Command Center" })).toBeVisible();
  const chat = page.getByRole("main", { name: "CEO Command Center chat" });
  await expect(chat).toBeVisible();
  await expect(
    chat.getByRole("region", { name: "CEO conversation workspace" }),
  ).toBeVisible();
  await expect(
    chat.getByRole("log", { name: "CEO conversation transcript" }),
  ).toBeVisible();
  await expect(
    chat.getByRole("region", { name: "CEO message composer" }),
  ).toBeVisible();
  for (const control of [
    chat.getByRole("link", { name: "Open CEO activity feed" }),
    chat.getByRole("button", { name: "Clear conversation view" }),
    chat.getByRole("textbox", { name: "Message to CEO" }),
    chat.getByRole("button", { name: "Send message" }),
  ]) {
    await expect(control).toHaveCSS("min-height", "44px");
  }
  const quickCommands = chat
    .getByRole("region", { name: "CEO chat quick commands" })
    .getByRole("button");
  await expect(quickCommands).toHaveCount(4);
  for (const command of await quickCommands.all()) {
    await expect(command).toHaveCSS("min-height", "44px");
  }
  await expect(page.getByText("retained CEO response", { exact: true })).toBeVisible();

  await expect(page.getByText("Showing last known CEO conversation")).toBeVisible();
  await expect(page.getByText(/CEO chat fixture unavailable/i)).toBeVisible();
  await expect(page.getByText("retained CEO response", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry CEO conversation" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry CEO conversation" })).toHaveCSS(
    "min-height",
    "44px",
  );

  await page.getByRole("button", { name: "Retry CEO conversation" }).click();
  await expect(page.getByText("Showing last known CEO conversation")).toHaveCount(0);
  await expect(page.getByText("recovered CEO response", { exact: true })).toBeVisible();
});

test("CEO chat renders a read-only state when conversation history access is denied", async ({
  page,
}) => {
  await page.route("**/api/streams/exec_ceo**", async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("history") === "1") {
      await route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({ error: "CEO history fixture denied" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: "",
    });
  });

  await authenticate(page, "/ceo/chat");
  const chat = page.getByRole("main", { name: "CEO Command Center chat" });
  await expect(
    chat.getByRole("region", { name: "CEO chat access status" }),
  ).toBeVisible();
  await expect(
    chat.getByRole("heading", { name: "CEO chat access denied" }),
  ).toBeVisible();
  await expect(chat.getByText("CEO conversation is read-only", { exact: true })).toBeVisible();
  await expect(chat.getByRole("link", { name: "Open CEO activity feed" })).toBeVisible();
  await expect(chat.getByRole("button", { name: "Clear conversation view" })).toHaveCount(0);
  await expect(chat.getByRole("region", { name: "CEO message composer" })).toHaveCount(0);
  await expect(chat.getByRole("region", { name: "CEO chat quick commands" })).toHaveCount(0);
  await expect(chat.getByRole("textbox", { name: "Message to CEO" })).toHaveCount(0);
});

test("CEO chat becomes read-only when message submission access is denied", async ({
  page,
}) => {
  await page.addInitScript(() => {
    class StableEventSource extends EventTarget {
      static readonly CONNECTING = 0;
      static readonly OPEN = 1;
      static readonly CLOSED = 2;
      readonly url: string;
      readyState = StableEventSource.OPEN;
      onerror: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent<string>) => void) | null = null;

      constructor(url: string) {
        super();
        this.url = url;
        window.setTimeout(() => {
          const event = new MessageEvent("connected", {
            data: JSON.stringify({ team: "exec_ceo" }),
          });
          this.dispatchEvent(event);
        }, 0);
      }

      close() {
        this.readyState = StableEventSource.CLOSED;
      }
    }

    Object.defineProperty(window, "EventSource", {
      configurable: true,
      writable: true,
      value: StableEventSource,
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
  await page.route("**/api/ceo/messages", async (route) => {
    await route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ error: "CEO message fixture denied" }),
    });
  });

  await authenticate(page, "/ceo/chat");
  const chat = page.getByRole("main", { name: "CEO Command Center chat" });
  const input = chat.getByRole("textbox", { name: "Message to CEO" });
  await expect(input).toBeVisible();
  await input.fill("show company status while access is denied");
  await input.press("Enter");

  await expect(
    chat.getByRole("heading", { name: "CEO chat access denied" }),
  ).toBeVisible();
  await expect(chat.getByText("retained CEO response", { exact: true })).toBeVisible();
  await expect(chat.getByText("Not delivered", { exact: true })).toBeVisible();
  await expect(chat.getByRole("link", { name: "Open CEO activity feed" })).toBeVisible();
  await expect(chat.getByRole("button", { name: "Clear conversation view" })).toHaveCount(0);
  await expect(chat.getByRole("region", { name: "CEO message composer" })).toHaveCount(0);
  await expect(chat.getByRole("region", { name: "CEO chat quick commands" })).toHaveCount(0);
  await expect(chat.getByRole("textbox", { name: "Message to CEO" })).toHaveCount(0);
});
