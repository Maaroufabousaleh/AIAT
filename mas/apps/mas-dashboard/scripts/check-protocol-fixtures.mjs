import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "../../..");
const schemaPath = resolve(repoRoot, "packages/mas-core/schemas/protocol/aiat.v1.schema.json");
const fixturesDir = resolve(repoRoot, "packages/mas-core/tests/fixtures/protocol");
const dashboardFixturePath = resolve(here, "../lib/protocol-fixtures.ts");

const schema = JSON.parse(readFileSync(schemaPath, "utf8"));
const dashboardFixtureSource = readFileSync(dashboardFixturePath, "utf8");

const fixtureMap = [
  ["MessageEnvelope", "message_envelope.json", "MessageEnvelopeSample"],
  ["ToolRequest", "tool_request.json", "ToolRequestSample"],
  ["ToolResponse", "tool_response.json", "ToolResponseSample"],
  ["WorkerManifest", "worker_manifest.json", "WorkerManifestSample"],
];

const failures = [];

for (const [schemaName, filename, tsTypeName] of fixtureMap) {
  const schemaDef = schema.schemas?.[schemaName];
  const fixture = JSON.parse(readFileSync(resolve(fixturesDir, filename), "utf8"));

  if (!schemaDef) {
    failures.push(`${schemaName}: missing schema definition`);
    continue;
  }

  const required = schemaDef.required ?? [];
  for (const key of required) {
    if (!(key in fixture)) failures.push(`${filename}: missing required key ${key}`);
  }

  const properties = schemaDef.properties ?? {};
  for (const key of Object.keys(fixture)) {
    if (!(key in properties)) failures.push(`${filename}: unknown root key ${key}`);
  }

  const protocolConst = properties.protocol_version?.const ?? schema.protocol_version;
  if (fixture.protocol_version !== protocolConst) {
    failures.push(`${filename}: protocol_version ${fixture.protocol_version} does not match ${protocolConst}`);
  }

  if (!dashboardFixtureSource.includes(`satisfies ${tsTypeName}`)) {
    failures.push(`protocol-fixtures.ts: missing compile-time satisfies check for ${tsTypeName}`);
  }
}

if (failures.length > 0) {
  console.error(failures.join("\n"));
  process.exit(1);
}

console.log(`Checked ${fixtureMap.length} protocol fixtures against ${schema.protocol_version}.`);
