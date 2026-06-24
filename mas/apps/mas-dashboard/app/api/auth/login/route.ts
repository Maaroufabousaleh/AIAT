import { NextResponse } from "next/server";
import { verifyPassword, signToken, COOKIE_NAME, MAX_AGE } from "@/lib/auth";

export async function POST(req: Request) {
  try {
    const body = await req.json();
    console.log("Login attempt:", { hasUsername: !!body.username, hasPassword: !!body.password });
    const { username, password } = body as { username: string; password: string };
    const requestUrl = new URL(req.url);
    const forwardedProto = req.headers.get("x-forwarded-proto");
    const secureCookie = forwardedProto === "https" || requestUrl.protocol === "https:";

    if (username !== process.env.DASHBOARD_USERNAME) {
      console.log("Login failed: invalid credentials");
      return NextResponse.json({ error: "Invalid credentials" }, { status: 401 });
    }

    const ok = await verifyPassword(password);
    if (!ok) {
      console.log("Login failed: invalid credentials");
      return NextResponse.json({ error: "Invalid credentials" }, { status: 401 });
    }

    const token = await signToken(username);
    const res = NextResponse.json({ ok: true });
    res.cookies.set(COOKIE_NAME, token, {
      httpOnly: true,
      sameSite: "strict",
      path: "/",
      maxAge: MAX_AGE,
      secure: secureCookie,
    });
    return res;
  } catch (e) {
    console.error("Login error:", e);
    return NextResponse.json({ error: "Bad request" }, { status: 400 });
  }
}
