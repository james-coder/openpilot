import copy
import json
from types import SimpleNamespace

import pytest

from opendbc.car.can_definitions import CanData
from openpilot.selfdrive.car.gm_egr import FLOW_CONTROL, GmEgrScanner
from openpilot.selfdrive.car.gm_egr_archive import archive_report
from openpilot.selfdrive.car.gm_egr_data import MAX_CAPTURE, QUERIES, SUPPORT_QUERIES, decode, egr_position, egr_temperature, mode06, readiness, supported
from openpilot.selfdrive.car.tests.test_gm_diagnostics import VEHICLE, uudt
from openpilot.selfdrive.car.tests.test_obd_scan import sf
from openpilot.selfdrive.car.tests.test_obd_scan import FakeSM
from openpilot.selfdrive.car.obd_scan_controller import ObdScanController
from openpilot.selfdrive.ui.layouts.settings.gm_diagnostics_data import gm_rows, valid_gm_report
from openpilot.tools.car_porting.export_gm_diagnostics import export_report


def isotp(payload):
  if len(payload) <= 7:
    return [sf(payload)]
  frames = [CanData(0x7E8, (0x1000 | len(payload)).to_bytes(2, 'big') + payload[:6], 0)]
  frames.extend(CanData(0x7E8, (bytes([0x20 | (index & 15)]) + payload[offset:offset + 7]).ljust(8, b'\x00'), 0)
                for index, offset in enumerate(range(6, len(payload), 7), 1))
  return frames


def reply_for(frame):
  request = frame.dat[1:1 + frame.dat[0]]
  service, pid = request[0], request[1] if len(request) > 1 else 0
  if service in (3, 7, 10):
    return bytes([service + 0x40, 1, 4, 1])
  if (service, pid) in SUPPORT_QUERIES:
    data = b'\xff' * 4
  elif service == 2:
    data = b'\x00' + (b'\x04\x01' if pid == 2 else b'\x00\x00' if pid in (12, 16) else b'\x00')
  elif service == 9:
    data = b'\x01' + (b'12683203'.ljust(16, b'\x00') if pid == 4 else b'\x01\x02\x03\x04')
  elif service == 6:
    return bytes.fromhex('46 31 A8 FD 00 01 00 00 00 02 31 A9 FD 00 00 00 00 00 00')
  elif pid in (1, 0x41):
    data = bytes.fromhex('81 07 80 80')
  elif pid == 0x69:
    data = bytes.fromhex('07 00 00 00 00 00 00')
  elif pid == 0x6B:
    data = bytes.fromhex('21 50 50 50 50')
  else:
    data = b'\x00\x00' if pid in (12, 16) else b'\x00'
  return bytes([service + 0x40, pid]) + data


def simulate():
  scanner = GmEgrScanner()
  scanner.start('egr-test', VEHICLE, 100.)
  frames, remaining, sent = [], [], []
  for step in range(12001):
    now = 100 + step / 100
    out = scanner.tick(now, frames, '')
    frames = []
    for frame in out:
      sent.append((now, frame))
      if frame.dat == FLOW_CONTROL:
        frames.extend(remaining)
        remaining = []
      elif frame.address == 0x101:
        frames.extend([uudt('81 04 01 00 BB'), uudt('81 C1 04 00 7B'), uudt('81 00 00 00 FF')])
      else:
        response = isotp(reply_for(frame))
        frames.append(response[0])
        remaining = response[1:]
    if not scanner.active:
      return scanner, sent
  raise AssertionError('Scanner failed to finish within bound')


