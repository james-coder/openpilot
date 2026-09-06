"""Join recorded radar to candidate video frames; run in the openpilot environment."""
import argparse
from collections import defaultdict
import json
from pathlib import Path

import zstandard
from cereal import log
from openpilot.tools.alpr.assisted_selection import radar_at



def main():
  p = argparse.ArgumentParser(description=__doc__)
  p.add_argument('data', type=Path)
  p.add_argument('--output', type=Path, required=True)
  args = p.parse_args()
  dataset = args.data / '2026-09-study'
  existing = defaultdict(lambda: defaultdict(list))
  for path in (args.data / 'runs/s-native-5fps').glob('*/*/predictions.jsonl'):
    if not path.with_name('complete.json').exists():
      continue
    for line in path.read_text().splitlines():
      row = json.loads(line)
      if row['box'][2] - row['box'][0] >= 40:
        existing[(row['segment'], row['camera'])][row['frame']].append(row)
  manifest = json.loads((dataset / 'manifest.json').read_text())
  result = []
  for segment in manifest['segments']:
    name = segment['segment']
    folder = dataset / name
    index = json.loads((folder / 'index.json').read_text())
    with (folder / 'rlog.zst').open('rb') as f, zstandard.ZstdDecompressor().stream_reader(f) as stream:
      raw = stream.read()
    snapshots = []
    for e in log.Event.read_multiple_bytes(raw):
      if e.which() != 'liveTracks':
        continue
      r = e.liveTracks
      snapshots.append({'mono_ns': e.logMonoTime,
                        'healthy': not (r.errors.canError or r.errors.radarFault or r.errors.radarDegraded),
                        'points': [{'id': v.trackId, 'range_m': round(v.dRel, 3), 'left_m': round(v.yRel, 3)}
                                   for v in r.points if 2 <= v.dRel <= 35 and abs(v.yRel) < 7]})
    snapshots.sort(key=lambda s: s['mono_ns'])
    times = [s['mono_ns'] for s in snapshots]
    for camera in ['fcamera', 'ecamera']:
      frames = []
      for number, stamp in index['frames'][camera].items():
        number = int(number)
        radar = radar_at(snapshots, times, stamp['mono_ns'])
        near = radar and any(3 <= p['range_m'] <= 10 and abs(p['left_m']) < 1.8 for p in radar['points'])
        prior = existing[(name, camera)][number]
        if not prior and not (camera == 'fcamera' and number % 4 == 0 and near):
          continue
        offset = index['clock_offset_ns']
        frames.append({'frame': number, 'mono_ns': stamp['mono_ns'],
                       'wall_ns': stamp['mono_ns'] + offset if offset is not None else None,
                       'radar': radar, 'existing_plates': prior})
      if frames:
        result.append({'segment': name, 'camera': camera, 'frames': sorted(frames, key=lambda f: f['frame']),
                       'source_sha256': manifest['verified'][f'{name}/{camera}.hevc']})
  args.output.parent.mkdir(parents=True, exist_ok=True)
  args.output.write_text(json.dumps({'version': 1, 'clips': result}, indent=2) + '\n')
  print(f'Prepared {sum(len(c["frames"]) for c in result)} candidate frames from {len(result)} clips')


if __name__ == '__main__':
  main()
