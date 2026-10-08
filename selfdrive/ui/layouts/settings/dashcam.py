"""Dashcam screen (Settings -> Device -> Dashcam): list saved events and play the footage around one. OPTIONAL and OFFROAD ONLY.

A thin drawing layer over openpilot.system.review.player, where all the logic lives and is tested headless. The UI process is a driving
process, so this file follows three rules: it closes itself the moment the car starts, nothing it does can block the render thread
(file reads, video decoding and the preserve flag all run on the session's worker thread), and any exception closes the view instead of
reaching the main loop. device.py imports this module lazily inside a try/except.
"""
import time
from datetime import UTC, datetime

import numpy as np
import pyray as rl

from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.ui.ui_state import device, ui_state
from openpilot.system.hardware.hw import Paths
from openpilot.system.review.player.layout import TouchRouter, compute_layout, fit_rect, list_row_at
from openpilot.system.review.player.present import info_lines, list_empty_text, row_lines
from openpilot.system.review.player.session import DashcamSession
from openpilot.system.review.player.timeline import JUMP_S, format_clock, scrub_x
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.scroll_panel import GuiScrollPanel
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

SCREEN_TIMEOUT_S = 300     # keep the screen on while reviewing footage (restored on close)
BG = rl.Color(15, 20, 28, 255)
PANEL = rl.Color(24, 33, 46, 255)
BUTTON = rl.Color(44, 60, 82, 255)
BUTTON_DOWN = rl.Color(78, 106, 144, 255)
BUTTON_OFF = rl.Color(30, 38, 50, 255)
TEXT = rl.Color(232, 239, 247, 255)
DIM = rl.Color(150, 165, 185, 255)
CYAN = rl.Color(0, 205, 235, 255)
AMBER = rl.Color(255, 190, 70, 255)
GREEN = rl.Color(110, 220, 140, 255)
RED = rl.Color(255, 90, 80, 255)
TONES = {'normal': TEXT, 'dim': DIM, 'warn': AMBER, 'good': GREEN}
BUTTON_LABELS = {'back5': f'-{JUMP_S:g} s', 'stepback': '< frame', 'stepfwd': 'frame >', 'fwd5': f'+{JUMP_S:g} s'}


def _rec(t) -> rl.Rectangle:
  return rl.Rectangle(t[0], t[1], t[2], t[3])


