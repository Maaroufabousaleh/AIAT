import { chromium } from './node_modules/playwright/index.mjs';
import { SignJWT } from 'jose';
import { mkdirSync, writeFileSync } from 'fs';

const VIDEO_DIR = '/tmp/artifacts/videos';
const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || 'http://host.docker.internal:4000';
const JWT_SECRET = process.env.JWT_SECRET || 'bX0wVUKd4M214L8laNitaXJWdBgoCavZ9o0Xr/MhLnw=';

mkdirSync(VIDEO_DIR, { recursive: true });

const SECRET = new TextEncoder().encode(JWT_SECRET);

async function authenticate(context, baseUrl) {
  const token = await new SignJWT({ sub: 'e2e', role: 'operator' })
    .setProtectedHeader({ alg: 'HS256' })
    .setIssuedAt()
    .setExpirationTime('1h')
    .sign(SECRET);

  await context.addCookies([{
    name: 'mas_session',
    value: token,
    url: baseUrl,
    httpOnly: false,
    sameSite: 'Lax',
    secure: baseUrl.startsWith('https'),
  }]);
}

// Test scenarios to run
const testScenarios = [
  {
    name: 'spec-01-runtime-status',
    tests: [
      { name: 'runtime-panel-visible', run: async (page) => {
        await page.waitForLoadState('networkidle');
        await page.waitForTimeout(1000);
      }},
    ]
  },
  {
    name: 'spec-02-hiring-board', 
    tests: [
      { name: 'register-worker', run: async (page) => {
        await page.waitForLoadState('networkidle');
        // Click register worker button if visible
        const btn = page.getByRole('button', { name: /register worker/i });
        if (await btn.isVisible()) {
          await btn.click();
          await page.waitForTimeout(1000);
        }
      }},
    ]
  },
  {
    name: 'spec-03-projects',
    tests: [
      { name: 'create-project', run: async (page) => {
        await page.waitForLoadState('networkidle');
        const newProjectBtn = page.getByRole('button', { name: /new project/i });
        if (await newProjectBtn.isVisible()) {
          await newProjectBtn.click();
          await page.waitForTimeout(1500);
        }
      }},
    ]
  },
  {
    name: 'spec-04-flows',
    tests: [
      { name: 'flows-list', run: async (page) => {
        await page.waitForLoadState('networkidle');
        await page.waitForTimeout(1000);
      }},
    ]
  },
  {
    name: 'spec-05-flow-builder',
    tests: [
      { name: 'create-flow', run: async (page) => {
        await page.waitForLoadState('networkidle');
        // Try to add nodes
        const addNodeBtn = page.getByTestId('add-node-task');
        if (await addNodeBtn.isVisible()) {
          await addNodeBtn.click();
          await page.waitForTimeout(500);
        }
      }},
    ]
  },
  {
    name: 'spec-06-credentials',
    tests: [
      { name: 'credentials-manager', run: async (page) => {
        await page.waitForLoadState('networkidle');
        const newSecretBtn = page.getByRole('button', { name: /new secret/i });
        if (await newSecretBtn.isVisible()) {
          await newSecretBtn.click();
          await page.waitForTimeout(1000);
        }
      }},
    ]
  },
  {
    name: 'spec-07-dlq',
    tests: [
      { name: 'dead-letter-queue', run: async (page) => {
        await page.waitForLoadState('networkidle');
        await page.waitForTimeout(1000);
      }},
    ]
  },
  {
    name: 'spec-08-metrics',
    tests: [
      { name: 'metrics-dashboard', run: async (page) => {
        await page.waitForLoadState('networkidle');
        await page.waitForTimeout(1000);
      }},
    ]
  },
  {
    name: 'spec-09-system',
    tests: [
      { name: 'system-control', run: async (page) => {
        await page.waitForLoadState('networkidle');
        await page.waitForTimeout(1000);
      }},
    ]
  },
  {
    name: 'spec-10-logs',
    tests: [
      { name: 'container-logs', run: async (page) => {
        await page.waitForLoadState('networkidle');
        const loadBtn = page.getByRole('button', { name: /^load$/i });
        if (await loadBtn.isVisible()) {
          await loadBtn.click();
          await page.waitForTimeout(1500);
        }
      }},
    ]
  },
  {
    name: 'spec-11-system-viz',
    tests: [
      { name: 'system-visualization', run: async (page) => {
        await page.waitForLoadState('networkidle');
        await page.waitForTimeout(1000);
      }},
    ]
  },
  {
    name: 'spec-12-tools',
    tests: [
      { name: 'tools-page', run: async (page) => {
        await page.waitForLoadState('networkidle');
        await page.waitForTimeout(1000);
      }},
    ]
  },
  {
    name: 'spec-13-streams',
    tests: [
      { name: 'streams-page', run: async (page) => {
        await page.waitForLoadState('networkidle');
        await page.waitForTimeout(1000);
      }},
    ]
  },
];

console.log('Starting E2E test scenarios with video recording...');
console.log(`BASE_URL: ${BASE_URL}`);
console.log(`VIDEO_DIR: ${VIDEO_DIR}`);

const browser = await chromium.launch({ 
  headless: true,
  args: ['--no-sandbox', '--disable-setuid-sandbox']
});

const results = [];

for (const scenario of testScenarios) {
  console.log(`\n=== Running: ${scenario.name} ===`);
  
  const context = await browser.newContext({
    recordVideo: { dir: VIDEO_DIR, size: { width: 1920, height: 1080 } }
  });
  const page = await context.newPage();
  
  await authenticate(context, BASE_URL);
  
  // Navigate to appropriate page
  const pageMap = {
    'spec-01-runtime-status': '/workers',
    'spec-02-hiring-board': '/workers',
    'spec-03-projects': '/projects',
    'spec-04-flows': '/flows',
    'spec-05-flow-builder': '/flows/new',
    'spec-06-credentials': '/credentials',
    'spec-07-dlq': '/dlq',
    'spec-08-metrics': '/metrics',
    'spec-09-system': '/system',
    'spec-10-logs': '/logs',
    'spec-11-system-viz': '/system-viz',
    'spec-12-tools': '/tools',
    'spec-13-streams': '/streams',
  };
  
  const path = pageMap[scenario.name] || '/';
  
  try {
    console.log(`Navigating to ${path}...`);
    await page.goto(`${BASE_URL}${path}`, { timeout: 30000, waitUntil: 'networkidle' });
    
    // Run all tests in scenario
    for (const test of scenario.tests) {
      try {
        console.log(`  Running test: ${test.name}`);
        await test.run(page);
        console.log(`  ✓ ${test.name} passed`);
        results.push({ scenario: scenario.name, test: test.name, status: 'passed' });
      } catch (e) {
        console.log(`  ✗ ${test.name}: ${e.message}`);
        results.push({ scenario: scenario.name, test: test.name, status: 'failed', error: e.message });
      }
    }
    
  } catch (e) {
    console.log(`✗ Scenario ${scenario.name}: ${e.message}`);
    results.push({ scenario: scenario.name, status: 'failed', error: e.message });
  }
  
  await context.close();
}

await browser.close();

console.log('\n=== Test Results ===');
const passed = results.filter(r => r.status === 'passed').length;
console.log(`Passed: ${passed}/${results.length}`);
results.forEach(r => {
  console.log(`  ${r.status === 'passed' ? '✓' : '✗'} ${r.scenario}/${r.test || 'scenario'}`);
});

console.log('\n=== Videos saved to:', VIDEO_DIR, '===');
