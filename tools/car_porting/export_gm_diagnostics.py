"""Export an existing parked scan for a mechanic. Does not connect to CAN or start scans."""
import argparse
import copy
import json

from cereal import car
from openpilot.common.params import Params
from openpilot.selfdrive.ui.layouts.settings.gm_diagnostics_data import gm_rows, valid_gm_report


def export_report(report, *, include_vin=False, as_json=False):
  report = copy.deepcopy(report)
  if not include_vin:
    report.get('vehicle', {}).pop('vin', None)
  if as_json:
    return json.dumps(report, indent=2, sort_keys=True)
  lines = ['GM read-only diagnostic report', f"Scan time: {report.get('timestamp', 'unknown')}",
           'This is not a complete vehicle inspection or a determination of driving safety.', '']
  for title, value, detail in gm_rows(report):
    lines.extend([f'{title}: {value}', detail, ''])
  if include_vin:
    lines.append(f"VIN: {report.get('vehicle', {}).get('vin', 'unknown')}")
  return '\n'.join(lines)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--json', action='store_true', help='Include raw response data in JSON output')
  parser.add_argument('--include-vin', action='store_true', help='Explicitly include vehicle VIN (omitted by default)')
  args = parser.parse_args()
  params = Params()
  raw_cp = params.get('CarParamsPersistent')
  if not raw_cp:
    parser.error('No saved vehicle identification available')
  with car.CarParams.from_bytes(raw_cp) as CP:
    vehicle = {'fingerprint': CP.carFingerprint, 'vin': CP.carVin}
  report = valid_gm_report(params.get('GmLastScan'), vehicle)
  if not report:
    parser.error('No valid saved GM scan for this vehicle')
  print(export_report(report, include_vin=args.include_vin, as_json=args.json))


if __name__ == '__main__':
  main()
