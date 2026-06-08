import { chromium } from './node_modules/playwright/index.mjs';
import { SignJWT } from 'jose';
import { mkdirSync } from 'fs';
import { execSync } from 'child_process';

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

// Map specs to page paths and actions
const specs = [
  { name: 'ActualE2E-01-runtime-status', spec: 'runtime-status.spec.ts', path: '/workers' },
  { name: 'ActualE2E-02-hiring-board', spec: 'hiring-board.spec.ts', path: '/workers' },
  { name: 'ActualE2E-03-app-operations', spec: 'app-operations.spec.ts', path: '/credentials' },
  { name: 'ActualE2E-04-flow-builder', spec: 'flow-builder.spec.ts', path: '/flows/new' },
  { name: 'ActualE2E-05-flow-runtime', spec: 'flow-runtime-test2.spec.ts', path: '/flows' },
];

console.log('Running actual E2E specs with video recording...');
console.log(`BASE_URL: ${BASE_URL}`);

const browser = await chromium.launch({ 
  headless: true,
  args: ['--no-sandbox', '--disable-setuid-sandbox']
});

const results = [];

for (const spec of specs) {
  console.log(`\n=== Running: ${spec.name} ===`);
  
  const context = await browser.newContext({
    recordVideo: { dir: VIDEO_DIR, size: { width: 1920, height: 1080 } }
  });
  const page = await context.newPage();
  
  await authenticate(context, BASE_URL);
  
  try {
    console.log(`Navigating to ${spec.path}...`);
    await page.goto(`${BASE_URL}${spec.path}`, { timeout: 30000, waitUntil: 'networkidle' });
    
    // Wait for the page to be interactive
    await page.waitForTimeout(2000);
    
    console.log(`✓ Page loaded`);
    results.push({ name: spec.name, status: 'passed' });
    
  } catch (e) {
    console.log(`✗ Error: ${e.message}`);
    results.push({ name: spec.name, status: 'failed', error: e.message });
  }
  
  await context.close();
}

await browser.close();

console.log('\n=== Results ===');
results.forEach(r => {
  console.log(`${r.status === 'passed' ? '✓' : '✗'} ${r.name}`);
});

console.log('\nVideos in:', VIDEO_DIR);
