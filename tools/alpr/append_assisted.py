"""Append a selected offline batch without replacing existing encounters or human reviews."""
import argparse
from copy import deepcopy
from datetime import datetime, UTC
import hashlib
import json
import os
from pathlib import Path


def extend_queue(current, source, selection):
  result = deepcopy(current)
  existing = {e['id'] for e in current['encounters']}
  additions = {e['id']: e for e in source['encounters']}
  selected = selection['append_ids']
  if len(selected) != len(set(selected)) or set(selected) & existing or not set(selected) <= additions.keys():
    raise ValueError('Selected additions must be unique new encounter IDs from the source queue')
  result['encounters'].extend(deepcopy(additions[key]) for key in selected)
  recommended = selection['recommended_ids']
  if len(recommended) != len(set(recommended)) or not set(recommended) <= existing | set(selected):
    raise ValueError('Recommendations must refer to unique retained or appended encounters')
  result['recommended_ids'] = recommended
  result['stats']['encounters'] = len(result['encounters'])
  result['stats']['close_encounters'] = sum(e['tier'] == 'close' for e in result['encounters'])
  result['stats']['all_observations'] = sum(e['observation_count'] for e in result['encounters'])
  return result


def atomic_write(file, raw):
  temp = file.with_suffix(file.suffix+'.tmp')
  with temp.open('wb') as stream:
    stream.write(raw)
    stream.flush()
    os.fsync(stream.fileno())
  temp.replace(file)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('queue', type=Path)
  parser.add_argument('--source', type=Path, required=True)
  parser.add_argument('--selection', type=Path, required=True)
  args = parser.parse_args()
  original = args.queue.read_bytes()
  source_bytes, selection_bytes = args.source.read_bytes(), args.selection.read_bytes()
  result = extend_queue(json.loads(original), json.loads(source_bytes), json.loads(selection_bytes))
  result.setdefault('append_batches', []).append({
    'added_at': datetime.now(UTC).isoformat(),
    'source_queue_sha256': hashlib.sha256(source_bytes).hexdigest(),
    'selection_sha256': hashlib.sha256(selection_bytes).hexdigest(),
    'prior_queue_sha256': hashlib.sha256(original).hexdigest(),
    'added_encounters': len(json.loads(selection_bytes)['append_ids']),
    'source_stats': json.loads(source_bytes)['stats'],
  })
  if args.queue.read_bytes() != original:
    raise RuntimeError('Queue changed during preparation; rerun against the current queue')
  atomic_write(args.queue.with_name('queue.previous.json'), original)
  atomic_write(args.queue, (json.dumps(result, indent=2)+'\n').encode())
  print(json.dumps({'encounters': len(result['encounters']), 'recommended': len(result['recommended_ids'])}))


if __name__ == '__main__':
  main()
