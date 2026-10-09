import { expect, test, type Page } from "@playwright/test";

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

async function submitAmbiguousCeoMessage(page: Page, draft: string) {
  let requestId: string | undefined;
  await installCeoReceiptStream(page);
  await page.route((url) => url.pathname === "/api/ceo/messages", async (route) => {
    const body = route.request().postDataJSON() as { request_id?: unknown };
    requestId = typeof body.request_id === "string" ? body.request_id : undefined;
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ error: "The response was lost after publication." }),
    });
  });

  await authenticate(page, "/ceo/chat");
  const chat = page.getByRole("main", { name: "CEO Command Center chat" });
  const input = chat.getByRole("textbox", { name: "Message to CEO" });
  await input.fill(draft);
  await input.press("Enter");
  await expect(chat.getByText("Delivery unconfirmed", { exact: true })).toBeVisible();
  await expect(input).toHaveValue(draft);
  await expect.poll(() => requestId).toBeTruthy();
  if (!requestId) throw new Error("CEO message request ID was not captured");
  return { chat, input, requestId };
}

async function installCeoReceiptStream(page: Page) {
  await page.addInitScript(() => {
    class ReceiptEventSource extends EventTarget {
      static readonly CONNECTING = 0;
      static readonly OPEN = 1;
      static readonly CLOSED = 2;
      readonly url: string;
      readyState = ReceiptEventSource.OPEN;
      onerror: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent<string>) => void) | null = null;
      private readonly receiveTestMessage = (event: Event) => {
        const data = (event as CustomEvent<string>).detail;
        const message = new MessageEvent("message", { data });
        this.onmessage?.(message);
      };

      constructor(url: string) {
        super();
        this.url = url;
        window.addEventListener("ceo-test-message", this.receiveTestMessage);
        window.setTimeout(() => {
          this.dispatchEvent(new MessageEvent("connected", {
            data: JSON.stringify({ team: "exec_ceo" }),
          }));
        }, 0);
      }

      close() {
        this.readyState = ReceiptEventSource.CLOSED;
        window.removeEventListener("ceo-test-message", this.receiveTestMessage);
      }
    }

    Object.defineProperty(window, "EventSource", {
      configurable: true,
      writable: true,
      value: ReceiptEventSource,
    });
  });
  await page.route("**/api/streams/exec_ceo**", async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("history") === "1") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ entries: [] }),
      });
      return;
    }
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: "" });
  });
}

async function emitLateCeoReceipt(page: Page, correlationId: string) {
  await page.evaluate((id) => {
    window.dispatchEvent(new CustomEvent("ceo-test-message", {
      detail: JSON.stringify({
        message_id: "ceo-late-receipt-001",
        correlation_id: id,
        message_type: "RESPONSE",
        sender_id: "ceo",
        timestamp: "2026-10-09T20:00:00Z",
        payload: { response: "The CEO accepted the request." },
      }),
    }));
  }, correlationId);
}

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

