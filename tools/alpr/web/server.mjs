import express from 'express';
import { installAssistedRoutes } from './assisted-server.mjs';
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (file) => JSON.parse(fs.readFileSync(file, 'utf8'));
const revision = (data) => createHash('sha256').update(JSON.stringify(data)).digest('hex');
const key = (frame) => `${frame.segment}/${frame.camera}/${frame.frame}`;

export function validateLabels(data, template) {
  if (
    data?.dataset_id !== template.dataset_id ||
    data?.version !== template.version ||
    !Array.isArray(data.frames) ||
    data.frames.length !== template.frames.length
  )
    throw Error('Dataset does not match this study.');
  const frames = template.frames.map((original, i) => {
    const f = data.frames[i];
    if (
      !f ||
      key(f) !== key(original) ||
      typeof f.reviewed !== 'boolean' ||
      !['unknown', 'day', 'night', 'dusk'].includes(f.lighting) ||
      !Array.isArray(f.plates) ||
      f.plates.length > 100
    )
      throw Error('Invalid frame annotation.');
    const plates = f.plates.map((p) => {
      if (
        !p ||
        !Array.isArray(p.box) ||
        p.box.length !== 4 ||
        !p.box.every((v) => Number.isFinite(v) && v >= 0 && v <= 10000) ||
        p.box[2] <= p.box[0] ||
        p.box[3] <= p.box[1] ||
        typeof p.text !== 'string' ||
        p.text.length > 100 ||
        typeof p.encounter !== 'string' ||
        p.encounter.length > 100 ||
        typeof p.readable !== 'boolean' ||
        !['unknown', 'sharp', 'blurred'].includes(p.blur)
      )
        throw Error('Invalid plate annotation.');
      if (f.reviewed && (!p.encounter.trim() || (p.readable && !/[a-z0-9]/i.test(p.text))))
        throw Error('Reviewed plates need an encounter ID and readable plates need text.');
      return { box: p.box, text: p.text, encounter: p.encounter, readable: p.readable, blur: p.blur };
    });
    return { ...original, reviewed: f.reviewed, lighting: f.lighting, plates };
  });
  return { version: template.version, dataset_id: template.dataset_id, frames };
}

export function createApp({
  dataDir = '/mnt/algo14/comma3-alpr',
  repoDir = path.resolve(here, '../../..'),
} = {}) {
  const app = express();
  app.disable('x-powered-by');
  const template = read(path.join(dataDir, 'labels.template.json'));
  const labelsPath = path.join(dataDir, 'labels.json');
  const current = () => (fs.existsSync(labelsPath) ? validateLabels(read(labelsPath), template) : template);
  app.use((req, res, next) => {
    const ip = req.socket.remoteAddress?.replace(/^::ffff:/, '');
    if (ip !== '::1' && ip !== '127.0.0.1' && !/^192\.168\.(98|99)\./.test(ip ?? ''))
      return res.status(403).json({ error: 'Local subnet only.' });
    res.set('X-Content-Type-Options', 'nosniff');
    res.set('Referrer-Policy', 'same-origin');
    if (req.path.startsWith('/api/')) res.set('Cache-Control', 'no-store');
    if (
      req.method !== 'GET' &&
      req.method !== 'HEAD' &&
      req.headers.origin &&
      req.headers.origin !== `http://${req.headers.host}`
    )
      return res.status(403).json({ error: 'Cross-origin writes are disabled.' });
    next();
  });
  app.use(express.json({ limit: '5mb' }));
  installAssistedRoutes(app, dataDir);
  app.get('/api/health', (_req, res) => res.json({ status: 'ok' }));
  app.get('/api/study', (_req, res) => res.json(read(path.join(dataDir, 'study-results.json'))));
  app.get('/api/labels', (_req, res) => {
    const data = current();
    res.json({ revision: revision(data), data });
  });
  app.get('/api/labels/export', (_req, res) =>
    res
      .attachment('labels.json')
      .type('json')
      .send(JSON.stringify(current(), null, 2)),
  );
  app.put('/api/labels', (req, res) => {
    const previous = current();
    if (req.body?.revision !== revision(previous))
      return res.status(409).json({
        error:
          'Another tab or reviewer saved changes. Download your draft, then reload to get the saved version.',
      });
    let data;
    try {
      data = validateLabels(req.body.data, template);
    } catch (error) {
      return res.status(400).json({ error: error.message });
    }
    // Synchronous, atomic updates serialize concurrent requests in this single process.
    const writeAtomic = (dest, value) => {
      const temp = `${dest}.tmp`;
      const fd = fs.openSync(temp, 'w', 0o600);
      try {
        fs.writeFileSync(fd, JSON.stringify(value, null, 2) + '\n');
        fs.fsyncSync(fd);
      } finally {
        fs.closeSync(fd);
      }
      fs.renameSync(temp, dest);
    };
    writeAtomic(path.join(dataDir, 'labels.previous.json'), previous);
    writeAtomic(labelsPath, data);
    res.json({ revision: revision(data), savedAt: new Date().toISOString() });
  });
  const docs = {
    results: 'tools/alpr/RESULTS.md',
    radar: 'tools/alpr/RADAR_PRIORITIZATION.md',
    reliability: 'docs/COMMA3_RELIABILITY.md',
  };
  app.get('/api/docs/:name', (req, res) => {
    const file = Object.hasOwn(docs, req.params.name) && docs[req.params.name];
    if (!file) return res.sendStatus(404);
    res.type('text/plain').send(fs.readFileSync(path.join(repoDir, file), 'utf8'));
  });
  for (const name of ['comparison.html', 'comparison-close-vehicle.html', 'radar-example.html']) {
    app.get('/' + name, (_req, res) => res.sendFile(path.join(dataDir, name)));
  }
  app.use(
    '/runs',
    (req, res, next) => {
      if (!/\.(png|jpg|jpeg)$/.test(req.path) && !/^\/[a-z0-9-]+\/report\.html$/.test(req.path))
        return res.sendStatus(404);
      next();
    },
    express.static(path.join(dataDir, 'runs'), { dotfiles: 'deny', index: false, maxAge: '1h' }),
  );
  app.use(express.static(path.join(here, 'dist'), { index: 'index.html' }));
  app.use((_req, res) => res.sendStatus(404));
  app.use((error, _req, res, _next) => {
    console.error(error.message);
    res.status(error.status || 500).json({
      error:
        error.status === 413
          ? 'Annotation document is too large.'
          : 'Request failed. Your saved labels were not replaced.',
    });
  });
  return app;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const host = process.env.REVIEW_HOST || '192.168.99.189';
  const port = Number(process.env.REVIEW_PORT || 8088);
  createApp({ dataDir: process.env.REVIEW_DATA_DIR }).listen(port, host, () =>
    console.log(`Road Review listening at http://${host}:${port}`),
  );
}
