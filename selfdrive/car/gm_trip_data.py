"""Passive full-rlog extraction. DBC mappings are not proof of EGR operation."""
import math

from opendbc.car.gm.values import CAR


class TripEvidence:
  def __init__(self):
    self.volt = False
    self.buses = {}
    self.stats = {}
    self.windows = []
    self.latest_engine = None
    self.window = None
    self.first = self.last = self.last_can = None
    self.can_gaps = 0
    self.invalid = 0
    self.utc_anchor = None

  def _values(self, t, values):
    for name, value in values.items():
      if type(value) not in (int, float, bool) or not math.isfinite(value):
        self.invalid += 1
        continue
      stat = self.stats.setdefault(name, {'count': 0, 'minimum': value, 'maximum': value, 'first_mono': t, 'last_mono': t})
      stat.update(count=stat['count'] + 1, minimum=min(stat['minimum'], value), maximum=max(stat['maximum'], value), last_mono=t)
    return values

  def _close(self):
    if self.window and self.window['end_mono'] - self.window['start_mono'] >= .2 and len(self.windows) < 2000:
      self.windows.append(self.window)
    self.window = None

  def feed(self, event):
    """Yield every decoded input with its original log timestamp (no resampling)."""
    t = event.logMonoTime / 1e9
    if not math.isfinite(t) or t < 0:
      return []
    self.first = t if self.first is None else min(t, self.first)
    self.last = t if self.last is None else max(t, self.last)
    kind = event.which()
    rows = []
    if kind == 'initData' and event.initData.wallTimeNanos:
      self.utc_anchor = {'mono': t, 'utc_ns': event.initData.wallTimeNanos}
    if kind == 'carParams':
      self.volt = event.carParams.carFingerprint == CAR.CHEVROLET_VOLT
    if kind == 'can':
      if not event.valid:
        self.invalid += 1
        self.latest_engine = None
        self._close()
      if self.last_can is not None and t - self.last_can > .25:
        self.can_gaps += 1
        self.latest_engine = None
        self._close()
      self.last_can = t
      can_decode = self.volt and event.valid
      for frame in event.can:
        src = frame.src
        bus = str(src)
        self.buses[bus] = self.buses.get(bus, 0) + 1
        if not can_decode or src != 0 or frame.address not in (0xC9, 0x4C1, 0x1C4):
          continue
        data = frame.dat
        if len(data) != 8:
          continue
        values = {}
        if frame.address == 0xC9:
          rpm = int.from_bytes(data[1:3], 'big') / 4
          # Existing gm_global_a_powertrain DBC, ECMEngineStatus. No EGR field inferred.
          values = {'engine_rpm': rpm, 'engine_rotating_from_rpm': rpm > 0,
                    'dbc_throttle_percent': data[4] * 100 / 255, 'ecm_brake_pressed': bool(data[5] & 1)}
          self.latest_engine = (t, rpm)
        elif frame.address == 0x4C1:
          values = {'dbc_coolant_c': data[2] - 40}
        elif frame.address == 0x1C4:
          values = {'pedal_normalized': data[5] / 254}
        if values:
          rows.append({'mono': t, 'source': f'CAN bus 0 {frame.address:03X}', 'values': self._values(t, values)})
    elif kind == 'carState' and self.volt:
      cs = event.carState
      valid = event.valid and cs.canValid and all(math.isfinite(v) for v in (cs.vEgo, cs.aEgo))
      if not valid:
        self.invalid += 1
        self._close()
        return rows
      values = {'speed_m_s': cs.vEgo, 'acceleration_m_s2': cs.aEgo,
                'pedal_pressed': cs.gasPressed, 'brake_pressed': cs.brakePressed}
      rows.append({'mono': t, 'source': 'validated carState', 'values': self._values(t, values)})
      engine = self.latest_engine
      opportunity = (engine is not None and 0 <= t - engine[0] <= .2 and 1100 <= engine[1] <= 1300 and
                     cs.vEgo > 1 and cs.aEgo < -.1 and not cs.gasPressed)
      if opportunity:
        if self.window and not 0 <= t - self.window['end_mono'] <= .2:
          self._close()
        if self.window is None:
          self.window = {'start_mono': t, 'end_mono': t, 'minimum_rpm': engine[1], 'maximum_rpm': engine[1],
                         'label': 'Candidate pedal-released deceleration at monitor-like RPM; P0401 execution UNCONFIRMED'}
        self.window.update(end_mono=t, minimum_rpm=min(self.window['minimum_rpm'], engine[1]), maximum_rpm=max(self.window['maximum_rpm'], engine[1]))
      else:
        self._close()
    return rows

  def finish(self):
    self._close()
    return {'vehicle_confirmed_volt': self.volt, 'first_mono': self.first, 'last_mono': self.last, 'utc_anchor': self.utc_anchor,
            'missing_passive_signals': sorted({'engine_rpm', 'speed_m_s', 'pedal_pressed', 'dbc_throttle_percent', 'dbc_coolant_c'} - self.stats.keys()),
            'can_frames_by_bus': self.buses, 'can_batch_gaps_over_250ms': self.can_gaps, 'invalid_samples': self.invalid,
            'signals': self.stats, 'candidate_windows': self.windows,
            'limitations': 'Pedal release is NOT confirmed throttle closure. RPM proves rotation, not combustion. ' +
            'Throttle/coolant are existing DBC-defined fields, not newly validated EGR signals. No verified passive MAP, BARO, ' +
            'commanded/actual EGR, error or EGR temperature mapping is available. Missing monitor gates prevent confirmation. ' +
            'No observed gap does not prove lossless capture. Unknown frames remain in full source logs.'}


def trip_rows(report):
  status = report.get('preservation', {})
  preservation = [('Raw-log preservation (last status)', status.get('state', 'Not started'),
                   f"{status.get('segments', 0)} pinned segments; {status.get('bytes', 0)} bytes. " + status.get('message', ''))]
  if not report:
    return [('Passive trip context', 'No report yet', 'Normal full CAN route logging continues while driving. Extract a report when off-road.')]
  if 'context' not in report:
    return preservation + [('Trip extraction', 'Pending', 'Select Extract when off-road. No new diagnostic requests are sent.')]
  data = report.get('context', {})
  rows = preservation + [('Route', report.get('route', 'Unknown'), report.get('limitations', '')),
          ('Source coverage', f"{len(report.get('segments', []))} full segments",
           f"Missing segments: {report.get('missing_segments', [])}; problems: {report.get('problems', [])}"),
          ('CAN frames by bus', str(data.get('can_frames_by_bus', {})), 'All recorded bus IDs and unknown messages remain in the pinned full rlogs.'),
          ('Candidate deceleration windows', str(len(data.get('candidate_windows', []))), data.get('limitations', ''))]
  for name, stat in data.get('signals', {}).items():
    rows.append((name.replace('_', ' '), f"{stat['minimum']:.2f}–{stat['maximum']:.2f}",
                 f"{stat['count']} native-timestamp samples; source logs retain individual readings."))
  return rows
