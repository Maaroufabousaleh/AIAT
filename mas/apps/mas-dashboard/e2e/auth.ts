import { type Page } from "@playwright/test";
import { SignJWT } from "jose";
import { runtimeEnv } from "./runtime-env";

export async function authenticate(page: Page, targetPath = "/"): Promise<void> {
  const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:4000";
  const url = new URL(baseURL);
  const secret = new TextEncoder().encode(
    runtimeEnv("JWT_SECRET", "dev-secret-change-in-production"),
  );
  const token = await new SignJWT({ sub: "e2e", role: "operator" })
    .setProtectedHeader({ alg: "HS256" })
    .setIssuedAt()
    .setExpirationTime("1h")
    .sign(secret);

  await page.context().addCookies([
    {
      name: "mas_session",
      value: token,
      url: url.origin,
      httpOnly: false,
      sameSite: "Lax",
      secure: url.protocol === "https:",
    },
  ]);

  await page.goto(targetPath);
}
