"""Offline traffic reconstruction and direct-command reproduction; no CAN I/O."""

import math
import numpy as np
from cereal import log
from cereal import car
import cereal.messaging as messaging
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.selfdrive.test.longitudinal_maneuvers.volt_plant import VoltPlant, DT
from openpilot.tools.profiling.volt_observations import sample_rows, autonomous_mask, runs, WARMUP


def service_records(rows, service, required):
  return sorted({r.get('sample_times', {}).get(service, r['t']): r for r in rows if all(k in r for k in required)}.items())


def at(records, t, max_age=.3):
  times, rows = records
  i = int(np.searchsorted(times, t, side='right')) - 1
  if i < 0 or t - times[i] > max_age + 1e-6:
    raise ValueError(f'Missing or stale observation at {t:.3f} s')
  return rows[i], float(t - times[i])


def channel(rows, service, required):
  records = service_records(rows, service, required)
  if not records:
    raise ValueError('Missing ' + service)
  return np.array([r[0] for r in records]), [r[1] for r in records]


def seed_plant(plant, row, history):
  plant.a = row['a']
  plant.sensed_v = row['v']
  plant.estimator.v_ego_kf.set_x([[row['v'],], [row['a']]])
  plant.long.pid.i = row.get('i', 0.)
  plant.long.last_output_accel = row.get('cmd', 0.)
  if row.get('state') in ('off', 'pid', 'stopping', 'starting'):
    plant.long.long_control_state = getattr(car.CarControl.Actuators.LongControlState, row['state'])
  if plant.dynamics is not None:
    t = row['t']
    records = service_records(history, 'carOutput', ['applied_gas', 'applied_brake'])
    preceding = [(stamp, r) for stamp, r in records if stamp <= t + 1e-9]
    if not preceding or t - preceding[0][0] < WARMUP - 1e-6:
      raise ValueError('Insufficient preceding actuator command history')
    if any(b[0] - a[0] > .3 for a, b in zip(preceding, preceding[1:], strict=False)):
      raise ValueError('Actuator command history crosses a telemetry gap')
    if not math.isfinite(row.get('pressure', float('nan'))):
      raise ValueError('Missing initial observed pressure')
    plant.dynamics.pressure_lag.seed(t, [(stamp, r['applied_brake'] / 400.) for stamp, r in preceding], row['pressure'] / 30000.)
    # A bounded, unscored warm-up precedes independent replay. It reconstructs
    # filter state from actual commands; it never assumes steady first demand.
    for lag, key, sign, divisor in ((plant.dynamics.gas_lag, 'applied_gas', 1, 1018.),
                                    (plant.dynamics.regen_lag, 'applied_gas', -1, 650.),
                                    (plant.dynamics.brake_regen_lag, 'applied_brake', 1, 400.)):
      lag.time = preceding[0][0]
      for (stamp, r), (end, _) in zip(preceding, preceding[1:] + [(t, {})], strict=True):
        if end > stamp + 1e-12:
          lag.step(max(0., sign * r[key]) / divisor, end - stamp)


def environment(plant, observation):
  if not math.isfinite(observation.get('controller_pitch', float('nan'))):
    raise ValueError('Missing or stale recorded road pitch')
  if not math.isfinite(observation.get('engine_rpm', float('nan'))):
    raise ValueError('Missing or stale recorded engine state')
  plant.grade = math.tan(observation['controller_pitch'])
  plant.cs.volt_engine_running = bool(observation['engine_rpm'] > 0)
  plant.cc.orientationNED = [0., float(observation['controller_pitch']), 0.]


