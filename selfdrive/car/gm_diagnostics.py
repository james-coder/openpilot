"""Bounded GM read-only diagnostics; GMW3110 sections 4.4 and 8.18.

No tester-present, session changes, clearing, unlocking, or device controls.
Coverage is the connected bus, not an assertion that every vehicle module was read.
"""
import copy
from datetime import UTC, datetime

from opendbc.car.can_definitions import CanData

GM_DIAGNOSTIC_FLAG = 16
GM_REQUEST = bytes.fromhex('FE 03 A9 81 12 00 00 00')
GM_RESPONSES = (*range(0x541, 0x560), *range(0x5E8, 0x5F0))
GM_NEGATIVES = (*range(0x641, 0x660), *range(0x7E8, 0x7F0))
MAX_GM_FRAMES = 128
MAX_CODES = 128
MODULE_TIMEOUT = 5.
CONTEXT_TIMEOUT = 2.
TOTAL_TIMEOUT = 50.
# PID: (label, payload bytes, unit). Freeze frame zero, and a parked live snapshot.
PIDS = {
  0x02: ('Freeze-frame trigger code', 2, ''),
  0x04: ('Engine load', 1, '%'),
  0x05: ('Coolant temperature', 1, 'C'),
  0x06: ('Short-term fuel trim, bank 1', 1, '%'),
  0x07: ('Long-term fuel trim, bank 1', 1, '%'),
  0x0B: ('Intake manifold pressure', 1, 'kPa'),
  0x0C: ('Engine speed', 2, 'rpm'),
  0x0D: ('Vehicle speed', 1, 'km/h'),
  0x0F: ('Intake air temperature', 1, 'C'),
  0x10: ('Mass airflow', 2, 'g/s'),
  0x2C: ('Commanded EGR', 1, '%'),
  0x2D: ('EGR error', 1, '%'),
}
CONTEXT_QUERIES = tuple((2, pid) for pid in PIDS) + tuple((1, pid) for pid in (0x05, 0x0C, 0x2C, 0x2D))


def code_text(hi, lo):
  return f'{"PCBU"[hi >> 6]}{(hi >> 4) & 3}{hi & 15:X}{lo:02X}'


def context_request(service, pid):
  payload = bytes([service, pid]) + (b'\x00' if service == 2 else b'')
  return CanData(0x7E0, (bytes([len(payload)]) + payload).ljust(8, b'\x00'), 0)


def context_value(pid, data):
  if len(data) != PIDS[pid][1]:
    raise ValueError('Wrong PID data length')
  a = data[0]
  if pid == 2:
    return code_text(*data) if any(data) else None
  if pid in (5, 15):
    return a - 40
  if pid in (6, 7, 0x2D):
    return (a - 128) * 100 / 128
  if pid in (4, 0x2C):
    return a * 100 / 255
  if pid in (0x0C, 0x10):
    return int.from_bytes(data, 'big') / (4 if pid == 0x0C else 100)
  return a


