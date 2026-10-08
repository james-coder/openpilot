"""SYNTHETIC preview of the Dashcam screen: renders the real widget on this workstation's display from generated footage and a
generated event list, then writes PNGs. It stands in for ui_state and the device and decodes made-up video, so it shows layout and
wording only. It is NOT device output and validates nothing about the comma 3X (GPU, decode speed, touch, screen timeout, engagement).

  DISPLAY=:0 BIG=1 SCALE=1 OFFSCREEN=1 .venv/bin/python -m openpilot.tools.profiling.render_dashcam <out-dir>
"""
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pyray as rl

from cereal import log
from openpilot.system.review.player.events import events_path_for
from openpilot.system.review.player.session import DashcamSession
from openpilot.system.review.tests.player_helpers import ROUTE, make_segment, write_events
from openpilot.system.ui.lib.application import gui_app

MONO0 = 5000.0
SEG_FRAMES = 20 * 40         # 40 s per segment


def painter(i: int, w: int = 526, h: int = 330) -> np.ndarray:
  """A made-up road scene: sky, road, moving lane dashes, and a lead car whose gap closes as the frames go on."""
  img = np.zeros((h, w, 3), np.uint8)
  img[: h // 2] = (120, 165, 215)
  img[h // 2:] = (70, 72, 78)
  for y in range(h // 2, h):
    half = int((y - h // 2) / (h / 2) * w * 0.45)
    img[y, w // 2 - half: w // 2 - half + 4] = (230, 230, 230)
    img[y, w // 2 + half - 4: w // 2 + half] = (230, 230, 230)
  for k in range(6):
    y = h // 2 + int(((k * 0.17 + (i * 0.02) % 0.17) ** 1.6) * (h / 2) * 2.6)
    if y < h - 6:
      img[y: y + 4 + (y - h // 2) // 18, w // 2 - 2: w // 2 + 2] = (240, 220, 120)
  size = 40 + (i % 800) // 12
  x0, y0 = w // 2 - size // 2, h // 2 + 10 - size // 4
  img[y0: y0 + size // 2, x0: x0 + size] = (150, 25, 25)
  return img


def build(root: Path) -> None:
  car3 = [(MONO0 - 40.0 + k * 0.1, 18.0 - 0.001 * k, False, False, False) for k in range(400)]
  car4 = []
  for k in range(400):
    t = k * 0.1
    speed = 17.5 if t < 12 else max(0.0, 17.5 - (t - 12) * 5.5)
    car4.append((MONO0 + t, speed, 12 <= t < 15, 4 <= t < 7, False))
  make_segment(root, 3, frames=SEG_FRAMES, mono0=MONO0 - 40.0, car=car3, gop=20, painter=painter)
  make_segment(root, 4, frames=SEG_FRAMES, mono0=MONO0, car=car4, gop=20, painter=painter)
  make_segment(root, 9, frames=SEG_FRAMES, mono0=MONO0 + 900.0, car=[], gop=20, painter=painter)
  records = []

  def event(ident, seg, mono, wall, kinds, peaks):
    return [dict(v=1, id=ident, phase='start', mono=mono, wall=wall, kinds=kinds, peaks=peaks, segment=seg),
            dict(v=1, id=ident, phase='end', mono=mono + 3, wall=wall + 3, kinds=kinds, peaks=peaks, segment=seg)]
  records += event(1, f'{ROUTE}--2', MONO0 - 4000.0, 1_789_000_000.0, ['hard_brake'], {'decel_g': 0.52})                     # deleted
  records += event(2, f'{ROUTE}--9', MONO0 + 915.0, 4_000.0, ['extreme_g'], {'horizontal_g': 1.04})                            # unset clock
  records += event(3, f'{ROUTE}--4', MONO0 + 12.0, 1_790_100_000.0, ['hard_brake', 'abs_stop'], {'decel_g': 0.74})            # the demo
  write_events(events_path_for(root), records)


class Preview:
  def __init__(self, root: Path, out: Path, protected: set):
    from openpilot.selfdrive.ui.layouts.settings import dashcam as module
    self.module, self.out, self.protected = module, out, protected
    self.state = SimpleNamespace(started=False, is_metric=False,
                                 sm={'deviceState': SimpleNamespace(thermalStatus=log.DeviceState.ThermalStatus.ok)})
    module.ui_state = self.state
    module.device = SimpleNamespace(awake=True, set_override_interactive_timeout=lambda t: None)
    self.session = DashcamSession(root, getxattr_fn=lambda p, a: b'1' if p in protected else None, drop_realtime_fn=lambda: None,
                                  protect_fn=lambda p: protected.add(p), logger=lambda m: None)
    self.widget = module.DashcamLayout(session=self.session)
    self.widget.show_event()
    self.target = rl.load_render_texture(gui_app.width, gui_app.height)

  def frame(self):
    rl.begin_texture_mode(self.target)
    rl.clear_background(rl.BLACK)
    self.widget.render(rl.Rectangle(0, 0, gui_app.width, gui_app.height))
    rl.draw_rectangle(0, gui_app.height - 44, gui_app.width, 44, rl.Color(120, 20, 20, 255))
    rl.draw_text_ex(gui_app.font(), 'SYNTHETIC PREVIEW: made-up footage and events on a workstation display. Not device output.',
                    rl.Vector2(24, gui_app.height - 36), 28, 0, rl.WHITE)
    rl.end_texture_mode()

  def until(self, cond, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
      self.frame()
      if cond():
        return True
      time.sleep(0.01)
    return False

  def save(self, name):
    self.frame()
    image = rl.load_image_from_texture(self.target.texture)
    rl.image_flip_vertical(image)
    assert rl.export_image(image, str(self.out / name))
    rl.unload_image(image)

  def select(self, event_id):
    row = next(r for r in self.session.view().rows if r.id == event_id)
    self.session.select(row.key)
    return self.until(lambda: self.session.view().clip_state in ('ready', 'error') and self.widget._view is not None
                      and self.widget._view.selected == row.key and (self.widget._view.clip_state != 'ready' or not self.widget._view.buffering))


def main():
  out = Path(sys.argv[1])
  out.mkdir(parents=True, exist_ok=True)
  work = Path(tempfile.mkdtemp(prefix='dashcam-preview-'))
  try:
    root = work / 'media' / '0' / 'realdata'
    root.mkdir(parents=True)
    build(root)
    rl.set_config_flags(rl.ConfigFlags.FLAG_WINDOW_HIDDEN)
    gui_app.init_window('Dashcam preview')
    p = Preview(root, out, {str(root / f'{ROUTE}--4')})
    assert p.until(lambda: p.session.view().rows and p.widget._view is not None)
    p.save('01-list-nothing-selected.png')
    assert p.select(3)
    p.save('02-event-paused-before-impact.png')
    p.session.set_rate(0.5)
    p.session.scrub(0.30, 'start')
    p.session.scrub(0.30, 'end')
    assert p.until(lambda: p.widget._view.frame_id is not None and not p.widget._view.buffering)
    p.save('03-scrubbed-half-speed.png')
    p.session.set_rate(2.0)
    p.session.toggle_play()
    p.until(lambda: p.session.view().position > 0.55 * p.session.view().duration)
    p.save('04-playing-2x.png')
    p.session.toggle_play()
    assert p.select(1)
    p.save('05-footage-deleted.png')
    assert p.select(2)
    p.save('06-segment-without-event-time.png')
    assert p.select(3)
    p.state.sm['deviceState'] = SimpleNamespace(thermalStatus=log.DeviceState.ThermalStatus.overheated)
    p.session.toggle_play()
    p.frame()
    p.save('07-device-hot.png')
    p.widget.hide_event()
    p.session.join(5)
    # empty state: a fresh view with no events file
    shutil.rmtree(root.parent / 'review')
    p2 = Preview(root, out, set())
    assert p2.until(lambda: p2.session.view().events_state == 'missing' and p2.widget._view is not None)
    p2.save('08-no-events-yet.png')
    p2.widget.hide_event()
    p2.session.join(5)
  finally:
    shutil.rmtree(work, ignore_errors=True)
  print(f'wrote previews to {out} (synthetic: not device output)')


if __name__ == '__main__':
  main()