def replay_commands(event, fit):
  rows = event['samples']
  grid = np.arange(rows[0]['t'], rows[-1]['t'], DT)
  data = sample_rows(rows, grid)
  valid = autonomous_mask(data, DT) & np.isfinite(data['engine_rpm'])
  parts = [part for part in runs(valid) if grid[part[-1]] - grid[part[0]] > WARMUP
           and (grid[part[0]] <= 0 <= grid[part[-1]] or rows[0]['t'] >= 0)]
  if not parts:
    raise ValueError('No continuous, known autonomous stop with actuator prehistory')
  part = max(parts, key=len)
  history = [r for r in rows if grid[part[0]] <= r['t'] <= grid[part[-1]]]
  initial_command = service_records(history, 'carOutput', ['applied_gas', 'applied_brake'])[0][0]
  start = int(np.searchsorted(grid, max(grid[part[0]], initial_command) + WARMUP - 1e-9))
  if rows[0]['t'] < 0 and grid[start] > -8:
    raise ValueError('Less than eight seconds of known autonomous approach remain after pedal/control gaps and initialization')
  indices = part[part >= start]
  commands = channel(rows, 'carOutput', ['applied_gas', 'applied_brake'])
  first = {key: float(values[start]) for key, values in data.items()}
  plant = VoltPlant(fit, speed=first['vraw'], recorded_params=event.get('car_params'))
  history = [r for r in rows if grid[part[0]] <= r['t'] <= grid[start]]
  seed_plant(plant, first, history)
  trace = []
  for index in indices[:-1]:
    t = grid[index]
    recorded, _ = at(commands, t)
    observation = {key: values[index] for key, values in data.items()}
    environment(plant, observation)
    row = plant.step(0., recorded_command=recorded)
    # step() returns the state at the END of the integration interval.
    after = index + 1
    row.update(t=float(grid[after]), recorded_a=float(data['a'][after]), recorded_v=float(data['v'][after]),
               recorded_pressure=float(data['pressure'][after]), recorded_regen=float(data['regen_raw'][after]),
               brake_mode=float(data['brake_mode'][index]), experiment='independent_command_replay')
    trace.append(row)
  return trace


def traffic_window(event):
  """Use a continuous primary identity through the stop; never stitch track IDs."""
  rows = event['samples']
  records = service_records(rows, 'radarState', ['leads'])
  final = [r for t, r in records if -3 <= t <= 0 and r.get('radar_valid') and r.get('lead')]
  ids = {r['leads'][0]['radarTrackId'] for r in final}
  if len(ids) != 1 or next(iter(ids)) < 0:
    raise ValueError('No unambiguous final radar identity')
  identity = next(iter(ids))
  start = rows[0]['t']
  last_good = None
  for t, row in records:
    lead = row['leads'][0]
    good = row.get('radar_valid') and not row.get('errors') and lead['status'] and lead['radar'] and lead['radarTrackId'] == identity
    if not good:
      start = max(start, t + DT)
      last_good = None
    else:
      if last_good is not None and t - last_good > .3:
        start = max(start, t)
      last_good = t
  if start > -8:
    raise ValueError('Less than eight seconds of unambiguous approach remains')
  window = [r for r in rows if r['t'] >= start]
  if any(b['t'] - a['t'] > .1 or not b['valid'] for a, b in zip(window, window[1:], strict=False)):
    raise ValueError('Ego telemetry gap prevents traffic reconstruction')
  return window


