from dataclasses import replace
import json
import math

from cereal import car
import pytest

from opendbc.car.gm.volt_protection import ProtectionGate
from openpilot.selfdrive.car.volt_protection import ACTUATE, MONITOR, REQUIRED_TEST, REQUIRED_ROAD, configure, make_bundle, read_bundle
from openpilot.selfdrive.controls.lib.volt_protection import Decision, write_request
from openpilot.tools.profiling.volt_protection_scenarios import ProtectionBench, hypothetical_calibration, run_scenarios


def confirmed(bench, gap=20., **kwargs):
  for i in range(9):
    if i % 5 == 0:
      bench.lead(gap-i*.1, **kwargs)
    cc, output, can = bench.step()
  return cc, output, can


@pytest.mark.parametrize('smooth', [False, True])
def test_priority_reaches_actual_can_despite_positive_comfort_plan(smooth):
  bench = ProtectionBench(smooth=smooth)
  cc, out, can = confirmed(bench, gap=8.)
  assert cc.longitudinalProtection.active
  assert str(cc.longitudinalProtection.state) == 'emergency'
  assert cc.actuators.accel == -4.
  assert out.brake == 400 and out.gas <= -650
  # Actual EBCMFrictionBrakeCmd has a complemented 12-bit brake field.
  brake = next(data for address, data, bus in can if address == 0x315 and bus == 2)
  assert ((brake[0] & 0xf) << 8 | brake[1]) == ((-400) & 0xfff)
  assert bench.controller.protection_gate.accepted


def test_monitor_has_identical_detection_without_changing_commands():
  bench = ProtectionBench(actuate=False)
  cc, out, _ = confirmed(bench, gap=8.)
  assert str(cc.longitudinalProtection.state) == 'emergency'
  assert not cc.longitudinalProtection.active and out.brake == 0
  assert cc.actuators.accel > 0 and not bench.controller.protection_gate.accepted


@pytest.mark.parametrize('fields', [{'lateral': 4.}, {'visionMatched': False}, {'visionProbability': .7},
                                  {'measured': False}, {'radar': False}, {'dRel': math.nan}, {'observationMonoTime': 0}])
def test_no_emergency_for_adjacent_or_unqualified_detection(fields):
  bench = ProtectionBench()
  cc, out, _ = confirmed(bench, gap=8., **fields)
  assert not cc.longitudinalProtection.active and out.brake < 400


def test_two_leads_choose_the_more_restrictive_target():
  bench = ProtectionBench()
  for i in range(9):
    if i % 5 == 0:
      bench.lead(24.-i*.1)
      bench.lead(8.-i*.1, identity=2, second=True)
    cc, out, _ = bench.step()
  assert cc.longitudinalProtection.trackId == 2 and out.brake == 400


def test_reused_track_and_frozen_frames_cannot_confirm():
  bench = ProtectionBench()
  bench.lead(8.)
  for _ in range(10):
    cc, _, _ = bench.step()
  assert not cc.longitudinalProtection.active
  bench.lead(3.)
  cc, _, _ = bench.step()
  assert not cc.longitudinalProtection.active


def test_first_valid_observation_waits_for_confirmation_without_false_takeover():
  bench = ProtectionBench()
  bench.lead(8.)
  cc, _, _ = bench.step()
  assert str(cc.longitudinalProtection.state) == 'monitoring'
  assert cc.longitudinalProtection.reason == 'confirming_target'
  assert not cc.longitudinalProtection.active


def test_negative_conservative_clearance_still_requests_emergency_mitigation():
  bench = ProtectionBench()
  cc, out, _ = confirmed(bench, gap=.9)
  assert str(cc.longitudinalProtection.state) == 'emergency'
  assert cc.longitudinalProtection.minimumGap < 0 and out.brake == 400


