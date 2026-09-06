import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import express from 'express';
import { stateCodes, plateStyles } from './plate-formats.mjs';
const hash = (data) => createHash('sha256').update(JSON.stringify(data)).digest('hex');
const read = (file) => JSON.parse(fs.readFileSync(file, 'utf8'));

export function installAssistedRoutes(app, dataDir) {
  const root = path.join(dataDir, 'assisted-v1');
  const queueFile = path.join(root, 'queue.json');
  const reviewFile = path.join(root, 'reviews.json');
  const queue = () => read(queueFile);
  const current = () =>
    fs.existsSync(reviewFile) ? read(reviewFile) : { version: 1, dataset_id: 'assisted-v1', decisions: {} };
  app.get('/api/assisted/fusion', (_req, res) => {
    const catalogue = path.join(root, 'fusion/catalogue.json');
    res.json(fs.existsSync(catalogue) ? read(catalogue) : {});
  });
  app.get('/api/assisted/queue', (_req, res) => {
    if (!fs.existsSync(queueFile))
      return res
        .status(503)
        .json({ error: 'Preparing vehicle and plate suggestions. Please check back shortly.' });
    res.json(queue());
  });
  app.get('/api/assisted/reviews', (_req, res) => {
    const data = current();
    res.json({ revision: hash(data), data });
  });
  app.get('/api/assisted/export', (_req, res) => res.attachment('assisted-reviews.json').json(current()));
  app.put('/api/assisted/reviews', (req, res) => {
    const previous = current();
    if (req.body?.revision !== hash(previous))
      return res.status(409).json({
        error:
          'Another reviewer saved changes. Your current correction is kept here; reload saved reviews before trying again.',
      });
    const encounter = queue().encounters.find((e) => e.id === req.body.encounter_id);
    const review = req.body.review;
    const sample = encounter?.samples.find((s) => s.id === review?.sample_id);
    if (
      !sample ||
      !['confirmed', 'corrected', 'unreadable', 'rejected'].includes(review.state) ||
      typeof review.text !== 'string' ||
      review.text.length > 100 ||
      typeof review.baseline !== 'boolean' ||
      !['auto', 'day', 'dusk', 'night', 'unknown'].includes(review.lighting) ||
      !queue().encounters.some((e) => e.id === review.vehicle_id) ||
      !Array.isArray(review.box) ||
      review.box.length !== 4 ||
      !review.box.every((v, i) => Number.isFinite(v) && v >= 0 && v <= (i % 2 === 0 ? 1928 : 1208)) ||
      review.box[2] <= review.box[0] ||
      review.box[3] <= review.box[1]
    )
      return res.status(400).json({ error: 'Invalid review correction.' });
    const readable = ['confirmed', 'corrected'].includes(review.state);
    // Preserve context when an older browser submits a correction without the new fields.
    const prior = previous.decisions[encounter.id] || {};
    const metadata = Object.fromEntries(
      Object.entries({
        jurisdiction: '',
        plate_style: 'unknown',
        certainty: 'unspecified',
        alternatives: [],
        uncertainty_note: '',
        vehicle_type: '',
      }).map(([key, fallback]) => [key, review[key] ?? prior[key] ?? fallback]),
    );
    if (
      !['', ...stateCodes].includes(metadata.jurisdiction) ||
      !plateStyles.includes(metadata.plate_style) ||
      (metadata.plate_style.startsWith('ut_') && metadata.jurisdiction !== 'UT') ||
      !['unspecified', 'certain', 'tentative'].includes(metadata.certainty) ||
      !Array.isArray(metadata.alternatives) ||
      metadata.alternatives.length > 10 ||
      !metadata.alternatives.every((v) => typeof v === 'string' && v.length > 0 && v.length <= 100) ||
      typeof metadata.uncertainty_note !== 'string' ||
      metadata.uncertainty_note.length > 1000 ||
      !['', 'car', 'truck', 'bus', 'motorcycle', 'other'].includes(metadata.vehicle_type)
    )
      return res.status(400).json({ error: 'Invalid plate context.' });
    if (metadata.alternatives.length) metadata.certainty = 'tentative';
    if (review.baseline && metadata.certainty === 'tentative')
      return res
        .status(400)
        .json({ error: 'Resolve uncertain characters and alternatives before adding a clear baseline.' });
    if (review.baseline) metadata.certainty = 'certain';
    if ((readable && !/[a-z0-9]/i.test(review.text)) || (review.baseline && !readable))
      return res
        .status(400)
        .json({ error: 'A clear baseline requires a readable, confirmed plate transcript.' });
    const decision = {
      ...metadata,
      state: review.state,
      sample_id: sample.id,
      text: readable ? review.text : '',
      box: review.box,
      vehicle_id: review.vehicle_id,
      lighting: review.lighting,
      baseline: review.baseline,
      assisted: true,
      saved_at: new Date().toISOString(),
    };
    const data = { ...previous, decisions: { ...previous.decisions, [encounter.id]: decision } };
    const atomic = (file, value) => {
      const temp = file + '.tmp',
        fd = fs.openSync(temp, 'w', 0o600);
      try {
        fs.writeFileSync(fd, JSON.stringify(value, null, 2) + '\n');
        fs.fsyncSync(fd);
      } finally {
        fs.closeSync(fd);
      }
      fs.renameSync(temp, file);
    };
    atomic(path.join(root, 'reviews.previous.json'), previous);
    atomic(reviewFile, data);
    res.json({ revision: hash(data), data });
  });
  app.use(
    '/assisted-v1',
    (req, res, next) => {
      if (!/\.(png|jpg)$/.test(req.path)) return res.sendStatus(404);
      next();
    },
    express.static(root, { dotfiles: 'deny', index: false, maxAge: '1h' }),
  );
}
