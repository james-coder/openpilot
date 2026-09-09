"""Create a protection candidate from supplied measurements; never invent evidence.

Calibration input uses ProtectionCalibration's JSON fields, with profile_id
omitted from authority. The evidence file is optional and must bind to the newly
computed source/calibration identity. Without evidence every mode stays blocked.
"""

import argparse
import json
from pathlib import Path

from opendbc.car.gm.volt_protection import ProtectionAuthority
from openpilot.selfdrive.car.volt_protection import REQUIRED_ROAD, REQUIRED_TEST, make_bundle, read_bundle
from openpilot.selfdrive.controls.lib.volt_collision import Envelope
from openpilot.selfdrive.controls.lib.volt_protection import ProtectionCalibration


def create_candidate(calibration, evidence=None):
  data = dict(calibration)
  data['envelope'] = Envelope(**data['envelope'])
  authority = {k: v for k, v in data['authority'].items() if k != 'profile_id'}
  data['authority'] = ProtectionAuthority(profile_id='0'*64, **authority)
  cal = ProtectionCalibration(**data)
  if not cal.valid():
    raise ValueError('Calibration is incomplete or outside existing command limits')
  bundle = make_bundle(cal, evidence.get('evidence', evidence) if evidence else None)
  verified = read_bundle(raw=json.dumps(bundle))
  if verified is None:
    raise ValueError('Source or calibration validation failed')
  return bundle, verified['readiness']


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('calibration', type=Path)
  parser.add_argument('output', type=Path)
  parser.add_argument('--evidence', type=Path)
  args = parser.parse_args()
  bundle, readiness = create_candidate(json.loads(args.calibration.read_text()), json.loads(args.evidence.read_text()) if args.evidence else None)
  args.output.write_text(json.dumps(bundle, indent=2, allow_nan=False)+'\n')
  template = args.output.with_name(f'protection-evidence-{bundle["id"][:12]}.json')
  if not template.exists():
    template.write_text(json.dumps({'profile_id': bundle['id'], 'evidence': {
      key: {'pass': None, 'profile_id': bundle['id'], 'archive_sha256': None} for key in ('runtime', *REQUIRED_TEST, *REQUIRED_ROAD)
    }}, indent=2)+'\n')
  print(json.dumps({'id': bundle['id'], **readiness}))
