// Isolated decisions; actual recordings are read-only fixtures.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createApp } from './server.mjs';
const source = '/mnt/algo14/comma3-alpr';
const fixture = fs.mkdtempSync(path.join(os.tmpdir(), 'braking-browser-'));
for (const file of ['labels.template.json', 'study-results.json'])
  fs.copyFileSync(path.join(source, file), path.join(fixture, file));
const review = path.join(fixture, 'braking', 'review');
fs.mkdirSync(review, { recursive: true });
for (const file of ['index.json', 'validation.json', 'function-fit.json'])
  fs.copyFileSync(path.join(source, 'braking', 'review', file), path.join(review, file));
for (const dir of ['events', 'media', 'simulation'])
  fs.symlinkSync(path.join(source, 'braking', 'review', dir), path.join(review, dir));
const server = createApp({ dataDir: fixture }).listen(0, '127.0.0.1');
await new Promise((resolve) => server.once('listening', resolve));
const browser = await chromium.launch();
const errors = [];
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/#braking`);
  await page.getByRole('heading', { name: 'What happens before the stop?' }).waitFor();
  await page.getByRole('heading', { name: 'Acceleration and braking', exact: false }).waitFor();
  await page.getByRole('heading', { name: 'A smoother stop must also finish promptly' }).waitFor();
  await page.getByRole('heading', { name: 'Reconstructed traffic: the planner runs again' }).waitFor();
  assert.equal(await page.getByRole('columnheader', { name: 'Brake candidate' }).count(), 1);
  const brakeOnly = JSON.parse(fs.readFileSync(path.join(review, 'validation.json'))).profile_kind === 'brake';
  if (brakeOnly) await page.getByRole('heading', { name: 'Where the response model goes wrong' }).waitFor();
  await page.getByLabel('Replay window').selectOption('finish');
  await page.getByLabel('Replay window').selectOption('approach');
  await page.waitForFunction((expected) => {
    const paths = [...document.querySelectorAll('[aria-label="Replayed speed over time"] path[d]')];
    return paths.length >= expected && paths.every((p) => p.getAttribute('d').length > 20);
  }, brakeOnly ? 3 : 4);
  assert.equal(await page.locator('[aria-label="Recorded stop"] option').count(), 3);
  await page.getByLabel('Inspect braking time').fill('-1');
  await page.getByLabel('Brightness', { exact: true }).fill('1.5');
  await page.getByLabel('Lift shadows', { exact: true }).fill('2');
  assert.equal(await page.locator('video').count(), 2);
  await page.waitForFunction(() => [...document.querySelectorAll('video')].every((v) => v.readyState >= 2));
  const primary = page.locator('video').first();
  await primary.evaluate((v) => v.play());
  await page.waitForFunction(
    () => Number(document.querySelector('[aria-label="Inspect braking time"]').value) > -0.7,
  );
  await primary.evaluate((v) => v.pause());
  await page.screenshot({
    path: '/mnt/algo14/comma3-alpr/braking/review/browser-review.png',
    fullPage: true,
  });
  await page.getByRole('heading', { name: 'Your braking fitted to a smooth function' }).waitFor();
  await page.getByRole('button', { name: 'Manual stop 1 · training', exact: true }).click();
  await page.getByRole('heading', { name: 'Speed: recording and fitted function', exact: false }).waitFor();
  assert.equal(await page.getByLabel('Braking event selection').inputValue(), 'manual');
  await page.locator('[aria-label="Fitted stopping function"]').screenshot({
    path: '/mnt/algo14/comma3-alpr/braking/review/browser-function-review.png',
  });
  await page.getByRole('button', { name: 'Yes, representative' }).waitFor();
  await page.getByRole('button', { name: 'Yes, representative' }).click();
  await page.getByText('representative', { exact: true }).waitFor();
  await page.reload();
  await page.getByLabel('Brightness', { exact: true }).waitFor();
  assert.equal(await page.getByLabel('Brightness', { exact: true }).inputValue(), '1.5');
  assert.equal(await page.getByLabel('Lift shadows', { exact: true }).inputValue(), '2');
  assert.deepEqual(errors, []);
  console.log(
    'Native review: synchronized video, all three stops, display persistence, and isolated annotation save passed.',
  );
} finally {
  await browser.close();
  await new Promise((resolve) => server.close(resolve));
  fs.rmSync(fixture, { recursive: true, force: true });
}
