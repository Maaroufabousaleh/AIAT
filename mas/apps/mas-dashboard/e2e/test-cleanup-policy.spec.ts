import { expect, test } from "@playwright/test";
import { isKnownE2EProjectName } from "./test-cleanup";

test("E2E cleanup does not treat a generic tmp project as disposable", () => {
  expect(isKnownE2EProjectName("tmp")).toBe(false);
  expect(isKnownE2EProjectName("customer-project-123")).toBe(false);
  expect(isKnownE2EProjectName("flow-ui-project-123")).toBe(true);
});
