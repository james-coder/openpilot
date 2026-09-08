import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import express from 'express';

const read = (file) => JSON.parse(fs.readFileSync(file, 'utf8'));
const revision = (value) => crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex');
const validId = (id) => /^[0-9a-f]{8}--[0-9a-f]{10}-stop-\d+$/.test(id);

export function installBrakingRoutes(app, dataDir) {
  const root = path.join(dataDir, 'braking', 'review');
  const index = () => read(path.join(root, 'index.json'));
  const labels = path.join(root, 'decisions.json');
  const current = () => (fs.existsSync(labels) ? read(labels) : {});
  app.get('/api/braking', (_req, res) => {
    if (!fs.existsSync(path.join(root, 'index.json'))) return res.sendStatus(404);
    res.json(index());
  });
  app.get('/api/braking/events/:id', (req, res) => {
    if (!validId(req.params.id)) return res.sendStatus(404);
    const file = path.join(root, 'events', req.params.id + '.json');
    if (!fs.existsSync(file)) return res.sendStatus(404);
    res.json(read(file));
  });
  app.get('/api/braking/decisions', (_req, res) => {
    const data = current();
    res.json({ data, revision: revision(data) });
  });
  app.put('/api/braking/decisions/:id', (req, res) => {
    if (!validId(req.params.id) || !index().events.some((e) => e.id === req.params.id))
      return res.sendStatus(404);
    const previous = current();
    if (req.body.revision !== revision(previous))
      return res.status(409).json({ error: 'Another tab saved a decision. Reload before saving.' });
    if (!['representative', 'exclude', 'unreviewed'].includes(req.body.decision)) return res.sendStatus(400);
    const data = { ...previous, [req.params.id]: req.body.decision };
    fs.mkdirSync(root, { recursive: true });
    const atomic = (file, value) => {
      fs.writeFileSync(file + '.tmp', JSON.stringify(value) + '\n', { mode: 0o600 });
      fs.renameSync(file + '.tmp', file);
    };
    atomic(labels.replace('.json', '.previous.json'), previous);
    atomic(labels, data);
    res.json({ data, revision: revision(data) });
  });
  app.get('/api/braking/validation', (_req, res) => {
    const file = path.join(root, 'validation.json');
    if (!fs.existsSync(file)) return res.sendStatus(404);
    res.json(read(file));
  });
  app.use(
    '/braking-media',
    (req, res, next) => {
      if (!/^\/[0-9a-f]{8}--[0-9a-f]{10}-stop-\d+-(fcamera|ecamera)\.mp4$/.test(req.path))
        return res.sendStatus(404);
      next();
    },
    express.static(path.join(root, 'media'), { dotfiles: 'deny', index: false, maxAge: '1h' }),
  );
}
