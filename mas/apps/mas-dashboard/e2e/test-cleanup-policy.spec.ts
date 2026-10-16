import { expect, test } from "@playwright/test";
import {
  isKnownE2EFlowName,
  isKnownE2EProjectName,
  isKnownE2EWorkerName,
} from "./test-cleanup";

test("E2E cleanup only targets explicitly marked project and flow fixtures", () => {
  expect(isKnownE2EProjectName("tmp")).toBe(false);
  expect(isKnownE2EProjectName("customer-project-123")).toBe(false);
  expect(isKnownE2EProjectName("proj-123")).toBe(false);
  expect(isKnownE2EProjectName("C-123 Workstream")).toBe(false);
  expect(isKnownE2EProjectName("Test Project 123")).toBe(false);
  expect(isKnownE2EProjectName("gamma-workspace-123")).toBe(false);
  expect(isKnownE2EProjectName("aiat_e2e_project_flow_ui_123")).toBe(true);
  expect(isKnownE2EFlowName("Simple Product Build Flow 123")).toBe(false);
  expect(isKnownE2EFlowName("aiat_e2e_flow_product_build_123")).toBe(true);
  expect(isKnownE2EWorkerName("test-worker-123")).toBe(false);
  expect(isKnownE2EWorkerName("aiat_e2e_worker_interactive_123")).toBe(true);
});
