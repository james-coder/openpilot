"""Offline EGR evidence analysis. No CAN transmitter or vehicle-control imports.

SAE J1979-DA B32/B33 defines relative error, not an independent position sensor.
Heuristic timing/quality limits below are deliberately NOT GM failure limits.
"""
import bisect
import math

from openpilot.selfdrive.car.gm_egr_data import QUERY_KEYS, decode

MAX_SAMPLES = 4096
COMMAND_LSB = 100 / 255
ERROR_LSB = 100 / 128
PASSIVE_KEYS = QUERY_KEYS | {'01:0D', '01:11', '01:45'}


def decode_sample(key, raw, mono):
  """Decode an already received payload; additional PIDs here authorize no TX."""
  if key not in PASSIVE_KEYS or type(mono) not in (int, float) or not math.isfinite(mono) or mono < 0:
    raise ValueError('Invalid key or monotonic time')
  if not isinstance(raw, str) or not 4 <= len(raw) <= 8190:
    raise ValueError('Invalid raw payload')
  payload = bytes.fromhex(raw)
  service, pid = (int(part, 16) for part in key.split(':'))
  if key in ('01:0D', '01:11', '01:45'):
    if len(payload) != 3 or payload[:2] != bytes([0x41, pid]):
      raise ValueError('Invalid passive speed/throttle reply')
    value = {'value': payload[2] if pid == 0x0D else payload[2] * COMMAND_LSB,
             'unit': 'km/h' if pid == 0x0D else '%'}
  else:
    value = decode(service, pid, payload)
  return {'key': key, 'raw': payload.hex(), 'read_mono': mono, 'state': 'ok', **value}


def samples_from_report(report):
  if not isinstance(report, dict):
    raise ValueError('Expected a report object')
  samples = report.get('samples')
  if samples is None:
    samples = [{'key': key, **value} for key, value in report.get('readings', {}).items()]
  if not isinstance(samples, list) or len(samples) > MAX_SAMPLES:
    raise ValueError('Invalid or excessive sample list')
  result = []
  for sample in samples:
    if not isinstance(sample, dict):
      raise ValueError('Invalid sample')
    if sample.get('state') not in ('ok', 'not_applicable', 'unsupported', 'timeout', 'unknown', 'error'):
      raise ValueError('Invalid sample state')
    if sample.get('state') not in ('ok', 'not_applicable'):
      continue
    # Never trust supplied decoded numbers; always reconstruct from preserved bytes.
    result.append(decode_sample(sample['key'], sample['raw'], sample['read_mono']))
  if any(a['read_mono'] > b['read_mono'] for a, b in zip(result, result[1:], strict=False)) and 'samples' in report:
    raise ValueError('Samples are not chronological (do not combine boot clocks)')
  return sorted(result, key=lambda s: s['read_mono'])


def derived_feedback(samples):
  commands = [s for s in samples if s['key'] == '01:2C']
  command_times = [s['read_mono'] for s in commands]
  rpms = [s for s in samples if s['key'] == '01:0C']
  estimates, rejected = [], {}
  for error in (s for s in samples if s['key'] == '01:2D'):
    t = error['read_mono']
    index = bisect.bisect_right(command_times, t)
    reason = None
    if not 0 < index < len(commands):
      reason = 'no_bracketing_command_samples'
    else:
      before, after = commands[index - 1], commands[index]
      c0, c1 = before['value'], after['value']
      rpm = next((s for s in reversed(rpms) if s['read_mono'] <= t), None)
      raw_error = bytes.fromhex(error['raw'])[-1]
      if not before['read_mono'] < t < after['read_mono'] or after['read_mono'] - before['read_mono'] > 1.2:
        reason = 'stale_or_nonsequential_command_bracket'
      elif min(c0, c1) < 5:
        reason = 'zero_or_small_command'
      elif abs(c0 - c1) > COMMAND_LSB + 1e-9:
        reason = 'changing_command'
      elif raw_error in (0, 255):
        reason = 'endpoint_error_may_be_clipped'
      elif rpm is None or t - rpm['read_mono'] > 3 or rpm['value'] < 700:
        reason = 'running_engine_not_established'
      else:
        c, e = (c0 + c1) / 2, error['value']
        estimate = c * (1 + e / 100)
        low = (min(c0, c1) - COMMAND_LSB / 2) * (1 + (e - ERROR_LSB / 2) / 100)
        high = (max(c0, c1) + COMMAND_LSB / 2) * (1 + (e + ERROR_LSB / 2) / 100)
        if not 0 <= low <= estimate <= high <= 100:
          reason = 'out_of_normalized_range'
        else:
          estimates.append({'mono': t, 'derived_normalized_feedback_percent': estimate,
                            'quantization_only_interval_percent': [low, high], 'command_percent': c,
                            'relative_error_percent': e, 'command_times': [before['read_mono'], after['read_mono']],
                            'label': 'Derived estimate; NOT independently measured valve position'})
    if reason:
      rejected[reason] = rejected.get(reason, 0) + 1
  return {'estimates': estimates, 'rejected': rejected,
          'limitations': 'Assumes SAE relative-error semantics in the same normalized domain. ' +
          'Stable sampled endpoints cannot exclude changes between samples. Interval covers quantization only, ' +
          'not sensor accuracy or transport lag. This is not independent proof of valve motion or flow.'}


