import copy
from types import SimpleNamespace

import pytest

from opendbc.car.can_definitions import CanData
from opendbc.car.gm.values import CAR
from openpilot.selfdrive.car.gm_diagnostics import (
  CONTEXT_QUERIES, GM_REQUEST, GM_RESPONSES, GmDiagnosticScanner, context_request, context_value,
)
from openpilot.selfdrive.car.obd_scan_controller import ObdScanController
from openpilot.selfdrive.car.tests.test_obd_scan import FakeSM, sf
from openpilot.selfdrive.ui.layouts.settings.gm_diagnostics_data import gm_rows, valid_gm_report

VEHICLE = {'fingerprint': CAR.CHEVROLET_VOLT, 'vin': 'test'}


@pytest.fixture
def scanner():
  scanner = GmDiagnosticScanner()
  scanner.start('gm-test', VEHICLE, 100.)
  assert scanner.tick(100., [], '') == [CanData(0x101, GM_REQUEST, 0)]
  return scanner


def uudt(data, addr=0x5E8, bus=0):
  return CanData(addr, bytes.fromhex(data).ljust(8, b'\xaa'), bus)


def test_gm_records_have_failure_type_and_distinct_status_encoding(scanner):
  frames = [uudt('81 04 01 00 92'), uudt('81 C1 00 71 10', 0x543),
            uudt('81 00 00 00 FF'), uudt('81 00 00 00 FF', 0x543)]
  scanner.tick(100.1, frames, '')
  scanner.tick(150., [], '')
  report = scanner.report
  assert report['state'] == 'partial'  # Not an all-vehicle inventory.
  assert valid_gm_report(report, VEHICLE)
  code = report['modules']['5E8']['codes'][0]
  assert code['code'] == 'P0401' and code['failure_type'] == 0 and code['status'] == 0x92
  rows = gm_rows(report)
  assert any(title == 'P0401-00' and 'Current' in value and 'History' in value for title, value, _ in rows)
  assert any(title == 'U0100-71' and value == 'History' for title, value, _ in rows)


def test_end_marker_is_required_and_silence_not_clean(scanner):
  scanner.tick(100.1, [uudt('81 04 01 00 12'), uudt('81 00 00 00 FF', 0x541)], '')
  scanner.tick(150., [], '')
  assert scanner.report['modules']['5E8']['state'] == 'incomplete'
  assert scanner.report['modules']['541']['state'] == 'ok'
  assert len(scanner.report['modules']) == 2
  rows = gm_rows(scanner.report)
  assert sum(title == 'No matching faults' for title, _, _ in rows) == 1


def test_malformed_and_late_records_fail_closed(scanner):
  scanner.tick(100.1, [uudt('81 00 00 00 FF'), uudt('81 04 01 00 12'),
                      CanData(0x541, b'\x81\x04', 0)], '')
  assert scanner.status['modules']['5E8']['state'] == 'error'
  assert scanner.status['modules']['541']['state'] == 'error'


def test_wrong_bus_unrelated_message_and_unmatched_status_ignored_or_flagged(scanner):
  scanner.tick(100.1, [uudt('81 04 01 00 12', bus=1), uudt('81 04 01 00 12', addr=0x540),
                      uudt('82 04 01 00 12')], '')
  assert scanner.status['modules'] == {}
  scanner.tick(100.2, [uudt('81 04 01 00 01')], '')
  assert scanner.status['modules']['5E8']['state'] == 'error'  # GM bit 0 is NOT UDS testFailed.


def test_pending_negative_uses_usdt_and_does_not_extend_deadline(scanner):
  scanner.tick(100.1, [sf(bytes.fromhex('7F A9 78')), sf(bytes.fromhex('7F A9 11'), 0x641)], '')
  assert scanner.status['modules']['5E8']['state'] == 'incomplete'
  assert scanner.status['modules']['541']['state'] == 'unsupported'
  assert scanner.tick(105., [], '') == [context_request(2, 2)]


def test_empty_report_with_missing_requested_status_bits_is_not_clean(scanner):
  scanner.tick(100.1, [uudt('81 00 00 00 02')], '')
  scanner.tick(150., [], '')
  assert any(title == 'Status coverage limited' for title, _, _ in gm_rows(scanner.report))