test("CEO chat restores a pending confirmation from history and clears it after the receipt", async ({
  page,
}) => {
  const confirmationToken = "7c294909-2dd8-41d6-96c4-cab319e58a73";
  const submission: { current: Record<string, unknown> | null } = { current: null };

  await page.addInitScript(() => {
    const sources: Array<{ dispatch(type: string, data: string): void }> = [];

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
        sources.push(this);
        window.setTimeout(() => {
          this.dispatch("connected", JSON.stringify({ team: "exec_ceo" }));
        }, 0);
      }

      close() {
        this.readyState = FakeEventSource.CLOSED;
      }

      dispatch(type: string, data: string) {
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
    (window as typeof window & {
      __emitCeoEvent?: (type: string, data: string) => void;
    }).__emitCeoEvent = (type, data) => {
      sources.forEach((source) => source.dispatch(type, data));
    };
  });

  await page.route("**/api/streams/exec_ceo**", async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("history") === "1") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          entries: [{
            entry_id: "ceo-pending-confirmation-001",
            envelope: JSON.stringify({
              message_id: "ceo-pending-confirmation-001",
              correlation_id: "ceo-pending-confirmation-001",
              message_type: "RESPONSE",
              sender_id: "ceo",
              timestamp: "2026-10-09T12:00:00Z",
              payload: {
                response: "Archive the completed project?",
                action: {
                  type: "project_archive",
                  status: "needs_confirmation",
                  requires_confirmation: true,
                },
                context: { confirmation_token: confirmationToken },
              },
            }),
          }],
        }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: "",
    });
  });

  await page.route((url) => url.pathname === "/api/ceo/messages", async (route) => {
    submission.current = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, status: "accepted" }),
    });
  });

  await authenticate(page, "/ceo/chat");
  const chat = page.getByRole("main", { name: "CEO Command Center chat" });
  const confirm = chat.getByRole("button", { name: "Confirm", exact: true });
  await expect(confirm).toBeVisible();
  await confirm.click();

  await expect.poll(() => submission.current).toMatchObject({
    message: "confirm",
    context_confirmation_token: confirmationToken,
  });
  const requestId = submission.current?.request_id;
  expect(requestId).toEqual(expect.any(String));

  await page.evaluate(({ requestId }) => {
    (window as typeof window & {
      __emitCeoEvent?: (type: string, data: string) => void;
    }).__emitCeoEvent?.("message", JSON.stringify({
      message_id: "ceo-confirmation-receipt-001",
      correlation_id: requestId,
      message_type: "RESPONSE",
      sender_id: "ceo",
      timestamp: "2026-10-09T12:00:01Z",
      payload: {
        response: "The project was archived.",
        action: { type: "project_archive", status: "completed", requires_confirmation: false },
        context: { confirmation_token: null },
      },
    }));
  }, { requestId });

  await expect(chat.getByText("The project was archived.", { exact: true })).toBeVisible();
  await expect(confirm).toHaveCount(0);
  await expect(chat.getByRole("button", { name: "Cancel", exact: true })).toHaveCount(0);
});

test("CEO chat keeps a draft when delivery cannot be confirmed", async ({ page }) => {
  let submissionCount = 0;
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
        body: JSON.stringify({ entries: [] }),
      });
      return;
    }
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: "" });
  });
  await page.route((url) => url.pathname === "/api/ceo/messages", async (route) => {
    submissionCount += 1;
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ error: "The response was lost after publication." }),
    });
  });

  await authenticate(page, "/ceo/chat");
  const chat = page.getByRole("main", { name: "CEO Command Center chat" });
  const input = chat.getByRole("textbox", { name: "Message to CEO" });
  const draft = "Summarize the local project state";
  await input.fill(draft);
  await input.press("Enter");

  await expect(chat.getByText("Delivery unconfirmed", { exact: true })).toBeVisible();
  await expect(input).toHaveValue(draft);
  await expect(chat.getByRole("alert")).toContainText("Check the conversation before sending again");
  await expect.poll(() => submissionCount, { timeout: 5_000 }).toBe(1);
});

test("CEO chat keeps delivery uncertainty specific to each request", async ({ page }) => {
  let submissionCount = 0;
  await page.route("**/api/streams/exec_ceo**", async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("history") === "1") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ entries: [] }),
      });
      return;
    }
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: "" });
  });
  await page.route((url) => url.pathname === "/api/ceo/messages", async (route) => {
    submissionCount += 1;
    await route.fulfill({
      status: submissionCount === 1 ? 503 : 403,
      contentType: "application/json",
      body: JSON.stringify({
        error: submissionCount === 1 ? "The response was lost after publication." : "CEO message access denied.",
      }),
    });
  });

  await authenticate(page, "/ceo/chat");
  const chat = page.getByRole("main", { name: "CEO Command Center chat" });
  const input = chat.getByRole("textbox", { name: "Message to CEO" });
  await input.fill("Check the project status");
  await input.press("Enter");
  await expect(chat.getByText("Delivery unconfirmed", { exact: true })).toBeVisible();

  await input.fill("A second request that is denied");
  await input.press("Enter");
  await expect(chat.getByRole("region", { name: "CEO chat access status" })).toBeVisible();
  await expect(chat.getByText("Delivery unconfirmed", { exact: true })).toHaveCount(1);
  await expect(chat.getByText("Not delivered", { exact: true })).toHaveCount(1);
  await expect.poll(() => submissionCount, { timeout: 5_000 }).toBe(2);
});

