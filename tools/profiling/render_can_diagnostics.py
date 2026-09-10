"""Render and exercise the native inspector with synthetic data; never opens CAN."""
import argparse
import math
from pathlib import Path
import time

import pyray as rl
from opendbc.can.packer import CANPacker
from opendbc.car.gm.values import CAR
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.ui.layouts.settings.can_diagnostics import CanDiagnosticsLayout, draw_live_value
from openpilot.selfdrive.ui.layouts.settings.can_diagnostics_data import ODOMETER_KEY
from openpilot.selfdrive.ui.layouts.settings.can_inspection import InspectionSession
from openpilot.system.ui.lib.application import gui_app, FontWeight


def render_previews(output):
  output.mkdir(parents=True, exist_ok=True)
  with OpenpilotPrefix():
    rl.set_config_flags(rl.ConfigFlags.FLAG_WINDOW_HIDDEN)
    gui_app.init_window('CAN touch preview')
    assert gui_app.width == 2160, 'Use BIG=1 SCALE=1'
    layout = CanDiagnosticsLayout()
    layout._update_state = lambda: None
    layout.session = InspectionSession(CAR.CHEVROLET_VOLT)
    packer = CANPacker('gm_global_a_powertrain_generated')
    frames = [packer.make_can_msg('ECMPRDNL', 0, {'PRNDL': 2}),
              packer.make_can_msg('ECMEngineStatus', 0, {'EngineRPM': 1260, 'EngineTPS': 18}),
              packer.make_can_msg('PSCMSteeringAngle', 0, {'SteeringWheelAngle': -3.5}), (0x7ff, b'\x01\x02\x03\x04', 0),
              (0x120, bytes.fromhex('00b0d4db00'), 0)]
    layout.session.ingest([(time.monotonic_ns(), frames)])
    layout._refresh()
    rect = rl.Rectangle(0, 0, 2160, 1080)

    def draw(name):
      for _ in range(3):
        rl.begin_drawing()
        rl.clear_background(rl.BLACK)
        layout.render(rect)
        rl.end_drawing()
      assert not layout._faulted, name
      for identity in layout._used_buttons:
        button = layout._buttons[identity]
        assert button.rect.width >= 120 and button.rect.height >= 120, identity
        assert button.rect.y+button.rect.height <= 1080, identity
      capture = rl.load_image_from_screen()
      assert rl.export_image(capture, str(output/name))
      rl.unload_image(capture)

    draw('can-list.png')
    assert sum(item.rect.y+item.rect.height <= 1056 for item in layout._scroller._items) >= 4
    rpm, tps = (0, 201, 'EngineRPM'), (0, 201, 'EngineTPS')
    gear = (0, 309, 'PRNDL')
    for key in (rpm, tps, gear, (0, 485, 'SteeringWheelAngle')):
      if key in layout.snapshot.metadata:
        layout.session.toggle_favorite(key)
    layout._set_tab('favorites')
    draw('can-favorites.png')
    layout.open_row(gear, 'signal')
    draw('can-signal.png')
    layout._decoding()
    draw('can-details.png')
    layout._open_bits()
    layout.session.ingest([(time.monotonic_ns(), [packer.make_can_msg('ECMPRDNL', 0, {'PRNDL': 3})])])
    layout._refresh()
    draw('can-bits.png')
    layout._select_bit(2)
    draw('can-bit-info.png')
    assert layout.highlight_signal == 'PRNDL'
    layout.back()
    layout._freeze()
    draw('can-bits-frozen.png')
    frozen = layout.display
    layout.session.ingest([(time.monotonic_ns(), frames)])
    layout._refresh()
    assert layout.display is frozen
    layout._freeze()
    layout._set_tab('browse')
    layout.open_row((0, 0x7ff, None), 'signal')
    layout._open_bits()
    draw('can-unknown-bits.png')
    layout.back()
    draw('can-message.png')
    layout._set_tab('browse')
    layout._go('catalog')
    layout.query = 'PRNDL'
    layout._update_list()
    draw('can-dbc.png')
    layout.query = ''
    layout._set_tab('browse')
    layout._reset_baseline()
    layout.filter = 'changed'
    layout._update_list()
    assert not layout._scroller._items
    layout.session.ingest([(time.monotonic_ns(), [packer.make_can_msg('ECMEngineStatus', 0, {'EngineRPM': 1400, 'EngineTPS': 18})])])
    layout._refresh()
    assert rpm in layout.display.changed
    draw('can-changed.png')
    layout.filter = 'raw'
    layout._update_list()
    draw('can-raw.png')
    layout._go('coverage')
    draw('can-coverage.png')
    layout._set_tab('browse')
    layout.query = 'no such signal'
    layout._update_list()
    draw('can-empty.png')
    layout.query = ''
    layout._open_search()
    draw('can-search.png')
    layout._search_finished('0x135')
    assert all(item.key[1] == 309 for item in layout._scroller._items)
    layout._search_finished('')
    layout.session.select_graph(rpm, 0)
    layout.session.select_graph(tps, 1)
    now = time.monotonic()
    for buffer in layout.session.histories.values():
      buffer.samples.clear()
    for i in range(601):
      layout.session.histories[rpm].add(now-30+i*.05, 1260+260*math.sin(i/90))
      layout.session.histories[tps].add(now-30+i*.05, 18+5*math.sin(i/90-.5))
    layout._set_tab('compare')
    layout._refresh()
    draw('can-graph.png')
    layout._freeze()
    layout._step_cursor(-1)
    assert layout.cursor < layout.display.now
    draw('can-compare-frozen.png')
    layout._freeze()
    layout.session.select_graph(gear, 0)
    layout.session.histories = {gear: layout.session.histories[gear]}
    layout.session.histories[gear].samples.clear()
    for i in range(301):
      layout.session.histories[gear].add(now-30+i*.1, 2)
    layout._refresh()
    draw('can-flat.png')
    from openpilot.system.ui.lib.text_measure import _cache
    count = len(_cache)
    rl.begin_drawing()
    for i in range(2000):
      draw_live_value(gui_app.font(FontWeight.NORMAL), f'{i*1.234567:.8f}', rl.Rectangle(600, 200, 500, 100))
    rl.end_drawing()
    assert len(_cache) == count
    layout._set_tab('browse')
    layout.filter = 'live'
    layout.query = 'odometer'
    layout._refresh()
    draw('can-odometer.png')
    layout.open_row(ODOMETER_KEY, 'signal')
    layout._decoding()
    draw('can-odometer-definition.png')
    layout._open_bits()
    draw('can-odometer-bits.png')
    layout.hide_event()
    assert layout.session is None and layout._sock is None
    rl.close_window()
  print('Native views, target sizes, DBC bits, freeze, baseline, search, graphs, cache and cleanup checks passed.')


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--output', type=Path, required=True)
  render_previews(parser.parse_args().output)
