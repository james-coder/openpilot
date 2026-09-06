"""Extract original frame timing and health summaries using openpilot's environment."""
import argparse
import json
from pathlib import Path

import zstandard
from cereal import log


def index_segment(folder: Path) -> dict:
  result = {'frames': {'fcamera': {}, 'ecamera': {}}, 'clock_offset_ns': None, 'warnings': [],
            'memory_max_percent': 0, 'events': {}}
  path = folder / 'rlog.zst'
  if not path.exists():
    result['warnings'].append('rlog missing: frame timing unavailable')
    return result
  with path.open('rb') as f, zstandard.ZstdDecompressor().stream_reader(f) as reader:
    data = reader.read()
  offsets = []
  try:
    for e in log.Event.read_multiple_bytes(data):
      kind = e.which()
      if kind in ('roadEncodeIdx', 'wideRoadEncodeIdx'):
        idx = getattr(e, kind)
        if idx.segmentNum != int(folder.name.rsplit('--', 1)[1]):
          continue
        camera = 'fcamera' if kind == 'roadEncodeIdx' else 'ecamera'
        result['frames'][camera][idx.segmentId] = {'frame_id': idx.frameId, 'mono_ns': idx.timestampSof}
      elif kind == 'clocks' and e.clocks.wallTimeNanos > 1577836800 * 10**9:  # Reject an unset/pre-2020 clock.
        offsets.append(e.clocks.wallTimeNanos - e.logMonoTime)
      elif kind == 'deviceState':
        result['memory_max_percent'] = max(result['memory_max_percent'], e.deviceState.memoryUsagePercent)
      elif kind == 'onroadEvents':
        for event in e.onroadEvents:
          name = str(event.name)
          result['events'][name] = result['events'].get(name, 0) + 1
  except Exception as e:
    result['warnings'].append(f'truncated or unreadable log: {type(e).__name__}')
  if offsets and max(offsets) - min(offsets) < 100_000_000:
    result['clock_offset_ns'] = sorted(offsets)[len(offsets) // 2]
  else:
    result['warnings'].append('wall clock unavailable or discontinuous; use monotonic timing')
  return result


def main():
  p = argparse.ArgumentParser(description=__doc__)
  p.add_argument('dataset', type=Path)
  args = p.parse_args()
  manifest = json.loads((args.dataset / 'manifest.json').read_text())
  complete = []
  for row in manifest['segments']:
    folder = args.dataset / row['segment']
    if f'{folder.name}/rlog.zst' not in manifest['verified']:
      continue
    result = index_segment(folder)
    (folder / 'index.json').write_text(json.dumps(result) + '\n')
    complete.append({'segment': folder.name, **{k: v for k, v in result.items() if k != 'frames'}})
  (args.dataset / 'health.json').write_text(json.dumps(complete, indent=2) + '\n')
  print(f'Indexed {len(complete)} segments')


if __name__ == '__main__':
  main()
