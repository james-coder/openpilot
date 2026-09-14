import copy
import json
from types import SimpleNamespace

import pytest

from opendbc.car.can_definitions import CanData
from openpilot.selfdrive.car.gm_egr import FLOW_CONTROL, GmEgrScanner
from openpilot.selfdrive.car.gm_egr_analysis import analyze, decode_sample, derived_feedback, samples_from_report
from openpilot.selfdrive.car.gm_egr_data import MAX_REPORT_BYTES, QUERIES
from openpilot.selfdrive.car.gm_egr_passive import EgrPassiveObserver
from openpilot.selfdrive.car.obd_scan_controller import ObdScanController
from openpilot.selfdrive.car.tests.test_gm_egr import isotp, simulate
from openpilot.selfdrive.car.tests.test_gm_diagnostics import VEHICLE
from openpilot.selfdrive.car.tests.test_obd_scan import FakeSM
from openpilot.selfdrive.ui.layouts.settings.gm_diagnostics_data import valid_gm_report, gm_rows


def sample(pid, raw, t):
  return decode_sample(f'01:{pid:02X}', bytes([0x41, pid]).hex() + raw, t)


def tracking_samples(c0='80', c1='80', error='80', rpm='12c0', after=101.1):
  return [sample(12, rpm, 100), sample(0x2C, c0, 100.1), sample(0x2D, error, 100.6), sample(0x2C, c1, after)]


def test_feedback_is_derived_and_reconstructed_not_trusted():
  samples = tracking_samples(error='90')
  samples[2]['value'] = 999  # Offline parser must reconstruct the raw reply.
  result = analyze({'samples': samples})['feedback']
  estimate = result['estimates'][0]
  assert estimate['derived_normalized_feedback_percent'] == pytest.approx(128 * 100 / 255 * 1.125)
  assert 'NOT independently' in estimate['label']
  assert not result['rejected']


@pytest.mark.parametrize('kwargs,reason', [({'c0': '00', 'c1': '00'}, 'zero_or_small_command'),
                                        ({'c0': '02', 'c1': '02'}, 'zero_or_small_command'),
                                        ({'c1': '88'}, 'changing_command'),
                                        ({'error': '00'}, 'endpoint_error_may_be_clipped'),
                                        ({'error': 'ff'}, 'endpoint_error_may_be_clipped'),
                                        ({'rpm': '0000'}, 'running_engine_not_established'),
                                        ({'after': 104}, 'stale_or_nonsequential_command_bracket'),
                                        ({'c0': 'f0', 'c1': 'f0', 'error': 'f0'}, 'out_of_normalized_range')])
def test_feedback_rejections(kwargs, reason):
  result = derived_feedback(tracking_samples(**kwargs))
  assert not result['estimates'] and result['rejected'] == {reason: 1}


def test_unbracketed_error_not_inferred():
  assert not derived_feedback(tracking_samples()[:-1])['estimates']


def test_candidate_needs_high_rate_relative_throttle_and_is_never_a_pass():
  samples = [sample(13, '32', 100)]
  samples += [sample(pid, raw, 100.1) for pid, raw in ((11, '21'), (12, '12c0'), (0x33, '56'), (0x45, '00'), (0x2C, '00'))]
  samples.append(sample(13, '31', 100.2))
  result = analyze({'samples': samples})['deceleration']
  assert len(result['candidates']) == 1 and 'UNCONFIRMED' in result['candidates'][0]['label']
  absolute = [s for s in samples if s['key'] != '01:45']
  absolute.insert(1, sample(0x11, '00', 100.1))
  assert analyze({'samples': absolute})['deceleration']['state'] == 'insufficient_inputs'
  samples[-1]['read_mono'] = 102
  assert not analyze({'samples': samples})['deceleration']['candidates']


