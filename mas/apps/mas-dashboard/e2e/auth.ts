import { type Page } from "@playwright/test";
import { SignJWT } from "jose";

export async function authenticate(page: Page, targetPath = "/"): Promise<void> {
  const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:4000";
  const url = new URL(baseURL);
  const secret = new TextEncoder().encode(
    process.env.JWT_SECRET ?? "bX0wVUKd4M214L8laNitaXJWdBgoCavZ9o0Xr/MhLnw="
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
