import json
import numpy as np
import pytest
from openpilot.tools.profiling.volt_brake_diagnostics import event_arrays, cached_event, masks, pressure_prediction, physical_fit


def fixture():
  rows = [{'t': float(t), 'vraw': 1., 'v': 1., 'a': -.5, 'foot': False, 'pedal': 0., 'gas': False, 'regen': False,
           'valid': True, 'steering_angle': 0., 'active': True, 'applied_brake': 0. if t < 1. else 100., 'applied_gas': -650.,
           'pressure': 0., 'brake_mode': 1 if t < 1 else 10, 'regen_raw': 0., 'engine_rpm': 0.,
           'controller_pitch': 0., 'vehicle_ax': -.5} for t in np.arange(0., 4., .05)]
  return {'id': 'fixture', 'stop_mono': 100., 'samples': rows}


def test_cached_arrays_are_causal_and_unknown_channels_remain_unknown(tmp_path):
  event = fixture()
  for row in event['samples']:
    row['sample_times'] = {'can_560': 0.}
    del row['engine_rpm']
  data = event_arrays(event)
  assert np.isnan(data['regen_raw'][data['t'] > .31]).all()
  assert np.isnan(data['engine_rpm']).all()
  assert not masks(data)[1].any()
  path = tmp_path / 'event.json'
  path.write_text(json.dumps(event))
  _, first = cached_event(path, tmp_path / 'cache')
  _, second = cached_event(path, tmp_path / 'cache')
  np.testing.assert_equal(first['vraw'], second['vraw'])
  event['samples'][0]['vraw'] = 2.
  path.write_text(json.dumps(event))
  _, third = cached_event(path, tmp_path / 'cache')
  assert third['vraw'][0] == 2.


def test_manual_brakes_are_never_command_pressure_training_samples():
  event = fixture()
  for row in event['samples']:
    row.update(active=False, foot=True, pedal=.2, pressure=5000.)
  data = event_arrays(event)
  autonomous, physical = masks(data)
  assert not autonomous.any() and physical.any()
  assert physical_fit([(event, data)], 'autonomous')['coefficients'] is None
  assert physical_fit([(event, data)], 'manual')['samples'] > 20


def test_pressure_onset_experiment_delays_application_without_changing_release():
  model = {'pressure': {'delay': 0., 'rise': .1, 'release': .1, 'gain': 1., 'gain_low': 0., 'deadband': 0.},
           'regen_delay': 0., 'regen_fade_speed': [0., 5.], 'coefficients': [1., 2., 1., .1, 0.]}
  data = event_arrays(fixture())
  baseline = pressure_prediction(data, model)
  delayed = pressure_prediction(data, model, onset_delay=.2, onset_rise=.2)
  assert np.max(delayed[data['t'] < 1.15]) == 0.
  assert np.max(baseline[data['t'] < 1.15]) > 0.
  assert delayed[-1] == pytest.approx(baseline[-1], rel=.01)