def replay_traffic(event, fit, smooth=False, approach_profile=None, controller_profile=None):
  rows = traffic_window(event)
  beginning = rows[0]['t']
  first_command = service_records(rows, 'carOutput', ['applied_gas', 'applied_brake'])[0][0]
  rows = [r for r in rows if r['t'] >= max(beginning, first_command) + WARMUP]
  if not rows:
    raise ValueError('Traffic window lacks initialization history')
  times = np.array([r['t'] for r in rows])
  all_rows = event['samples']
  world_times = np.array([r['t'] for r in all_rows])
  speed = np.array([r['vraw'] for r in all_rows])
  ego_x = np.r_[0., np.cumsum((speed[1:] + speed[:-1]) * np.diff(world_times) / 2)]
  ego_x -= np.interp(times[0], world_times, ego_x)
  radar_records = channel(rows, 'radarState', ['leads'])
  model_records = channel(rows, 'modelV2', ['gas_press_probs'])
  control_records = channel(rows, 'controlsState', ['force_decel'])
  parameter_records = channel(rows, 'liveParameters', ['angle_offset'])
  plant = VoltPlant(fit, speed=rows[0]['vraw'], smooth=smooth, recorded_params=event.get('car_params'), controller_profile=controller_profile)
  seed_plant(plant, rows[0], [r for r in all_rows if beginning <= r['t'] <= times[0]])
  planner = LongitudinalPlanner(plant.CP, init_v=rows[0]['v'], init_a=rows[0]['a'])
  planner.mpc.set_personal_curve(approach_profile)
  if approach_profile is not None and 'model' in approach_profile:
    from opendbc.car.gm.volt_longitudinal import VoltFlags
    plant.CP.flags |= int(VoltFlags.PERSONAL)
  if approach_profile is not None and 'model' not in approach_profile:
    from dataclasses import replace
    from openpilot.selfdrive.controls.lib.volt_stopping import VoltStopping
    plant.long.volt_stopping = VoltStopping(replace(plant.controller.volt_profile,
      stop_speed=tuple(approach_profile['speed']), stop_decel=tuple(approach_profile['deceleration'])))
  names = ('carState', 'controlsState', 'selfdriveState', 'liveParameters', 'carControl', 'modelV2', 'radarState')
  sm = {name: getattr(messaging.new_message(name), name) for name in names}
  sm['selfdriveState'].enabled = True
  sm['selfdriveState'].personality = getattr(log.LongitudinalPersonality, rows[0].get('personality', 'standard'))
  sm['selfdriveState'].experimentalMode = False
  trace = []
  target, stop = 0., False
  previous_secondary = None
  secondary_changes = 0
  replay_times = np.arange(times[0], times[-1], DT)
  observed = sample_rows(all_rows, replay_times)
  for k, t in enumerate(replay_times):
    observation = rows[max(0, int(np.searchsorted(times, t, side='right')) - 1)]
    if observation.get('experimental'):
      raise ValueError('Experimental-mode model predictions cannot be reconstructed')
    environment(plant, {key: values[k] for key, values in observed.items()})
    radar, age = at(radar_records, t)
    stamp = t - age
    origin = float(np.interp(stamp, world_times, ego_x))
    if k % 5 == 0:
      model, _ = at(model_records, t)
      controls, _ = at(control_records, t)
      parameters, _ = at(parameter_records, t)
      sm['modelV2'].meta.disengagePredictions.gasPressProbs = model['gas_press_probs']
      sm['controlsState'].forceDecel = controls['force_decel']
      sm['liveParameters'].angleOffsetDeg = parameters['angle_offset']
      for i, lead in enumerate(radar['leads']):
        out = sm['radarState'].leadOne if i == 0 else sm['radarState'].leadTwo
        for key, value in lead.items():
          setattr(out, key, value)
        if out.status:
          out.dRel = origin + lead['dRel'] + lead['vLead'] * age - plant.x
          out.vRel = lead['vLead'] - plant.sensed_v
        if i == 1:
          identity = lead['radarTrackId'] if lead['status'] else None
          if previous_secondary is not None and identity != previous_secondary:
            secondary_changes += 1
          previous_secondary = identity
      sm['radar_age'] = age if radar.get('radar_valid') and not radar.get('errors') else float('inf')
      sm['carState'].vEgo, sm['carState'].aEgo = plant.sensed_v, plant.a
      sm['carState'].standstill = abs(plant.v) <= .0864
      cruise = observation.get('v_cruise_kph')
      if cruise is None or not 0 < cruise < 255:
        raise ValueError('Recorded cruise setting missing')
      sm['carState'].vCruise = cruise
      sm['carState'].steeringAngleDeg = observation.get('steering_angle', 0.)
      sm['controlsState'].longControlState = plant.long.long_control_state
      sm['carControl'].orientationNED = plant.cc.orientationNED
      planner.update(sm)
      target, stop = planner.output_a_target, planner.output_should_stop
    row = plant.step(float(target), bool(stop), stop_trajectory_active=planner.output_volt_trajectory_active)
    row.update(t=float(t), gap=origin + radar['leads'][0]['dRel'] + radar['leads'][0]['vLead'] * age - plant.x,
               recorded_v=observation['v'], recorded_a=observation['a'], recorded_gap=observation.get('d'),
               personal_blend=planner.mpc.personal_blend, solver_status=planner.mpc.solution_status)
    row['allow_throttle'] = planner.allow_throttle
    trajectory = getattr(planner.mpc, 'stop_trajectory', None)
    row['trajectory_reason'] = trajectory.reason if trajectory else 'stock'
    row['stop_time_remaining'] = trajectory.remaining_time if trajectory else None
    row['target_gap'] = approach_profile['gap'] if approach_profile else None
    row['function_active'] = planner.output_volt_trajectory_active
    if trajectory is not None and hasattr(trajectory.reference, 'evaluate'):
      ref = trajectory.reference.evaluate([max(0., trajectory.elapsed-planner.dt)])[0]
      row.update(function_v=float(ref[1]), function_a=float(ref[2]), function_j=float(ref[3]))
    trace.append(row)
  return {'samples': trace, 'start': times[0], 'secondary_changes': secondary_changes,
          'limitations': ['Lead motion is reconstructed from noisy radar range and wheel odometry.',
                         'Both leads are replayed as observed; no identity is stitched across a primary-track change.',
                         'The lead is assumed not to react to the simulated ego vehicle; this is not proof of a road outcome.']}
