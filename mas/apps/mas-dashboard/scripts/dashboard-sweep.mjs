#!/usr/bin/env node
// Dashboard sweep probe: visits every dashboard route as an operator,
// records console errors, failed requests, timings, and screenshots.
// Usage: JWT_SECRET=... PLAYWRIGHT_BASE_URL=http://127.0.0.1:14000 node scripts/dashboard-sweep.mjs [--routes=/,/projects]
import { chromium } from "playwright";
import { SignJWT } from "jose";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:14000";
const JWT_SECRET = process.env.JWT_SECRET ?? "dev-secret-change-in-production";

const ALL_ROUTES = [
  "/", "/ceo", "/ceo/chat", "/projects", "/companies", "/approvals",
  "/workers", "/credentials", "/flows", "/flows/new", "/system-viz",
  "/streams", "/analytics/litellm", "/analytics/omniroute", "/metrics",
  "/dlq", "/logs", "/identities", "/mail-domains", "/mailboxes",
  "/outbound-mail", "/mail-relay", "/external-accounts", "/auth-sessions",
  "/identity-approvals", "/identity-audit", "/system", "/tools",
  "/governance", "/models", "/models/profiles", "/integrations",
];

const routesArg = process.argv.find((a) => a.startsWith("--routes="));
const ROUTES = routesArg ? routesArg.slice(9).split(",").filter(Boolean) : ALL_ROUTES;

const outDir = path.join("sweep-results", "sweep");
fs.mkdirSync(outDir, { recursive: true });

const secret = new TextEncoder().encode(JWT_SECRET);
const token = await new SignJWT({ sub: "sweep", role: "operator" })
  .setProtectedHeader({ alg: "HS256" })
  .setIssuedAt()
  .setExpirationTime("1h")
  .sign(secret);

const browser = await chromium.launch();
const context = await browser.newContext({ baseURL: BASE });
await context.addCookies([{
  name: "mas_session", value: token, url: new URL(BASE).origin,
  httpOnly: false, sameSite: "Lax",
}]);

const report = [];
for (const route of ROUTES) {
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  const consoleErrors = [];
  const failedRequests = [];
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text().slice(0, 300)); });
  page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${String(e).slice(0, 300)}`));
  page.on("requestfailed", (r) => { if (r.failure()?.errorText !== "net::ERR_ABORTED") failedRequests.push(`${r.method()} ${r.url().replace(BASE, "")} :: ${r.failure()?.errorText}`); });
  page.on("response", (r) => { if (r.status() >= 500) failedRequests.push(`${r.request().method()} ${r.url().replace(BASE, "")} :: HTTP ${r.status()}`); });
  const started = Date.now();
  let status = "ok", heading = "", detail = "";
  try {
    await page.goto(route, { waitUntil: "commit", timeout: 20000 });
    await page.locator("#dashboard-main").waitFor({ timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(2000);
    heading = (await page.locator("#dashboard-main h1, #dashboard-main h2").first().textContent().catch(() => ""))?.trim().slice(0, 120) ?? "";
    if (!heading) {
      const body = (await page.locator("#dashboard-main").textContent().catch(() => ""))?.trim() ?? "";
      if (!body) { status = "blank"; detail = "empty #dashboard-main"; }
      else heading = body.slice(0, 100);
    }
    if (status === "ok" && (consoleErrors.length > 0 || failedRequests.length > 0)) status = "errors";
  } catch (e) {
    status = "failed";
    detail = String(e).split("\n")[0].slice(0, 300);
  }
  const ms = Date.now() - started;
  const slug = route === "/" ? "overview" : route.replaceAll("/", "_").replace(/^_/, "");
  await page.screenshot({ path: path.join(outDir, `${slug}.png`), fullPage: false, timeout: 10000 }).catch(() => {});
  report.push({ route, status, ms, heading, detail, consoleErrors: consoleErrors.slice(0, 5), failedRequests: failedRequests.slice(0, 8) });
  console.log(`${status.toUpperCase().padEnd(7)} ${String(ms).padStart(5)}ms ${route} :: ${heading || detail}`.slice(0, 200));
  await page.close().catch(() => {});
}
await browser.close();
fs.writeFileSync(path.join(outDir, "report.json"), JSON.stringify({ base: BASE, at: new Date().toISOString(), report }, null, 2));
const bad = report.filter((r) => r.status !== "ok");
console.log(`\n${report.length - bad.length}/${report.length} clean. Report: ${outDir}/report.json`);
process.exit(bad.length ? 1 : 0);
