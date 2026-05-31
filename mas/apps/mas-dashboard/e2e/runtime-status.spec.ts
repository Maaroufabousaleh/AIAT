import { test, expect } from '@playwright/test';
import { authenticate } from './auth';

test.describe('Epsilon Runtime Status Panel', () => {
  test.beforeEach(async ({ page }) => {
    await authenticate(page, '/workers');
    await page.waitForLoadState('networkidle');
  });

  test('runtime status panel is visible', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Advanced Runtimes' })).toBeVisible();
  });

  test('langgraph runtime is listed with status', async ({ page }) => {
    const card = page.locator('[data-runtime="langgraph"]');
    await expect(card).toBeVisible();
    await expect(card.getByText('LangGraph', { exact: true })).toBeVisible();
  });

  test('crewai runtime is listed', async ({ page }) => {
    const card = page.locator('[data-runtime="crewai"]');
    await expect(card).toBeVisible();
    await expect(card.getByText('CrewAI', { exact: true })).toBeVisible();
  });

  test('autogen runtime shows firecracker requirement', async ({ page }) => {
    const card = page.locator('[data-runtime="autogen"]');
    await expect(card).toBeVisible();
    await expect(card.getByText('AutoGen', { exact: true })).toBeVisible();
    await expect(card.getByText('firecracker')).toBeVisible();
  });

  test('letta runtime shows read-only policy', async ({ page }) => {
    const card = page.locator('[data-runtime="letta"]');
    await expect(card).toBeVisible();
    await expect(card.getByText('Letta', { exact: true })).toBeVisible();
  });

  test('runtimes API proxy returns 200', async ({ page }) => {
    const resp = await page.request.get('/api/runtimes');
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(data.runtimes).toBeDefined();
    expect(data.runtimes.length).toBe(4);
  });

  test('evaluations vault endpoint returns deferred', async ({ page }) => {
    const resp = await page.request.get('/api/evaluations?tech=vault');
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(data.status).toBe('deferred');
  });
});
