"""Package a tested calibration for the fork; never selects a mode or contacts a car."""

import argparse
import json
from pathlib import Path
from openpilot.selfdrive.car.volt_profile import BUNDLE_PATH, read_bundle
from openpilot.tools.profiling.volt_braking import atomic_json


def export_candidate(review, output=BUNDLE_PATH):
  source = review / 'candidate-bundle.json'
  bundle = read_bundle(source)
  if bundle is None:
    raise ValueError('Missing, invalid, or stale candidate. Rerun validation on the current sources.')
  atomic_json(output, json.loads(source.read_text()))
  return {'profile_id': bundle['id'], **bundle['readiness'], 'path': str(output)}


if __name__ == '__main__':
  p = argparse.ArgumentParser(description=__doc__)
  p.add_argument('review', type=Path)
  p.add_argument('--output', type=Path, default=BUNDLE_PATH)
  args = p.parse_args()
  print(json.dumps(export_candidate(args.review, args.output), indent=2))
