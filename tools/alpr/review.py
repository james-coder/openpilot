"""Create a local, prediction-blind frame annotation page with downloadable labels."""
import argparse
import json
import os
from pathlib import Path

from openpilot.tools.alpr.evaluate import split_for_route



def main():
  p = argparse.ArgumentParser(description=__doc__)
  p.add_argument('run', type=Path)
  p.add_argument('--output', required=True, type=Path)
  p.add_argument('--stride', type=int, default=5, help='Review every Nth one-second context frame')
  args = p.parse_args()
  if args.stride < 1:
    p.error('stride must be positive')
  frames = []
  for folder in sorted(args.run.glob('*/*')):
    if not folder.is_dir():
      continue
    for path in sorted(folder.glob('context-*.jpg'))[::args.stride]:
      frames.append({'segment': folder.parent.name, 'camera': folder.name,
                     'frame': int(path.stem.split('-')[1]), 'split': split_for_route(folder.parent.name),
                     'image': os.path.relpath(path.resolve(), args.output.parent.resolve()),
                     'reviewed': False, 'lighting': 'unknown', 'plates': []})
  data = {'version': 1, 'dataset_id': str(args.run.resolve()), 'frames': frames}
  args.output.parent.mkdir(parents=True, exist_ok=True)
  page = Path(__file__).with_suffix('.html').read_text()
  args.output.write_text(page.replace('DATA', json.dumps(data).replace('<', '\\u003c')))
  print(f'Created {args.output} with {len(frames)} unlabeled frames')


if __name__ == '__main__':
  main()
