import time
import uuid
from datetime import datetime
from html import escape

import pyray as rl

from opendbc.car.gm.values import CAR
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.car.obd_scan import OBD_SAFETY_FLAG, scan_block_reason
from openpilot.selfdrive.ui.layouts.settings.obd_diagnostics_data import valid_report, lamp_status, result_rows
from openpilot.selfdrive.ui.ui_state import ui_state, device
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.list_view import ITEM_BASE_HEIGHT, ListItem, TextAction, button_item
from openpilot.system.ui.widgets.scroller_tici import Scroller


class ObdResultItem(ListItem):
  def set_parent_rect(self, rect):
    super().set_parent_rect(rect)
    self._rect.height = self.get_item_height(self._font, int(rect.width - 40))

  def show_event(self):
    super().show_event()
    self._set_description_visible(True)


class ObdDiagnosticsLayout(Widget):
  def __init__(self):
    super().__init__()
    self._params = Params()
    self._status = {}
    self._report = {}
    self._last_poll = 0.
    self._request_id = None
    self._requested_at = 0.
    self._local_error = ""
    self._reason = "Waiting for vehicle data."
    self._faulted = False
    self._active = False
    self._awaiting = False
    self._rows = []
    self._scroller = Scroller([], line_separator=True, spacing=0)
    self._action = button_item(lambda: f"Check engine: {lamp_status(self._report)}", lambda: "Cancel" if self._active else "Scan", callback=self._request,
                               enabled=lambda: self._active or not self._reason)

  def show_event(self):
    super().show_event()
    self._last_poll = 0.
    self._action.show_event()
    self._scroller.show_event()

  def hide_event(self):
    super().hide_event()
    device.set_override_interactive_timeout(None)
    self._action.hide_event()
    self._scroller.hide_event()

  def _request(self):
    try:
      now = time.monotonic()
      cancel = self._active
      if cancel:
        request_id = self._request_id or self._status.get("request_id")
      else:
        if self._reason:
          return
        request_id = uuid.uuid4().hex
        self._request_id = request_id
        self._requested_at = now
        self._active = True
      self._params.put("ObdScanRequest", {"version": 1, "request_id": request_id,
                       "command": "cancel" if cancel else "scan", "issued_mono": now})
      self._local_error = ""
    except Exception:
      self._fault()

  def _fault(self):
    cloudlog.exception("Check-engine panel unavailable")
    self._faulted = True
    self._active = False
    self._reason = "Check-engine panel unavailable; see logs."
    device.set_override_interactive_timeout(None)

  def _update_state(self):
    if self._faulted:
      return
    try:
      now = time.monotonic()
      CP, sm = ui_state.CP, ui_state.sm
      CS = sm['carState']
      pandas = sm['pandaStates']
      self._reason = scan_block_reason(
        supported=CP is not None and CP.carFingerprint == CAR.CHEVROLET_VOLT and not CP.passive,
        started=ui_state.started, initialized=sm.seen['carState'],
        fresh=sm.all_checks(['carState', 'pandaStates']) and 0 <= now-sm.logMonoTime['carState']/1e9 < .5,
        park=CS.gearShifter == "park", speed=CS.vEgo, enabled=ui_state.engaged or any(p.controlsAllowed for p in pandas),
        firmware_ready=bool(pandas) and all(p.safetyModel == "gm" and p.safetyParam & OBD_SAFETY_FLAG for p in pandas))
      device.set_override_interactive_timeout(300 if not ui_state.started or not self._reason else None)
      if now - self._last_poll < .2:
        return
      self._last_poll = now
      persisted_cp = self._params.get("CarParamsPersistent") if CP is None else None
      if persisted_cp:
        from cereal import car
        with car.CarParams.from_bytes(persisted_cp) as previous:
          vehicle = {"fingerprint": previous.carFingerprint, "vin": previous.carVin}
      else:
        vehicle = {"fingerprint": CP.carFingerprint, "vin": CP.carVin} if CP is not None else None
      status = valid_report(self._params.get("ObdScanStatus"), vehicle) or {}
      previous = valid_report(self._params.get("ObdLastScan"), vehicle) or {}
      self._status = status
      status_active = (status.get("state") == "scanning" and isinstance(status.get("updated_mono"), (float, int)) and
                       0 <= now - status["updated_mono"] < 2 and ui_state.started)
      awaiting = self._request_id is not None and status.get("request_id") != self._request_id and now-self._requested_at < 2
      self._awaiting = awaiting
      self._active = status_active or awaiting
      if status.get("state") == "scanning" and not status_active and not awaiting:
        self._local_error = "Scan interrupted. Previous saved results are shown."
      if self._request_id and not self._active:
        if status.get("request_id") != self._request_id or status.get("state") == "scanning":
          self._local_error = "Scan stopped or did not start. Try again with the car on and in Park."
        self._request_id = None
      current = status.get("state") in ("complete", "partial") or status_active
      self._report = status if status.get("ecus") and current else previous
      self._refresh_results()
    except Exception:
      self._fault()

  def _refresh_results(self):
    rows = result_rows(self._report)
    if rows != self._rows:
      self._rows = rows
      items = [ObdResultItem(title=title, action_item=TextAction(value), description=escape(detail), description_visible=True)
               for title, value, detail in rows]
      self._scroller = Scroller(items, line_separator=True, spacing=0)
      self._scroller.show_event()

  def _render(self, rect):
    try:
      header = rl.Rectangle(rect.x, rect.y, rect.width, ITEM_BASE_HEIGHT)
      self._action.set_parent_rect(header)
      self._action.render(header)
      font = gui_app.font(FontWeight.NORMAL)
      message = self._local_error or (self._status.get("message") if self._active else self._reason) or self._status.get("message", "Ready to scan.")
      if self._awaiting:
        message = "Starting scan..."
      rl.draw_text_ex(font, message, rl.Vector2(rect.x+20, rect.y+175), 28, 0, rl.WHITE)
      stamp = self._report.get("timestamp")
      try:
        label = "Last scan: " + datetime.fromisoformat(stamp).astimezone().strftime('%b %d, %H:%M:%S') if stamp else "No scan results yet."
      except (ValueError, TypeError):
        label = "Previous scan (time unavailable)."
      if self._report.get("state") == "partial":
        label += " | partial results"
      rl.draw_text_ex(font, label, rl.Vector2(rect.x+20, rect.y+220), 28, 0, rl.GRAY)
      self._scroller.render(rl.Rectangle(rect.x, rect.y+270, rect.width, rect.height-270))
    except Exception:
      if not self._faulted:
        self._fault()
