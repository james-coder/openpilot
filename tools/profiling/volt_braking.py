"""Full-rate, read-only Volt braking extraction and synchronized review export."""

import argparse
from collections import Counter
from datetime import datetime, UTC
import gzip
import hashlib
import io
import json
from pathlib import Path
import statistics

import numpy as np
import zstandard
from cereal import log
from opendbc.can import CANParser
from openpilot.tools.profiling.braking_audit import find_events
from openpilot.selfdrive.locationd.helpers import Pose, PoseCalibrator

EXTRACT_VERSION = 5


def lead_snapshot(lead):
  return {k: getattr(lead, k) for k in ('status', 'dRel', 'yRel', 'vRel', 'vLead', 'vLeadK', 'aLeadK', 'aLeadTau', 'radar', 'radarTrackId', 'modelProb')}


def calibrated_motion(pose, calibrator):
  result = {'imu_ax': pose.accelerationDevice.x if pose.accelerationDevice.valid else None,
            'device_pitch': pose.orientationNED.y if pose.orientationNED.valid else None,
            'calibration_valid': calibrator.calib_valid}
  if calibrator.calib_valid and pose.inputsOK and pose.sensorsOK:
    calibrated = calibrator.build_calibrated_pose(Pose.from_live_pose(pose))
    if pose.orientationNED.valid and np.isfinite(calibrated.orientation.pitch):
      result['vehicle_pitch'] = float(calibrated.orientation.pitch)
    if pose.accelerationDevice.valid and np.isfinite(calibrated.acceleration.x):
      result['vehicle_ax'] = float(calibrated.acceleration.x)
  return result


