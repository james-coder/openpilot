"""Pure, strict decoders for the parked EGR evidence scan.

References: SAE J1979-DA OCT2011 B2/B46/B85/B87, E91, F; GM GMLAN
Mode 06 rev4. GM result names are NOT service-test activation commands.
"""
from openpilot.selfdrive.car.gm_diagnostics import PIDS, context_value

EGR_SAFETY_FLAG = 32
SUPPORT_QUERIES = ((1, 0), (1, 0x20), (1, 0x40), (1, 0x60), (6, 0), (6, 0x20), (9, 0))
LIVE_PIDS = (1, 0x41, 5, 0x0B, 0x0C, 0x0F, 0x10, 0x2C, 0x2D, 0x33, 0x69, 0x6B)
QUERIES = SUPPORT_QUERIES + ((9, 4), (9, 6), (6, 0x31)) + tuple((1, pid) for pid in LIVE_PIDS)
QUERY_KEYS = {f'{s:02X}:{p:02X}' for s, p in QUERIES}
MAX_REPLY = 4095
MAX_CAPTURE = 2048
MAX_REPORT_BYTES = 512 * 1024


def supported(readings, service, pid):
  """True/False only from a valid bitmap; None means discovery failed."""
  if pid == 0:
    return True
  base = ((pid - 1) // 32) * 32
  result = readings.get(f'{service:02X}:{base:02X}', {})
  if result.get('state') == 'unsupported':
    return False
  if result.get('state') != 'ok' or 'bitmap' not in result:
    return None
  return bool(result['bitmap'] & (1 << (32 - (pid - base))))


def readiness(pid, data):
  if len(data) != 4:
    raise ValueError('Readiness needs four data bytes')
  a, b, c, d = data
  compression = bool(b & 8)
  names = ('NMHC catalyst', 'NOx/SCR', None, 'Boost pressure', None, 'Exhaust sensor', 'PM filter', 'EGR/VVT') if compression else (
    'Catalyst', 'Heated catalyst', 'Evaporative system', 'Secondary air', 'A/C refrigerant', 'Oxygen sensor', 'Oxygen sensor heater', 'EGR/VVT')
  availability = 'supported' if pid == 1 else 'enabled_this_cycle'
  monitors = {name: {availability: bool(b & (1 << bit)), 'complete': not bool(b & (1 << (bit + 4)))}
              for bit, name in enumerate(('Misfire', 'Fuel system', 'Components'))}
  monitors.update({name: {availability: bool(c & (1 << bit)), 'complete': not bool(d & (1 << bit))}
                   for bit, name in enumerate(names) if name is not None})
  result = {'scope': 'since_clear' if pid == 1 else 'this_cycle', 'ignition': 'compression' if compression else 'spark', 'monitors': monitors}
  if pid == 1:
    result.update(mil=bool(a & 128), count=a & 127)
  return result


def egr_position(data):
  if len(data) != 7:
    raise ValueError('PID 69 needs seven data bytes')
  flags = data[0]
  fields = {}
  for bit, name in enumerate(('commanded_a', 'actual_a', 'error_a', 'commanded_b', 'actual_b', 'error_b')):
    raw = data[bit + 1]
    result = {'state': 'ok' if flags & (1 << bit) else 'unsupported', 'raw': raw, 'unit': '%'}
    if result['state'] == 'ok':
      result['value'] = (raw - 128) * 100 / 128 if bit in (2, 5) else raw * 100 / 255
    fields[name] = result
  for bank in ('a', 'b'):
    command, error = fields[f'commanded_{bank}'], fields[f'error_{bank}']
    if command.get('value') == 0 and error['state'] == 'ok':
      error.update(state='not_applicable', reason='Relative error at zero command is not a flow measurement')
      error.pop('value', None)
  return {'support_bits': flags, 'fields': fields}


def egr_temperature(data):
  if len(data) != 5:
    raise ValueError('PID 6B needs five data bytes')
  flags = data[0]
  fields = {}
  for bit, name in enumerate(('a_bank1_sensor1', 'c_bank1_sensor2', 'b_bank2_sensor1', 'd_bank2_sensor2')):
    normal, wide = bool(flags & (1 << bit)), bool(flags & (1 << (bit + 4)))
    state = 'ambiguous' if normal and wide else 'ok' if normal or wide else 'unsupported'
    result = {'state': state, 'raw': data[bit + 1], 'unit': 'C'}
    if state == 'ok':
      result['value'] = data[bit + 1] * (4 if wide else 1) - 40
    fields[name] = result
  return {'support_bits': flags, 'fields': fields}


def mode06(data):
  if not data or len(data) % 9 or len(data) // 9 > 256:
    raise ValueError('Mode 06 requires complete nine-byte records')
  records = []
  for offset in range(0, len(data), 9):
    raw = data[offset:offset + 9]
    mid, tid, uas = raw[:3]
    if mid != 0x31:
      raise ValueError('Unexpected monitor in MID 31 response')
    values = [int.from_bytes(raw[i:i + 2], 'big') for i in (3, 5, 7)]
    record = dict(zip(('test_raw', 'minimum_raw', 'maximum_raw'), values, strict=True))
    record.update(mid=mid, tid=tid, uasid=uas, raw=raw.hex(), state='unknown_scaling')
    if uas == 0xFD:
      test, low, high = [(value if value < 0x8000 else value - 0x10000) / 1000 for value in values]
      state = 'no_valid_result' if not any(values) else 'invalid_limits' if low > high else 'passed' if low <= test <= high else 'failed'
      record.update(value=test, minimum=low, maximum=high, unit='kPa', state=state)
      if tid in (0xA8, 0xA9):
        record['label'] = 'EGR Flow Decel Service Test result' if tid == 0xA8 else 'EGR Flow Quick Test result'
    elif not any(values):
      record['state'] = 'no_valid_result'
    records.append(record)
  return {'records': records}


def decode(service, pid, reply):
  prefix = bytes([service + 0x40, pid])
  if not reply.startswith(prefix):
    raise ValueError('Wrong response service/identifier')
  data = reply[2:]
  if (service, pid) in SUPPORT_QUERIES:
    if len(data) != 4:
      raise ValueError('Support bitmap needs four bytes')
    return {'bitmap': int.from_bytes(data, 'big')}
  if service == 6:
    return mode06(reply[1:])  # Each record includes its MID.
  if service == 9:
    width = 16 if pid == 4 else 4
    if not data or data[0] == 0 or len(data) != 1 + width * data[0]:
      raise ValueError('Calibration count/length mismatch')
    entries = [data[i:i + width] for i in range(1, len(data), width)]
    if pid == 4:
      entries = [entry.rstrip(b'\x00') for entry in entries]
      if any(not entry or any(b < 0x20 or b > 0x7E for b in entry) for entry in entries):
        raise ValueError('Invalid calibration ID text')
      return {'calibration_ids': [entry.decode('ascii') for entry in entries]}
    return {'cvns': [entry.hex().upper() for entry in entries]}
  if pid in (1, 0x41):
    return readiness(pid, data)
  if pid == 0x69:
    return egr_position(data)
  if pid == 0x6B:
    return egr_temperature(data)
  if pid == 0x33:
    if len(data) != 1:
      raise ValueError('BARO needs one byte')
    return {'value': data[0], 'unit': 'kPa'}
  return {'value': context_value(pid, data), 'unit': PIDS[pid][2]}