def test_timestamp_and_payload_validation():
  for raw, t in [('412c80', float('nan')), ('412c80', -1), ('416b00', 100), ('412c8000', 100)]:
    with pytest.raises(ValueError):
      decode_sample('01:2C', raw, t)
  with pytest.raises(ValueError):
    samples_from_report({'samples': list(reversed(tracking_samples()))})


def test_passive_observer_reassembles_without_any_transmitter():
  observer = EgrPassiveObserver()
  frames = isotp(bytes.fromhex('416907808080000000'))
  for i, frame in enumerate(frames):
    assert observer.feed(100 + i * .01, frame.address, frame.dat, frame.src) is None
  assert observer.samples[0]['fields']['actual_a']['state'] == 'ok'
  assert not hasattr(observer, '_send')
  observer = EgrPassiveObserver()
  for i, frame in enumerate(frames):
    observer.feed(100 + i * 3, frame.address, frame.dat, frame.src)
  assert not observer.samples


def test_passive_wrong_sequence_and_excess_traffic_are_bounded():
  observer = EgrPassiveObserver()
  frames = isotp(bytes.fromhex('416907808080000000'))
  observer.feed(100, 0x7E8, frames[0].dat, 0)
  observer.feed(100.1, 0x7E8, b'\x22' + frames[1].dat[1:], 0)
  assert not observer.samples
  for _ in range(2100):
    observer.feed(101, 0x7E8, bytes(8), 0)
  assert observer.full and len(observer.evidence) == 2048


def test_logger_keeps_baseline_bounds_allowlist_and_old_report_compatibility():
  old, _ = simulate()
  assert valid_gm_report(old.report, VEHICLE)
  scanner, sent = simulate(logging=True)
  report = scanner.report
  assert report and valid_gm_report(report, VEHICLE)
  assert len(report['samples']) > 100
  assert len(json.dumps(report).encode()) < MAX_REPORT_BYTES
  assert report['emissions']['ecus']['7E8']['stored']['codes'] == ['P0401']
  assert any('Bounded parked log' == title for title, _, _ in gm_rows(report))
  requests = [(t, frame) for t, frame in sent if frame.dat != FLOW_CONTROL and frame.address == 0x7E0]
  for _, frame in requests:
    if frame.dat[1] != 2:
      assert tuple(frame.dat[1:3]) in QUERIES
  assert all(b[0] - a[0] >= .5 for a, b in zip(requests, requests[1:], strict=False))
  assert not analyze(report)['feedback']['estimates']  # Synthetic engine-off replies.
  damaged = copy.deepcopy(report)
  damaged['samples'][0]['read_mono'] = float('nan')
  assert not valid_gm_report(damaged, VEHICLE)


@pytest.mark.parametrize('flag,commanded', [(60, True), (28, False)])
def test_logging_controller_firmware_and_park_gates(mocker, flag, commanded):
  params = mocker.Mock()
  controller = ObdScanController(SimpleNamespace(carFingerprint=VEHICLE['fingerprint'], carVin=VEHICLE['vin'], passive=False), params)
  sm = FakeSM()
  sm['pandaStates'][0].safetyParam = flag
  cs = SimpleNamespace(canValid=True, gearShifter='park', vEgo=0.)
  batch = [(100_000_000_000, [CanData(0x1F5, bytes(8), 0), CanData(0x34A, bytes(5), 0)])]
  controller._requests.put({'version': 1, 'command': 'log_egr', 'request_id': 'log', 'issued_mono': 100.})
  out = controller.step(100, batch, cs, sm, True)
  assert bool(out) == commanded
  if commanded:
    assert isinstance(controller.gm_scanner, GmEgrScanner) and controller.gm_scanner.logging
    assert controller.gm_scanner.status['vehicle_samples'][0]['speed_m_s'] == 0
    cs.gearShifter = 'drive'
    assert not controller.step(100.01, batch, cs, sm, True)
    assert controller.gm_scanner.status['state'] == 'cancelled'
    assert controller.gm_scanner.report is None
