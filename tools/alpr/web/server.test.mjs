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

test('tentative context survives older clients and cannot become a clear baseline', async () => {
  const load = () => fetch(url + '/api/assisted/reviews').then((r) => r.json());
  const initial = await load();
  const oldReview = initial.data.decisions['vehicle-a'];
  const send = async (review) =>
    fetch(url + '/api/assisted/reviews', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ revision: (await load()).revision, encounter_id: 'vehicle-a', review }),
    });
  const uncertain = {
    ...oldReview,
    text: 'A12 3BC',
    baseline: false,
    jurisdiction: 'UT',
    plate_style: 'unknown',
    certainty: 'tentative',
    alternatives: ['12 3BC'],
    uncertainty_note: 'Leading character unclear',
    vehicle_type: 'truck',
  };
  assert.equal((await send(uncertain)).status, 200);
  assert.equal((await send({ ...uncertain, baseline: true })).status, 400);
  const legacy = { ...oldReview, baseline: false, text: 'A12 3BC' };
  for (const key of [
    'jurisdiction',
    'plate_style',
    'certainty',
    'alternatives',
    'uncertainty_note',
    'vehicle_type',
  ])
    delete legacy[key];
  assert.equal((await send(legacy)).status, 200);
  assert.equal((await load()).data.decisions['vehicle-a'].certainty, 'tentative');
  assert.deepEqual((await load()).data.decisions['vehicle-a'].alternatives, ['12 3BC']);
  assert.equal((await send({ ...legacy, baseline: true })).status, 400);
  assert.equal((await send({ ...uncertain, jurisdiction: 'FL', plate_style: 'ut_skier' })).status, 400);
  assert.equal(
    (await send({ ...uncertain, certainty: 'certain', alternatives: [], baseline: true })).status,
    200,
  );
});

test('state and design hints never rewrite OCR or impose a universal state format', async () => {
  const { formatHint } = await import('./plate-formats.mjs');
  assert.equal(formatHint('UT', 'ut_skier', 'A12 3BC').matches, true);
  assert.equal(formatHint('UT', 'ut_arches', '12 3BC').matches, false);
  assert.equal(formatHint('UT', 'ut_skier', 'A1O 3BC').matches, false);
  assert.equal(formatHint('UT', 'unknown', 'A12 3BC').known, false);
  assert.equal(formatHint('UT', 'personalized', 'HELLO'), null);
  assert.equal(formatHint('FL', 'unknown', 'A12 3BC'), null);
});

test('diagnostic reports are read-only and preview files use an exact allowlist', async () => {
  assert.equal((await fetch(url + '/api/braking-audit')).status, 404);
  fs.writeFileSync(path.join(dir, 'braking-audit.json'), JSON.stringify({ events: [] }));
  assert.deepEqual(await (await fetch(url + '/api/braking-audit')).json(), { events: [] });
  assert.equal((await fetch(url + '/api/braking-audit', { method: 'PUT' })).status, 404);
  fs.mkdirSync(path.join(dir, 'diagnostics-ui'));
  fs.writeFileSync(path.join(dir, 'diagnostics-ui', 'can-list.png'), 'preview');
  fs.writeFileSync(path.join(dir, 'diagnostics-ui', 'private.json'), '{}');
  assert.equal((await fetch(url + '/diagnostics-ui/can-list.png')).status, 200);
  assert.equal((await fetch(url + '/diagnostics-ui/private.json')).status, 404);
});

test('native braking details and decisions are isolated, revision-protected, and allowlisted', async () => {
  const root = path.join(dir, 'braking', 'review');
  fs.mkdirSync(path.join(root, 'events'), { recursive: true });
  fs.mkdirSync(path.join(root, 'media'));
  const id = '00000022--0123456789-stop-1';
  fs.writeFileSync(path.join(root, 'index.json'), JSON.stringify({ events: [{ id }] }));
  fs.writeFileSync(path.join(root, 'events', id + '.json'), JSON.stringify({ id, samples: [] }));
  assert.equal((await fetch(url + '/api/braking')).status, 200);
  assert.equal((await fetch(url + '/api/braking/events/' + id)).status, 200);
  assert.equal((await fetch(url + '/api/braking/events/unknown')).status, 404);
  fs.mkdirSync(path.join(root, 'simulation'));
  fs.writeFileSync(
    path.join(root, 'simulation', id + '-traffic-stock.json'),
    JSON.stringify({ samples: [{ t: 0 }] }),
  );
  assert.deepEqual(await (await fetch(url + '/api/braking/simulation/' + id + '/traffic-stock')).json(), {
    samples: [{ t: 0 }],
  });
  assert.equal((await fetch(url + '/api/braking/simulation/' + id + '/decisions')).status, 404);
  assert.equal((await fetch(url + '/api/braking/simulation/' + id + '/traffic-personal')).status, 404);
  const before = await get();
  const original = await (await fetch(url + '/api/braking/decisions')).json();
  const send = (body) =>
    fetch(url + '/api/braking/decisions/' + id, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  assert.equal((await send({ ...original, decision: 'representative' })).status, 200);
  assert.equal((await send({ ...original, decision: 'exclude' })).status, 409);
  const current = await (await fetch(url + '/api/braking/decisions')).json();
  assert.equal(current.data[id], 'representative');
  assert.equal((await send({ ...current, decision: 'invalid' })).status, 400);
  assert.deepEqual(await get(), before);
  fs.writeFileSync(path.join(root, 'media', id + '-fcamera.mp4'), '0123456789');
  const range = await fetch(url + '/braking-media/' + id + '-fcamera.mp4', {
    headers: { Range: 'bytes=0-3' },
  });
  assert.equal(range.status, 206);
  assert.equal(await range.text(), '0123');
  for (const route of [
    '/braking-media/decisions.json',
    '/braking-media/response-fit.json',
    '/braking/review/cache',
  ])
    assert.equal((await fetch(url + route)).status, 404);
});
