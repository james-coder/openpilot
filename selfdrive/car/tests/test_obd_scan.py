import copy
import pytest
from types import SimpleNamespace

from opendbc.car.can_definitions import CanData
from opendbc.car.gm.values import CAR
from openpilot.selfdrive.car.obd_scan import ObdScanner, decode_reply, scan_block_reason
from openpilot.selfdrive.car.obd_scan_controller import ObdScanController
from openpilot.selfdrive.ui.layouts.settings.obd_diagnostics_data import lamp_status, result_rows, valid_report, descriptions


VEHICLE = {"fingerprint": CAR.CHEVROLET_VOLT, "vin": "test-vehicle"}


def sf(data, addr=0x7E8, bus=0):
  return CanData(addr, (bytes([len(data)]) + data).ljust(8, b"\x00"), bus)


class TestObdScanner:
  def setup_method(self):
    self.scanner = ObdScanner()
    self.scanner.start("test", VEHICLE, 100.)
    assert self.scanner.tick(100., [], "") == [sf(b"\x01\x01", 0x7DF)]

  def test_all_services_and_multiple_ecus(self):
    replies = (b"\x41\x01\x81\x00\x00\x00", b"\x43\x01\x03\x00", b"\x47\x01\x01\x71", b"\x4a\x00")
    for stage, reply in enumerate(replies):
      if stage:
        sends = self.scanner.tick(100.+2*stage, [], "")
        assert len(sends) == 1
        assert sends[0].address == 0x7DF
      self.scanner.tick(100.1+2*stage, [sf(reply), sf(reply, 0x7E9)], "")
    assert self.scanner.tick(108., [], "") == []
    report = self.scanner.report
    assert report['state'] == 'complete'
    assert set(report['ecus']) == {'7E8', '7E9'}
    assert report['ecus']['7E8']['stored']['codes'] == ['P0300']
    assert report['ecus']['7E9']['pending']['codes'] == ['P0171']
    assert lamp_status(report) == 'On'
    assert valid_report(report, VEHICLE) is not None
    assert valid_report(report, {**VEHICLE, 'vin': 'another-car'}) is None
    rows = result_rows(report)
    assert sum(title == 'P0300' for title, _, _ in rows) == 2
    assert any('Misfire' in detail for _, _, detail in rows)

  def test_multiframe_reply_and_flow_control(self):
    self.scanner.tick(102., [], '')
    # Four DTCs, including an alphanumeric manufacturer code and network code.
    data = bytes.fromhex('43 04 03 00 01 71 1E 00 C1 00')
    first = CanData(0x7EA, bytes([0x10, len(data)]) + data[:6], 0)
    assert self.scanner.tick(102.1, [first], '') == [CanData(0x7E2, bytes.fromhex('30 00 0A 00 00 00 00 00'), 0)]
    last = CanData(0x7EA, (b'\x21' + data[6:]).ljust(8, b'\x00'), 0)
    assert self.scanner.tick(102.2, [last], '') == []
    assert self.scanner.status['ecus']['7EA']['stored']['codes'] == ['P0171', 'P0300', 'P1E00', 'U0100']

  def test_cancel_prevents_requests_and_flow_control(self):
    self.scanner.tick(102., [], '')
    first = CanData(0x7E8, bytes.fromhex('10 08 43 03 03 00 01 71'), 0)
    assert self.scanner.tick(102.1, [first], 'Shift to Park.') == []
    assert not self.scanner.active
    assert self.scanner.status['state'] == 'cancelled'
    assert self.scanner.tick(104., [], '') == []
    assert self.scanner.report is None

  def test_malformed_reply_does_not_hide_other_ecu(self):
    self.scanner.tick(100.1, [sf(b'\x41\x01'), sf(bytes.fromhex('41 01 00 00 00 00'), 0x7E9)], '')
    self.scanner.tick(110., [], '')
    assert self.scanner.report['state'] == 'partial'
    assert lamp_status(self.scanner.report) == 'Unknown'

  def test_silence_is_not_no_codes_or_light_off(self):
    self.scanner.tick(110., [], '')
    assert self.scanner.report is None
    assert self.scanner.status['state'] == 'error'
    assert lamp_status(self.scanner.status) == 'Unknown'

  def test_unsupported_and_response_pending_have_fixed_deadline(self):
    self.scanner.tick(100.1, [sf(b'\x7f\x01\x11'), sf(b'\x7f\x01\x78', 0x7E9)], '')
    assert self.scanner.status['ecus']['7E8']['lamp']['state'] == 'unsupported'
    self.scanner.tick(100.2, [sf(bytes.fromhex('41 01 00 00 00 00'), 0x7E9)], '')
    assert self.scanner.status['ecus']['7E9']['lamp']['state'] == 'ok'
    self.scanner.tick(102., [], '')
    assert self.scanner.status['progress'] == 1

  def test_bad_bus_address_service_and_oversize(self):
    self.scanner.tick(100.1, [sf(bytes.fromhex('41 01 00 00 00 00'), bus=1), sf(b'\x43\x00'), sf(b'\x41', 0x700)], '')
    assert not self.scanner.status['ecus']
    assert self.scanner.tick(100.2, [CanData(0x7E8, bytes.fromhex('12 01 41 01 00 00 00 00'), 0)], '') == []
    assert self.scanner.status['ecus']['7E8']['lamp']['state'] == 'error'

  def test_partial_frame_timeout_and_bad_sequence(self):
    self.scanner.tick(102., [], '')
    first = CanData(0x7E8, bytes.fromhex('10 08 43 03 03 00 01 71'), 0)
    self.scanner.tick(102.1, [first, CanData(0x7E9, first.dat, 0)], '')
    self.scanner.tick(102.2, [CanData(0x7E9, bytes.fromhex('22 04 20 00 00 00 00 00'), 0)], '')
    self.scanner.tick(104., [], '')
    assert self.scanner.status['ecus']['7E8']['stored']['state'] == 'timeout'
    assert self.scanner.status['ecus']['7E9']['stored']['state'] == 'error'

  def test_code_count_and_padding(self, subtests):
    for reply in (b'\x43', bytes.fromhex('43 02 03 00'), bytes.fromhex('43 00 03 00')):
      with subtests.test(), pytest.raises(ValueError):
        decode_reply('stored', reply)
    assert decode_reply('stored', bytes.fromhex('43 01 03 00 00 00'))['codes'] == ['P0300']
    assert decode_reply('permanent', bytes.fromhex('4A 00 00 00'))['codes'] == []

  def test_pending_only_ecu_is_not_silently_omitted(self):
    self.scanner.tick(100.1, [sf(b'\x7f\x01\x78')], '')
    self.scanner.tick(110., [], '')
    assert self.scanner.status['ecus']['7E8']['lamp']['state'] == 'timeout'
    assert lamp_status(self.scanner.status) == 'Unknown'

  def test_excess_traffic_cancels_without_sending(self):
    assert self.scanner.tick(100.1, [sf(b'\x7f\x01\x78')] * 129, '') == []
    assert self.scanner.status['state'] == 'cancelled'

  def test_dictionary_corrects_throttle_direction_and_module_names(self):
    assert descriptions()['P2111'] == 'The throttle actuator is stuck open.'
    assert descriptions()['P2112'] == 'The throttle actuator is stuck closed.'
    assert 'body controller' in descriptions()['U0140']
    assert 'instrument cluster' in descriptions()['U0155']
    assert 'U0120' not in descriptions()

  def test_saved_results_and_unknown_description(self):
    report = {'version': 1, 'vehicle': VEHICLE, 'ecus': {'7E8': {'lamp': {'state': 'ok', 'mil': False, 'count': 1},
              'stored': {'state': 'ok', 'codes': ['P3FFF']}}}}
    assert lamp_status(report) == 'Off'
    assert any('Description unavailable.' in detail for _, _, detail in result_rows(report))
    malformed = copy.deepcopy(report)
    malformed['ecus']['7E8']['stored']['codes'] = 'P0300'
    assert valid_report(malformed, VEHICLE) is None
    assert 'requested' in descriptions()['P1E00']