def extract_native(path):
  segment = path.parent.name
  compressed = path.read_bytes()
  with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(compressed)) as reader:
    raw = reader.read()
  cp = CANParser('gm_global_a_chassis', [('EBCMFrictionBrakeStatus', 0), ('EBCMRegen', 0), ('EBCMFrictionBrakeCmd', 0)], 2)
  pt = CANParser('gm_global_a_powertrain_generated', [('ECMEngineStatus', 0)], 0)
  latest = {}
  stamps = {}
  rows = []
  frames = []
  clock = []
  params = {}
  car_params = {}
  calibrator = PoseCalibrator()
  tracks = []
  track_stamp = -1.0
  events = Counter()
  software = {}
  begin, end = None, None
  for e in log.Event.read_multiple_bytes(raw):
    t = e.logMonoTime / 1e9
    k = e.which()
    if k in ('carOutput', 'controlsState', 'longitudinalPlan', 'modelV2', 'livePose') and not e.valid:
      latest[k] = {}
      stamps[k] = t
      continue
    if begin is None:
      begin = t
    end = t
    if k == 'initData':
      software = {key: str(getattr(e.initData, key)) for key in ('gitCommit', 'version', 'gitBranch')}
    elif k == 'clocks' and e.clocks.wallTimeNanos > 1577836800 * 10**9:
      clock.append(e.clocks.wallTimeNanos / 1e9 - t)
    elif k in ('roadEncodeIdx', 'wideRoadEncodeIdx'):
      r = getattr(e, k)
      if r.segmentNum == int(segment.rsplit('--', 1)[1]):
        frames.append({'camera': 'fcamera' if k == 'roadEncodeIdx' else 'ecamera', 'frame': r.segmentId, 't': r.timestampSof / 1e9, 'segment': segment})
    elif k == 'carParams':
      p = e.carParams
      cp_data = p.to_dict()
      car_params = {key: cp_data[key] for key in (
        'carFingerprint', 'flags', 'networkLocation', 'openpilotLongitudinalControl', 'pcmCruise',
        'stoppingControl', 'startingState', 'stopAccel', 'startAccel', 'stoppingDecelRate',
        'vEgoStopping', 'vEgoStarting', 'longitudinalActuatorDelay', 'longitudinalTuning', 'wheelSpeedFactor',
      ) if key in cp_data}
      params = {
        'car': p.carFingerprint,
        'stopping_rate': p.stoppingDecelRate,
        'stop_accel': p.stopAccel,
        'vstop': p.vEgoStopping,
        'vstart': p.vEgoStarting,
        'delay': p.longitudinalActuatorDelay,
        'kp': list(p.longitudinalTuning.kpV),
        'ki': list(p.longitudinalTuning.kiV),
        'openpilot_longitudinal': p.openpilotLongitudinalControl,
      }
    elif k == 'selfdriveState':
      r = e.selfdriveState
      latest[k] = {'enabled': r.enabled, 'experimental': r.experimentalMode, 'personality': str(r.personality)}
    elif k == 'carControl':
      latest[k] = {}
      if not e.valid:
        stamps[k] = t
        continue
      r = e.carControl
      latest[k] = {'active': r.longActive, 'cmd': r.actuators.accel, 'cmd_state': str(r.actuators.longControlState)}
      if len(r.orientationNED) == 3 and np.isfinite(r.orientationNED[1]):
        latest[k]['controller_pitch'] = r.orientationNED[1]
    elif k == 'carOutput' and e.valid:
      r = e.carOutput.actuatorsOutput
      latest[k] = {'applied_accel': r.accel, 'applied_gas': r.gas, 'applied_brake': r.brake}
    elif k == 'controlsState' and e.valid:
      r = e.controlsState
      latest[k] = {'state': str(r.longControlState), 'p': r.upAccelCmd, 'i': r.uiAccelCmd, 'f': r.ufAccelCmd,
                   'force_decel': r.forceDecel}
    elif k == 'longitudinalPlan' and e.valid:
      r = e.longitudinalPlan
      latest[k] = {
        'target': r.aTarget,
        'should_stop': r.shouldStop,
        'source': str(r.longitudinalPlanSource),
        'plan_v0': r.speeds[0] if len(r.speeds) else None,
        'allow_throttle': r.allowThrottle,
        'plan_speeds': list(r.speeds),
        'plan_accels': list(r.accels),
      }
    elif k == 'radarState':
      r = e.radarState
      a = r.leadOne
      b = r.leadTwo
      latest[k] = {
        'radar_valid': e.valid,
        'lead': a.status,
        'd': a.dRel if a.status else None,
        'vl': a.vLeadK if a.status else None,
        'vr': a.vRel if a.status else None,
        'radar': a.radar,
        'track': a.radarTrackId,
        'prob': a.modelProb,
        'lead2_d': b.dRel if b.status else None,
        'errors': [key for key, value in r.radarErrors.to_dict().items() if value],
        'leads': [lead_snapshot(a), lead_snapshot(b)],
      }
    elif k == 'liveTracks':
      tracks = [{'id': p.trackId, 'd': p.dRel, 'vr': p.vRel, 'y': p.yRel, 'measured': p.measured} for p in e.liveTracks.points] if e.valid else []
      track_stamp = t
    elif k == 'modelV2':
      r = e.modelV2
      latest[k] = {
        'model_a': r.action.desiredAcceleration,
        'model_stop': r.action.shouldStop,
        'gas_press_probs': list(r.meta.disengagePredictions.gasPressProbs),
        'model_leads': [{'prob': p.prob, 'd': p.x[0] if len(p.x) else None, 'v': p.v[0] if len(p.v) else None} for p in r.leadsV3],
      }
    elif k == 'liveParameters':
      latest[k] = {'angle_offset': e.liveParameters.angleOffsetDeg} if e.valid else {}
    elif k == 'liveCalibration':
      if e.valid and len(e.liveCalibration.rpyCalib) == 3:
        calibrator.feed_live_calib(e.liveCalibration)
      else:
        calibrator.calib_valid = False
    elif k == 'livePose':
      latest[k] = calibrated_motion(e.livePose, calibrator)
    elif k in ('can', 'sendcan'):
      if k == 'can':
        updated_pt = pt.update([(e.logMonoTime, [(f.address, f.dat, f.src) for f in e.can if f.src == 0])])
        if updated_pt:
          latest['engine'] = {'engine_rpm': pt.vl['ECMEngineStatus']['EngineRPM']}
          stamps['engine'] = t
      fs = [(f.address, f.dat, f.src) for f in getattr(e, k) if f.src == 2 and f.address in (368, 560, 789)]
      if fs:
        updated = cp.update([(e.logMonoTime, fs)])
        for addr, name, fields in [
          (368, 'EBCMFrictionBrakeStatus', {'pressure': 'FrictionBrakePressure'}),
          (560, 'EBCMRegen', {'regen_raw': 'Regen'}),
          (789, 'EBCMFrictionBrakeCmd', {'brake_mode': 'FrictionBrakeMode', 'brake_cmd': 'FrictionBrakeCmd'}),
        ]:
          if addr in updated:
            key = 'can_' + str(addr)
            latest[key] = {out: cp.vl[name][signal] for out, signal in fields.items()}
            stamps[key] = t
    elif k == 'onroadEvents':
      for event in e.onroadEvents:
        events[str(event.name)] += 1
    elif k == 'carState':
      r = e.carState
      row = {
        't': t,
        'v': r.vEgo,
        'vraw': r.vEgoRaw,
        'a': r.aEgo,
        'foot': r.brakePressed,
        'pedal': r.brake,
        'regen': r.regenBraking,
        'gas': r.gasPressed,
        'standstill': r.standstill,
        'valid': e.valid,
        'gear': str(r.gearShifter),
        'v_cruise_kph': r.vCruise,
        'segment': segment,
      }
      for service, data in latest.items():
        if 0 <= t - stamps[service] <= 0.3:
          row.update(data)
      row['freshness'] = {key: round(t - stamp, 4) for key, stamp in stamps.items() if 0 <= t - stamp <= 0.3 and latest.get(key)}
      row['sample_times'] = {key: stamp for key, stamp in stamps.items() if 0 <= t - stamp <= 0.3 and latest.get(key)}
      row['steering_angle'] = r.steeringAngleDeg
      if row.get('lead') and 0 <= t - track_stamp <= 0.3:
        row['raw_track'] = next((p for p in tracks if p['id'] == row.get('track')), None)
        row['tracks_age'] = t - track_stamp
      rows.append(row)
    if k in latest:
      stamps[k] = t
  return {
    'segment': segment,
    'rows': rows,
    'frames': frames,
    'clock': statistics.median(clock) if clock else None,
    'params': params,
    'car_params': car_params,
    'begin': begin,
    'end': end,
    'events': dict(events),
    'software': software,
    'sha256': hashlib.sha256(compressed).hexdigest(),
  }