def test_complete_serialized_evidence_scan():
  scanner, sent = simulate()
  report = scanner.report
  assert report and valid_gm_report(report, VEHICLE)
  assert report['version'] == 2 and report['state'] == 'partial'
  assert len(report['readings']) == len(QUERIES)
  for kind in ('stored', 'pending', 'permanent'):
    assert report['emissions']['ecus']['7E8'][kind]['codes'] == ['P0401']
  assert report['readings']['01:69']['fields']['actual_a']['value'] == 0
  assert report['readings']['01:69']['fields']['error_a']['state'] == 'not_applicable'
  assert report['readings']['01:2D']['state'] == 'not_applicable'
  assert report['readings']['06:31']['records'][0]['state'] == 'passed'
  assert report['readings']['06:31']['records'][1]['state'] == 'no_valid_result'
  assert report['calibration_pairing'] == 'ordered'
  requests = [(now, frame) for now, frame in sent if frame.dat != FLOW_CONTROL and frame.address != 0x101]
  assert all(b[0] - a[0] >= .5 for a, b in zip(requests, requests[1:], strict=False))
  assert len(requests) < 48 and len(report['evidence']) < MAX_CAPTURE
  assert any('intentionally disconnected ASCM' in detail for _, _, detail in gm_rows(report))
  redacted = json.loads(export_report(report, as_json=True))
  assert 'vin' not in redacted['vehicle'] and 'vin' not in redacted['emissions']['vehicle']
  assert report['vehicle']['vin'] == VEHICLE['vin']


@pytest.mark.parametrize('raw,expected', [(0x8000, -32.768), (0xFFFF, -.001), (0, 0), (0x7FFF, 32.767)])
def test_signed_monitor_scaling(raw, expected):
  record = mode06(bytes.fromhex('31 A8 FD') + raw.to_bytes(2, 'big') + bytes.fromhex('80 00 7F FF'))['records'][0]
  assert record['value'] == expected and record['state'] == 'passed'


@pytest.mark.parametrize('raw,state', [('000300000002', 'failed'), ('000100020000', 'invalid_limits'),
                                     ('000000000000', 'no_valid_result'), ('000200000002', 'passed')])
def test_monitor_outcomes(raw, state):
  assert mode06(bytes.fromhex('31 A9 FD' + raw))['records'][0]['state'] == state


def test_unknown_and_malformed_monitor_records():
  result = mode06(bytes.fromhex('31 EE 7F 00 01 00 00 00 02'))['records'][0]
  assert result['state'] == 'unknown_scaling' and 'label' not in result
  for data in (b'', bytes(8), bytes.fromhex('32 A8 FD 00 00 00 00 00 00')):
    with pytest.raises(ValueError):
      mode06(data)


def test_pid_support_fields_and_temperature_ranges():
  fields = egr_position(bytes.fromhex('02 00 00 00 00 00 00'))['fields']
  assert fields['actual_a']['value'] == 0 and fields['commanded_a']['state'] == 'unsupported'
  assert 'value' not in fields['commanded_a']
  fields = egr_temperature(bytes.fromhex('21 50 50 50 50'))['fields']
  assert fields['a_bank1_sensor1']['value'] == 40
  assert fields['c_bank1_sensor2']['value'] == 280
  assert fields['b_bank2_sensor1']['state'] == 'unsupported'
  assert egr_temperature(bytes.fromhex('11 00 00 00 00'))['fields']['a_bank1_sensor1']['state'] == 'ambiguous'
  assert supported({}, 1, 0x69) is None
  assert supported({'01:60': {'state': 'ok', 'bitmap': 1 << 23}}, 1, 0x69) is True
  assert supported({'01:60': {'state': 'ok', 'bitmap': 0}}, 1, 0x69) is False


def test_readiness_is_not_support_or_pass():
  since = readiness(1, bytes.fromhex('81 07 80 80'))
  cycle = readiness(0x41, bytes.fromhex('00 07 80 00'))
  assert since['monitors']['EGR/VVT'] == {'supported': True, 'complete': False}
  assert cycle['monitors']['EGR/VVT'] == {'enabled_this_cycle': True, 'complete': True}
  assert 'NOx/SCR' in readiness(1, bytes.fromhex('00 08 FF 00'))['monitors']