test("a late receipt does not clear a later access-denied error", async ({ page }) => {
  const { chat, input, requestId } = await submitAmbiguousCeoMessage(
    page,
    "Check the project status",
  );
  await page.route((url) => url.pathname === "/api/ceo/messages", async (route) => {
    await route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ error: "CEO message access denied." }),
    });
  });

  await input.fill("A follow-up request with denied access");
  await input.press("Enter");
  await expect(chat.getByRole("region", { name: "CEO chat access status" })).toBeVisible();
  await expect(chat.getByRole("region", { name: "CEO chat access status" })).toContainText("CEO message access denied.");

  await emitLateCeoReceipt(page, requestId);

  await expect(chat.getByText("Delivery unconfirmed", { exact: true })).toHaveCount(0);
  await expect(chat.getByRole("region", { name: "CEO chat access status" })).toContainText("CEO message access denied.");
});

test("CEO chat honors a correlated receipt observed before an access-denied response", async ({ page }) => {
  await installCeoReceiptStream(page);
  let requestId: string | undefined;
  await page.route((url) => url.pathname === "/api/ceo/messages", async (route) => {
    const body = route.request().postDataJSON() as { request_id?: unknown };
    requestId = typeof body.request_id === "string" ? body.request_id : undefined;
    if (!requestId) throw new Error("CEO message request ID was not captured");
    await emitLateCeoReceipt(page, requestId);
    await page.getByText("The CEO accepted the request.", { exact: true }).waitFor({ state: "visible" });
    await route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ error: "CEO message access denied." }),
    });
  });

  await authenticate(page, "/ceo/chat");
  const chat = page.getByRole("main", { name: "CEO Command Center chat" });
  const input = chat.getByRole("textbox", { name: "Message to CEO" });
  await expect(chat.getByText("Real-time control stream connected")).toBeVisible();
  await input.fill("Check the project status");
  await input.press("Enter");

  await expect(chat.getByText("The CEO accepted the request.", { exact: true })).toBeVisible();
  await expect(chat.getByRole("region", { name: "CEO chat access status" })).toContainText("CEO message access denied.");
  await expect(chat.getByText("Not delivered", { exact: true })).toHaveCount(0);
});

test("CEO chat clears the restored draft after a late receipt", async ({ page }) => {
  const draft = "Summarize the local project state";
  const { chat, input, requestId } = await submitAmbiguousCeoMessage(page, draft);
  await emitLateCeoReceipt(page, requestId);

  await expect(chat.getByText("The CEO accepted the request.", { exact: true })).toBeVisible();
  await expect(chat.getByText("Delivery unconfirmed", { exact: true })).toHaveCount(0);
  await expect(input).toHaveValue("");
  await expect(chat.getByRole("alert")).toHaveCount(0);
});

test("CEO chat preserves a newer draft after a late receipt", async ({ page }) => {
  const { chat, input, requestId } = await submitAmbiguousCeoMessage(
    page,
    "Summarize the local project state",
  );
  const newerDraft = "Also check whether any approvals are waiting";
  await input.fill(newerDraft);
  await emitLateCeoReceipt(page, requestId);

  await expect(chat.getByText("The CEO accepted the request.", { exact: true })).toBeVisible();
  await expect(chat.getByText("Delivery unconfirmed", { exact: true })).toHaveCount(0);
  await expect(input).toHaveValue(newerDraft);
  await expect(chat.getByRole("alert")).toHaveCount(0);
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