def atomic_json(path, data):
  path.parent.mkdir(parents=True, exist_ok=True)
  tmp = path.with_suffix(path.suffix + '.tmp')
  tmp.write_text(json.dumps(data, allow_nan=False, separators=(',', ':')) + '\n')
  tmp.replace(path)


def coarse_rows(rows):
  """Use the existing 10 Hz candidate finder; all metrics use native samples."""
  result, last = [], -1.0
  for r in rows:
    if r['t'] - last < 0.095:
      continue
    result.append(
      {
        **r,
        'speed': r['v'],
        'accel': r['a'],
        'brake_pressed': r['foot'],
        **({'long_active': r['active']} if 'active' in r else {}),
        **({'command_accel': r['cmd']} if 'cmd' in r else {}),
        'lead_status': r.get('lead', False),
        'lead_distance': r.get('d'),
        'lead_speed': r.get('vl'),
        'radar_errors': r.get('errors', []),
      }
    )
    last = r['t']
  return result


def describe_event(rows):
  approach = [r for r in rows if r['t'] <= 0]
  final = [r for r in approach if r['t'] >= -3]
  foot = any(r['foot'] or r['regen'] for r in approach)
  active = [r for r in approach if 'active' in r]
  coverage = len(active) / max(1, len(approach))
  unknown_start = None
  max_unknown_gap = 0.0
  for r in approach:
    if 'active' not in r:
      unknown_start = r['t'] if unknown_start is None else unknown_start
      max_unknown_gap = max(max_unknown_gap, r['t'] - unknown_start)
    else:
      unknown_start = None
  takeover = any((r['foot'] or r['regen']) and any(p.get('active') for p in approach[max(0, i - 50) : i]) for i, r in enumerate(approach))
  if takeover:
    kind = 'takeover'
  elif foot:
    kind = 'manual'
  elif coverage >= 0.8 and max_unknown_gap <= 0.3 and active and all(r['active'] for r in active):
    kind = 'autonomous'
  else:
    kind = 'unknown'
  # Last contiguous low-speed phase, not accumulated stop-and-go time.
  low_start = len(approach) - 1
  while low_start > 0 and approach[low_start - 1]['v'] < 2 and approach[low_start]['t'] - approach[low_start - 1]['t'] < 0.1:
    low_start -= 1
  low = approach[low_start:]
  times = np.array([r['t'] for r in rows])
  accel = np.array([r['a'] for r in rows])
  # 0.2 s central difference; fixed duration rather than sample count.
  jerk = (np.interp(times + 0.1, times, accel) - np.interp(times - 0.1, times, accel)) / 0.2
  final_jerk = [abs(j) for r, j in zip(rows, jerk, strict=True) if -3 <= r['t'] <= 0.5]
  settled = [r['d'] for r in rows if 0.5 <= r['t'] <= 2 and r.get('radar_valid') and r.get('lead') and not r.get('errors') and r.get('d') is not None]
  tracks = {r.get('track') for r in final if r.get('lead') and r.get('radar_valid')}
  peak = min(approach, key=lambda r: r['a'])
  running_min = low[0]['v']
  rebound = 0.
  for r in low:
    running_min = min(running_min, r['v'])
    rebound = max(rebound, r['v'] - running_min)
  return {
    'kind': kind,
    'control_coverage': coverage,
    'max_unknown_control_gap': max_unknown_gap,
    'max_speed_mph': max(r['v'] for r in approach) * 2.236936292,
    'min_accel': peak['a'],
    'min_accel_time': peak['t'],
    'final_min_accel': min(r['a'] for r in final),
    'final_jerk_p95': float(np.percentile(final_jerk, 95)),
    'low_speed_seconds': -low[0]['t'],
    'speed_rebound': rebound,
    'braking_onset': next((r['t'] for r in approach if r['a'] < -.3), None),
    'settled_radar_gap': statistics.median(settled) if settled else None,
    'final_track_ids': sorted(t for t in tracks if t is not None),
    'radar_error_samples': sum(bool(r.get('errors')) for r in approach),
    'steering_max': max(abs(r.get('steering_angle', 0)) for r in final),
  }


