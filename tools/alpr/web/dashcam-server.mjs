import fs from 'node:fs';
import path from 'node:path';

// Incident reports built by tools/dashcam/incident_report.py live in <dir>/incidents/<id>/report/.
// Only the finished report files are served; raw recordings, car logs and hash lists never are.
const validId = (id) => /^\d{4}-\d{2}-\d{2}-\d{4}$/.test(id);
const servable = (file) =>
  /^(index\.html|video\.mp4|graph\.png|report\.md|report\.json|notes\.md|stills\/[a-z0-9_-]+\.jpg)$/.test(file);

export function installDashcamRoutes(app, dir) {
  const root = path.join(dir, 'incidents');
  app.get('/api/dashcam', (_req, res) => {
    let ids = [];
    try {
      ids = fs.readdirSync(root).filter(validId).sort().reverse();
    } catch {
      /* nothing recorded yet */
    }
    const items = [];
    for (const id of ids) {
      try {
        const r = JSON.parse(fs.readFileSync(path.join(root, id, 'report', 'report.json'), 'utf8'));
        const { radar_tracks_within_15m: tracks, ...summary } = r;
        const stills = fs
          .readdirSync(path.join(root, id, 'report', 'stills'))
          .filter((f) => f.endsWith('.jpg'))
          .sort();
        let notes = '';
        try {
          notes = fs.readFileSync(path.join(root, id, 'report', 'notes.md'), 'utf8');
        } catch {
          /* notes are optional */
        }
        items.push({ id, ...summary, tracks, stills, notes });
      } catch {
        /* an incomplete report is skipped */
      }
    }
    res.json({ items });
  });
  // Express 5: *file captures path segments. sendFile with a root rejects any '..' traversal.
  app.get('/dashcam-files/:id/*file', (req, res) => {
    const file = [].concat(req.params.file).join('/');
    if (!validId(req.params.id) || !servable(file)) return res.sendStatus(404);
    res.sendFile(file, { root: path.join(root, req.params.id, 'report'), dotfiles: 'deny' }, (error) => {
      if (error && !res.headersSent) res.sendStatus(error.statusCode === 404 ? 404 : 500);
    });
  });
}
