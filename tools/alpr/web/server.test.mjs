import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createApp } from './server.mjs';

let dir, server, url;
const template = {
  version: 1,
  dataset_id: 'test-study',
  frames: [
    {
      segment: 'route--1',
      camera: 'fcamera',
      frame: 0,
      image: 'runs/test/context.jpg',
      split: 'test',
      reviewed: false,
      lighting: 'unknown',
      plates: [],
    },
  ],
};
before(async () => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), 'road-review-test-'));
  fs.writeFileSync(path.join(dir, 'labels.template.json'), JSON.stringify(template));
  server = createApp({ dataDir: dir }).listen(0, '127.0.0.1');
  await new Promise((resolve) => server.once('listening', resolve));
  url = `http://127.0.0.1:${server.address().port}`;
});
after(async () => {
  await new Promise((resolve) => server.close(resolve));
  fs.rmSync(dir, { recursive: true, force: true });
});
const get = () => fetch(url + '/api/labels').then((r) => r.json());
const put = (body, headers = {}) =>
  fetch(url + '/api/labels', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify(body),
  });

test('annotations persist, retain a prior copy, and reject stale revisions', async () => {
  const initial = await get();
  const data = structuredClone(initial.data);
  data.frames[0].lighting = 'day';
  assert.equal((await put({ ...initial, data })).status, 200);
  assert.equal((await get()).data.frames[0].lighting, 'day');
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(dir, 'labels.previous.json'))), template);
  assert.equal((await put(initial)).status, 409);
  assert.equal((await get()).data.frames[0].lighting, 'day');
});
test('invalid reviewed plates do not replace saved work', async () => {
  const original = await get();
  const data = structuredClone(original.data);
  data.frames[0].reviewed = true;
  data.frames[0].plates = [
    { box: [10, 10, 50, 40], text: '', encounter: '', readable: true, blur: 'unknown' },
  ];
  assert.equal((await put({ ...original, data })).status, 400);
  assert.deepEqual(await get(), original);
});
test('dataset identity and frame list cannot be changed', async () => {
  const original = await get();
  for (const data of [
    { ...original.data, dataset_id: 'other' },
    { ...original.data, frames: [] },
  ])
    assert.equal((await put({ ...original, data })).status, 400);
});
test('image paths and split membership remain canonical', async () => {
  const original = await get();
  const data = structuredClone(original.data);
  data.frames[0].image = '/etc/passwd';
  data.frames[0].split = 'tune';
  assert.equal((await put({ ...original, data })).status, 200);
  const actual = await get();
  assert.equal(actual.data.frames[0].image, template.frames[0].image);
  assert.equal(actual.data.frames[0].split, 'test');
});
test('cross-origin writes are rejected and non-review files are unavailable', async () => {
  assert.equal((await put(await get(), { Origin: 'https://unrelated.example' })).status, 403);
  for (const file of ['/runs/test/config.json', '/models/weights.onnx', '/server.mjs', '/api/docs/toString'])
    assert.equal((await fetch(url + file)).status, 404);
});
test('export has the evaluator-compatible document without revision metadata', async () => {
  const response = await fetch(url + '/api/labels/export');
  assert.match(response.headers.get('content-disposition'), /labels.json/);
  const data = await response.json();
  assert.equal(data.version, 1);
  assert.equal(data.revision, undefined);
  assert.equal(data.frames.length, 1);
});

test('assisted decisions are revision-protected and never alter independent annotations', async () => {
  const root = path.join(dir, 'assisted-v1');
  fs.mkdirSync(root);
  fs.writeFileSync(
    path.join(root, 'queue.json'),
    JSON.stringify({ encounters: [{ id: 'vehicle-a', samples: [{ id: 'sample-a' }] }] }),
  );
  const original = fs.readFileSync(path.join(dir, 'labels.json'), 'utf8');
  const current = await fetch(url + '/api/assisted/reviews').then((r) => r.json());
  const review = {
    state: 'confirmed',
    text: 'TEST123',
    sample_id: 'sample-a',
    box: [10, 20, 80, 50],
    lighting: 'auto',
    vehicle_id: 'vehicle-a',
    baseline: true,
  };
  const save = (body) =>
    fetch(url + '/api/assisted/reviews', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  const body = { revision: current.revision, encounter_id: 'vehicle-a', review };
  const response = await save(body);
  assert.equal(response.status, 200);
  const saved = await response.json();
  assert.equal(saved.data.decisions['vehicle-a'].assisted, true);
  assert.equal(saved.data.decisions['vehicle-a'].baseline, true);
  assert.equal(fs.readFileSync(path.join(dir, 'labels.json'), 'utf8'), original);
  assert.equal((await save(body)).status, 409);
  assert.equal(
    (await save({ ...body, revision: saved.revision, review: { ...review, state: 'unreadable' } })).status,
    400,
  );
  assert.equal(
    (await save({ ...body, revision: saved.revision, review: { ...review, box: [0, 0, 9999, 9999] } }))
      .status,
    400,
  );
});