@pytest.mark.parametrize('fault', ['lost', 'stale', 'radar', 'path', 'grade'])
def test_fault_preserves_existing_intervention_without_freshening_observation(fault):
  bench = ProtectionBench()
  cc, _, _ = confirmed(bench, gap=8.)
  observation = cc.longitudinalProtection.observationMonoTime
  for i in range(40):
    if fault == 'lost':
      bench.radar.leadOne.status = False
    if fault == 'radar':
      bench.radar.radarErrors.radarDegraded = True
    if fault == 'path':
      bench.model.position.x = []
      bench.lead(7.-i*.1)
    if fault == 'grade':
      bench.controls.calibrated_pose.orientation.xyz[1] = .1
    cc, out, _ = bench.step(fresh=fault != 'stale')
  assert str(cc.longitudinalProtection.state) == 'degraded'
  assert cc.longitudinalProtection.active and out.brake == 400
  assert cc.longitudinalProtection.observationMonoTime == observation
  assert bench.controller.protection_gate.accepted


@pytest.mark.parametrize('override', ['brakePressed', 'gasPressed', 'regenBraking', 'canValid'])
def test_override_clears_supervisor_and_backend(override):
  bench = ProtectionBench()
  confirmed(bench, gap=8.)
  setattr(bench.state, override, override != 'canValid')
  for _ in range(4):
    cc, _, _ = bench.step()
  assert not cc.longitudinalProtection.active
  assert bench.controller.protection_gate.last is None


def test_standstill_flag_does_not_taper_emergency_until_wheels_confirm():
  bench = ProtectionBench(smooth=True)
  confirmed(bench, gap=8.)
  bench.state.standstill = True
  bench.state.vEgoRaw = bench.state.vEgo = .05
  # Sensor loss after a threat keeps the established demand during final creep.
  bench.radar.leadOne.status = False
  for _ in range(30):
    cc, out, _ = bench.step()
  assert cc.actuators.accel == -4. and out.brake == 400
  bench.state.vEgoRaw = bench.state.vEgo = 0.
  for _ in range(29):
    cc, out, _ = bench.step()
  assert str(cc.longitudinalProtection.state) == 'holding' and out.brake >= bench.cal.authority.hold_brake
  assert bench.controller.protection_gate.accepted


def test_stale_whole_plan_blocks_positive_pid_output_even_without_trajectory():
  bench = ProtectionBench(actuate=False)
  bench.sm.valid['longitudinalPlan'] = False
  cc, _, _ = bench.step(fresh=False)
  assert cc.actuators.accel <= 0.


@pytest.mark.parametrize('age,valid', [(.16, True), (-.1, True), (0., False)])
def test_card_does_not_retransmit_invalid_or_stale_controls(monkeypatch, age, valid):
  from types import SimpleNamespace
  from openpilot.selfdrive.car import card
  instance = card.Car.__new__(card.Car)
  bench = ProtectionBench()
  instance.CP = bench.cp
  instance.sm = SimpleNamespace(valid={'carControl': valid}, logMonoTime={'carControl': round((1.-age)*1e9)})
  def unexpected(*args):
    pytest.fail('Invalid controls reached CAN transmission')
  instance.CI = SimpleNamespace(apply=unexpected)
  instance.pm = SimpleNamespace(send=unexpected)
  monkeypatch.setattr(card, 'REPLAY', False)
  monkeypatch.setattr(card.time, 'monotonic_ns', lambda: 1_000_000_000)
  instance.controls_update(bench.state, car.CarControl.new_message().as_reader())


def test_duplicate_leads_do_not_invent_a_target_loss():
  bench = ProtectionBench()
  confirmed(bench)
  for i in range(30):
    if i % 5 == 0:
      bench.lead(19.2-i*.1)
      bench.lead(19.2-i*.1, second=True)
    cc, _, _ = bench.step()
  assert str(cc.longitudinalProtection.state) in ('protective', 'emergency')


