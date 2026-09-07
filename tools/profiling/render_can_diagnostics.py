"""Render illustrative CAN diagnostics previews without a CAN socket or device connection.

Example on a desktop with an X display:
  DISPLAY=:0 BIG=1 SCALE=1 OFFSCREEN=1 python -m tools.profiling.render_can_diagnostics --output /path/to/diagnostics-ui
"""
import argparse
import math
from pathlib import Path
import time

import pyray as rl
from opendbc.can.packer import CANPacker
from opendbc.car.gm.values import CAR
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.ui.layouts.settings.can_diagnostics import CanDiagnosticsLayout
from openpilot.selfdrive.ui.layouts.settings.can_diagnostics_data import CanSnapshot
from openpilot.system.ui.lib.application import gui_app, FontWeight


def render_previews(output: Path):
  output.mkdir(parents=True, exist_ok=True)
  with OpenpilotPrefix():
    rl.set_config_flags(rl.ConfigFlags.FLAG_WINDOW_HIDDEN)
    gui_app.init_window('CAN preview')
    if gui_app.width != 2160:
      raise ValueError('Use BIG=1 SCALE=1 to render the comma 3 geometry')
    layout = CanDiagnosticsLayout()
    layout._update_state = lambda: None  # Preview never subscribes to live CAN.
    snap = CanSnapshot(CAR.CHEVROLET_VOLT)
    packer = CANPacker('gm_global_a_powertrain_generated')
    frames = [packer.make_can_msg('ECMPRDNL', 0, {'PRNDL': 2}),
              packer.make_can_msg('ECMEngineStatus', 0, {'EngineRPM': 1260, 'EngineTPS': 18}),
              packer.make_can_msg('PSCMSteeringAngle', 0, {'SteeringWheelAngle': -3.5}),
              (0x7ff, b'\x01\x02\x03\x04', 0)]
    snap.ingest([(time.monotonic_ns(), frames)])
    snap.rows = {k: r for k, r in snap.rows.items() if k[2] in ('PRNDL', 'EngineRPM', 'EngineTPS', 'SteeringWheelAngle', None)}
    layout._snapshot = snap
    layout._dirty = set(snap.rows)
    layout._refresh_display(time.monotonic())
    rect = rl.Rectangle(550, 25, 1560, 1030)

    def draw(name):
      for _ in range(3):
        rl.begin_drawing()
        rl.clear_background(rl.BLACK)
        rl.draw_text_ex(gui_app.font(FontWeight.MEDIUM), 'CAN Bus', rl.Vector2(45, 75), 60, 0, rl.WHITE)
        rl.draw_text_ex(gui_app.font(FontWeight.NORMAL), 'Desktop preview', rl.Vector2(45, 180), 28, 0, rl.GRAY)
        layout.render(rect)
        rl.end_drawing()
      assert not layout._faulted
      capture = rl.load_image_from_screen()
      assert rl.export_image(capture, str(output/name))
      rl.unload_image(capture)

    draw('can-list.png')
    layout._open_graph((0, 201, 'EngineRPM'))
    graph = layout._graph
    graph.reset('00C9 EngineRPM [RPM]')
    now = time.monotonic()
    for i in range(601):
      graph.add_sample(now-30+i*.05, 1260+260*math.sin(i/90)+70*math.sin(i/29))
    draw('can-graph.png')

    dismissed = []
    graph.on_dismiss = lambda: dismissed.append(True)
    graph._handle_mouse_release(rl.Vector2(1000, 500))
    assert not dismissed
    pause = rl.Vector2(graph._pause_rect.x+20, graph._pause_rect.y+20)
    graph._handle_mouse_release(pause)
    assert graph._paused
    samples = list(graph._buffer.samples)
    graph.add_sample(now+1, 0)
    assert list(graph._buffer.samples) == samples
    graph._handle_mouse_release(pause)
    assert not graph._paused
    graph._handle_mouse_release(rl.Vector2(graph._close_rect.x+20, graph._close_rect.y+20))
    assert dismissed

    graph.reset('0135 PRNDL | 2 (D)')
    for i in range(301):
      graph.add_sample(now-30+i*.1, 2)
    draw('can-flat.png')
    rl.close_window()
  print('Rendered list, graph and flatline; graph Close/Freeze/plot-tap checks passed.')


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--output', type=Path, required=True)
  render_previews(parser.parse_args().output)
