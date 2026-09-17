"""Render actual comma 3 alert widgets with synthetic failures; no vehicle access.

BIG=1 SCALE=1 OFFSCREEN=1 python -m tools.profiling.render_recognition_alerts --output /tmp/recognition-previews
"""
import argparse
from pathlib import Path

import pyray as rl

from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.selfdrived.events import Events, EventName, ET
from openpilot.selfdrive.selfdrived.alertmanager import set_offroad_alert
from openpilot.selfdrive.ui.onroad.alert_renderer import AlertRenderer, Alert, ALERT_PADDING, ALERT_FONT_BIG, ALERT_FONT_SMALL
from openpilot.selfdrive.ui.widgets.offroad_alerts import OffroadAlert
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.text_measure import measure_text_cached


def render(output):
  output.mkdir(parents=True, exist_ok=True)
  failures = []
  with OpenpilotPrefix():
    rl.set_config_flags(rl.ConfigFlags.FLAG_WINDOW_HIDDEN)
    gui_app.init_window('Vehicle recognition alert preview')
    assert (gui_app.width, gui_app.height) == (2160, 1080), 'Use BIG=1 SCALE=1'
    target = rl.load_render_texture(2160, 1080)
    renderer = AlertRenderer()

    def save(name, widget, rect):
      for _ in range(3):
        rl.begin_texture_mode(target)
        rl.clear_background(rl.Color(25, 35, 45, 255))
        widget.render(rect)
        rl.end_texture_mode()
      capture = rl.load_image_from_texture(target.texture)
      rl.image_flip_vertical(capture)
      assert rl.export_image(capture, str(output / name))
      rl.unload_image(capture)

    for count in (0, 1, 2, 3):
      for event, name in ((EventName.startupNoCar, 'startup'), (EventName.carUnrecognized, 'permanent')):
        events = Events(car_recognition_attempts=count)
        events.add(event)
        msg = events.create_alerts([ET.PERMANENT])[0]
        alert = Alert(msg.alert_text_1, msg.alert_text_2, msg.alert_size, msg.alert_status)
        renderer.get_alert = lambda _, alert=alert: alert
        for sidebar in (0, 300):
          rect = rl.Rectangle(sidebar + 30, 30, 2160 - sidebar - 60, 1020)
          available = renderer._get_alert_rect(rect, alert.size).width - 2 * ALERT_PADDING
          widths = [measure_text_cached(font, text, size).x for text, font, size in (
            (alert.text1, renderer.font_bold, ALERT_FONT_BIG), (alert.text2, renderer.font_regular, ALERT_FONT_SMALL))]
          if max(widths) > available:
            failures.append(f'{name}/{count}/sidebar={sidebar}: widths={widths}, available={available}')
          save(f'{name}-{count}-sidebar-{sidebar}.png', renderer, rect)

    for count in (0, 1, 2, 3):
      extra = f"Vehicle identification failed after {count} attempt{'s' if count != 1 else ''}." if count else None
      set_offroad_alert('Offroad_CarUnrecognized', True, extra_text=extra)
      widget = OffroadAlert()
      assert widget.refresh() == 1
      if count:
        assert any(f'after {count} attempt' in a.text for a in widget.sorted_alerts if a.visible)
      save(f'offroad-{count}.png', widget, rl.Rectangle(340, 145, 1780, 895))
      assert widget.get_content_height() <= widget.scroll_panel_rect.height, 'Offroad message requires scrolling'
    rl.unload_render_texture(target)
    gui_app.close()
  assert not failures, '\n'.join(failures)
  print('20 actual-widget previews rendered; text widths and offroad height fit.')


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--output', type=Path, required=True)
  render(parser.parse_args().output)
