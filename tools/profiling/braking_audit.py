"""Read-only audit of recorded braking, longitudinal activation and lead approaches.

No inference about driver intent, no counterfactual collision prediction and no
controller tuning. Retained segments are a convenience sample, not every drive.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

import zstandard
from cereal import log


def extract(path):
  with path.open('rb') as file, zstandard.ZstdDecompressor().stream_reader(file) as stream:
    raw = stream.read()
  latest, stamps, rows, params, counts = {}, {}, [], {}, Counter()
  can_addresses = defaultdict(Counter)
  last_sample = -1.
  for event in log.Event.read_multiple_bytes(raw):
    kind, t = event.which(), event.logMonoTime/1e9
    if kind == 'can':
      for frame in event.can:
        can_addresses[frame.src][frame.address] += 1
      continue
    if kind == 'carParams':
      cp = event.carParams
      params = {'car': cp.carFingerprint, 'openpilot_longitudinal': cp.openpilotLongitudinalControl,
                'pcm_cruise': cp.pcmCruise, 'stopping_decel_rate': cp.stoppingDecelRate,
                'stop_accel': cp.stopAccel, 'actuator_delay': cp.longitudinalActuatorDelay}
    elif kind == 'selfdriveState':
      r = event.selfdriveState
      latest[kind] = {'enabled': r.enabled, 'experimental': r.experimentalMode, 'personality': str(r.personality)}
    elif kind == 'carControl':
      r = event.carControl
      latest[kind] = {'long_active': r.longActive, 'control_valid': event.valid, 'command_accel': r.actuators.accel} if event.valid else {}
    elif kind == 'controlsState':
      latest[kind] = {'long_state': str(event.controlsState.longControlState)}
    elif kind == 'radarState':
      r = event.radarState
      lead = r.leadOne
      latest[kind] = {'radar_valid': event.valid, 'lead_status': lead.status, 'lead_distance': lead.dRel if lead.status else None,
                      'lead_speed': lead.vLeadK if lead.status else None,
                      'relative_speed': lead.vRel if lead.status else None,
                      'radar_errors': [key for key, value in r.radarErrors.to_dict().items() if value]}
    elif kind == 'longitudinalPlan':
      r = event.longitudinalPlan
      latest[kind] = {'target_accel': r.aTarget, 'should_stop': r.shouldStop, 'plan_source': str(r.longitudinalPlanSource)}
    elif kind == 'carState':
      if t-last_sample < .095:
        continue
      r = event.carState
      row = {'t': t, 'speed': r.vEgo, 'accel': r.aEgo, 'brake_pressed': r.brakePressed,
             'brake_pedal': r.brake, 'regen': r.regenBraking, 'gas_pressed': r.gasPressed,
             'standstill': r.standstill, 'valid': event.valid, 'steering_angle': r.steeringAngleDeg}
      for service, data in latest.items():
        if t-stamps[service] <= .3:
          row.update(data)
      rows.append(row)
      last_sample = t
      if row.get('long_active'):
        counts['long_active_samples'] += 1
        mode = 'mode_unknown' if 'experimental' not in row else 'experimental' if row['experimental'] else 'standard_mode'
        counts[mode] += 1
        counts['personality_'+row.get('personality', 'unknown')] += 1
    if kind in latest:
      stamps[kind] = t
  return {'schema_version': 3, 'segment': path.parent.name, 'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
          'params': params, 'rows': rows, 'counts': dict(counts),
          'can_addresses': {str(bus): dict(values) for bus, values in can_addresses.items()}}


def find_events(rows, route):
  events = []
  for i, row in enumerate(rows):
    if not row.get('valid') or row['speed'] >= .3 or i == 0 or rows[i-1]['speed'] < .3:
      continue
    if row['t']-rows[i-1]['t'] > .3:
      continue
    # Require a sustained stop, previous motion, and continuous retained history.
    future = [r for r in rows[i:] if r['t'] <= row['t']+1]
    if (not future or future[-1]['t']-row['t'] < .8 or max(r['speed'] for r in future) >= .5 or
        any(not r.get('valid') for r in future) or any(b['t']-a['t'] > .3 for a, b in zip(future, future[1:], strict=False))):
      continue
    history = [r for r in rows[max(0, i-500):i+1] if row['t']-r['t'] <= 35]
    gaps = [k for k in range(1, len(history)) if history[k]['t']-history[k-1]['t'] > .3 or not history[k-1].get('valid')]
    if gaps:
      history = history[gaps[-1]:]
    if len(history) < 20 or max(r['speed'] for r in history) < 2:
      continue
    # A previous sustained stop separates stop-and-go episodes.
    prior_stops = [k for k, r in enumerate(history[:-10]) if r['speed'] < .3]
    if prior_stops:
      history = history[prior_stops[-1]:]
    if max(r['speed'] for r in history) < 2:
      continue
    brake_edges = [k for k in range(1, len(history)) if history[k]['brake_pressed'] and not history[k-1]['brake_pressed']]
    brake_takeovers = [k for k in brake_edges if any(r.get('long_active') for r in history[max(0, k-5):k])]
    brake_index = brake_takeovers[-1] if brake_takeovers else brake_edges[0] if brake_edges else None
    brake = history[brake_index] if brake_index is not None else None
    before_brake = history[max(0, brake_index-5):brake_index] if brake_index is not None else []
    regen_edges = [k for k in range(1, len(history)) if history[k]['regen'] and not history[k-1]['regen']]
    regen_takeover = any(any(r.get('long_active') for r in history[max(0, k-5):k]) for k in regen_edges)
    recent_leads = [r for r in history if row['t']-r['t'] <= 3 and r.get('radar_valid') and r.get('lead_status') and
                    not r.get('radar_errors') and 1 < r['lead_distance'] < 35 and abs(r.get('lead_speed', 99)) < 2]
    controlled = [r for r in history if r.get('long_active')]
    # One-second acceleration difference suppresses sample-to-sample estimator noise.
    jerk = [(history[k]['accel']-history[k-10]['accel'])/(history[k]['t']-history[k-10]['t'])
            for k in range(10, len(history)) if .8 < history[k]['t']-history[k-10]['t'] < 1.2]
    snippet = history+future[1:]
    relative = [{**r, 't': round(r['t']-row['t'], 3)} for r in snippet]
    events.append({'id': f'{route}/stop-{len(events)+1}', 'route': route,
                   'stop_mono_s': row['t'], 'retained_history_s': row['t']-history[0]['t'],
                   'lead_near_stop': bool(recent_leads), 'lead_distance_at_stop': recent_leads[-1]['lead_distance'] if recent_leads else None,
                   'brake_before_stop_s': row['t']-brake['t'] if brake else None,
                   'brake_speed': brake['speed'] if brake else None,
                   'brake_lead_distance': brake.get('lead_distance') if brake else None,
                   'long_active_before_brake': any(r.get('long_active') for r in before_brake),
                   'long_active_in_approach': bool(controlled), 'regen_takeover': regen_takeover,
                   'brake_applications': len(brake_edges),
                   'unknown_control_samples': sum('long_active' not in r for r in history),
                   'min_actual_accel': min(r['accel'] for r in history),
                   'min_active_command_accel': min((r['command_accel'] for r in controlled if 'command_accel' in r), default=None),
                   'min_smoothed_jerk': min(jerk, default=None), 'samples': relative})
  return events



def can_coverage(segments):
  from openpilot.selfdrive.ui.layouts.settings.can_diagnostics_data import BUS_LABELS, build_parsers, signal_metadata
  from opendbc.car.gm.values import DBC as GM_DBC_MAP
  from openpilot.selfdrive.ui.layouts.settings.can_diagnostics_data import BUS_DBC_KEYS

  cars = {s['params']['car'] for s in segments if s['params']}
  if len(cars) != 1:
    raise ValueError('Coverage requires one identified GM car across the selected recordings')
  car, = cars
  _, known = build_parsers(car)
  metadata = signal_metadata(car)
  buses = []
  for bus, addresses in known.items():
    observed = {int(address) for segment in segments for address in segment['can_addresses'].get(str(bus), {})}
    buses.append({'bus': bus, 'name': BUS_LABELS[bus], 'dbc': GM_DBC_MAP[car][BUS_DBC_KEYS[bus]],
                  'defined_messages': len(addresses), 'observed_messages': len(observed),
                  'known_observed_messages': len(observed & addresses),
                  'known_observed_signals': sum(b == bus and address in observed for b, address, signal in metadata),
                  'unknown_addresses': [f'0x{address:X}' for address in sorted(observed-addresses)]})
  return {'buses': buses, 'signals_with_units': sum(bool(unit) for _, unit, _ in metadata.values()),
          'signals_with_value_tables': sum(bool(choices) for _, _, choices in metadata.values()),
          'limits': 'Counts refer to recorded receive buses 0–2 and the current matched GM DBC files; no cross-bus or cross-model guessing.'}


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('inputs', type=Path, nargs='+')
  parser.add_argument('--output', type=Path, required=True)
  parser.add_argument('--cache', type=Path, required=True)
  parser.add_argument('--coverage-output', type=Path)
  args = parser.parse_args()
  args.cache.mkdir(parents=True, exist_ok=True)
  files = sorted({p for folder in args.inputs for p in folder.glob('*/rlog.zst')})
  segments, routes, counts = [], defaultdict(list), Counter()
  for path in files:
    cached = args.cache/(path.parent.name+'.json')
    data = json.loads(cached.read_text()) if cached.exists() else {}
    if data.get('schema_version') != 3 or data.get('source_sha256') != hashlib.sha256(path.read_bytes()).hexdigest():
      data = extract(path)
      cached.write_text(json.dumps(data)+'\n')
    routes[data['segment'].rsplit('--', 1)[0]].extend(data['rows'])
    counts.update(data['counts'])
    segments.append({k: v for k, v in data.items() if k != 'rows'})
    print('Audited', path.parent.name, len(data['rows']), 'samples', flush=True)
  events = []
  for route, rows in routes.items():
    events.extend(find_events(sorted(rows, key=lambda r: r['t']), route))
  lead_events = [e for e in events if e['lead_near_stop']]
  summary = {'segments': len(segments), 'routes': len(routes), 'samples': sum(len(r) for r in routes.values()),
             'qualifying_stops': len(events), 'stops_with_lead_near_stop': len(lead_events),
             'lead_stops_with_prior_brake_press': sum(e['brake_before_stop_s'] is not None for e in lead_events),
             'lead_stops_with_active_to_brake': sum(e['long_active_before_brake'] for e in lead_events),
             'mode_counts_while_long_active': dict(counts)}
  result = {'version': 1, 'summary': summary, 'segments': segments, 'events': events,
            'limits': ['Selected retained recordings are not every drive; missing approaches are not negative examples.',
                       'A brake press shows an intervention, not why the driver intervened or what openpilot would have done.',
                       'Lead identity and road context need video review; a nearby stopped radar/vision lead is only a candidate.',
                       'Commands after longActive becomes false do not describe autonomous braking.']}
  args.output.parent.mkdir(parents=True, exist_ok=True)
  args.output.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
  if args.coverage_output:
    args.coverage_output.parent.mkdir(parents=True, exist_ok=True)
    args.coverage_output.write_text(json.dumps(can_coverage(segments), indent=2)+'\n')
  print(json.dumps(summary, indent=2))


if __name__ == '__main__':
  main()