def test_context_association_values_and_all_requests(scanner):
  for index, (service, pid) in enumerate(CONTEXT_QUERIES):
    now = 105. + 2 * index
    assert scanner.tick(now, [], '') == [context_request(service, pid)]
    if pid == 2:
      data = bytes.fromhex('04 01')
    elif pid in (0x0C, 0x10):
      data = bytes.fromhex('10 00')
    else:
      data = bytes([128])
    payload = bytes([service + 0x40, pid]) + (b'\x00' if service == 2 else b'') + data
    assert scanner.tick(now + .1, [sf(payload)], '') == []
  assert scanner.tick(137., [], '') == []
  assert scanner.report['context']['02:02']['value'] == 'P0401'
  assert scanner.report['context']['02:0C']['value'] == 1024
  assert scanner.report['context']['01:2D']['value'] == 0
  assert valid_gm_report(scanner.report, VEHICLE)


@pytest.mark.parametrize('pid,data,expected', [(5, b'\x64', 60), (0x0F, b'\x50', 40), (6, b'\x80', 0),
                                             (7, b'\x00', -100), (0x2C, b'\xff', 100), (0x10, b'\x01\x00', 2.56),
                                             (0x0B, b'\x64', 100), (0x0D, b'\x00', 0), (2, b'\x00\x00', None)])
def test_pid_scaling(pid, data, expected):
  assert context_value(pid, data) == expected


def test_wrong_context_service_pid_frame_and_length(scanner):
  scanner.tick(105., [], '')
  scanner.tick(105.1, [sf(bytes.fromhex('42 02 01 04 01')), sf(bytes.fromhex('41 02 04 01'))], '')
  assert scanner.status['context']['02:02']['state'] == 'timeout'
  scanner.tick(105.2, [sf(bytes.fromhex('42 02 00 04'))], '')
  assert scanner.status['context']['02:02']['state'] == 'error'


def test_cancel_and_overload_never_send_more(scanner):
  assert scanner.tick(105., [], 'Shift to Park.') == []
  assert scanner.status['state'] == 'cancelled'
  assert scanner.report is None
  scanner.start('next', VEHICLE, 110.)
  assert scanner.tick(110., [uudt('81 04 01 00 12')] * 129, '') == []
  assert not scanner.active


def test_bounded_module_codes_and_duplicate_records(scanner):
  frame = uudt('81 04 01 00 12')
  scanner.tick(100.1, [frame] * 128, '')
  assert len(scanner.status['modules']['5E8']['codes']) == 1
  for n in range(1, 129):
    scanner.tick(100.2, [CanData(0x5E8, bytes([0x81, 0x04, 0x02, n, 0x12]), 0)], '')
  assert len(scanner.status['modules']['5E8']['codes']) == 128
  assert scanner.status['modules']['5E8']['state'] == 'error'


def test_report_identity_and_malformed_data(scanner):
  scanner.tick(100.1, [uudt('81 00 00 00 FF')], '')
  scanner.tick(150., [], '')
  report = scanner.report
  assert valid_gm_report(report, {'fingerprint': CAR.CHEVROLET_VOLT, 'vin': 'other'}) is None
  for changed in ({'modules': {'540': {'state': 'ok', 'codes': []}}}, {'context': {'02:FF': {'state': 'timeout'}}}):
    assert valid_gm_report({**report, **changed}, VEHICLE) is None
  corrupted = copy.deepcopy(report)
  corrupted['modules']['5E8']['status_mask'] = 'FF'
  assert valid_gm_report(corrupted, VEHICLE) is None


