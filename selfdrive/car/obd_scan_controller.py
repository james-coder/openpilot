"""Bridge the UI's parameter mailbox to card's sole CAN publisher."""
import copy
import queue

from opendbc.car.can_definitions import CanData
from opendbc.car.gm.values import CAR
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.car.obd_scan import MAX_FRAMES_PER_TICK, OBD_SAFETY_FLAG, ObdScanner, scan_block_reason
from openpilot.selfdrive.car.gm_diagnostics import GM_DIAGNOSTIC_FLAG, GM_RESPONSES, GM_NEGATIVES, GmDiagnosticScanner
from openpilot.selfdrive.car.gm_egr import GmEgrScanner
from openpilot.selfdrive.car.gm_egr_data import EGR_SAFETY_FLAG
from openpilot.selfdrive.car.gm_egr_archive import archive_report


class ObdScanController:
  def __init__(self, CP, params, replay=False):
    self.params = params
    self.supported = CP.carFingerprint == CAR.CHEVROLET_VOLT and not CP.passive and not replay
    self.vehicle = {"fingerprint": CP.carFingerprint, "vin": CP.carVin}
    self.scanner = ObdScanner()
    self.gm_scanner = GmDiagnosticScanner()
    self._gm_snapshot = self._gm_written = self._gm_signature = None
    self._gm_last_publish = 0.
    self._requests = queue.SimpleQueue()
    self._snapshot = None
    self._written = None
    self._signature = None
    self._last_publish = 0.
    self._last_gear = self._last_speed = -100.
    self._faulted = False
    self._archive_ready = False

  def poll_params(self):
    """Called by the background parameter thread; all disk I/O stays here."""
    if not self.supported:
      return
    request = self.params.get("ObdScanRequest")
    if request is not None:
      self.params.remove("ObdScanRequest")
      if isinstance(request, dict):
        self._requests.put(request)
    snapshot = self._snapshot
    if snapshot is not None and snapshot is not self._written:
      status, report = snapshot
      self.params.put("ObdScanStatus", status, block=True)
      if report is not None and (self._written is None or report != self._written[1]):
        self.params.put("ObdLastScan", report, block=True)
      self._written = snapshot
    snapshot = self._gm_snapshot
    if snapshot is not None and snapshot is not self._gm_written:
      status, report = snapshot
      if status.get('version') == 2:
        try:
          if not self._archive_ready:
            for key in ('GmLastScan', 'ObdLastScan'):
              old = self.params.get(key)
              if isinstance(old, dict):
                archive_report(old)
            self._archive_ready = True
          if status.get('state') in ('partial', 'complete', 'cancelled', 'error'):
            archive_report(report or status)
        except (OSError, ValueError) as error:
          status = copy.deepcopy(status)
          status['archive_error'] = str(error)
          status['message'] = 'Evidence archive failed; previous saved report retained.'
          report = None
      self.params.put('GmScanStatus', status, block=True)
      if report is not None and (self._gm_written is None or report != self._gm_written[1]):
        self.params.put('GmLastScan', report, block=True)
      self._gm_written = snapshot

  def step(self, now, batches, CS, sm, initialized):
    if not self.supported or self._faulted:
      return []
    try:
      return self._step(now, batches, CS, sm, initialized)
    except Exception:
      cloudlog.exception("Disabling check-engine scanner after unexpected error")
      self._faulted = True
      self.scanner.cancel("Scanner unavailable; restart openpilot to retry.")
      self.gm_scanner.cancel('Scanner unavailable; restart openpilot to retry.')
      status = copy.deepcopy(self.scanner.status)
      status.update(state="error", message="Scanner unavailable; restart openpilot to retry.", available=False)
      self._snapshot = (status, None)
      gm_status = copy.deepcopy(self.gm_scanner.status)
      gm_status.update(state='error', message=status['message'], available=False, vehicle=self.vehicle)
      self._gm_snapshot = (gm_status, None)
      return []

  def _step(self, now, batches, CS, sm, initialized):
    frames = []
    for timestamp, batch in batches:
      if not 0 <= now - timestamp / 1e9 < .5:
        continue
      for addr, data, bus in batch:
        if bus == 0:
          if addr == 0x1F5 and len(data) == 8:
            self._last_gear = timestamp / 1e9
          elif addr == 0x34A and len(data) == 5:
            self._last_speed = timestamp / 1e9
          if (0x7E8 <= addr <= 0x7EF or addr in GM_RESPONSES or addr in GM_NEGATIVES) and len(frames) <= MAX_FRAMES_PER_TICK:
            frames.append(CanData(addr, data, bus))
    pandas = sm["pandaStates"]
    fresh = (CS.canValid and sm.all_checks(["carControl", "pandaStates", "onroadEvents"]) and
             0 <= now - sm.logMonoTime["carControl"] / 1e9 <= .15 and
             0 <= now - sm.logMonoTime["pandaStates"] / 1e9 <= .5 and
             now - self._last_gear < .5 and now - self._last_speed < .5)
    started = any(p.ignitionLine or p.ignitionCan for p in pandas)
    firmware_ready = bool(pandas) and all(p.safetyModel == "gm" and p.safetyParam & OBD_SAFETY_FLAG for p in pandas)
    enabled = sm["carControl"].enabled or any(p.controlsAllowed for p in pandas)
    reason = scan_block_reason(supported=True, started=started, initialized=initialized, fresh=fresh,
                               park=CS.gearShifter == "park", speed=CS.vEgo, enabled=enabled, firmware_ready=firmware_ready)
    gm_reason = reason or ('' if all(p.safetyParam & GM_DIAGNOSTIC_FLAG for p in pandas) else 'Matching GM diagnostic firmware required.')
    try:
      request = self._requests.get_nowait()
    except queue.Empty:
      request = None
    if request is not None:
      valid = (request.get("version") == 1 and isinstance(request.get("request_id"), str) and
               0 < len(request["request_id"]) <= 64 and isinstance(request.get("issued_mono"), (int, float)) and
               0 <= now - request["issued_mono"] < 2.)
      if valid and request.get('command') == 'cancel':
        for scanner in (self.scanner, self.gm_scanner):
          if request['request_id'] == scanner.status.get('request_id'):
            scanner.cancel()
      elif valid and request.get('command') in ('scan', 'scan_gm'):
        if request['command'] == 'scan_gm' and not self.gm_scanner.active:
          enhanced = bool(pandas) and all(p.safetyParam & EGR_SAFETY_FLAG for p in pandas)
          if enhanced != isinstance(self.gm_scanner, GmEgrScanner):
            self.gm_scanner = GmEgrScanner() if enhanced else GmDiagnosticScanner()
        scanner = self.gm_scanner if request['command'] == 'scan_gm' else self.scanner
        other = self.scanner if scanner is self.gm_scanner else self.gm_scanner
        blocked = (gm_reason if scanner is self.gm_scanner else reason) or ('Another diagnostic scan is running.' if other.active else '')
        if not scanner.active:
          if blocked:
            scanner.status = {'version': 1, 'request_id': request['request_id'], 'vehicle': self.vehicle, 'state': 'error', 'message': blocked}
            scanner.revision += 1
          else:
            scanner.start(request['request_id'], self.vehicle, now)
            frames = []
    sends = self.scanner.tick(now, frames, reason)
    egr_reason = gm_reason
    if isinstance(self.gm_scanner, GmEgrScanner) and not all(p.safetyParam & EGR_SAFETY_FLAG for p in pandas):
      egr_reason = egr_reason or 'Matching EGR diagnostic firmware required.'
    sends += self.gm_scanner.tick(now, frames, egr_reason)
    signature = (self.scanner.revision, reason)
    if signature != self._signature or (self.scanner.active and now - self._last_publish >= .5):
      status = copy.deepcopy(self.scanner.status)
      status.update(vehicle=self.vehicle, available=not reason, block_reason=reason, updated_mono=now)
      self._snapshot = (status, copy.deepcopy(self.scanner.report))
      self._signature = signature
      self._last_publish = now
    gm_signature = (self.gm_scanner.revision, gm_reason)
    if gm_signature != self._gm_signature or (self.gm_scanner.active and now - self._gm_last_publish >= .5):
      status = copy.deepcopy(self.gm_scanner.status)
      status.update(profile='gm', vehicle=self.vehicle, available=not gm_reason, block_reason=gm_reason, updated_mono=now)
      self._gm_snapshot = (status, copy.deepcopy(self.gm_scanner.report))
      self._gm_signature = gm_signature
      self._gm_last_publish = now
    return sends