def candidate_decelerations(samples):
  """Flag high-rate observed opportunities, NOT monitor execution or pass/fail.

  Relative throttle (45), not pedal position or absolute throttle (11), is used.
  Raw MAP is not the ECM altitude-compensated MAP. Missing inputs yield unknown.
  """
  required = ('01:0B', '01:0C', '01:0D', '01:33', '01:45', '01:2C')
  latest, events = {}, []
  previous_speed = None
  for sample in samples:
    key, t = sample['key'], sample['read_mono']
    latest[key] = sample
    if key != '01:0D':
      continue
    previous, previous_speed = previous_speed, sample
    if previous is None or not 0 < t - previous['read_mono'] <= .25:
      continue
    if any(k not in latest or not 0 <= t - latest[k]['read_mono'] <= (.5 if k == '01:33' else .2) for k in required):
      continue
    v = {k: latest[k]['value'] for k in required}
    if (v['01:0D'] > 0 and v['01:0D'] < previous['value'] and v['01:45'] <= 1 and
        1100 <= v['01:0C'] <= 1300 and 20 <= v['01:0B'] <= 50 and v['01:33'] > 70):
      if not events or t - events[-1]['mono'] > .4:
        events.append({'mono': t, 'raw_map_kpa': v['01:0B'], 'rpm': v['01:0C'], 'speed_kmh': v['01:0D'],
                       'command_percent': v['01:2C'], 'label': 'Candidate deceleration window; monitor execution UNCONFIRMED'})
  missing = sorted(set(required) - latest.keys())
  return {'candidates': events, 'missing_inputs': missing,
          'state': 'insufficient_inputs' if missing else 'candidates' if events else 'no_candidate_observed',
          'limitations': 'Not all GM enabling conditions are observable. Raw MAP is not altitude-compensated. ' +
          'No raw MAP-rise threshold or flow verdict is applied. Sparse data cannot exclude a 0.4-second event. ' +
          'Parked polling cannot reproduce a driving deceleration monitor.'}


def analyze(report):
  samples = samples_from_report(report)
  latest = {s['key']: s for s in samples}
  monitors = {key: latest[key]['monitors']['EGR/VVT'] for key in ('01:01', '01:41') if key in latest}
  return {'sample_count': len(samples), 'observed_interfaces': sorted(latest),
          'feedback': derived_feedback(samples), 'deceleration': candidate_decelerations(samples),
          'egr_vvt_readiness': monitors,
          'readiness_limitations': '01:01 supported/complete since clear; 01:41 enabled/complete this cycle. ' +
          'Neither exposes instantaneous P0401 enablement, execution, or a passed test.',
          'stored_mode06': latest.get('06:31', {}).get('records', []),
          'diagnosis': 'Insufficient independent motion and contemporaneous flow evidence to identify a failed component.'}