class GmDiagnosticScanner:
  def __init__(self, queries=CONTEXT_QUERIES):
    self.queries = queries
    self.active = False
    self.revision = 0
    self.report = None
    self.status = {'version': 1, 'profile': 'gm', 'state': 'idle', 'message': 'Ready for GM diagnostics.'}
    self._out = []

  def start(self, request_id, vehicle, now):
    if self.active:
      return
    self.active = True
    self.report = None
    self._started = now
    self._deadline = now + MODULE_TIMEOUT
    self._stage = -1
    self._out = [CanData(0x101, GM_REQUEST, 0)]
    self.status = {'version': 1, 'profile': 'gm', 'request_id': request_id, 'vehicle': vehicle,
                   'state': 'scanning', 'timestamp': datetime.now(UTC).isoformat(), 'ecus': {}, 'modules': {}, 'context': {},
                   'message': 'Reading GM current/history faults on bus 0...', 'progress': 0,
                   'coverage': 'Connected high-speed bus 0 only. Other networks, sleeping modules and gateway-only modules are not verified.'}
    self.revision += 1

  def cancel(self, reason='Scan cancelled.'):
    if self.active:
      self.active = False
      self._out.clear()
      self.status.update(state='cancelled', message=reason)
      self.revision += 1

  def _module(self, addr, data):
    if addr in GM_NEGATIVES:
      if len(data) != 8 or data[:3] != b'\x03\x7f\xa9':
        return
      addr = addr - (0x200 if addr >= 0x7E8 else 0x100)
      if data[3] == 0x78:
        result = {'state': 'incomplete', 'error': 'ECU pending; fixed deadline applies'}
      else:
        result = {'state': 'unsupported' if data[3] in (0x11, 0x12, 0x31) else 'error', 'error': f'ECU response 0x{data[3]:02X}'}
      module = self.status['modules'].setdefault(f'{addr:03X}', {'state': 'incomplete', 'codes': []})
      if module['state'] == 'incomplete':
        module.update(result)
        module['negative_raw'] = data.hex()
      self.revision += 1
      return
    if addr not in GM_RESPONSES or not data or data[0] != 0x81:
      return
    module = self.status['modules'].setdefault(f'{addr:03X}', {'state': 'incomplete', 'codes': []})
    if module['state'] in ('error', 'unsupported'):
      return
    if not 5 <= len(data) <= 8:
      module.update(state='error', error='Malformed GM fault response')
    elif data[1:4] == b'\x00\x00\x00':
      module.update(state='ok', status_mask=data[4], end_raw=data.hex())
      module.pop('error', None)
    elif module['state'] == 'ok':
      module.update(state='error', error='Fault record after end-of-report marker')
    else:
      entry = {'code': code_text(data[1], data[2]), 'failure_type': data[3], 'status': data[4], 'raw': data.hex()}
      if not data[4] & 0x12 or not any(data[1:3]):
        module.update(state='error', error='Fault record does not match requested current/history mask')
      elif not any(all(old[key] == entry[key] for key in ('code', 'failure_type', 'status')) for old in module['codes']):
        if len(module['codes']) >= MAX_CODES:
          module.update(state='error', error='Fault list truncated at safety limit')
        else:
          module['codes'].append(entry)
    self.revision += 1

  def _next(self, now):
    self._stage += 1
    if self._stage >= len(self.queries):
      self._finish()
      return
    service, pid = self.queries[self._stage]
    self._key = f'{service:02X}:{pid:02X}'
    self.status['context'][self._key] = {'state': 'timeout', 'error': 'No response; not assumed unsupported'}
    self.status.update(progress=self._stage + 1, message=f'Reading {"freeze frame" if service == 2 else "parked snapshot"}: {PIDS[pid][0]}')
    self._deadline = now + CONTEXT_TIMEOUT
    self._out.append(context_request(service, pid))
    self.revision += 1

  def _context(self, data, now):
    result = self.status['context'][self._key]
    if result['state'] != 'timeout':
      return
    service, pid = self.queries[self._stage]
    if len(data) != 8 or not 1 <= data[0] <= 7:
      return
    reply = data[1:1 + data[0]]
    if reply[:2] == bytes([0x7F, service]):
      result['raw'] = reply.hex()
      if len(reply) != 3:
        result.update(state='error', error='Malformed negative response')
      elif reply[2] != 0x78:
        result.update(state='unsupported' if reply[2] in (0x11, 0x12, 0x31) else 'error', error=f'ECU response 0x{reply[2]:02X}')
      else:
        return
    else:
      prefix = bytes([service + 0x40, pid]) + (b'\x00' if service == 2 else b'')
      if not reply.startswith(prefix):
        return
      try:
        value = context_value(pid, reply[len(prefix):])
        result.clear()
        result.update(state='ok', value=value, raw=reply.hex(), read_mono=now, unit=PIDS[pid][2])
      except ValueError as e:
        result.update(state='error', error=str(e))
    self.revision += 1

  def _finish(self):
    self.active = False
    self._out.clear()
    modules = self.status['modules']
    for module in modules.values():
      if module['state'] == 'incomplete':
        module['error'] = 'No end-of-report marker; list may be incomplete'
    success = any(m['state'] == 'ok' or m['codes'] for m in modules.values()) or any(r['state'] == 'ok' for r in self.status['context'].values())
    # A broadcast cannot establish a complete inventory of the vehicle.
    self.status.update(state='partial' if success else 'error', message='GM scan finished; see coverage and unread items below.',
                       progress=len(self.queries) + 1)
    if success:
      self.report = copy.deepcopy(self.status)
    self.revision += 1

  def tick(self, now, frames, block_reason):
    if not self.active:
      return []
    if block_reason or len(frames) > MAX_GM_FRAMES:
      self.cancel(block_reason or 'Excessive diagnostic traffic; scan stopped.')
      return []
    if now - self._started >= TOTAL_TIMEOUT:
      self._finish()
      return []
    if now >= self._deadline:
      self._next(now)
      frames = []  # Never assign a previous stage's queued frames to a new request.
    if not self.active:
      return []
    for addr, data, bus in frames:
      if bus != 0:
        continue
      if self._stage < 0:
        self._module(addr, data)
      elif addr == 0x7E8:
        self._context(data, now)
    out, self._out = self._out, []
    return out
