import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const ENV_CANDIDATES = [
  resolve(process.cwd(), ".env"),
  resolve(process.cwd(), "../../../.env"),
  resolve(process.cwd(), "../../.env"),
];

function readLocalEnv(name: string): string | undefined {
  for (const path of ENV_CANDIDATES) {
    try {
      const line = readFileSync(path, "utf8")
        .split(/\r?\n/)
        .find((candidate) => candidate.trim().startsWith(`${name}=`));
      if (!line) continue;
      const value = line.slice(line.indexOf("=") + 1).trim();
      return value.replace(/^(["'])(.*)\1$/, "$2");
    } catch {
      // The dashboard can run in CI without a local .env file.
    }
  }
  return undefined;
}

export function runtimeEnv(name: string, fallback = ""): string {
  return process.env[name] ?? readLocalEnv(name) ?? fallback;
}
