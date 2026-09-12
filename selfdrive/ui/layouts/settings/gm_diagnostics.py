from openpilot.selfdrive.car.gm_diagnostics import GM_DIAGNOSTIC_FLAG
from openpilot.selfdrive.car.obd_scan import OBD_SAFETY_FLAG
from openpilot.selfdrive.ui.layouts.settings.obd_diagnostics import ObdDiagnosticsLayout
from openpilot.selfdrive.ui.layouts.settings.gm_diagnostics_data import valid_gm_report, gm_rows


class GmDiagnosticsLayout(ObdDiagnosticsLayout):
  STATUS_KEY = 'GmScanStatus'
  REPORT_KEY = 'GmLastScan'
  COMMAND = 'scan_gm'
  SAFETY_FLAGS = OBD_SAFETY_FLAG | GM_DIAGNOSTIC_FLAG
  VALIDATE = staticmethod(valid_gm_report)
  ROWS = staticmethod(gm_rows)

  def _title(self):
    return 'GM diagnostics (read-only)'
