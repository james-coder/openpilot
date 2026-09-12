"""Serialized parked emissions + GM + EGR evidence scan. No actuator controls."""
import copy
from datetime import UTC, datetime

from opendbc.car.can_definitions import CanData
from opendbc.car.uds import CanClient, IsoTpMessage
from openpilot.selfdrive.car.gm_diagnostics import CONTEXT_QUERIES, GM_RESPONSES, GM_NEGATIVES, GmDiagnosticScanner, context_request
from openpilot.selfdrive.car.gm_egr_data import MAX_CAPTURE, MAX_REPLY, QUERIES, decode, supported
from openpilot.selfdrive.car.obd_scan import MAX_FRAMES_PER_TICK, ObdScanner

TOTAL_TIMEOUT = 120.
MAX_REQUESTS = 48
FLOW_CONTROL = bytes.fromhex('30 00 0A 00 00 00 00 00')


class GmEgrScanner:
  def __init__(self):
    self.active = False
    self.revision = 0
    self.report = None
    self.status = {'version': 2, 'profile': 'gm', 'state': 'idle', 'message': 'Ready for read-only EGR evidence.'}
    self._out = []

  def start(self, request_id, vehicle, now):
    if self.active:
      return
    self.active = True
    self.report = None
    self._started = now
    self._requests = 0
    self._phase = 'emissions'
    self._index = -1
    self._out = []
    self._buffer = []
    self._message = None
    self._last_tx = -100.
    self.status = {'version': 2, 'profile': 'gm', 'state': 'scanning', 'request_id': request_id, 'vehicle': vehicle,
                   'timestamp': datetime.now(UTC).isoformat(), 'started_mono': now,
                   'modules': {}, 'context': {}, 'emissions': {}, 'readings': {},
                   'evidence': [], 'message': 'Reading emissions codes before EGR evidence...', 'progress': 0,
                   'coverage': 'Connected bus 0 only; silent modules and other networks are unverified.',
                   'limitations': 'Stored test results and sequential parked snapshots, not an actuator test or driving-safety assessment.'}
    self._child = ObdScanner()
    self._child.start(request_id, vehicle, now)
    self.revision += 1

  def cancel(self, reason='Scan cancelled.'):
    if self.active:
      self.active = False
      self._child.cancel(reason)
      self._out.clear()
      self._buffer.clear()
      self._message = None
      self.status.update(state='cancelled', message=reason)
      self.revision += 1

  def _finish(self, error=None):
    self.active = False
    self._out.clear()
    self._buffer.clear()
    self._message = None
    self._child.cancel('Session finished')
    readings = self.status['readings']
    ids, cvns = readings.get('09:04', {}).get('calibration_ids', []), readings.get('09:06', {}).get('cvns', [])
    self.status['calibration_pairing'] = 'ordered' if ids and len(ids) == len(cvns) else 'unavailable_or_count_mismatch'
    # Never derive actual position from the legacy relative-error PID.
    if readings.get('01:2C', {}).get('value') == 0 and readings.get('01:2D', {}).get('state') == 'ok':
      readings['01:2D'].update(state='not_applicable', error='Relative EGR error at zero command is not a flow measurement')
      readings['01:2D'].pop('value', None)
    success = (any(m.get('state') == 'ok' or m.get('codes') for m in self.status['modules'].values()) or
               any(r.get('state') == 'ok' for r in readings.values()) or
               any(r.get('state') == 'ok' for ecu in self.status['emissions'].get('ecus', {}).values() for r in ecu.values()))
    self.status.update(state='error' if error or not success else 'partial', progress=1.,
                       message=error or 'Read-only scan finished; inspect support and coverage limitations.')
    if success and not error:
      self.report = copy.deepcopy(self.status)
    self.revision += 1

  def _capture(self, direction, now, frame):
    addr, data, bus = frame
    if bus != 0 or not (addr in GM_RESPONSES or addr in GM_NEGATIVES or 0x7DF <= addr <= 0x7EF or addr == 0x101):
      return True
    if len(self.status['evidence']) >= MAX_CAPTURE:
      self.cancel('Diagnostic evidence limit reached; scan stopped without replacing saved results.')
      return False
    self.status['evidence'].append({'direction': direction, 'mono': now, 'address': addr, 'bus': bus, 'data': data.hex()})
    return True

  def _send(self, addr, data, bus):
    if (addr, data, bus) != (0x7E0, FLOW_CONTROL, 0):
      raise ValueError('Unexpected EGR transmission')
    self._out.append(CanData(addr, data, bus))

  def _receive(self):
    frames, self._buffer = self._buffer, []
    return frames

  def _next(self, now):
    self._message = None
    self._buffer.clear()
    readings = self.status['readings']
    while self._index + 1 < len(QUERIES):
      self._index += 1
      service, pid = QUERIES[self._index]
      self._key = f'{service:02X}:{pid:02X}'
      request = bytes([service, pid])
      result = {'state': 'timeout', 'request': request.hex(), 'error': 'No response; support unknown'}
      readings[self._key] = result
      support = supported(readings, service, pid)
      if support is not True:
        result.update(state='unsupported' if support is False else 'unknown', error='Not advertised' if support is False else 'Support discovery unavailable')
        continue
      client = CanClient(self._send, self._receive, 0x7E0, 0x7E8, 0)
      self._message = IsoTpMessage(client, timeout=0, separation_time=.01)
      self._message.send(request, setup_only=True)
      self._request = request
      self._deadline = now + (8. if (service, pid) in ((1, 0x69), (6, 0x31), (9, 4), (9, 6)) else 2.)
      self._out.append(context_request(service, pid))
      self.status.update(message=f'Reading ECM {self._key} (read-only)...', progress=.4 + .6 * self._index / len(QUERIES))
      self.revision += 1
      return
    self._finish()

  def _engine(self, now, frames):
    if self._message is None:
      if now - self._last_tx >= .5:
        self._next(now)
      return
    if now >= self._deadline:
      self._next(now)
      return  # Discard frames queued for the previous query.
    result = self.status['readings'][self._key]
    if result['state'] != 'timeout':
      return
    for addr, data, bus in frames:
      if (addr, bus) != (0x7E8, 0):
        continue
      try:
        if len(data) != 8:
          raise ValueError('Malformed CAN frame length')
        kind = data[0] >> 4
        if kind not in (0, 1, 2):
          raise ValueError('Unexpected ISO-TP frame type')
        if kind == 0 and not 1 <= data[0] <= 7:
          raise ValueError('Invalid ISO-TP single-frame length')
        if kind == 1 and not 8 <= ((data[0] & 15) << 8 | data[1]) <= MAX_REPLY:
          raise ValueError('Invalid or excessive ISO-TP length')
        if kind in (0, 1):
          offset = 1 if kind == 0 else 2
          prefix = data[offset:offset + 2]
          if prefix not in (bytes([self._request[0] + 0x40, self._request[1]]), bytes([0x7F, self._request[0]])):
            continue  # Must match before allowing flow control.
        self._buffer.append(CanData(addr, data, bus))
        reply, _ = self._message.recv()
        if reply is None:
          continue
        result.update(raw=reply.hex(), read_mono=now)
        if reply[:1] == b'\x7f':
          if len(reply) != 3 or reply[1] != self._request[0]:
            raise ValueError('Malformed negative reply')
          if reply[2] == 0x78:
            self._message.send(self._request, setup_only=True)
            continue
          result.update(state='unsupported' if reply[2] in (0x11, 0x12, 0x31) else 'error', error=f'ECU response 0x{reply[2]:02X}')
        else:
          result.update(decode(*self._request, reply), state='ok')
          result.pop('error', None)
        self.revision += 1
        break
      except (AssertionError, ValueError, IndexError) as error:
        self._out.clear()
        result.update(state='error', error=str(error) or 'Malformed reply')
        self.revision += 1
        break

  def tick(self, now, frames, block_reason):
    if not self.active:
      return []
    if block_reason or len(frames) > MAX_FRAMES_PER_TICK:
      self.cancel(block_reason or 'Excessive diagnostic traffic')
      return []
    if now - self._started >= TOTAL_TIMEOUT:
      self._finish('Session deadline reached; previous saved report retained.')
      return []
    for frame in frames:
      if not self._capture('rx', now, frame):
        return []
    if self._phase in ('emissions', 'gm'):
      self._out.extend(self._child.tick(now, frames, ''))
      self.status['message'] = self._child.status.get('message', '')
      if not self._child.active:
        if self._phase == 'emissions':
          self.status['emissions'] = copy.deepcopy(self._child.status)
          self._phase = 'gm'
          self._child = GmDiagnosticScanner(tuple(q for q in CONTEXT_QUERIES if q[0] == 2))
          self._child.start(self.status['request_id'], self.status['vehicle'], now)
        else:
          self.status.update({key: copy.deepcopy(self._child.status[key]) for key in ('modules', 'context')})
          self._phase = 'engine'
    else:
      self._engine(now, frames)
    if not self.active:
      return []
    for frame in self._out:
      if frame.dat != FLOW_CONTROL:
        self._requests += 1
        if self._requests > MAX_REQUESTS:
          self.cancel('Request limit exceeded')
          return []
        self._last_tx = now
      if not self._capture('tx', now, frame):
        return []
    out, self._out = self._out, []
    return out
