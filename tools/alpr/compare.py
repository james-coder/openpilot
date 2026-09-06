"""Build a local side-by-side comparison anchored to the primary run's crops."""
import argparse
import html
import json
import os
from collections import defaultdict
from pathlib import Path

from openpilot.tools.alpr.evaluate import overlap


def load_run(path):
  files = sorted(p for p in path.glob('*/*/predictions.jsonl') if p.with_name('complete.json').exists())
  if not files and (path / 'predictions.jsonl').exists():
    files = [path / 'predictions.jsonl']
  rows = [json.loads(line) for file in files for line in file.read_text().splitlines()]
  frames = defaultdict(list)
  for row in rows:
    frames[(row['segment'], row['camera'], row['frame'])].append(row)
  return {'name': path.name, 'path': path, 'frames': frames, 'rows': rows,
          'tracks': json.loads((path / 'tracks.json').read_text()),
          'stats': json.loads((path / 'stats.json').read_text())}


def main():
  p = argparse.ArgumentParser(description=__doc__)
  p.add_argument('runs', nargs='+', type=Path, help='First run anchors the comparison')
  p.add_argument('--output', type=Path, required=True)
  p.add_argument('--limit', type=int, default=200)
  args = p.parse_args()
  if args.limit < 1:
    p.error('limit must be positive')
  runs = [load_run(path) for path in args.runs]
  base = runs[0]
  by_crop = {r['crop']: r for r in base['rows']}
  cards = []
  for track in base['tracks'][:args.limit]:
    row = by_crop[track['crop']]
    key = (row['segment'], row['camera'], row['frame'])
    cells = []
    for run in runs:
      matches = run['frames'][key]
      best = max(matches, key=lambda r: overlap(row['box'], r['box']), default=None)
      if best and overlap(row['box'], best['box']) >= .5:
        text = html.escape(best['text']) or '(empty)'
        cells.append(f'<td>{text}<br>score {best["ocr_confidence"]:.3f}</td>')
      else:
        cells.append('<td>No matching detection</td>')
    image = os.path.relpath((base['path'] / row['crop']).resolve(), args.output.parent.resolve())
    cards.append(f'<tr><td><img src="{html.escape(image, quote=True)}"><br>'
                 + html.escape('/'.join(map(str, key))) + '</td>' + ''.join(cells) + '</tr>')
  headers = ''.join('<th>' + html.escape(run['name']) + '</th>' for run in runs)
  summary = []
  for run in runs:
    stats = run['stats']
    entry = {'run': run['name'], 'tracks': len(run['tracks']),
             'accepted_candidates': sum(t['accepted'] for t in run['tracks']), 'accuracy': 'unmeasured'}
    if isinstance(stats, list):
      sampled = sum(s['sampled_frames'] for s in stats)
      entry.update(clips=len(stats), sampled_frames=sampled,
                   seconds_per_sampled_frame=sum(s['elapsed_seconds'] for s in stats) / sampled if sampled else None,
                   peak_rss_mib=max((s['peak_rss_bytes'] for s in stats), default=0) / 1024**2)
    else:
      entry.update(ocr_device=stats['device'], crops=stats['crops'], mean_ocr_seconds=stats['mean_ocr_seconds'])
    summary.append(entry)
  args.output.parent.mkdir(parents=True, exist_ok=True)
  args.output.write_text('''<!doctype html><meta charset="utf-8"><title>ALPR model comparison</title>
    <style>body{font:16px sans-serif;margin:20px}td,th{padding:12px;border:1px solid #ccc;overflow-wrap:anywhere}
    table{border-collapse:collapse}img{width:320px;max-width:400px;image-rendering:pixelated}pre{white-space:pre-wrap}</style>
    <h1>ALPR model comparison</h1><p>Same-frame predictions on primary-run crops; matching uses box IoU >= 0.5.
    This view omits vehicles missed by the primary detector. Use independently reviewed full frames to measure recall.
    Scores are not measured accuracy. The OCR-only CPU timings are not comparable to GPU detector-plus-OCR timings.</p>
    <pre>''' + html.escape(json.dumps(summary, indent=2)) + '</pre><table><tr><th>Primary crop / frame</th>'
    + headers + '</tr>' + ''.join(cards) + '</table>')
  args.output.with_suffix('.json').write_text(json.dumps(summary, indent=2) + '\n')
  print(f'Wrote {args.output}')


if __name__ == '__main__':
  main()