class DashcamLayout(Widget):
  def __init__(self, log_root=None, session: DashcamSession | None = None):
    super().__init__()
    self._session = session or DashcamSession(log_root if log_root is not None else Paths.log_root())
    self._font = gui_app.font(FontWeight.MEDIUM)
    self._scroll = GuiScrollPanel()
    self._geom = None
    self._router: TouchRouter | None = None
    self._view = None
    self._closed = False
    self._last_t = 0.0
    self._last_keepawake = 0.0
    self._wall_now = datetime.now(UTC).timestamp()
    self._wall_t = 0.0
    self._texture: rl.Texture | None = None
    self._texture_size = (0, 0)
    self._texture_frame = None
    self._strip: tuple | None = None

  # ---- lifecycle ----------------------------------------------------------------------------------------------------------------

  def show_event(self):
    super().show_event()
    self._closed = False
    try:
      device.set_override_interactive_timeout(SCREEN_TIMEOUT_S)
      self._session.start()
    except Exception:
      self._fail('show')

  def hide_event(self):
    super().hide_event()
    self._release()

  def _release(self):
    """Stop decoding, free the texture, give the screen timeout back. Safe to repeat."""
    self._closed = True
    for cleanup in (self._session.close, self._unload_texture, lambda: device.set_override_interactive_timeout(None)):
      try:
        cleanup()
      except Exception:
        cloudlog.exception('Dashcam: cleanup step failed')

  def _close(self):
    self._closed = True
    try:
      if gui_app.widget_in_stack(self):   # pop our own entry, never a dialog someone else has put on top of us
        gui_app.pop_widget(None if gui_app.get_active_widget() is self else gui_app._nav_stack.index(self))   # runs hide_event
    except Exception:
      cloudlog.exception('Dashcam: could not pop the view')
    self._release()

  def _fail(self, where: str):
    cloudlog.exception(f'Dashcam: closing after an unexpected error in {where}')
    self._close()

  # ---- per-frame update ---------------------------------------------------------------------------------------------------------

  def _device_hot(self) -> bool:
    try:
      from cereal import log
      return ui_state.sm['deviceState'].thermalStatus != log.DeviceState.ThermalStatus.ok
    except Exception:
      return False   # unknown is not hot: never let a missing signal block a review

  def _update_state(self):
    if self._closed:
      return
    try:
      if ui_state.started:   # the car turned on: hand the screen back to the road view at once
        self._close()
        return
      now = time.monotonic()
      dt = now - self._last_t if self._last_t else 0.0
      self._last_t = now
      if now - self._wall_t > 5.0:
        self._wall_t, self._wall_now = now, datetime.now(UTC).timestamp()
      layout_size = (gui_app.width, gui_app.height)
      if self._geom is None or (self._geom.w, self._geom.h) != layout_size:
        self._geom = compute_layout(*layout_size)
        self._router = TouchRouter(self._geom)
      self._handle_touch()
      if self._closed:
        return
      self._session.update(dt, hot=self._device_hot())
      self._view = self._session.view()
      if self._view.playing and now - self._last_keepawake > 1.0:
        self._last_keepawake = now
        device.set_override_interactive_timeout(SCREEN_TIMEOUT_S)
    except Exception:
      self._fail('update')

  def _handle_touch(self):
    if not device.awake:
      for action in self._router.cancel():
        self._apply(action)
      return
    actions = []
    for e in gui_app.mouse_events:
      if e.slot != 0:
        continue
      if e.left_pressed:
        actions += self._router.press(e.pos.x, e.pos.y)
      elif e.left_released:
        actions += self._router.release(e.pos.x, e.pos.y)
      elif e.left_down:
        actions += self._router.move(e.pos.x, e.pos.y)
    for action in actions:
      if self._closed:
        return
      self._apply(action)

  def _apply(self, action: tuple):
    s = self._session
    if action[0] == 'scrub':
      s.scrub(action[1], action[2])
    elif action[0] == 'list_tap':
      rows = self._view.rows if self._view is not None else ()
      i = list_row_at(self._geom, action[1], self._scroll.offset, len(rows)) if self._scroll.is_touch_valid() else None
      if i is not None:
        s.select(rows[i].key)
    elif action[0] == 'button':
      name = action[1]
      if name == 'close':
        self._close()
      elif name == 'refresh':
        s.refresh()
      elif name == 'protect':
        s.protect()
      elif name in ('video', 'btn:play'):
        s.toggle_play()
      elif name == 'btn:back5':
        s.jump(-JUMP_S)
      elif name == 'btn:fwd5':
        s.jump(JUMP_S)
      elif name == 'btn:stepback':
        s.step(-1)
      elif name == 'btn:stepfwd':
        s.step(1)
      elif name.startswith('rate:'):
        s.set_rate(float(name[5:]))

  # ---- drawing ------------------------------------------------------------------------------------------------------------------

  def render(self, rect=None):
    """Widget.render also lays out children and routes touches; a failure anywhere in that path closes the view too."""
    if self._closed:
      return None
    try:
      return super().render(rect)
    except Exception:
      self._fail('render')
      return None

  def _render(self, rect):
    if self._closed or self._view is None or self._geom is None:
      return
    try:
      self._draw()
    except Exception:
      self._fail('render')

  def _text(self, text, x, y, size=32, color=TEXT, max_w=None):
    if max_w is not None:
      text = self._fit(text, max_w, size)
    rl.draw_text_ex(self._font, text, rl.Vector2(x, y), size, 0, color)

  def _fit(self, text, width, size):
    if measure_text_cached(self._font, text, size).x <= width:
      return text
    lo, hi = 0, len(text)
    while lo < hi:
      mid = (lo + hi + 1) // 2
      if measure_text_cached(self._font, text[:mid] + '...', size).x <= width:
        lo = mid
      else:
        hi = mid - 1
    return text[:lo] + '...'

  def _button(self, name, rect, label, enabled=True, active=False, size=36):
    down = self._router.pressed == name and enabled
    color = BUTTON_DOWN if down else (BUTTON_OFF if not enabled else BUTTON)
    r = _rec(rect)
    rl.draw_rectangle_rounded(r, 0.18, 8, color)
    if active:
      rl.draw_rectangle_rounded_lines_ex(r, 0.18, 8, 4, CYAN)
    w = measure_text_cached(self._font, label, size).x
    h = measure_text_cached(self._font, 'Ag', size).y
    self._text(label, rect[0] + (rect[2] - w) / 2, rect[1] + (rect[3] - h) / 2, size, TEXT if enabled else DIM)

  def _draw(self):
    v, L = self._view, self._geom
    rl.draw_rectangle(0, 0, int(L.w), int(L.h), BG)
    ready = v.clip_state == 'ready'
    # header
    self._button('close', L.close, 'Close')
    self._button('refresh', L.refresh, 'Refresh')
    self._text('Dashcam', L.title[0], L.title[1] + 30, 44)
    self._draw_list(v, L)
    self._draw_video(v, L)
    # info panel
    rl.draw_rectangle_rounded(_rec(L.info), 0.04, 8, PANEL)
    y = L.info[1] + 18
    for text, tone in info_lines(v, ui_state.is_metric, self._wall_now):
      self._text(text, L.info[0] + 18, y, 28, TONES[tone], L.info[2] - 36)
      y += 44
    self._draw_strip_and_scrub(v, L, ready)
    for name, rect in L.buttons.items():
      label = ('Pause' if v.playing else 'Play') if name == 'play' else BUTTON_LABELS[name]
      self._button(f'btn:{name}', rect, label, ready)
    protected = v.segment_status is not None and v.segment_status.protected
    self._button('protect', L.protect, 'Protected' if protected else 'Protect', v.can_protect, active=bool(protected))
    for rate, rect in L.rates.items():
      self._button(f'rate:{rate}', rect, f'{rate:g}x', ready, active=ready and v.rate == rate, size=34)

  def _draw_list(self, v, L):
    x, y, w, h = L.list
    rl.draw_rectangle_rounded(_rec(L.list), 0.02, 8, PANEL)
    if not v.rows:
      head, detail = list_empty_text(v.events_state)
      self._text(head, x + 24, y + 30, 38, TEXT, w - 48)
      for n, line in enumerate(detail.split('\n')):
        self._text(line, x + 24, y + 90 + n * 40, 28, DIM, w - 48)
      return
    content = rl.Rectangle(x, y, w, len(v.rows) * L.row_h)
    offset = self._scroll.update(_rec(L.list), content)
    rl.begin_scissor_mode(int(x), int(y), int(w), int(h))
    first = max(0, int(-offset // L.row_h))
    last = min(len(v.rows), first + int(h // L.row_h) + 2)
    for i in range(first, last):
      row = v.rows[i]
      ry = y + offset + i * L.row_h
      selected = row.key == v.selected
      if selected:
        rl.draw_rectangle(int(x), int(ry), int(w), int(L.row_h), BUTTON)
        rl.draw_rectangle(int(x), int(ry), 8, int(L.row_h), CYAN)
      title, detail, footage = row_lines(row, v.statuses.get(row.key), self._wall_now)
      status = v.statuses.get(row.key)
      usable = status is not None and status.state == 'ready'
      self._text(title, x + 24, ry + 12, 32, TEXT, w - 48)
      self._text(detail, x + 24, ry + 58, 28, CYAN if usable else DIM, w - 48)
      self._text(footage, x + 24, ry + 100, 26, GREEN if status is not None and status.protected else DIM, w - 48)
      rl.draw_line(int(x + 16), int(ry + L.row_h - 1), int(x + w - 16), int(ry + L.row_h - 1), BG)
    rl.end_scissor_mode()

  def _draw_video(self, v, L):
    rl.draw_rectangle_rec(_rec(L.video), rl.BLACK)
    if v.clip_state == 'ready' and v.frame is not None:
      self._upload(v)
      if self._texture is not None:
        w, h = self._texture_size
        dst = fit_rect(L.video, w / h)
        rl.draw_texture_pro(self._texture, rl.Rectangle(0, 0, w, h), _rec(dst), rl.Vector2(0, 0), 0.0, rl.WHITE)
        return
    msg = {'none': 'Select an event to review its footage', 'loading': 'Loading...', 'ready': 'Loading frames...'}.get(v.clip_state, v.message)
    if v.clip_state == 'error' and not msg:
      msg = 'Video unavailable'
    color = AMBER if v.clip_state == 'error' else DIM
    self._text(msg, L.video[0] + 30, L.video[1] + L.video[3] / 2 - 20, 34, color, L.video[2] - 60)

  def _upload(self, v):
    """Put the frame on screen: only when it changed, only on this (the GL) thread."""
    if v.frame_id == self._texture_frame:
      return
    frame = v.frame
    h, w = int(frame.shape[0]), int(frame.shape[1])
    if self._texture is None or self._texture_size != (w, h):
      self._unload_texture()
      img = rl.Image(None, w, h, 1, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8)
      self._texture = rl.load_texture_from_image(img)
      rl.set_texture_filter(self._texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
      self._texture_size = (w, h)
    rl.update_texture(self._texture, rl.ffi.cast('void *', rl.ffi.from_buffer(np.ascontiguousarray(frame))))
    self._texture_frame = v.frame_id

  def _unload_texture(self):
    if self._texture is not None:
      rl.unload_texture(self._texture)
    self._texture = None
    self._texture_frame = None
    self._texture_size = (0, 0)

  def _draw_strip_and_scrub(self, v, L, ready):
    sx, sy, sw, sh = L.strip
    rl.draw_rectangle_rounded(_rec(L.strip), 0.1, 6, PANEL)
    bx, by, bw, bh = L.scrub
    if not ready or v.duration <= 0:
      rl.draw_rectangle_rounded(_rec(L.scrub), 0.5, 6, BUTTON_OFF)
      return
    tele = v.telemetry
    if tele is not None:
      n = max(1, int(sw // 3))
      if self._strip is None or self._strip[0] is not tele or self._strip[1] != n:
        self._strip = (tele, n, tele.buckets(n, v.duration))
      speed, brake, blink = self._strip[2]
      top = max(float(np.nanmax(speed)) if np.isfinite(speed).any() else 0.0, 5.0)
      for i in range(n):
        px = sx + i * sw / n
        if brake[i]:
          rl.draw_rectangle(int(px), int(sy), max(1, int(sw / n)), int(sh), rl.Color(120, 40, 40, 255))
        if np.isfinite(speed[i]):
          bar = max(2.0, speed[i] / top * (sh - 14))
          rl.draw_rectangle(int(px), int(sy + sh - bar - 4), max(1, int(sw / n) - 0), int(bar), CYAN)
        if blink[i]:
          rl.draw_rectangle(int(px), int(sy + 2), max(1, int(sw / n)), 6, AMBER)
      self._text('speed (cyan)  brake (red)  blinker (amber)', sx + 10, sy + 2, 22, DIM)
    else:
      self._text('No speed data for this segment', sx + 12, sy + sh / 2 - 14, 26, DIM)
    rl.draw_rectangle_rounded(_rec(L.scrub), 0.5, 6, BUTTON)
    px = scrub_x(v.position, v.duration, bx, bw)
    rl.draw_rectangle_rounded(rl.Rectangle(bx, by, max(px - bx, 1), bh), 0.5, 6, rl.Color(0, 120, 140, 255))
    if v.event_time is not None:
      ex = scrub_x(v.event_time, v.duration, bx, bw)
      rl.draw_rectangle(int(ex - 5), int(by - 14), 10, int(bh + 28), RED)
      rl.draw_rectangle(int(ex - 5), int(sy), 10, int(sh), rl.Color(255, 90, 80, 120))
      self._text('event', min(ex + 12, bx + bw - 90), by + bh / 2 - 12, 24, RED)
    rl.draw_rectangle(int(px - 7), int(sy), 14, int(by + bh - sy), TEXT)
    rl.draw_circle_v(rl.Vector2(px, by + bh / 2), 26, TEXT)
    self._text(f'{format_clock(v.position)} / {format_clock(v.duration)}', bx + bw - 270, by + bh / 2 - 16, 28, TEXT)
