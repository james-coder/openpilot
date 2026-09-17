"""Off-road extraction UI. This panel never sends a diagnostic request."""
import json
import subprocess
import sys
import time

from openpilot.selfdrive.car.gm_trip_data import trip_rows
from openpilot.selfdrive.car.gm_trip_monitor import TRIP_ROOT
from openpilot.selfdrive.ui.layouts.settings.obd_diagnostics import ObdDiagnosticsLayout
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.widgets.list_view import button_item


class GmTripLayout(ObdDiagnosticsLayout):
  ROWS = staticmethod(trip_rows)

  def __init__(self):
    super().__init__()
    self._worker = None
    self._loaded = False
    self._action = button_item(self._title, lambda: 'Cancel' if self._active else 'Extract', callback=self._request,
                               enabled=lambda: self._active or not ui_state.started)

  def _title(self):
    return 'Passive trip context (existing full route logs)'

  def _request(self):
    if self._worker is not None and self._worker.poll() is None:
      self._worker.terminate()
      return
    if ui_state.started:
      return
    self._worker = subprocess.Popen([sys.executable, '-m', 'openpilot.tools.car_porting.gm_trip_report', '--offroad-only'],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    self._active = True
    self._local_error = ''

  def _load(self):
    path = TRIP_ROOT / 'latest_report.json'
    if path.is_file():
      with path.open('rb') as stream:
        payload = stream.read(8 * 1024**2 + 1)
      if len(payload) > 8 * 1024**2:
        raise ValueError('Trip report exceeds display size limit')
      report = json.loads(payload)
      if report.get('profile') != 'gm_trip':
        raise ValueError('Wrong trip report profile')
      self._report = report
    self._loaded = True

  def _update_state(self):
    if self._faulted or time.monotonic() - self._last_poll < 1:
      return
    self._last_poll = time.monotonic()
    try:
      if not self._loaded:
        self._load()
      status_path = TRIP_ROOT / 'status.json'
      if status_path.exists():
        with status_path.open('rb') as stream:
          self._report['preservation'] = json.loads(stream.read(8192))
      self._reason = 'Extraction is off-road only. Full route logging continues during driving.' if ui_state.started else ''
      if self._worker is not None:
        if ui_state.started and self._worker.poll() is None:
          self._worker.terminate()
        code = self._worker.poll()
        self._active = code is None
        if code is not None:
          self._worker = None
          if code == 0:
            self._load()
          else:
            self._local_error = 'Extraction stopped/unavailable; previous report retained. Raw logs are unchanged.'
      self._status['message'] = 'Extracting full logs at low priority...' if self._active else 'Passive context only; not an EGR flow test.'
      self._refresh_results()
    except (OSError, ValueError, TypeError, KeyError):
      self._fault()
