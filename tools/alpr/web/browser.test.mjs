// Run against an isolated copy of the labels; never write test annotations to the study.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createApp } from './server.mjs';

const artifacts = process.env.REVIEW_TEST_ARTIFACTS || '/mnt/algo14/comma3-diagnostics/2026-09-06/web-review';
fs.mkdirSync(artifacts, { recursive: true });
const fixture = fs.mkdtempSync(path.join(os.tmpdir(), 'road-review-browser-'));
const source = process.env.REVIEW_DATA_DIR || '/mnt/algo14/comma3-alpr';
for (const file of [
  'labels.template.json',
  'study-results.json',
  'comparison.html',
  'comparison-close-vehicle.html',
  'radar-example.html',
])
  fs.copyFileSync(path.join(source, file), path.join(fixture, file));
fs.symlinkSync(path.join(source, 'runs'), path.join(fixture, 'runs'));
const assisted = path.join(fixture, 'assisted-v1');
fs.mkdirSync(assisted);
fs.copyFileSync(path.join(source, 'assisted-v1/queue.json'), path.join(assisted, 'queue.json'));
for (const entry of fs.readdirSync(path.join(source, 'assisted-v1'), { withFileTypes: true })) {
  if (entry.isDirectory())
    fs.symlinkSync(path.join(source, 'assisted-v1', entry.name), path.join(assisted, entry.name));
}
const server = createApp({ dataDir: fixture }).listen(0, '127.0.0.1');
await new Promise((resolve) => server.once('listening', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch(
  process.env.REVIEW_CHROMIUM ? { executablePath: process.env.REVIEW_CHROMIUM } : {},
);
const errors = [];
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1050 },
    acceptDownloads: true,
  });
  const page = await context.newPage();
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(base);
  await page.getByRole('heading', { name: 'A closer look at the road.' }).waitFor();
  await page.screenshot({ path: path.join(artifacts, 'overview.png'), fullPage: true });
  for (const [hash, title] of [
    ['radar', 'Let distance guide the capture'],
    ['models', 'Compare the same plate crops'],
    ['sampling', 'More frames, or more image detail?'],
  ]) {
    await page.goto(base + '/#' + hash);
    await page.getByRole('heading', { name: title }).waitFor();
    const frame = page.frameLocator('iframe');
    await frame.locator('img').first().waitFor();
    await frame
      .locator('img')
      .first()
      .evaluate((img) => img.decode());
  }
  for (const name of ['results', 'reliability', 'radar-notes']) {
    await page.goto(base + '/#' + name);
    await page.locator('.document h1').waitFor();
  }
  await page.goto(base + '/#manual');
  const canvas = page.getByLabel('Road frame: drag to draw a plate box');
  await canvas.waitFor();
  await page.getByLabel('Zoom', { exact: true }).selectOption('1');
  const bounds = await canvas.boundingBox();
  await page.mouse.move(bounds.x + 70, bounds.y + 70);
  await page.mouse.down();
  await page.mouse.move(bounds.x + 190, bounds.y + 120);
  await page.mouse.up();
  await page.getByLabel('Encounter ID').fill('test-vehicle-01');
  await page.getByLabel('Plate text').fill('TEST123');
  await page.getByLabel('Confidently readable').check();
  await page.getByRole('button', { name: 'Mark frame reviewed' }).click();
  await page.getByRole('status').filter({ hasText: 'All changes saved' }).waitFor();
  let saved = JSON.parse(fs.readFileSync(path.join(fixture, 'labels.json')));
  assert.equal(saved.frames[0].reviewed, true);
  assert.deepEqual(saved.frames[0].plates[0].box, [70, 70, 190, 120]);
  assert.equal(saved.frames[0].plates[0].text, 'TEST123');
  await page.reload();
  await page.getByRole('button', { name: 'Reviewed · click to reopen' }).waitFor();
  assert.equal(await page.getByLabel('Plate text').inputValue(), 'TEST123');
  await page.screenshot({ path: path.join(artifacts, 'annotation.png'), fullPage: true });
  const exportEvent = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download labels' }).click();
  const exported = await exportEvent;
  assert.equal(exported.suggestedFilename(), 'labels.json');
  // Concurrent tabs must not overwrite one another.
  const other = await context.newPage();
  await other.goto(base + '/#manual');
  await other.getByLabel('Lighting', { exact: true }).waitFor();
  await page.getByLabel('Lighting', { exact: true }).selectOption('day');
  await page.getByRole('status').filter({ hasText: 'All changes saved' }).waitFor();
  await other.getByLabel('Lighting', { exact: true }).selectOption('night');
  await other.getByRole('status').filter({ hasText: 'Another tab or reviewer saved changes' }).waitFor();
  saved = JSON.parse(fs.readFileSync(path.join(fixture, 'labels.json')));
  assert.equal(saved.frames[0].lighting, 'day');
  // A fresh browser retrieves saved work independently of localStorage.
  const fresh = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const mobile = await fresh.newPage();
  await mobile.goto(base + '/#manual');
  await mobile.getByLabel('Plate text').waitFor();
  assert.equal(await mobile.getByLabel('Plate text').inputValue(), 'TEST123');
  await mobile.screenshot({ path: path.join(artifacts, 'mobile.png'), fullPage: true });
  // Machine-assisted reviews use separate decisions, persistent display controls and automatic identities.
  const independentBefore = fs.readFileSync(path.join(fixture, 'labels.json'), 'utf8');
  const guided = await context.newPage();
  guided.on('pageerror', (e) => errors.push(e.message));
  await guided.goto(base + '/#review');
  await guided.getByLabel('Enlarged plate', { exact: true }).waitFor();
  assert.equal(await guided.getByLabel('Candidate selection').inputValue(), 'close');
  await guided.getByLabel('Lift shadows', { exact: true }).focus();
  await guided.keyboard.press('End');
  assert.equal(await guided.getByLabel('Lift shadows', { exact: true }).inputValue(), '3');
  await guided.getByRole('button', { name: 'Next encounter', exact: true }).click();
  assert.equal(await guided.getByLabel('Lift shadows', { exact: true }).inputValue(), '3');
  await guided.reload();
  await guided.getByLabel('Enlarged plate', { exact: true }).waitFor();
  assert.equal(await guided.getByLabel('Lift shadows', { exact: true }).inputValue(), '3');
  await guided.getByRole('button', { name: 'Lift shadows preset', exact: true }).click();
  await guided.getByLabel('Next after saving').uncheck();
  await guided.getByLabel('Suggested plate text', { exact: true }).fill('TEST456');
  await guided
    .getByRole('button', { name: 'Every character is clear — add to baseline', exact: true })
    .click();
  await guided.getByRole('status').filter({ hasText: 'Review saved' }).waitFor();
  const decisions = JSON.parse(fs.readFileSync(path.join(assisted, 'reviews.json'))).decisions;
  assert.equal(Object.values(decisions)[0].baseline, true);
  assert.equal(Object.values(decisions)[0].text, 'TEST456');
  assert.equal(Object.values(decisions)[0].state, 'corrected');
  assert.equal(fs.readFileSync(path.join(fixture, 'labels.json'), 'utf8'), independentBefore);
  await guided.reload();
  await guided.getByLabel('Suggested plate text', { exact: true }).waitFor();
  assert.equal(await guided.getByLabel('Suggested plate text', { exact: true }).inputValue(), 'TEST456');
  await guided.screenshot({ path: path.join(artifacts, 'assisted.png'), fullPage: true });
  await guided.getByLabel('Candidate selection').selectOption('baseline');
  assert.equal(await guided.locator('.saved-decision').textContent(), ' Saved: clear baseline');
  assert.deepEqual(errors, []);
  console.log(
    'Browser checks passed: reports/images, native-coordinate boxes, autosave, reload, download, concurrent edits, fresh browser, narrow layout, assisted baseline, persistent brightness, independent-label preservation.',
  );
} finally {
  await browser.close();
  await new Promise((resolve) => server.close(resolve));
  fs.rmSync(fixture, { recursive: true, force: true });
}