def export_review(raw, output, cache, additional_raw=()):
  cache.mkdir(parents=True, exist_ok=True)
  routes = {}
  manifest = []
  paths = {}
  for root in (raw, *additional_raw):
    for path in root.glob('*/rlog.zst'):
      if path.parent.name in paths and path.resolve() != paths[path.parent.name].resolve():
        raise ValueError('Duplicate route segment across archive roots')
      paths[path.parent.name] = path
  if not paths:
    raise ValueError('No rlogs found; the existing review is unchanged')
  if (output / 'index.json').exists():
    existing_routes = {e['route'] for e in json.loads((output / 'index.json').read_text())['events']}
    available_routes = {name.rsplit('--', 1)[0] for name in paths}
    if existing_routes - available_routes:
      raise ValueError('Retain existing review routes and add new trips with --additional-raw, or use a new review directory')
  for path in sorted(paths.values()):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    saved = cache / (path.parent.name + '.json.gz')
    data = json.loads(gzip.decompress(saved.read_bytes())) if saved.exists() else {}
    if data.get('extract_version') != EXTRACT_VERSION or data.get('sha256') != digest:
      data = extract_native(path)
      data['extract_version'] = EXTRACT_VERSION
      saved.write_bytes(gzip.compress(json.dumps(data, allow_nan=False).encode(), compresslevel=3))
    route = path.parent.name.rsplit('--', 1)[0]
    routes.setdefault(route, []).append(data)
    manifest.append({k: v for k, v in data.items() if k not in ('rows', 'frames')})
    print('Extracted', path.parent.name, len(data['rows']), flush=True)
  summaries = []
  for route, segments in routes.items():
    rows = sorted((r for s in segments for r in s['rows']), key=lambda r: r['t'])
    # Segment boundaries can repeat messages. Do not inflate metrics with duplicates.
    rows = list({r['t']: r for r in rows}.values())
    frames = sorted((f for s in segments for f in s['frames']), key=lambda f: f['t'])
    offsets = [s['clock'] for s in segments if s['clock'] is not None]
    offset = statistics.median(offsets) if offsets else None
    for candidate in find_events(coarse_rows(rows), route):
      stop = candidate['stop_mono_s']
      start = stop - candidate['retained_history_s']
      event_rows = [{**r, 't': round(r['t'] - stop, 6),
                     'sample_times': {k: round(t - stop, 6) for k, t in r.get('sample_times', {}).items()}}
                    for r in rows if start <= r['t'] <= stop + 2]
      event_frames = [{**f, 't': f['t'] - stop} for f in frames if start <= f['t'] <= stop + 2]
      key = route + '-' + candidate['id'].split('/')[-1]
      metrics = describe_event(event_rows)
      summary = {
        'id': key,
        'route': route,
        'stop_mono': stop,
        'stop_utc': datetime.fromtimestamp(stop + offset, UTC).isoformat() if offset else None,
        'start': event_rows[0]['t'],
        'end': event_rows[-1]['t'],
        **metrics,
        'lead_near_stop': candidate['lead_near_stop'],
        'recommended_manual': metrics['kind'] in ('manual', 'takeover')
        and not any(r.get('active') for r in event_rows if r['t'] >= -3)
        and candidate['lead_near_stop']
        and len(metrics['final_track_ids']) == 1
        and metrics['steering_max'] < 25,
        'detail_url': '/api/braking/events/' + key,
      }
      event_path = output / 'events' / (key + '.json')
      if event_path.exists():
        previous = json.loads(event_path.read_text())
        if abs(previous['stop_mono'] - stop) > 0.15:
          raise ValueError('Input set would reassign an existing event ID. Use a new review directory to preserve annotations.')
      prior_videos = previous.get('videos', {}) if event_path.exists() else {}
      source = next(s for s in segments if s['segment'] == event_rows[0]['segment'])
      atomic_json(event_path, {**summary, 'samples': event_rows, 'frames': event_frames, 'videos': prior_videos,
                              'car_params': source['car_params'], 'extract_version': EXTRACT_VERSION})
      summaries.append(summary)
  summaries.sort(key=lambda e: (e['kind'] != 'autonomous', e['stop_utc'] or ''))
  result = {
    'version': EXTRACT_VERSION,
    'segments': len(manifest),
    'routes': len(routes),
    'events': summaries,
    'hardware': 'Factory ASCM powered down while engaged; openpilot sends brake/powertrain commands directly.',
    'limits': [
      'Pressure and regen CAN values are raw signals, not calibrated physical units.',
      'Sampled recordings compare different traffic conditions; they do not prove a counterfactual outcome.',
      'The three original autonomous examples have small telemetry gaps; coverage is reported per event.',
    ],
  }
  atomic_json(output / 'index.json', result)
  atomic_json(output / 'sources.json', manifest)
  return result