def test_future_observation_cannot_freeze_confirmation_after_clock_recovers():
  bench = ProtectionBench()
  bench.lead(8., observationMonoTime=bench.ns+10_000_000_000)
  cc, _, _ = bench.step()
  assert str(cc.longitudinalProtection.state) == 'degraded'
  assert cc.longitudinalProtection.reason == 'future_observation'
  cc, out, _ = confirmed(bench, gap=8.)
  assert cc.longitudinalProtection.active and out.brake == 400


def test_fresh_model_cannot_hide_stale_vision_match_inside_radar_state():
  bench = ProtectionBench()
  confirmed(bench, gap=8.)
  bench.lead(7.)
  bench.radar.mdMonoTime = 1
  cc, _, _ = bench.step()
  assert str(cc.longitudinalProtection.state) == 'degraded'
  assert cc.longitudinalProtection.reason == 'stale_or_faulted_perception'


def test_protection_memory_with_production_message_construction():
  import os
  import subprocess
  import sys
  result = subprocess.run([sys.executable, '-c',
    'from openpilot.tools.profiling.benchmark_volt_protection import benchmark; '
    + 'r = benchmark(); print(r); assert r["rss_growth_bytes"] < 5*1024**2; assert r["unreachable_objects"] < 100'],
    env=os.environ.copy(), capture_output=True, text=True, check=False)
  assert result.returncode == 0, result.stdout+result.stderr


def test_clearance_requires_fresh_same_target_and_gradual_qualified_release():
  bench = ProtectionBench()
  confirmed(bench, gap=20.)
  bench.state.vEgoRaw = bench.state.vEgo = 1.
  bench.state.aEgo = -3.
  floors = []
  for i in range(95):
    if i % 5 == 0:
      bench.lead(19.2-i*.01, speed=1.)
    cc, out, can = bench.step()
    if can and (bench.controller.frame-1) % 4 == 0:
      floors.append(cc.longitudinalProtection.brakeFloor)
      if cc.longitudinalProtection.active:
        assert bench.controller.protection_gate.accepted
        assert out.brake >= cc.longitudinalProtection.brakeFloor
  assert floors[0] > 0 and floors[-1] == 0
  assert all(a >= b for a, b in zip(floors[:-1], floors[1:], strict=True))


def test_emergency_can_obeys_existing_panda_safety_interlocks():
  from opendbc.safety.tests.libsafety import libsafety_py
  safety = libsafety_py.libsafety
  safety.set_safety_hooks(car.CarParams.SafetyModel.gm, 0)
  safety.init_tests()
  bench = ProtectionBench()
  _, _, can = confirmed(bench, gap=8.)
  address, data, bus = next(msg for msg in can if msg[0] == 0x315 and msg[2] == 2)
  packet = libsafety_py.make_CANPacket(address, bus, data)
  safety.set_controls_allowed(True)
  assert safety.safety_tx_hook(packet)
  safety.set_controls_allowed(False)
  assert not safety.safety_tx_hook(packet)


@pytest.mark.parametrize('field,value,reason', [('profileId', 'bad', 'calibration_mismatch'),
  ('monoTime', 1, 'stale_command'), ('observationMonoTime', 1, 'stale_observation'),
  ('brakeFloor', 399, 'unqualified_brake_floor'), ('accelCeiling', math.nan, 'out_of_bounds')])
def test_backend_rejects_bad_commands(field, value, reason):
  bench = ProtectionBench()
  cc, _, _ = confirmed(bench, gap=8.)
  setattr(cc.longitudinalProtection, field, value)
  gate = ProtectionGate(bench.cal.authority)
  assert not gate.update(cc.longitudinalProtection, bench.ns, True, bench.state)
  assert gate.reason == reason


