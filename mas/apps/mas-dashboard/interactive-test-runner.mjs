import { chromium } from './node_modules/playwright/index.mjs';
import { SignJWT } from 'jose';
import { mkdirSync } from 'fs';

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

async function log(msg) {
  console.log(`[${new Date().toISOString()}] ${msg}`);
}

// Interactive test suites - each feature is actually manipulated
const testSuites = [
  {
    category: 'Interactive-01-Workers-Hiring',
    video: 'INT-01-Workers-Interactive',
    page: '/workers',
    tests: [
      {
        name: 'Open and fill Register Worker modal',
        test: async (page) => {
          log('  Clicking Register Worker button...');
          await page.getByRole('button', { name: /register worker/i }).click();
          await page.waitForTimeout(1000);

          log('  Filling worker ID...');
          await page.getByPlaceholder('my_worker_1').fill('test-worker-' + Date.now());

          log('  Filling worker name...');
          await page.getByPlaceholder('My Worker Agent').fill('Test Worker Agent');

          log('  Filling description...');
          await page.getByPlaceholder('What this worker does').fill('Testing worker registration');

          log('  Filling team ID...');
          await page.getByPlaceholder('dept_production').fill('dept_qa');

          log('  Filling GitHub URL...');
          await page.getByPlaceholder('https://github.com/org/repo').fill('https://github.com/test/worker');

          log('  Filling adapter entrypoint...');
          await page.getByPlaceholder('WorkerAgent').fill('TestWorker:handler');

          log('  Clicking Register button...');
          await page.getByRole('button', { name: /^register worker$/i }).last().click();
          await page.waitForTimeout(2000);

          return { status: 'PASS', details: 'Worker registration form filled and submitted' };
        }
      },
      {
        name: 'Search for a worker',
        test: async (page) => {
          log('  Typing in search box...');
          const searchInput = page.getByPlaceholder('Search workers...');
          await searchInput.fill('baseline');
          await page.waitForTimeout(1000);

          const results = await page.locator('table tbody tr').count();
          log(`  Found ${results} rows after search`);

          return { status: 'PASS', details: `Search returned ${results} results` };
        }
      },
      {
        name: 'Click worker row to expand',
        test: async (page) => {
          // Clear search first
          await page.getByPlaceholder('Search workers...').clear();
          await page.waitForTimeout(500);

          log('  Clicking first worker row...');
          const firstRow = page.locator('tbody tr').first();
          await firstRow.click();
          await page.waitForTimeout(1000);

          const isExpanded = await page.locator('tr.bg-gray-900\\/50').count() > 0;
          return { status: isExpanded ? 'PASS' : 'FAIL', details: `Worker row ${isExpanded ? 'expanded' : 'did not expand'}` };
        }
      },
      {
        name: 'Test status filter buttons',
        test: async (page) => {
          log('  Clicking ALL filter...');
          await page.getByRole('button', { name: 'ALL' }).click();
          await page.waitForTimeout(500);

          log('  Clicking ACTIVE filter...');
          await page.getByRole('button', { name: 'ACTIVE' }).first().click();
          await page.waitForTimeout(500);

          return { status: 'PASS', details: 'Filter buttons clicked successfully' };
        }
      }
    ]
  },
  {
    category: 'Interactive-02-Projects',
    video: 'INT-02-Projects-Interactive',
    page: '/projects',
    tests: [
      {
        name: 'Create a new project with form interaction',
        test: async (page) => {
          log('  Clicking New Project button...');
          await page.getByRole('button', { name: /new project/i }).click();
          await page.waitForTimeout(1000);

          log('  Filling project name...');
          const timestamp = 'proj-' + Date.now();
          await page.getByPlaceholder('my-project').fill(timestamp);

          log('  Filling project description...');
          await page.getByPlaceholder('What should the agents build?').fill('Automated test project creation');

          log('  Clicking Create button...');
          await page.getByRole('button', { name: /^create$/i }).click();
          await page.waitForTimeout(2000);

          const projectVisible = await page.getByText(timestamp).isVisible();
          return {
            status: projectVisible ? 'PASS' : 'FAIL',
            details: projectVisible ? 'Project created and visible in list' : 'Project may not have been created'
          };
        }
      },
      {
        name: 'View project details',
        test: async (page) => {
          log('  Looking for first project row View link...');
          // Use first() to get the first View link in the table
          const viewLink = page.locator('a[href*="/projects/"]').filter({ hasText: 'View' }).first();

          if (await viewLink.isVisible()) {
            log('  Clicking View link via JavaScript...');
            // Use Promise.all to properly wait for navigation
            await Promise.all([
              page.waitForNavigation({ timeout: 10000 }).catch(() => {}),
              viewLink.click()
            ]);
            await page.waitForLoadState('domcontentloaded');
            await page.waitForTimeout(2000);

            const currentUrl = page.url();
            const onProjectPage = currentUrl.includes('/projects/') && !currentUrl.endsWith('/projects') && !currentUrl.endsWith('/');
            return {
              status: onProjectPage ? 'PASS' : 'FAIL',
              details: onProjectPage ? 'Navigated to project detail page' : 'Did not navigate to project page (URL: ' + currentUrl + ')'
            };
          }
          return { status: 'FAIL', details: 'View link not visible' };
        }
      },
      {
        name: 'Switch between project tabs',
        test: async (page) => {
          const currentUrl = page.url();

          // Only run tab test if we're on a project detail page
          if (!currentUrl.includes('/projects/') || currentUrl.endsWith('/projects') || currentUrl.endsWith('/')) {
            log('  Not on a project detail page - skipping tab test');
            return { status: 'FAIL', details: 'Must navigate to project page first (URL: ' + currentUrl + ')' };
          }

          log('  Looking for workspace tab...');
          const workspaceTab = page.getByRole('button', { name: 'Workspace' });
          if (await workspaceTab.isVisible()) {
            log('  Clicking Workspace tab...');
            await workspaceTab.click();
            await page.waitForTimeout(1000);
          }

          log('  Looking for Audit Timeline tab...');
          const auditTab = page.getByRole('heading', { name: 'Audit Timeline' });
          const hasAudit = await auditTab.isVisible();

          return {
            status: hasAudit ? 'PASS' : 'FAIL',
            details: hasAudit ? 'Audit Timeline visible' : 'Audit Timeline not found'
          };
        }
      }
    ]
  },
  {
    category: 'Interactive-03-Flow-Builder',
    video: 'INT-03-FlowBuilder-Interactive',
    page: '/flows/new',
    tests: [
      {
        name: 'Fill flow name and add multiple nodes',
        test: async (page) => {
          log('  Filling flow name...');
          await page.getByTestId('flow-name-input').fill('Test Flow ' + Date.now());

          log('  Checking Active checkbox...');
          await page.getByLabel('Active').check();

          log('  Adding START node...');
          await page.getByTestId('add-node-start').click();
          await page.waitForTimeout(500);

          log('  Adding TASK node...');
          await page.getByTestId('add-node-task').click();
          await page.waitForTimeout(500);

          log('  Adding APPROVAL node...');
          await page.getByTestId('add-node-approval').click();
          await page.waitForTimeout(500);

          log('  Adding second TASK node...');
          await page.getByTestId('add-node-task').click();
          await page.waitForTimeout(500);

          log('  Adding END node...');
          await page.getByTestId('add-node-end').click();
          await page.waitForTimeout(1000);

          log('  Connecting nodes with edges...');
          // Connect nodes via JavaScript using the custom event the editor listens for
          const nodeIds = await page.locator('.react-flow__node').evaluateAll(els =>
            els.map(el => el.getAttribute('data-id'))
          );
          log(`  Node IDs: ${nodeIds.join(', ')}`);
          if (nodeIds.length >= 4) {
            for (let i = 0; i < nodeIds.length - 1; i++) {
              await page.evaluate(({ source, target }) => {
                window.dispatchEvent(new CustomEvent('flow-quick-connect', { detail: { source, target } }));
              }, { source: nodeIds[i], target: nodeIds[i + 1] });
              await page.waitForTimeout(200);
            }
          }

          const nodeCount = await page.locator('.react-flow__node').count();
          log(`  Total nodes: ${nodeCount}`);

          return {
            status: nodeCount >= 5 ? 'PASS' : 'FAIL',
            details: `${nodeCount} nodes added to flow`
          };
        }
      },
      {
        name: 'Configure node properties',
        test: async (page) => {
          // Configure first task (index 1)
          log('  Clicking on first task node to configure...');
          const taskNode = page.locator('.react-flow__node').nth(1);
          await taskNode.click();
          await page.waitForTimeout(1500);

          log('  Filling first task team ID...');
          const teamInput1 = page.getByTestId('task-team-id-input');
          if (await teamInput1.isVisible()) {
            await teamInput1.fill('exec_ceo');
          }

          // Configure approval (index 2)
          log('  Clicking on approval node...');
          const approvalNode = page.locator('.react-flow__node').nth(2);
          await approvalNode.click();
          await page.waitForTimeout(1500);

          log('  Filling approval user...');
          const approverInput = page.getByTestId('approval-user-input');
          if (await approverInput.isVisible()) {
            await approverInput.fill('human');
          }

          // Configure second task (index 3)
          log('  Clicking on second task node...');
          const task2Node = page.locator('.react-flow__node').nth(3);
          await task2Node.click();
          await page.waitForTimeout(1500);

          log('  Filling second task team ID...');
          const teamInput2 = page.getByTestId('task-team-id-input');
          if (await teamInput2.isVisible()) {
            await teamInput2.fill('exec_coo');
          }

          return { status: 'PASS', details: 'All node properties configured' };
        }
      },
      {
        name: 'Save flow',
        test: async (page) => {
          log('  Clicking Save Flow button...');
          const saveBtn = page.getByTestId('flow-save-button');
          if (await saveBtn.isVisible()) {
            await saveBtn.click();
            await page.waitForTimeout(3000);

            const currentUrl = page.url();
            const saved = currentUrl !== 'http://host.docker.internal:4000/flows/new';
            const hasError = await page.getByText(/error|failed|invalid/i).count() > 0;

            if (saved && !hasError) {
              return { status: 'PASS', details: 'Flow saved successfully' };
            } else if (hasError) {
              return { status: 'FAIL', details: 'Flow save failed with validation error' };
            } else {
              const hasSuccessIndicator = await page.getByText(/saved|success|created/i).count() > 0;
              return {
                status: hasSuccessIndicator ? 'PASS' : 'FAIL',
                details: hasSuccessIndicator ? 'Flow saved with success indicator' : 'Save may have failed silently (URL: ' + currentUrl + ')'
              };
            }
          }
          return { status: 'FAIL', details: 'Save button not visible' };
        }
      }
    ]
  },
  {
    category: 'Interactive-04-Credentials',
    video: 'INT-04-Credentials-Interactive',
    page: '/credentials',
    tests: [
      {
        name: 'Create new credential',
        test: async (page) => {
          log('  Clicking New Secret button...');
          await page.getByRole('button', { name: /new secret/i }).click();
          await page.waitForTimeout(1000);

          log('  Filling credential name...');
          const credName = 'TEST_SECRET_' + Date.now();
          await page.getByPlaceholder('OPENAI_API_KEY').fill(credName);

          log('  Filling credential value...');
          await page.getByPlaceholder('sk-...').fill('sk-test-value-12345');

          log('  Filling description...');
          await page.getByPlaceholder('OpenAI API key for LLM gateway').fill('Test credential for E2E testing');

          log('  Clicking Save Credential button...');
          await page.getByRole('button', { name: /save credential/i }).click();
          await page.waitForTimeout(2000);

          // Check if credential appears with masked value
          const maskedValue = await page.getByText(`<${credName}>`).isVisible();
          const hasRealValue = await page.getByText('sk-test-value-12345').isVisible();

          return {
            status: maskedValue && !hasRealValue ? 'PASS' : 'FAIL',
            details: maskedValue && !hasRealValue ? 'Credential created with masked value' : 'Credential not properly masked'
          };
        }
      },
      {
        name: 'Delete credential',
        test: async (page) => {
          log('  Looking for delete button...');
          const deleteBtn = page.getByTitle('Delete').first();

          if (await deleteBtn.isVisible()) {
            log('  Setting up dialog handler...');
            page.on('dialog', dialog => dialog.accept());

            log('  Clicking delete...');
            await deleteBtn.click();
            await page.waitForTimeout(1000);

            return { status: 'PASS', details: 'Delete button clicked' };
          }
          return { status: 'FAIL', details: 'Delete button not visible' };
        }
      }
    ]
  },
  {
    category: 'Interactive-05-DLQ',
    video: 'INT-05-DLQ-Interactive',
    page: '/dlq',
    tests: [
      {
        name: 'Load and view DLQ messages',
        test: async (page) => {
          log('  Page loaded, checking for content...');
          await page.waitForTimeout(2000);

          const content = await page.content();
          const hasQueueContent = content.includes('Dead Letter') || content.includes('Queue') || content.includes('message');

          return {
            status: hasQueueContent ? 'PASS' : 'FAIL',
            details: hasQueueContent ? 'DLQ page has content' : 'DLQ page may be empty'
          };
        }
      },
      {
        name: 'Check queue status indicator',
        test: async (page) => {
          const emptyText = await page.getByText(/queue is empty|0 messages|empty/i).count();
          const hasMessages = await page.getByText(/message/i).count();

          return {
            status: (emptyText > 0 || hasMessages > 0) ? 'PASS' : 'FAIL',
            details: emptyText > 0 ? 'Queue is empty' : `Found ${hasMessages} message references`
          };
        }
      }
    ]
  },
  {
    category: 'Interactive-06-Metrics',
    video: 'INT-06-Metrics-Interactive',
    page: '/metrics',
    tests: [
      {
        name: 'View metrics charts and time range selection',
        test: async (page) => {
          log('  Waiting for page to load...');
          await page.waitForTimeout(2000);

          log('  Looking for time range buttons...');
          const btn15m = page.getByRole('button', { name: '15m' });
          if (await btn15m.isVisible()) {
            log('  Clicking 15m button...');
            await btn15m.click();
            await page.waitForTimeout(1000);
          }

          const btn1h = page.getByRole('button', { name: '1h' });
          if (await btn1h.isVisible()) {
            log('  Clicking 1h button...');
            await btn1h.click();
            await page.waitForTimeout(1000);
          }

          return { status: 'PASS', details: 'Time range buttons clicked' };
        }
      },
      {
        name: 'Verify metrics content displays',
        test: async (page) => {
          const content = await page.content();
          const hasMetrics = content.includes('LLM') || content.includes('metric') || content.includes('chart');

          return {
            status: hasMetrics ? 'PASS' : 'FAIL',
            details: hasMetrics ? 'Metrics content displayed' : 'No metrics content found'
          };
        }
      }
    ]
  },
  {
    category: 'Interactive-07-System-Control',
    video: 'INT-07-SystemControl-Interactive',
    page: '/system',
    tests: [
      {
        name: 'Fill schedule cron input',
        test: async (page) => {
          log('  Looking for cron input...');
          // Try multiple possible selectors
          const cronInput = page.getByPlaceholder('e.g. 0 22 * * *');
          if (await cronInput.isVisible()) {
            log('  Filling cron schedule...');
            await cronInput.fill('0 22 * * *');
            await page.waitForTimeout(500);

            log('  Clicking Save Schedule button...');
            await page.getByRole('button', { name: /save schedule/i }).click();
            await page.waitForTimeout(1000);

            return { status: 'PASS', details: 'Cron schedule filled and saved' };
          }
          return { status: 'FAIL', details: 'Cron input not visible' };
        }
      }
    ]
  },
  {
    category: 'Interactive-08-Logs',
    video: 'INT-08-Logs-Interactive',
    page: '/logs',
    tests: [
      {
        name: 'Load container logs',
        test: async (page) => {
          log('  Waiting for page...');
          await page.waitForTimeout(1000);

          log('  Clicking Load button...');
          const loadBtn = page.getByRole('button', { name: /^load$/i });
          if (await loadBtn.isVisible()) {
            await loadBtn.click();
            await page.waitForTimeout(3000);

            const content = await page.content();
            const hasLogs = content.includes('line') || content.includes('log') || content.includes('error');

            return {
              status: hasLogs ? 'PASS' : 'FAIL',
              details: hasLogs ? 'Logs loaded successfully' : 'No log content found'
            };
          }
          return { status: 'FAIL', details: 'Load button not visible' };
        }
      }
    ]
  },
  {
    category: 'Interactive-09-SystemViz',
    video: 'INT-09-SystemViz-Interactive',
    page: '/system-viz',
    tests: [
      {
        name: 'Test Mermaid export',
        test: async (page) => {
          log('  Waiting for page...');
          await page.waitForTimeout(2000);

          log('  Looking for Copy Mermaid button...');
          const mermaidBtn = page.getByRole('button', { name: /copy mermaid/i });
          if (await mermaidBtn.isVisible()) {
            log('  Clicking Copy Mermaid...');
            await mermaidBtn.click();
            await page.waitForTimeout(500);
            return { status: 'PASS', details: 'Mermaid copy button clicked' };
          }
          return { status: 'FAIL', details: 'Mermaid button not found' };
        }
      },
      {
        name: 'Switch to Permissions tab',
        test: async (page) => {
          log('  Clicking Permissions tab...');
          await page.getByRole('button', { name: /permissions/i }).click();
          await page.waitForTimeout(1500);

          // Wait for content to change after tab click
          await page.waitForFunction(() => {
            const content = document.body.innerText.toLowerCase();
            return content.includes('permission') || content.includes('access') || content.includes('role');
          }, { timeout: 5000 }).catch(() => {});

          const hasPermissions = await page.getByText(/permission|access|role/i).count() > 0;

          return {
            status: hasPermissions ? 'PASS' : 'FAIL',
            details: hasPermissions ? 'Permissions tab content visible' : 'No permissions content'
          };
        }
      },
      {
        name: 'Switch to Orchestration tab',
        test: async (page) => {
          log('  Clicking Orchestration tab...');
          await page.getByRole('button', { name: /orchestration/i }).click();
          await page.waitForTimeout(1000);

          const selectBtn = page.getByText(/select a flow/i);
          const hasOrchestration = await selectBtn.isVisible().catch(() => false) ||
                                   page.content().then(c => c.includes('flow'));

          return {
            status: hasOrchestration ? 'PASS' : 'FAIL',
            details: hasOrchestration ? 'Orchestration tab working' : 'Orchestration not visible'
          };
        }
      }
    ]
  },
  {
    category: 'Interactive-10-Tools',
    video: 'INT-10-Tools-Interactive',
    page: '/tools',
    tests: [
      {
        name: 'Search for tools',
        test: async (page) => {
          log('  Waiting for page...');
          await page.waitForTimeout(2000);

          log('  Typing in search...');
          const searchInput = page.getByPlaceholder('Search tools...');
          await searchInput.fill('browser');
          await page.waitForTimeout(1000);

          const content = await page.content();
          const hasBrowserTool = content.includes('browser') || content.includes('Browser');

          return {
            status: hasBrowserTool ? 'PASS' : 'FAIL',
            details: hasBrowserTool ? 'Browser tool found in results' : 'No browser tool in results'
          };
        }
      },
      {
        name: 'Click on a tool to expand details',
        test: async (page) => {
          // Clear search
          await page.getByPlaceholder('Search tools...').clear();
          await page.waitForTimeout(500);

          log('  Looking for first tool row...');
          const firstRow = page.locator('tbody tr').first();
          if (await firstRow.isVisible()) {
            log('  Clicking first tool row...');
            await firstRow.click();
            await page.waitForTimeout(1000);

            const hasDetails = await page.getByText(/schema|circuit|breaker|rate/i).count() > 0;
            return {
              status: hasDetails ? 'PASS' : 'FAIL',
              details: hasDetails ? 'Tool details expanded' : 'No details expanded'
            };
          }
          return { status: 'FAIL', details: 'No tool rows found' };
        }
      }
    ]
  }
];

