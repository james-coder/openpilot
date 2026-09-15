from copy import deepcopy

from openpilot.tools.profiling.braking_audit import find_events


def approach():
  return [{'t': k*.1, 'speed': max(0, 10-k*.1), 'accel': -1, 'valid': True,
           'brake_pressed': k >= 60, 'regen': False, 'long_active': k < 60,
           'command_accel': -.5, 'radar_valid': True, 'lead_status': True,
           'lead_distance': 12., 'lead_speed': 0., 'radar_errors': []} for k in range(121)]


def test_controlled_to_brake_stop_preserves_command_and_actual_distinction():
  event, = find_events(approach(), 'test')
  assert event['lead_near_stop'] and event['long_active_before_brake']
  assert event['brake_speed'] == 4
  assert 3 < event['brake_before_stop_s'] < 5
  assert event['min_active_command_accel'] == -.5
  assert event['min_actual_accel'] == -1
  assert any(r['t'] == 0 for r in event['samples'])


def test_manual_braking_is_not_attributed_to_controller():
  rows = approach()
  for row in rows:
    row['long_active'] = False
  event, = find_events(rows, 'test')
  assert not event['long_active_before_brake']
  assert not event['long_active_in_approach']
  assert event['min_active_command_accel'] is None


def test_unobserved_or_invalid_stop_is_not_an_example():
  original = approach()
  assert not find_events(original[:70]+original[100:], 'gap')
  assert not find_events(original[:99], 'incomplete')
  invalid = deepcopy(original)
  for row in invalid[99:]:
    row['valid'] = False
  assert not find_events(invalid, 'invalid')
  # A large gap inside the nominal sustained-stop interval is not continuity.
  assert not find_events(original[:100]+original[107:], 'future-gap')


def test_invalid_radar_does_not_establish_a_lead_stop():
  for field, value in [('radar_valid', False), ('radar_errors', ['canError']), ('lead_status', False)]:
    rows = approach()
    for row in rows:
      row[field] = value
    event, = find_events(rows, 'test')
    assert not event['lead_near_stop']


def test_regen_paddle_is_distinct_from_foot_brake():
  rows = approach()
  for row in rows:
    row['regen'] = row['brake_pressed']
    row['brake_pressed'] = False
  event, = find_events(rows, 'test')
  assert event['regen_takeover']
  assert not event['long_active_before_brake']
  assert event['brake_speed'] is None
