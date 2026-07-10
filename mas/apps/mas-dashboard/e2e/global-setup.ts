import { cleanupE2EArtifacts } from "./test-cleanup";

export default async function globalSetup() {
  await cleanupE2EArtifacts();
}
