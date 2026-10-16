import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const helperFiles = [
  'comprehensive-test-runner.mjs',
  'record-videos.mjs',
  'run-actual-e2e.mjs',
  'run-specs-with-video.mjs',
];

for (const helper of helperFiles) {
  test(`${helper} requires an explicit JWT_SECRET`, async () => {
    const source = await readFile(new URL(`../${helper}`, import.meta.url), 'utf8');
    assert.match(source, /const JWT_SECRET = process\.env\.JWT_SECRET;/);
    assert.match(source, /if \(!JWT_SECRET\) throw new Error\(/);
    assert.doesNotMatch(source, /process\.env\.JWT_SECRET\s*\|\|/);
  });
}

test('legacy login diagnostic cannot read or serve credentials', async () => {
  const source = await readFile(new URL('../debug_login.mjs', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /readFileSync|createServer|DASHBOARD_PASSWORD_HASH|JSON\.stringify/);
});
