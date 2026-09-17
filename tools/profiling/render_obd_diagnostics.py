"""Render the real check-engine widgets with fixture data, without accessing a vehicle.

DISPLAY=:0 BIG=1 SCALE=1 OFFSCREEN=1 python -m tools.profiling.render_obd_diagnostics --output /tmp/obd-preview
"""
import argparse
from pathlib import Path

import pyray as rl

from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.ui.layouts.settings.can_diagnostics import CanDiagnosticsLayout
from openpilot.system.ui.lib.application import gui_app, FontWeight


def render_previews(output: Path):
  output.mkdir(parents=True, exist_ok=True)
  with OpenpilotPrefix():
    rl.set_config_flags(rl.ConfigFlags.FLAG_WINDOW_HIDDEN)
    gui_app.init_window('Check-engine preview')
    assert gui_app.width == 2160, 'Use BIG=1 SCALE=1 for comma 3 geometry'
    target = rl.load_render_texture(gui_app.width, gui_app.height)
    layout = CanDiagnosticsLayout()
    layout._select(1)
    layout._tabs.action_item.set_selected_button(1)
    panel = layout._views[1]
    panel._update_state = lambda: None
    panel._reason = ''
    rect = rl.Rectangle(550, 25, 1560, 1030)

    def draw(name):
      panel._refresh_results()
      for _ in range(4):
        rl.begin_texture_mode(target)
        rl.clear_background(rl.BLACK)
        rl.draw_text_ex(gui_app.font(FontWeight.MEDIUM), 'CAN Bus', rl.Vector2(45, 75), 60, 0, rl.WHITE)
        rl.draw_text_ex(gui_app.font(FontWeight.NORMAL), 'Simulated results', rl.Vector2(45, 180), 28, 0, rl.GRAY)
        layout.render(rect)
        rl.end_texture_mode()
      assert not panel._faulted
      capture = rl.load_image_from_texture(target.texture)
      rl.image_flip_vertical(capture)
      assert rl.export_image(capture, str(output / name))
      rl.unload_image(capture)

    panel._status = {'message': 'Turn the car on and shift to Park.'}
    panel._reason = panel._status['message']
    draw('obd-idle.png')
    panel._reason = ''
    panel._report = {'timestamp': '2026-09-12T06:20:00+00:00', 'state': 'complete', 'ecus': {'7E8': {
      'lamp': {'state': 'ok', 'mil': True, 'count': 1}, 'stored': {'state': 'ok', 'codes': ['P0300']},
      'pending': {'state': 'ok', 'codes': []}, 'permanent': {'state': 'ok', 'codes': ['P0300']},
    }}}
    panel._status = {'message': 'Scan complete.'}
    draw('obd-codes.png')
    panel._report['state'] = 'partial'
    panel._report['ecus']['7E8']['stored']['codes'] = ['P1E00']
    panel._report['ecus']['7E8']['permanent'] = {'state': 'unsupported', 'error': 'Service not supported by this computer.'}
    panel._status = {'message': 'Partial scan: some requests could not be read.'}
    draw('obd-partial.png')
    panel._active = True
    panel._status = {'message': 'Reading pending (3/4)...'}
    draw('obd-scanning.png')
    panel.hide_event()
    layout._select(2)
    layout._tabs.action_item.set_selected_button(2)
    panel = layout._views[2]
    panel._update_state = lambda: None
    panel._reason = ''
    panel._report = {'timestamp': '2026-09-12T06:20:00+00:00', 'state': 'partial', 'modules': {
      '5E8': {'state': 'ok', 'status_mask': 255, 'codes': [{'code': 'P0401', 'failure_type': 0, 'status': 0x92}]},
      '543': {'state': 'incomplete', 'codes': [], 'error': 'No end-of-report marker'},
    }, 'context': {'02:02': {'state': 'ok', 'value': 'P0401', 'raw': '4202000401'}}}
    panel._status = {'message': 'GM scan finished; see coverage and unread items below.'}
    draw('gm-details.png')
    from openpilot.selfdrive.car.tests.test_gm_egr import simulate
    panel._report = simulate()[0].report
    panel._status = {'message': 'Simulated EGR evidence; no vehicle requests.'}
    draw('gm-egr-evidence.png')
    from openpilot.selfdrive.ui.layouts.settings.gm_egr_data import egr_rows
    panel.ROWS = lambda report: egr_rows(report)[3:]
    draw('gm-egr-results.png')
    panel.hide_event()
    rl.unload_render_texture(target)
    gui_app.close()
  print('Rendered emissions and GM diagnostic views.')


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--output', type=Path, required=True)
  render_previews(parser.parse_args().output)
