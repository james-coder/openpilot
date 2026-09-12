from types import SimpleNamespace

import pytest

from opendbc.car.gm.values import CAR
from openpilot.selfdrive.ui.layouts.settings import obd_diagnostics as panel


@pytest.fixture
def layout(mocker):
  # Exercise mailbox/lifecycle logic without creating a graphics context.
  widget = panel.ObdDiagnosticsLayout.__new__(panel.ObdDiagnosticsLayout)
  widget._params = mocker.Mock()
  widget._params.get.return_value = None
  widget._status = {}
  widget._report = {}
  widget._last_poll = 0.
  widget._request_id = None
  widget._requested_at = 0.
  widget._local_error = ''
  widget._reason = ''
  widget._faulted = False
  widget._active = False
  widget._awaiting = False
  widget._refresh_results = mocker.Mock()
  mocker.patch.object(panel.time, 'monotonic', return_value=100.)
  mocker.patch.object(panel, 'device')
  CP = SimpleNamespace(carFingerprint=CAR.CHEVROLET_VOLT, carVin='test', passive=False)

  class SM(dict):
    seen = {'carState': True}
    logMonoTime = {'carState': 100_000_000_000}

    def all_checks(self, _):
      return True

  sm = SM(carState=SimpleNamespace(gearShifter='park', vEgo=0.),
          pandaStates=[SimpleNamespace(controlsAllowed=False, safetyModel='gm', safetyParam=12)])
  mocker.patch.object(panel, 'ui_state', SimpleNamespace(CP=CP, sm=sm, started=True, engaged=False))
  return widget


def test_manual_scan_and_cancel_use_same_id(layout):
  layout._request()
  request = layout._params.put.call_args.args[1]
  assert request['command'] == 'scan'
  assert request['version'] == 1
  assert layout._active
  layout._request()
  cancel = layout._params.put.call_args.args[1]
  assert cancel['command'] == 'cancel'
  assert cancel['request_id'] == request['request_id']


def test_blocked_panel_does_not_submit_request(layout):
  layout._reason = 'Shift to Park.'
  layout._request()
  layout._params.put.assert_not_called()


def test_stale_scan_falls_back_to_saved_report(layout):
  vehicle = {'fingerprint': CAR.CHEVROLET_VOLT, 'vin': 'test'}
  status = {'version': 1, 'vehicle': vehicle, 'state': 'scanning', 'updated_mono': 90., 'request_id': 'old'}
  saved = {'version': 1, 'vehicle': vehicle, 'state': 'partial',
           'ecus': {'7E8': {'lamp': {'state': 'ok', 'mil': True, 'count': 1}}}}
  layout._params.get.side_effect = lambda key: {'ObdScanStatus': status, 'ObdLastScan': saved}.get(key)
  layout._update_state()
  assert not layout._active
  assert not layout._faulted
  assert layout._report == saved
  assert 'interrupted' in layout._local_error


def test_unacknowledged_request_expires(layout, mocker):
  layout._request()
  mocker.patch.object(panel.time, 'monotonic', return_value=103.)
  layout._update_state()
  assert not layout._active
  assert layout._request_id is None
  assert 'did not start' in layout._local_error


def test_gm_panel_uses_separate_saved_report_and_firmware_gate(layout):
  from openpilot.selfdrive.ui.layouts.settings.gm_diagnostics import GmDiagnosticsLayout
  layout.__class__ = GmDiagnosticsLayout
  layout._update_state()
  assert layout._reason  # Old flag 12 must not enable GM queries.
  panel.ui_state.sm['pandaStates'][0].safetyParam = 28
  layout._last_poll = 0.
  saved = {'version': 1, 'profile': 'gm', 'vehicle': {'fingerprint': CAR.CHEVROLET_VOLT, 'vin': 'test'},
           'state': 'partial', 'modules': {'5E8': {'state': 'ok', 'status_mask': 255, 'codes': []}}}
  layout._params.get.side_effect = lambda key: saved if key == 'GmLastScan' else None
  layout._update_state()
  assert not layout._reason
  assert layout._report == saved
  assert not layout._faulted
  layout._request()
  assert layout._params.put.call_args.args[1]['command'] == 'scan_gm'


def test_egr_only_results_remain_visible_and_cancellable(layout):
  from openpilot.selfdrive.ui.layouts.settings.gm_diagnostics import GmDiagnosticsLayout
  from openpilot.selfdrive.car.tests.test_gm_egr import simulate
  layout.__class__ = GmDiagnosticsLayout
  report = simulate()[0].report
  report.update(state='scanning', updated_mono=100., modules={}, context={}, emissions={})
  panel.ui_state.sm['pandaStates'][0].safetyParam = 60
  layout._params.get.side_effect = lambda key: report if key == 'GmScanStatus' else None
  layout._update_state()
  assert layout._active and layout._report == report and not layout._faulted
  layout._request()
  assert layout._params.put.call_args.args[1]['command'] == 'cancel'