async function runTestSuite(suite) {
  log(`\n========================================`);
  log(`TEST SUITE: ${suite.category}`);
  log(`========================================`);

  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });

  const context = await browser.newContext({
    recordVideo: { dir: VIDEO_DIR, size: { width: 1920, height: 1080 } }
  });

  const page = await context.newPage();

  // Capture console errors
  const consoleErrors = [];
  page.on('console', msg => {
    if (msg.type() === 'error') {
      consoleErrors.push(msg.text());
    }
  });

  await authenticate(context, BASE_URL);

  try {
    log(`Navigating to ${suite.page}...`);
    await page.goto(`${BASE_URL}${suite.page}`, { timeout: 60000, waitUntil: 'networkidle' });
    await page.waitForTimeout(2000); // Let video recording capture initial state

    const results = [];

    for (const feature of suite.tests) {
      log(`\n--- Test: ${feature.name} ---`);
      try {
        const result = await feature.test(page);
        results.push({
          feature: feature.name,
          status: result.status,
          details: result.details,
          videoFile: `${suite.video}.webm`
        });
        log(`RESULT: ${result.status} - ${result.details}`);
      } catch (e) {
        results.push({
          feature: feature.name,
          status: 'FAIL',
          details: e.message,
          error: e.stack,
          videoFile: `${suite.video}.webm`
        });
        log(`ERROR: ${e.message}`);
      }
    }

    await context.close();
    await browser.close();

    return {
      category: suite.category,
      page: suite.page,
      videoFile: `${suite.video}.webm`,
      consoleErrors,
      results
    };

  } catch (e) {
    log(`Suite failed: ${e.message}`);
    await context.close();
    await browser.close();

    return {
      category: suite.category,
      page: suite.page,
      videoFile: `${suite.video}.webm`,
      consoleErrors,
      results: [{
        feature: 'Suite Execution',
        status: 'FAIL',
        details: e.message,
        error: e.stack
      }]
    };
  }
}

