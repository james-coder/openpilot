"""Export an existing parked scan for a mechanic. Does not connect to CAN or start scans."""
import argparse
import copy
import json
from pathlib import Path

from cereal import car
from openpilot.common.params import Params
from openpilot.selfdrive.car.gm_egr_data import MAX_REPORT_BYTES
from openpilot.selfdrive.ui.layouts.settings.gm_diagnostics_data import gm_rows, valid_gm_report


def export_report(report, *, include_vin=False, as_json=False):
  report = copy.deepcopy(report)
  if not include_vin:
    def redact(value):
      if isinstance(value, dict):
        value.pop('vin', None)
        for child in value.values():
          redact(child)
      elif isinstance(value, list):
        for child in value:
          redact(child)
    redact(report)
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
  parser.add_argument('--report', type=Path, help='Read an archived GM report instead of the latest saved result')
  args = parser.parse_args()
  params = Params()
  raw_cp = params.get('CarParamsPersistent')
  if not raw_cp:
    parser.error('No saved vehicle identification available')
  with car.CarParams.from_bytes(raw_cp) as CP:
    vehicle = {'fingerprint': CP.carFingerprint, 'vin': CP.carVin}
  raw_report = params.get('GmLastScan')
  if args.report:
    try:
      with args.report.open('rb') as stream:
        payload = stream.read(MAX_REPORT_BYTES + 1)
      if len(payload) > MAX_REPORT_BYTES:
        parser.error('Archived report exceeds size limit')
      raw_report = json.loads(payload)
    except (OSError, ValueError) as error:
      parser.error(f'Cannot read archived report: {error}')
  report = valid_gm_report(raw_report, vehicle)
  if not report:
    parser.error('No valid saved GM scan for this vehicle')
  print(export_report(report, include_vin=args.include_vin, as_json=args.json))


if __name__ == '__main__':
  main()
