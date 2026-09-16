import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import bcrypt from "bcryptjs";
import { SignJWT } from "jose";

const dashboardRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
// Keep the temporary module below node_modules so Node resolves the dashboard's
// installed dependencies normally while linters and Git ignore the build.
const buildRoot = mkdtempSync(
  join(dashboardRoot, "node_modules", ".tmp-auth-test-"),
);
const password = "correct horse battery staple";
const jwtSecret = "dashboard-auth-test-secret";

try {
  // Compile the real TypeScript module with the repository's TypeScript toolchain
  // so this harness also runs on the Node 20 CI baseline.
  writeFileSync(join(buildRoot, "package.json"), '{"type":"module"}\n');
  execFileSync(
    process.execPath,
    [
      resolve(dashboardRoot, "node_modules/typescript/bin/tsc"),
      "lib/auth.ts",
      "--outDir",
      buildRoot,
      "--target",
      "ES2022",
      "--module",
      "ES2022",
      "--moduleResolution",
      "bundler",
      "--esModuleInterop",
      "--skipLibCheck",
      "--noEmitOnError",
    ],
    { cwd: dashboardRoot, stdio: "inherit" },
  );

  process.env.JWT_SECRET = jwtSecret;
  delete process.env.DASHBOARD_PASSWORD_HASH;

  const auth = await import(
    `${pathToFileURL(join(buildRoot, "auth.js"))}?test=${Date.now()}`,
  );

  assert.equal(auth.COOKIE_NAME, "mas_session");
  assert.equal(auth.MAX_AGE, 60 * 60 * 8);
  assert.equal(
    await auth.verifyPassword(password),
    false,
    "missing password hash must fail closed",
  );

  process.env.DASHBOARD_PASSWORD_HASH = bcrypt.hashSync(password, 4);
  assert.equal(await auth.verifyPassword(password), true);
  assert.equal(await auth.verifyPassword("wrong password"), false);

  const token = await auth.signToken("operator");
  assert.equal(await auth.verifyToken(token), true);
  assert.equal(await auth.verifyToken(`${token}tampered`), false);
  assert.equal(await auth.verifyToken(""), false);

  const expiredToken = await new SignJWT({ sub: "operator" })
    .setProtectedHeader({ alg: "HS256" })
    .setIssuedAt(1)
    .setExpirationTime(1)
    .sign(new TextEncoder().encode(jwtSecret));
  assert.equal(await auth.verifyToken(expiredToken), false);

  console.log("dashboard auth harness: PASS");
} finally {
  delete process.env.DASHBOARD_PASSWORD_HASH;
  delete process.env.JWT_SECRET;
  rmSync(buildRoot, { recursive: true, force: true });
}
