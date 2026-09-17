import math
import re

from openpilot.selfdrive.car.gm_diagnostics import CONTEXT_QUERIES, GM_RESPONSES, MAX_CODES, PIDS
from openpilot.selfdrive.ui.layouts.settings.obd_diagnostics_data import descriptions


def valid_gm_report(report, vehicle):
  if not isinstance(report, dict) or report.get('version') != 1 or not isinstance(vehicle, dict) or report.get('vehicle') != vehicle:
    return None
  if report.get('profile') != 'gm':
    return None
  modules, context = report.get('modules', {}), report.get('context', {})
  if not isinstance(modules, dict) or len(modules) > len(GM_RESPONSES) or not isinstance(context, dict) or len(context) > len(CONTEXT_QUERIES):
    return None
  for addr, module in modules.items():
    if addr not in {f'{a:03X}' for a in GM_RESPONSES} or not isinstance(module, dict):
      return None
    if module.get('state') not in ('ok', 'incomplete', 'unsupported', 'error'):
      return None
    if not isinstance(module.get('codes'), list) or len(module['codes']) > MAX_CODES:
      return None
    if module['state'] == 'ok' and (type(module.get('status_mask')) is not int or not 0 <= module['status_mask'] <= 255):
      return None
    for code in module['codes']:
      if not isinstance(code, dict) or not isinstance(code.get('code'), str) or not re.fullmatch(r'[PCBU][0-3][0-9A-F]{3}', code['code']):
        return None
      if any(type(code.get(k)) is not int or not 0 <= code[k] <= 255 for k in ('failure_type', 'status')):
        return None
      if not isinstance(code.get('raw'), str) or not re.fullmatch(r'(?:[0-9a-f]{2}){5,8}', code['raw']):
        return None
  for key, result in context.items():
    if key not in {f'{service:02X}:{pid:02X}' for service, pid in CONTEXT_QUERIES} or not isinstance(result, dict):
      return None
    if result.get('state') not in ('ok', 'timeout', 'error', 'unsupported'):
      return None
    if result['state'] == 'ok':
      value = result.get('value')
      if key == '02:02':
        if value is not None and (not isinstance(value, str) or not re.fullmatch(r'[PCBU][0-3][0-9A-F]{3}', value)):
          return None
      elif type(value) not in (int, float) or not math.isfinite(value):
        return None
      if not isinstance(result.get('raw'), str) or not re.fullmatch(r'(?:[0-9a-f]{2}){2,7}', result['raw']):
        return None
  return report


def gm_rows(report):
  modules = report.get('modules', {})
  finished = sum(m['state'] == 'ok' for m in modules.values())
  rows = [('Coverage: bus 0 only', f'{finished}/{len(modules)} finished',
           'Other networks and silent modules are unverified. Lists require an end marker; silence does not mean no faults.')]
  for addr, module in sorted(modules.items(), key=lambda item: (not bool(item[1]['codes']), item[0])):
    # CAN response address is identity here. It is not a verified module name.
    for code in sorted(module['codes'], key=lambda c: (c['code'], c['failure_type'])):
      flags = [label for bit, label in ((2, 'Current'), (16, 'History'), (128, 'Warning requested')) if code['status'] & bit]
      title = f"{code['code']}-{code['failure_type']:02X}"
      detail = f"Module 0x{addr}; GM status 0x{code['status']:02X}. " + descriptions().get(code['code'], 'Description unavailable.')
      detail += f" {', '.join(flags)}. Suffix: reported failure-type byte, not a confirmed failed part."
      rows.append((title, ', '.join(flags), detail))
    if not module['codes'] or module['state'] != 'ok':
      rows.append((f'GM module 0x{addr}', module['state'].title(), 'Bus 0, GM UUDT response address. ' + module.get('error', 'End-of-report received.')))
    if module['state'] == 'ok' and not module['codes']:
      readable = module['status_mask'] & 0x12 == 0x12
      rows.append(('No matching faults' if readable else 'Status coverage limited', '',
                   f"Module 0x{addr}: requested current/history mask 0x12; supported mask 0x{module['status_mask']:02X}."))
  context = report.get('context', {})
  trigger = context.get('02:02', {})
  association = trigger.get('value') if trigger.get('state') == 'ok' else 'not read'
  rows.append(('Engine freeze frame', str(association or 'No trigger code'),
               'ECU 0x7E8, frame 0. Historical data; only the trigger code identifies the associated fault.'))
  for service, pid in CONTEXT_QUERIES:
    result = context.get(f'{service:02X}:{pid:02X}', {})
    label, _, unit = PIDS[pid]
    if service == 1 and pid == 5:
      rows.append(('Parked engine snapshot', 'Not a road test',
                   'Values were read at different times while parked. Zero RPM or zero EGR command does not establish an EGR fault.'))
    if result.get('state') == 'ok':
      value = result['value']
      text = f'{value:.2f} {unit}'.strip() if type(value) in (int, float) else str(value or 'None')
      detail = f"ECU 0x7E8, service {service:02X}, PID {pid:02X}, raw {result['raw']}."
    else:
      text = 'Not read'
      detail = result.get('error', 'Not collected.')
    rows.append((label, text, detail))
  return rows
