import { chromium } from "playwright";
import { SignJWT } from "jose";
import { mkdir } from "fs/promises";
import { readFileSync } from "fs";

const BASE = "http://127.0.0.1:3500";
const OUT = "screenshots";
await mkdir(OUT, { recursive: true });

const envFile = readFileSync(".env.local", "utf8");
const secretMatch = envFile.match(/^JWT_SECRET=(.+)$/m);
const secret = new TextEncoder().encode(secretMatch ? secretMatch[1] : "fallback");

const token = await new SignJWT({ sub: "e2e", role: "operator" })
  .setProtectedHeader({ alg: "HS256" }).setIssuedAt().setExpirationTime("1h").sign(secret);

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
await ctx.addCookies([{ name: "mas_session", value: token, url: BASE, sameSite: "Lax", secure: false }]);
const page = await ctx.newPage();

const pages = [
  { path: "/", name: "01-overview" },
  { path: "/projects", name: "02-projects" },
  { path: "/flows", name: "03-flows" },
  { path: "/credentials", name: "04-credentials" },
  { path: "/workers", name: "05-workers" },
  { path: "/dlq", name: "06-dlq" },
  { path: "/tools", name: "07-tools" },
  { path: "/ceo", name: "08-ceo" },
  { path: "/streams", name: "09-streams" },
  { path: "/logs", name: "10-logs" },
  { path: "/system", name: "12-system" },
  { path: "/system-viz", name: "13-system-viz" },
];

for (const p of pages) {
  console.log("Visiting", p.path);
  try {
    await page.goto(`${BASE}${p.path}`, { waitUntil: "domcontentloaded", timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(2500);
    await page.screenshot({ path: `${OUT}/${p.name}.png`, fullPage: false });
  } catch (e) {
    console.error("  error:", e.message);
  }
}

await browser.close();
console.log("done");