def test_controller_routes_gm_and_excludes_concurrent_scans(mocker):
  params = mocker.Mock()
  params.get.return_value = None
  CP = SimpleNamespace(carFingerprint=CAR.CHEVROLET_VOLT, carVin='test', passive=False)
  controller = ObdScanController(CP, params)
  sm = FakeSM()
  sm['pandaStates'][0].safetyParam = 28
  CS = SimpleNamespace(canValid=True, gearShifter='park', vEgo=0.)
  batches = [(100_000_000_000, [CanData(0x1F5, bytes(8), 0), CanData(0x34A, bytes(5), 0)])]
  request = {'version': 1, 'command': 'scan_gm', 'request_id': 'gm', 'issued_mono': 100.}
  controller._requests.put(request)
  assert controller.step(100., batches, CS, sm, True) == [CanData(0x101, GM_REQUEST, 0)]
  controller._requests.put({**request, 'command': 'scan', 'request_id': 'obd'})
  assert controller.step(100.01, batches, CS, sm, True) == []
  assert controller.scanner.status['state'] == 'error'
  assert controller.gm_scanner.active
  controller._requests.put({**request, 'command': 'cancel'})
  assert controller.step(100.02, batches, CS, sm, True) == []
  assert not controller.gm_scanner.active
  controller.poll_params()
  assert any(call.args[0] == 'GmScanStatus' for call in params.put.call_args_list)
  assert all(call.args[0] not in ('ObdLastScan', 'GmLastScan') for call in params.put.call_args_list)


def test_old_firmware_cannot_start_enhanced_scan(mocker):
  CP = SimpleNamespace(carFingerprint=CAR.CHEVROLET_VOLT, carVin='test', passive=False)
  controller = ObdScanController(CP, mocker.Mock())
  controller._requests.put({'version': 1, 'command': 'scan_gm', 'request_id': 'gm', 'issued_mono': 100.})
  CS = SimpleNamespace(canValid=True, gearShifter='park', vEgo=0.)
  batches = [(100_000_000_000, [CanData(0x1F5, bytes(8), 0), CanData(0x34A, bytes(5), 0)])]
  assert controller.step(100., batches, CS, FakeSM(), True) == []
  assert 'firmware' in controller.gm_scanner.status['message']


def test_module_address_space_is_bounded():
  assert len(GM_RESPONSES) == 39


def test_export_is_read_only_and_redacts_vin_without_mutating_report(scanner):
  from openpilot.tools.car_porting.export_gm_diagnostics import export_report
  scanner.tick(100.1, [uudt('81 04 01 00 92'), uudt('81 00 00 00 FF')], '')
  scanner.tick(150., [], '')
  report = scanner.report
  assert '"vin"' not in export_report(report, as_json=True)
  assert '"vin": "test"' in export_report(report, as_json=True, include_vin=True)
  assert report['vehicle']['vin'] == 'test'
  text = export_report(report)
  assert 'P0401-00' in text and 'Current' in text and 'Not a road test' in text


def test_gm_persistence_survives_failed_retry_and_is_separate_from_emissions(mocker):
  CP = SimpleNamespace(carFingerprint=CAR.CHEVROLET_VOLT, carVin='test', passive=False)
  params = mocker.Mock()
  params.get.return_value = None
  controller = ObdScanController(CP, params)
  sm = FakeSM()
  sm['pandaStates'][0].safetyParam = 28
  CS = SimpleNamespace(canValid=True, gearShifter='park', vEgo=0.)

  def tick(now, frames=()):
    timestamp = int(now * 1e9)
    sm.logMonoTime = dict.fromkeys(sm.logMonoTime, timestamp)
    batch = [CanData(0x1F5, bytes(8), 0), CanData(0x34A, bytes(5), 0), *frames]
    sends = controller.step(now, [(timestamp, batch)], CS, sm, True)
    controller.poll_params()
    return sends

  request = {'version': 1, 'command': 'scan_gm', 'request_id': 'gm', 'issued_mono': 100.}
  controller._requests.put(request)
  assert len(tick(100.)) == 1
  tick(100.1, [uudt('81 04 01 00 92'), uudt('81 00 00 00 FF')])
  tick(150.)
  saved = [call.args[1] for call in params.put.call_args_list if call.args[0] == 'GmLastScan']
  assert len(saved) == 1
  assert valid_gm_report(saved[0], VEHICLE)
  assert all(call.args[0] != 'ObdLastScan' for call in params.put.call_args_list)
  params.put.reset_mock()
  controller._requests.put({**request, 'request_id': 'retry', 'issued_mono': 151.})
  tick(151.)
  tick(201.)
  assert controller.gm_scanner.status['state'] == 'error'
  assert all(call.args[0] != 'GmLastScan' for call in params.put.call_args_list)