def export_video(raw, output, additional_raw=()):
  """Encode browser clips from indexed frames with their logged capture timestamps."""
  import av
  from fractions import Fraction

  for path in sorted((output / 'events').glob('*.json')):
    event = json.loads(path.read_text())
    videos = {}
    for camera in ('fcamera', 'ecamera'):
      frames = [f for f in event['frames'] if f['camera'] == camera]
      if not frames:
        continue
      sources = {f['segment'] for f in frames}
      locations = {s: next((root / s / (camera + '.hevc') for root in (raw, *additional_raw)
                            if (root / s / (camera + '.hevc')).exists()), None) for s in sources}
      if any(value is None for value in locations.values()):
        continue
      dest = output / 'media' / (event['id'] + '-' + camera + '.mp4')
      dest.parent.mkdir(parents=True, exist_ok=True)
      if not dest.exists():
        tmp = dest.with_suffix('.tmp.mp4')
        first = frames[0]['t']
        with av.open(str(tmp), 'w') as container:
          stream = container.add_stream('libx264', rate=20)
          stream.width, stream.height, stream.pix_fmt = 960, 600, 'yuv420p'
          stream.options = {'crf': '21', 'preset': 'veryfast'}
          stream.time_base = Fraction(1, 90000)
          for segment in sorted(sources, key=lambda s: int(s.rsplit('--', 1)[1])):
            selected = {f['frame']: f for f in frames if f['segment'] == segment}
            with av.open(str(locations[segment]), format='hevc') as source:
              for i, frame in enumerate(source.decode(video=0)):
                if i > max(selected):
                  break
                if i not in selected:
                  continue
                frame = frame.reformat(width=960, height=600, format='yuv420p')
                frame.pts = round((selected[i]['t'] - first) * 90000)
                frame.time_base = Fraction(1, 90000)
                for packet in stream.encode(frame):
                  container.mux(packet)
          for packet in stream.encode():
            container.mux(packet)
        tmp.replace(dest)
      videos[camera] = {'url': '/braking-media/' + dest.name, 'start': frames[0]['t'], 'end': frames[-1]['t']}
    event['videos'] = videos
    atomic_json(path, event)
    print('Video', event['id'], list(videos), flush=True)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('raw', type=Path)
  parser.add_argument('output', type=Path)
  parser.add_argument('--video-only', action='store_true')
  parser.add_argument('--video', action='store_true')
  parser.add_argument('--additional-raw', type=Path, action='append', default=[])
  args = parser.parse_args()
  if not args.video_only:
    result = export_review(args.raw, args.output, args.output / 'cache', args.additional_raw)
    print(Counter(e['kind'] for e in result['events']))
  if args.video or args.video_only:
    export_video(args.raw, args.output, args.additional_raw)


if __name__ == '__main__':
  main()
