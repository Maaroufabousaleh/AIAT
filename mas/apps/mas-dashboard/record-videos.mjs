import { chromium } from './node_modules/playwright/index.mjs';
import { SignJWT } from 'jose';
import { mkdirSync } from 'fs';

const VIDEO_DIR = process.env.VIDEO_DIR || '/tmp/artifacts/videos';
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

const tests = [
  { path: '/workers', name: '01-Workers-Hiring-Board', spec: 'runtime-status' },
  { path: '/workers', name: '02-Hiring-Board-Full', spec: 'hiring-board' },
  { path: '/projects', name: '03-Projects', spec: 'app-operations' },
  { path: '/flows', name: '04-Flows', spec: 'app-operations' },
  { path: '/flows/new', name: '05-Flow-Builder', spec: 'flow-builder' },
  { path: '/credentials', name: '06-Credentials', spec: 'app-operations' },
  { path: '/dlq', name: '07-DLQ', spec: 'app-operations' },
  { path: '/metrics', name: '08-Metrics', spec: 'app-operations' },
  { path: '/system', name: '09-System-Control', spec: 'app-operations' },
  { path: '/logs', name: '10-Logs', spec: 'app-operations' },
  { path: '/system-viz', name: '11-System-Viz', spec: 'app-operations' },
  { path: '/tools', name: '12-Tools', spec: 'app-operations' },
  { path: '/streams', name: '13-Streams' },
];

console.log(`Starting video recording tests...`);
console.log(`BASE_URL: ${BASE_URL}`);
console.log(`VIDEO_DIR: ${VIDEO_DIR}`);

const browser = await chromium.launch({ 
  headless: true,
  args: ['--no-sandbox', '--disable-setuid-sandbox']
});

for (const test of tests) {
  console.log(`\n=== Recording: ${test.name} ===`);
  const videoPath = `${VIDEO_DIR}/${test.name}.webm`;
  
  const context = await browser.newContext({
    recordVideo: { dir: VIDEO_DIR, size: { width: 1920, height: 1080 } }
  });
  const page = await context.newPage();
  
  await authenticate(context, BASE_URL);
  
  try {
    console.log(`Navigating to ${BASE_URL}${test.path}...`);
    await page.goto(`${BASE_URL}${test.path}`, { timeout: 30000, waitUntil: 'networkidle' });
    await page.waitForTimeout(3000); // Let it record a bit
    console.log(`✓ ${test.name} loaded successfully`);
  } catch (e) {
    console.log(`✗ ${test.name}: ${e.message}`);
  }
  
  await context.close();
}

await browser.close();
console.log('\n=== All recordings complete ===');
console.log('Videos saved to:', VIDEO_DIR);
