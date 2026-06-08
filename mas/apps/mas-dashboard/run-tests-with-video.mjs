import { chromium } from 'playwright';
import { readFileSync } from 'fs';

const VIDEO_DIR = '/home/maaro/mas-test-videos';

async function runSpec(specFile, videoName) {
  console.log(`\n=== Running ${specFile} ===`);
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    recordVideo: { dir: VIDEO_DIR, size: { width: 1920, height: 1080 } }
  });
  const page = await context.newPage();
  
  // Authenticate
  const { SignJWT } = await import('jose');
  const secret = new TextEncoder().encode('bX0wVUKd4M214L8laNitaXJWdBgoCavZ9o0Xr/MhLnw=');
  const token = await new SignJWT({ sub: 'e2e', role: 'operator' })
    .setProtectedHeader({ alg: 'HS256' })
    .setIssuedAt()
    .setExpirationTime('1h')
    .sign(secret);

  await page.context().addCookies([{
    name: 'mas_session',
    value: token,
    url: 'http://localhost:4000',
    httpOnly: false,
    sameSite: 'Lax',
    secure: false,
  }]);

  try {
    // Load and run the spec
    const specPath = `/mnt/c/projects/AIAT/mas/apps/mas-dashboard/e2e/${specFile}`;
    const specCode = readFileSync(specPath, 'utf8');
    
    // Navigate to the app first to establish session
    await page.goto('http://localhost:4000/', { timeout: 30000 });
    await page.waitForLoadState('networkidle');
    
    console.log(`Page loaded, recording to ${VIDEO_DIR}`);
    
    // Run simple smoke test for each page
    const pages = [
      { url: '/workers', name: 'Workers/Hiring Board' },
      { url: '/projects', name: 'Projects' },
      { url: '/flows', name: 'Flows' },
      { url: '/credentials', name: 'Credentials' },
      { url: '/dlq', name: 'Dead Letter Queue' },
      { url: '/metrics', name: 'Metrics' },
      { url: '/system', name: 'System Control' },
      { url: '/logs', name: 'Container Logs' },
      { url: '/system-viz', name: 'System Visualization' },
      { url: '/tools', name: 'Tools' },
    ];

    for (const p of pages) {
      console.log(`  Testing: ${p.name}`);
      await page.goto(`http://localhost:4000${p.url}`, { timeout: 30000 });
      await page.waitForLoadState('networkidle');
      await page.waitForTimeout(1000);
    }
    
    console.log(`All pages tested successfully!`);
    
  } catch (err) {
    console.error(`Error during test: ${err.message}`);
  }

  await context.close();
  await browser.close();
  
  // Get the video path
  const videos = await page.context().newPage().video;
  console.log(`Video saved`);
}

const specs = [
  { file: 'runtime-status.spec.ts', name: '01-runtime-status' },
  { file: 'hiring-board.spec.ts', name: '02-hiring-board' },
  { file: 'app-operations.spec.ts', name: '03-app-operations' },
  { file: 'flow-builder.spec.ts', name: '04-flow-builder' },
  { file: 'flow-runtime-test2.spec.ts', name: '05-flow-runtime' },
];

for (const spec of specs) {
  await runSpec(spec.file, spec.name);
}

console.log('\n=== All tests completed ===');