class FakeSM(dict):
  def __init__(self):
    super().__init__(carControl=SimpleNamespace(enabled=False),
                     pandaStates=[SimpleNamespace(ignitionLine=True, ignitionCan=False, safetyModel='gm', safetyParam=12, controlsAllowed=False)],
                     onroadEvents=[])
    self.logMonoTime = {'carControl': 100_000_000_000, 'pandaStates': 100_000_000_000}
    self.valid = True

  def all_checks(self, _):
    return self.valid


class TestObdController:
  @pytest.fixture(autouse=True)
  def setup_method(self, mocker):
    self.mocker = mocker
    self.reset()

  def reset(self):
    self.params = self.mocker.Mock()
    self.params.get.return_value = None
    self.CP = SimpleNamespace(carFingerprint=CAR.CHEVROLET_VOLT, carVin=VEHICLE['vin'], passive=False)
    self.controller = ObdScanController(self.CP, self.params)
    self.CS = SimpleNamespace(canValid=True, gearShifter='park', vEgo=0.)
    self.sm = FakeSM()
    self.batches = [(100_000_000_000, [CanData(0x1F5, bytes(8), 0), CanData(0x34A, bytes(5), 0)])]
    self.request = {'version': 1, 'command': 'scan', 'request_id': 'test', 'issued_mono': 100.}

  def start(self):
    self.params.get.return_value = self.request
    self.controller.poll_params()
    return self.controller.step(100., self.batches, self.CS, self.sm, True)

  def test_request_and_disk_io_is_outside_main_loop(self):
    sends = self.start()
    assert len(sends) == 1
    self.params.put.assert_not_called()
    self.params.get.return_value = None
    self.controller.poll_params()
    assert [call.args[0] for call in self.params.put.call_args_list] == ['ObdScanStatus', 'GmScanStatus']
    self.params.put.reset_mock()
    self.controller.poll_params()
    self.params.put.assert_not_called()

  def test_fail_closed_conditions_and_replay(self, subtests):
    for field, value in [('gearShifter', 'drive'), ('vEgo', 1.), ('vEgo', float('nan')), ('canValid', False)]:
      with subtests.test():
        self.reset()
        setattr(self.CS, field, value)
        assert self.start() == []
    for flag in ('enabled', 'stale_control', 'stale_speed', 'no_flag', 'not_initialized', 'ignition_off', 'panda_allowed'):
      with subtests.test():
        self.reset()
        if flag == 'enabled':
          self.sm['carControl'].enabled = True
        elif flag == 'stale_control':
          self.sm.logMonoTime['carControl'] = 98_000_000_000
        elif flag == 'stale_speed':
          self.batches = [(98_000_000_000, self.batches[0][1])]
        elif flag == 'no_flag':
          self.sm['pandaStates'][0].safetyParam = 4
        elif flag == 'ignition_off':
          self.sm['pandaStates'][0].ignitionLine = False
        elif flag == 'panda_allowed':
          self.sm['pandaStates'][0].controlsAllowed = True
        self.controller._requests.put(self.request)
        assert self.controller.step(100., self.batches, self.CS, self.sm, flag != 'not_initialized') == []
    self.reset()
    self.controller = ObdScanController(self.CP, self.params, replay=True)
    assert self.start() == []
    self.params.get.assert_not_called()

  def test_leaving_park_cancels_existing_scan(self):
    self.start()
    self.CS.gearShifter = 'drive'
    assert self.controller.step(100.01, self.batches, self.CS, self.sm, True) == []
    assert self.controller.scanner.status['state'] == 'cancelled'

  def test_expired_request_and_wrong_cancel_id(self):
    self.request['issued_mono'] = 90.
    assert self.start() == []
    self.reset()
    self.start()
    self.controller._requests.put({**self.request, 'command': 'cancel', 'request_id': 'other'})
    self.controller.step(100.01, self.batches, self.CS, self.sm, True)
    assert self.controller.scanner.active
    self.controller._requests.put({**self.request, 'command': 'cancel'})
    self.controller.step(100.02, self.batches, self.CS, self.sm, True)
    assert not self.controller.scanner.active

  def test_unexpected_failure_is_contained(self):
    self.controller.scanner.tick = self.mocker.Mock(side_effect=RuntimeError('simulated'))
    assert self.start() == []
    assert self.controller._faulted
    assert self.controller.step(100., self.batches, self.CS, self.sm, True) == []

  def test_complete_bridge_persists_results_and_failed_retry_preserves_them(self):
    self.start()
    self.params.get.return_value = None

    def tick(now, frames=()):
      timestamp = int(now * 1e9)
      self.sm.logMonoTime = dict.fromkeys(self.sm.logMonoTime, timestamp)
      batches = [(timestamp, [*self.batches[0][1], *frames])]
      sends = self.controller.step(now, batches, self.CS, self.sm, True)
      self.controller.poll_params()
      return sends

    replies = ('41 01 81 00 00 00', '43 01 03 00', '47 00', '4A 00')
    for stage, reply in enumerate(replies):
      if stage:
        assert len(tick(100. + 2 * stage)) == 1
      assert tick(100.1 + 2 * stage, [sf(bytes.fromhex(reply))]) == []
    assert tick(108.) == []
    saved = [call.args[1] for call in self.params.put.call_args_list if call.args[0] == 'ObdLastScan']
    assert len(saved) == 1
    assert saved[0]['state'] == 'complete'
    assert saved[0]['ecus']['7E8']['stored']['codes'] == ['P0300']
    self.params.put.reset_mock()
    self.controller._requests.put({**self.request, 'request_id': 'retry', 'issued_mono': 109.})
    assert len(tick(109.)) == 1
    assert tick(119.) == []
    assert self.controller.scanner.status['state'] == 'error'
    assert all(call.args[0] != 'ObdLastScan' for call in self.params.put.call_args_list)

  def test_real_capnp_enums_and_volt_safety_configuration(self):
    from collections import defaultdict
    from cereal import car, log
    from opendbc.car.gm.interface import CarInterface

    CP = CarInterface.get_params(CAR.CHEVROLET_VOLT, defaultdict(dict), [], False, False, False)
    assert CP.safetyConfigs[0].safetyParam & 8
    assert CP.safetyConfigs[0].safetyParam & 32
    self.controller = ObdScanController(CP, self.params)
    self.CS = car.CarState.new_message(canValid=True, gearShifter='park', vEgo=0.)
    self.sm['carControl'] = car.CarControl.new_message(enabled=False)
    self.sm['pandaStates'] = [log.PandaState.new_message(ignitionLine=True, safetyModel='gm', safetyParam=12, controlsAllowed=False)]
    assert len(self.start()) == 1


class TestScanPermission:
  def test_default_is_manual_parked_scan(self):
    args = dict(supported=True, started=True, initialized=True, fresh=True, park=True, speed=0., enabled=False, firmware_ready=True)
    assert scan_block_reason(**args) == ''
    for field in ('supported', 'started', 'initialized', 'fresh', 'park', 'firmware_ready'):
      assert scan_block_reason(**{**args, field: False})