@pytest.mark.parametrize('service,pid,payload', [(1, 0x69, '416900'), (1, 0x6B, '416B00'), (1, 1, '410100'),
                                              (1, 0, '410000'), (1, 0x33, '41330000'), (9, 4, '49040200'),
                                              (9, 6, '49060100'), (9, 4, '490400'), (1, 0x69, '416B00000000000000')])
def test_strict_decoder_lengths(service, pid, payload):
  with pytest.raises(ValueError):
    decode(service, pid, bytes.fromhex(payload))


def engine_scanner(query=(1, 0)):
  scanner = GmEgrScanner()
  scanner.start('transport-test', VEHICLE, 100.)
  scanner._child.cancel()
  scanner._phase = 'engine'
  scanner._index = QUERIES.index(query) - 1
  for service, pid in SUPPORT_QUERIES:
    scanner.status['readings'][f'{service:02X}:{pid:02X}'] = {'state': 'ok', 'bitmap': 0xFFFFFFFF}
  scanner.tick(100., [], '')
  return scanner


def test_pending_wrong_ecu_malformed_and_cancellation():
  scanner = engine_scanner((1, 0x69))
  deadline = scanner._deadline
  assert scanner.tick(100.1, [sf(bytes.fromhex('7F 01 78'))], '') == []
  assert scanner._deadline == deadline
  first = isotp(bytes.fromhex('416907000000000000'))[0]
  assert scanner.tick(100.2, [CanData(0x7E9, first.dat, 0)], '') == []
  assert scanner.tick(100.3, [first], '') == [CanData(0x7E0, FLOW_CONTROL, 0)]
  assert scanner.tick(100.4, [CanData(0x7E8, bytes.fromhex('22 00 00 00 00 00 00 00'), 0)], '') == []
  assert scanner.status['readings']['01:69']['state'] == 'error'
  scanner.cancel()
  assert scanner.tick(109., [first], '') == [] and scanner.report is None


def test_support_failure_and_no_response_are_distinct():
  scanner = engine_scanner()
  scanner.status['readings'] = {'01:00': scanner.status['readings']['01:00']}
  scanner.tick(102., [], '')
  assert scanner.status['readings']['01:20']['state'] == 'unknown'
  assert scanner.status['readings']['01:60']['state'] == 'unknown'
  scanner = engine_scanner()
  scanner.status['readings'] = {'01:00': scanner.status['readings']['01:00']}
  scanner.tick(100.1, [sf(bytes.fromhex('410000000000'))], '')
  scanner.tick(102., [], '')
  assert scanner.status['readings']['01:20']['state'] == 'unsupported'


def test_safety_block_capture_and_time_bounds():
  for reason in ('Shift to Park', 'Stale vehicle state', 'Controls enabled'):
    scanner = engine_scanner((1, 0x69))
    assert scanner.tick(100.1, isotp(bytes.fromhex('416907000000000000')), reason) == []
    assert not scanner.active and scanner.report is None
  scanner = engine_scanner()
  scanner.status['evidence'] = [{}] * MAX_CAPTURE
  assert scanner.tick(100.1, [sf(b'\x41\x00' + bytes(4))], '') == []
  assert scanner.status['state'] == 'cancelled'
  scanner = engine_scanner()
  assert scanner.tick(220., [], '') == [] and scanner.report is None


def test_report_validation_and_immutable_archive(tmp_path):
  scanner, _ = simulate()
  report = scanner.report
  path = archive_report(report, tmp_path)
  assert archive_report(report, tmp_path) == path
  changed = copy.deepcopy(report)
  changed['readings']['06:31']['records'][0]['value'] = 1e6
  assert valid_gm_report(changed, VEHICLE) is None
  changed = copy.deepcopy(report)
  changed['readings']['01:69']['fields']['actual_a'] = 'bad'
  assert valid_gm_report(changed, VEHICLE) is None
  changed = copy.deepcopy(report)
  changed['state'] = 'cancelled'
  assert archive_report(changed, tmp_path) != path
  assert len(list(tmp_path.glob('*.json'))) == 2
  with pytest.raises(ValueError):
    archive_report({'data': 'x' * (512 * 1024)}, tmp_path)