def test_backend_continuity_and_float32_serialization():
  bench = ProtectionBench()
  request = car.CarControl.new_message().longitudinalProtection
  authority = replace(bench.cal.authority, brake_decelerations=(0., .9, 1.8, 2.7, 4.))
  gate = ProtectionGate(authority)
  write_request(request, Decision('protective', -2.7, 300, assessed=True, observation_ns=bench.ns), bench.ns, authority.profile_id, True)
  assert gate.update(request, bench.ns, True, bench.state)
  request.state = 'degraded'
  assert gate.update(request, bench.ns+40_000_000, True, bench.state)
  assert not ProtectionGate(authority).update(request, bench.ns, True, bench.state)
  request.state = 'holding'
  request.brakeFloor = authority.hold_brake
  assert not gate.update(request, bench.ns+50_000_000, True, bench.state)


def evidence(identity):
  return {'pass': True, 'profile_id': identity, 'archive_sha256': 'a'*64}


def qualified_bundle():
  bundle = make_bundle(hypothetical_calibration())
  identity = bundle['id']
  runtime = {**evidence(identity), 'device': 'comma3', 'duration_seconds': 60., 'includes_cold_start': True,
             'includes_candidate_work': True, 'memory_pressure': False, 'thermal_throttling': False, 'processes': {}}
  for name, core, priority, period in [('controlsd', 4, 53, 10), ('card', 4, 53, 10), ('selfdrived', 4, 53, 10),
                                        ('plannerd', 5, 51, 50), ('radard', 5, 51, 50), ('modeld', 7, 54, 50)]:
    runtime['processes'][name] = {'core': core, 'fifo_priority': priority, 'deadline_misses': 0,
                                 'maximum_cycle_ms': 1., 'samples': 60000/period}
  bundle['evidence'] = {key: evidence(identity) for key in (*REQUIRED_TEST, *REQUIRED_ROAD)}
  bundle['evidence']['runtime'] = runtime
  return bundle


def test_qualification_is_separate_source_bound_and_fail_closed():
  bench = ProtectionBench()
  unqualified = read_bundle(raw=json.dumps(make_bundle(bench.cal)))
  assert not any(unqualified['readiness'].values())
  assert configure(bench.cp, 'enabled', unqualified) == 'unavailable'
  assert not bench.cp.flags & (ACTUATE | MONITOR)
  raw = qualified_bundle()
  bundle = read_bundle(raw=json.dumps(raw))
  assert configure(bench.cp, 'test', bundle) == 'test'
  raw['evidence']['friction_response']['profile_id'] = 'b'*64
  bundle = read_bundle(raw=json.dumps(raw))
  assert configure(bench.cp, 'enabled', bundle) == 'monitor'
  assert not bench.cp.flags & ACTUATE
  raw['sources']['selfdrive/controls/controlsd.py'] = 'wrong'
  assert read_bundle(raw=json.dumps(raw)) is None


@pytest.mark.parametrize('fault', ['miss', 'memory', 'thermal', 'cold', 'device'])
def test_device_runtime_evidence_cannot_be_replaced_by_host_timings(fault):
  raw = qualified_bundle()
  runtime = raw['evidence']['runtime']
  if fault == 'miss':
    runtime['processes']['controlsd']['deadline_misses'] = 1
  else:
    key, value = {'memory': ('memory_pressure', True), 'thermal': ('thermal_throttling', True),
                  'cold': ('includes_cold_start', False), 'device': ('device', '4090-host')}[fault]
    runtime[key] = value
  assert not any(read_bundle(raw=json.dumps(raw))['readiness'].values())


def test_synthetic_production_path_matrix():
  report = run_scenarios()
  cases = {case['name']: case for case in report['cases']}
  assert all(c['bounded'] and c['arbitration_pass'] for c in cases.values())
  for name in ('stationary target', 'moving target hard braking', 'cut in', 'response delay bound', 'target loss during intervention', 'downhill'):
    assert not cases[name]['collision_in_assumed_plant'], name
  assert cases['adjacent vehicle']['first_intervention'] is None
  assert cases['insufficient initial distance']['collision_in_assumed_plant']
  assert any(r['applied_brake'] == 400 for r in cases['insufficient initial distance']['trace'])
  assert report['vehicle_calibrated'] is report['road_ready'] is False