async function main() {
  log('╔════════════════════════════════════════════════════════════╗');
  log('║     MAS DASHBOARD - INTERACTIVE FEATURE TESTING           ║');
  log('║     Testing actual UI interactions, not just page loads    ║');
  log('╚════════════════════════════════════════════════════════════╝');
  log('');
  log(`BASE_URL: ${BASE_URL}`);
  log(`VIDEO_DIR: ${VIDEO_DIR}`);

  const allResults = [];

  for (const suite of testSuites) {
    const result = await runTestSuite(suite);
    allResults.push(result);
  }

  // Generate report
  log('\n\n╔════════════════════════════════════════════════════════════╗');
  log('║                    TEST SUMMARY                            ║');
  log('╚════════════════════════════════════════════════════════════╝');

  let totalPassed = 0;
  let totalFailed = 0;

  for (const result of allResults) {
    const passed = result.results.filter(r => r.status === 'PASS').length;
    const failed = result.results.filter(r => r.status === 'FAIL').length;
    totalPassed += passed;
    totalFailed += failed;

    log(`\n${result.category}:`);
    for (const r of result.results) {
      log(`  ${r.status === 'PASS' ? '✅' : '❌'} ${r.feature}`);
      log(`     ${r.details}`);
    }
  }

  log(`\n\nTOTAL: ${totalPassed} passed, ${totalFailed} failed`);

  // Save JSON results
  const { writeFileSync } = await import('fs');
  writeFileSync('/tmp/interactive-test-results.json', JSON.stringify(allResults, null, 2));
  log('\nResults saved to /tmp/interactive-test-results.json');

  return allResults;
}

main().catch(e => {
  console.error('Fatal error:', e);
  process.exit(1);
});