def test_maximum_calibration_and_monitor_multiframe_replies():
  for query, payload in (((9, 4), b'\x49\x04\xff' + b'1234567890ABCDEF' * 255),
                         ((6, 0x31), b'\x46' + bytes.fromhex('31 A8 FD 00 01 00 00 00 02') * 256)):
    scanner = engine_scanner(query)
    frames = isotp(payload)
    assert scanner.tick(100.01, frames[:1], '') == [CanData(0x7E0, FLOW_CONTROL, 0)]
    for index, frame in enumerate(frames[1:]):
      assert scanner.tick(100.02 + index * .01, [frame], '') == []
    result = scanner.status['readings'][f'{query[0]:02X}:{query[1]:02X}']
    assert result['state'] == 'ok' and result['raw'] == payload.hex()


def test_bad_first_frame_never_gets_flow_control():
  for raw in ('10 07 41 69 00 00 00 00', '10 00 41 69 00 00 00 00', '10 09 41 6B 00 00 00 00', '00 41 69 00 00 00 00 00'):
    scanner = engine_scanner((1, 0x69))
    assert scanner.tick(100.1, [CanData(0x7E8, bytes.fromhex(raw), 0)], '') == []


def test_controller_selects_egr_and_archives_outside_step(mocker):
  params = mocker.Mock()
  params.get.return_value = None
  archive = mocker.patch('openpilot.selfdrive.car.obd_scan_controller.archive_report')
  controller = ObdScanController(SimpleNamespace(carFingerprint=VEHICLE['fingerprint'], carVin=VEHICLE['vin'], passive=False), params)
  sm = FakeSM()
  sm['pandaStates'][0].safetyParam = 60
  cs = SimpleNamespace(canValid=True, gearShifter='park', vEgo=0.)
  batch = [(100_000_000_000, [CanData(0x1F5, bytes(8), 0), CanData(0x34A, bytes(5), 0)])]
  controller._requests.put({'version': 1, 'command': 'scan_gm', 'request_id': 'egr', 'issued_mono': 100.})
  assert controller.step(100., batch, cs, sm, True)
  assert isinstance(controller.gm_scanner, GmEgrScanner)
  archive.assert_not_called()
  params.put.assert_not_called()
  sm['pandaStates'][0].safetyParam = 28
  assert controller.step(100.01, batch, cs, sm, True) == []
  assert controller.gm_scanner.status['state'] == 'cancelled'
  controller.poll_params()
  archive.assert_called_once()
  assert not any(call.args[0] == 'GmLastScan' for call in params.put.call_args_list)


def test_archive_failure_retains_last_good_and_reports_error(mocker):
  scanner, _ = simulate()
  old = {'version': 1, 'timestamp': 'older'}
  params = mocker.Mock()
  params.get.side_effect = lambda key: old if key == 'GmLastScan' else None
  controller = ObdScanController(SimpleNamespace(carFingerprint=VEHICLE['fingerprint'], carVin=VEHICLE['vin'], passive=False), params)
  controller._gm_snapshot = (scanner.status, scanner.report)
  archive = mocker.patch('openpilot.selfdrive.car.obd_scan_controller.archive_report', side_effect=OSError('disk full'))
  controller.poll_params()
  archive.assert_called_once_with(old)
  writes = params.put.call_args_list
  assert not any(call.args[0] == 'GmLastScan' for call in writes)
  assert any(call.args[0] == 'GmScanStatus' and call.args[1]['archive_error'] == 'disk full' for call in writes)


def test_no_responses_cannot_replace_a_saved_report():
  scanner = GmEgrScanner()
  scanner.start('silent', VEHICLE, 100.)
  for step in range(12001):
    scanner.tick(100 + step / 100, [], '')
    if not scanner.active:
      break
  assert scanner.report is None and scanner.status['state'] == 'error'
