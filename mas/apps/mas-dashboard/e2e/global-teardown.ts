import { cleanupE2EArtifacts } from "./test-cleanup";

export default async function globalTeardown() {
  await cleanupE2EArtifacts();
}
