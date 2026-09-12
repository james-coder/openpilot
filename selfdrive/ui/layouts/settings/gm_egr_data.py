"""Validation and presentation of version-2 evidence; never touches CAN."""
import json
import math
import re

from openpilot.selfdrive.car.gm_diagnostics import PIDS
from openpilot.selfdrive.car.gm_egr_data import MAX_CAPTURE, MAX_REPLY, MAX_REPORT_BYTES, QUERY_KEYS, decode
from openpilot.selfdrive.ui.layouts.settings.obd_diagnostics_data import valid_report, result_rows


def valid_egr(report, vehicle):
  try:
    if len(json.dumps(report, allow_nan=False).encode()) > MAX_REPORT_BYTES:
      return False
    if report.get('calibration_pairing') not in (None, 'ordered', 'unavailable_or_count_mismatch'):
      return False
    if 'archive_error' in report and not isinstance(report['archive_error'], str):
      return False
    emissions = report.get('emissions', {})
    if emissions and valid_report(emissions, vehicle) is None:
      return False
    readings, evidence = report.get('readings'), report.get('evidence')
    if not isinstance(readings, dict) or not readings.keys() <= QUERY_KEYS or not isinstance(evidence, list) or len(evidence) > MAX_CAPTURE:
      return False
    for key, result in readings.items():
      if not isinstance(result, dict) or result.get('state') not in ('ok', 'timeout', 'unknown', 'unsupported', 'error', 'not_applicable'):
        return False
      if result.get('request') != key.replace(':', '').lower() or not isinstance(result.get('error', ''), str):
        return False
      raw = result.get('raw')
      if raw is not None and (not isinstance(raw, str) or not re.fullmatch(r'(?:[0-9a-f]{2}){1,4095}', raw)):
        return False
      if result['state'] in ('ok', 'not_applicable'):
        if type(result.get('read_mono')) not in (int, float) or not math.isfinite(result['read_mono']):
          return False
        if not isinstance(raw, str) or len(raw) > 2 * MAX_REPLY:
          return False
        expected = decode(*(int(part, 16) for part in key.split(':')), bytes.fromhex(raw))
        if result['state'] == 'not_applicable':
          if key != '01:2D' or readings.get('01:2C', {}).get('value') != 0:
            return False
          expected.pop('value', None)
        if any(result.get(name) != value for name, value in expected.items()):
          return False
    for frame in evidence:
      if not isinstance(frame, dict) or frame.get('direction') not in ('rx', 'tx') or frame.get('bus') != 0:
        return False
      if type(frame.get('address')) is not int or not 0 <= frame['address'] <= 0x7FF:
        return False
      if not isinstance(frame.get('data'), str) or not re.fullmatch(r'(?:[0-9a-f]{2}){0,8}', frame['data']):
        return False
      if type(frame.get('mono')) not in (float, int) or not math.isfinite(frame['mono']):
        return False
    return True
  except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
    return False


def egr_rows(report):
  rows = [('EGR evidence', 'Read-only', 'These are stored results and sequential parked readings, not a commanded service-bay test.')]
  if report.get('archive_error'):
    rows.append(('Archive failure', 'Not saved', report['archive_error']))
  rows.extend(result_rows(report.get('emissions', {})))
  readings = report.get('readings', {})
  priority = {'06:31': 0, '01:69': 1, '01:6B': 2, '01:01': 3, '01:41': 4}
  for key, result in sorted(readings.items(), key=lambda item: (priority.get(item[0], 5), item[0])):
    service, pid = (int(part, 16) for part in key.split(':'))
    title = f'ECM {key}'
    detail = f"Read at monotonic {result.get('read_mono', 'unavailable')}; raw {result.get('raw', 'unavailable')}."
    if result.get('state') != 'ok':
      rows.append((title, result.get('state', 'unknown').replace('_', ' ').title(), result.get('error', detail)))
    elif 'bitmap' in result:
      rows.append((title + ' support', f"{result['bitmap']:08X}", detail))
    elif 'records' in result:
      for record in result['records']:
        description = f"MID {record['mid']:02X}, TID {record['tid']:02X}, scaling {record['uasid']:02X}; raw {record['raw']}. "
        if 'value' in record:
          description += f"Value {record['value']:.3f}; inclusive limits {record['minimum']:.3f} to {record['maximum']:.3f} kPa. "
        if record['state'] == 'no_valid_result':
          description += 'May be uncompleted or an unused alternate criterion; this is not a pass.'
        title = f"EGR {'decel' if record['tid'] == 0xA8 else 'quick'} flow result ({record['tid']:02X})" if 'label' in record else 'Unmapped EGR result'
        rows.append((title, record['state'].replace('_', ' ').title(), description))
    elif 'monitors' in result:
      support = readings.get('01:01', {}).get('monitors', {})
      for name, monitor in result['monitors'].items():
        supported = monitor.get('supported', support.get(name, {}).get('supported'))
        state = 'Unsupported' if supported is False else 'Support unknown' if supported is None else 'Complete' if monitor['complete'] else 'Incomplete'
        enabled = f" Enabled this cycle: {monitor['enabled_this_cycle']}." if 'enabled_this_cycle' in monitor else ''
        rows.append((f"{name}: {result['scope'].replace('_', ' ')}", state, 'Readiness is not a passed flow test.' + enabled))
    elif 'fields' in result:
      for name, field in result['fields'].items():
        value = f"{field['value']:.2f} {field['unit']}" if 'value' in field else field['state'].replace('_', ' ').title()
        rows.append((f"PID {pid:02X} {name.replace('_', ' ')}", value, detail + ' ECU-reported field; sensor placement and adequate flow are not established.'))
    elif 'calibration_ids' in result or 'cvns' in result:
      values = result.get('calibration_ids', result.get('cvns', []))
      rows.append(('Fresh calibration IDs' if pid == 4 else 'Fresh CVNs', ', '.join(values), detail))
    elif 'value' in result:
      label = PIDS[pid][0] if pid in PIDS else 'Barometric pressure'
      rows.append((label, f"{result['value']:.2f} {result['unit']}", detail))
  rows.append(('Calibration pairing', report.get('calibration_pairing', 'Not finished').replace('_', ' '),
               'IDs/CVNs remain separate ordered lists; mismatched counts are never paired.'))
  rows.append(('Raw evidence', f"{len(report.get('evidence', []))} frames",
               'JSON export includes requests and replies. Acquisition time is not the time a monitor ran.'))
  return rows
