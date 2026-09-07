import math
import time
import pyray as rl

import cereal.messaging as messaging
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.ui.layouts.settings.can_diagnostics_data import (
  BUS_LABELS, CanSnapshot, GraphBuffer, diagnostics_timeout, graph_display_bounds,
)
from openpilot.selfdrive.ui.ui_state import ui_state, device
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.list_view import ITEM_BASE_HEIGHT, ListItem, ButtonAction, multiple_button_item, text_item
from openpilot.system.ui.widgets.scroller_tici import Scroller

REFRESH_INTERVAL = 0.1  # seconds - matches selfdrive/debug/can_printer.py's own display cadence

TEXT_COLOR = rl.Color(225, 232, 240, 255)
GRAPH_LINE_COLOR = rl.Color(0, 230, 255, 255)
GRAPH_BG_COLOR = rl.Color(9, 17, 28, 255)

BUS_FILTER_LABELS: list[str] = ["All", "Powertrain", "Radar", "Chassis"]
BUS_FILTER_VALUES: list[int | None] = [None, *BUS_LABELS.keys()]


class SignalGraphAction(ButtonAction):
  def get_width_hint(self) -> float:
    # Leave a small margin so floating-point rectangle subtraction cannot elide
    # the last unit character when the value has exactly its measured width.
    return math.ceil(super().get_width_hint()) + 2


class SignalGraph(Widget):
  """High-contrast single-signal history, with explicit freeze and close controls."""

  def __init__(self, window_s: float = 30.0):
    super().__init__()
    self._buffer = GraphBuffer(window_s=window_s)
    self._label = ""
    self._font = gui_app.font(FontWeight.MEDIUM)
    self.on_dismiss = None
    self._paused = False
    self._close_rect = rl.Rectangle(0, 0, 0, 0)
    self._pause_rect = rl.Rectangle(0, 0, 0, 0)

  def reset(self, label: str):
    self._label = label
    self._buffer = GraphBuffer(window_s=self._buffer.window_s)
    self._paused = False

  def add_sample(self, t: float, value: float):
    if not self._paused:
      self._buffer.add(t, value)

  def _handle_mouse_release(self, pos):
    if rl.check_collision_point_rec(pos, self._pause_rect):
      self._paused = not self._paused
    elif rl.check_collision_point_rec(pos, self._close_rect) and self.on_dismiss:
      self.on_dismiss()

  def _render(self, rect: rl.Rectangle):
    rl.draw_rectangle_rec(rect, GRAPH_BG_COLOR)

    self._close_rect = rl.Rectangle(rect.x + rect.width-210, rect.y+15, 190, 80)
    self._pause_rect = rl.Rectangle(rect.x + rect.width-420, rect.y+15, 190, 80)
    for button, label in [(self._close_rect, 'Close'), (self._pause_rect, 'Resume' if self._paused else 'Freeze')]:
      rl.draw_rectangle_rounded(button, .2, 10, rl.Color(51, 69, 88, 255))
      size = measure_text_cached(self._font, label, 35)
      rl.draw_text_ex(self._font, label, rl.Vector2(button.x+(button.width-size.x)/2, button.y+22), 35, 0, rl.WHITE)
    font_size = min(44., 44.*max(1, rect.width-470)/max(1, measure_text_cached(self._font, self._label, 44).x))
    rl.draw_text_ex(self._font, self._label, rl.Vector2(rect.x+20, rect.y+30), font_size, 0, rl.WHITE)
    plot_rect = rl.Rectangle(rect.x+120, rect.y+175, rect.width-160, rect.height-255)

    bounds = self._buffer.bounds()
    if bounds is None or len(self._buffer.samples) < 2:
      rl.draw_text_ex(self._font, "waiting for data...", rl.Vector2(plot_rect.x, plot_rect.y), 35, 0, TEXT_COLOR)
      return

    lo, hi = graph_display_bounds(*bounds)
    span = (hi - lo) or 1.0
    window_s = self._buffer.window_s
    t_now = self._buffer.samples[-1][0]

    def to_px(t: float, v: float) -> rl.Vector2:
      x = plot_rect.x + plot_rect.width * (1.0 - (t_now - t) / window_s)
      y = plot_rect.y + plot_rect.height * (1.0 - (v - lo) / span)
      return rl.Vector2(x, y)

    for i in range(5):
      y = plot_rect.y+plot_rect.height*i/4
      rl.draw_line_ex(rl.Vector2(plot_rect.x, y), rl.Vector2(plot_rect.x+plot_rect.width, y), 1, rl.Color(80, 98, 118, 255))
      rl.draw_text_ex(self._font, f'{hi-(hi-lo)*i/4:.4g}', rl.Vector2(rect.x+12, y-15), 28, 0, TEXT_COLOR)
    samples = list(self._buffer.samples)
    stride = max(1, len(samples)//max(1, int(plot_rect.width)))
    sampled = samples[::stride]
    if sampled[-1] != samples[-1]:
      sampled.append(samples[-1])
    points = [to_px(t, v) for t, v in sampled]
    for a, b in zip(points, points[1:], strict=False):
      rl.draw_line_ex(a, b, 4, GRAPH_LINE_COLOR)
    rl.draw_circle_v(points[-1], 6, rl.Color(255, 221, 90, 255))
    state = 'Frozen' if self._paused else 'No recent CAN data - history retained' if time.monotonic()-t_now > 1 else 'Live'
    rl.draw_text_ex(self._font, f'{state} | value {self._buffer.samples[-1][1]:.5g}', rl.Vector2(rect.x+20, rect.y+115), 35, 0, rl.WHITE)
    rl.draw_text_ex(self._font, f'-{window_s:g} s', rl.Vector2(plot_rect.x, plot_rect.y+plot_rect.height+15), 28, 0, TEXT_COLOR)
    rl.draw_text_ex(self._font, 'latest', rl.Vector2(plot_rect.x+plot_rect.width-90, plot_rect.y+plot_rect.height+15), 28, 0, TEXT_COLOR)


class CanDiagnosticsLayout(Widget):
  def __init__(self):
    super().__init__()
    self._snapshot: CanSnapshot | None = None
    self._sock = None
    self._dirty: set[tuple[int, int, str | None]] = set()
    self._items: dict[tuple[int, int, str | None], ListItem] = {}
    self._last_refresh = 0.0
    self._bus_filter_index = 0
    self._timeout_override = None
    # This panel is newly-written, less-proven code running in the same process as the
    # onroad alert display - the render loop (system/ui/lib/application.py) has no
    # catch-all exception handler, so an uncaught bug here would crash the whole UI
    # process. Once faulted, stop touching CAN/DBC state entirely and show a static
    # error instead of risking a crash loop every frame.
    self._faulted = False

    # Kept out of the Scroller deliberately - a filter control that scrolls away with the
    # list it's filtering is a bad pattern. Rendered as a fixed header instead (see _render).
    self._bus_filter = multiple_button_item(
      "CAN Bus", "", BUS_FILTER_LABELS, selected_index=0,
      button_width=240, callback=self._on_bus_filter_changed,
    )
    self._scroller = Scroller([], line_separator=True, spacing=0)

    self._graph = SignalGraph()
    self._graph.on_dismiss = self._close_graph
    self._graph_key: tuple[int, int, str | None] | None = None

  def show_event(self):
    super().show_event()
    if self._faulted:
      return
    try:
      self._ensure_ready()
      self._scroller.show_event()
      self._bus_filter.show_event()
    except Exception:
      self._fault("show_event")

  def hide_event(self):
    super().hide_event()
    device.set_override_interactive_timeout(None)
    self._timeout_override = None
    self._sock = None  # SubSocket closes on garbage collection
    self._close_graph()
    if not self._faulted:
      self._scroller.hide_event()
      self._bus_filter.hide_event()

  def _fault(self, where: str):
    cloudlog.exception(f"CanDiagnosticsLayout: disabling after unexpected error in {where}")
    self._faulted = True
    device.set_override_interactive_timeout(None)
    self._timeout_override = None
    self._sock = None

  def _ensure_ready(self):
    # Lazily build the snapshot and open the CAN socket once the car is known. Called from
    # both show_event() and every _update_state() frame - retrying here (not just at
    # show_event time) matters because the panel can be opened before CarParams has
    # arrived yet; without retrying every frame, the socket would never open if CP became
    # available only after this panel was already on screen.
    if self._snapshot is None:
      if ui_state.CP is None:
        return
      self._snapshot = CanSnapshot(ui_state.CP.carFingerprint)
    if self._sock is None:
      self._sock = messaging.sub_sock('can', conflate=False)

  def _update_state(self):
    if self._faulted:
      return
    try:
      override = diagnostics_timeout(ui_state.started, ui_state.engaged, ui_state.sm['carState'].vEgo,
                                     ui_state.sm.valid['carState'] and ui_state.sm.alive['carState'])
      if override != self._timeout_override:
        device.set_override_interactive_timeout(override)
        self._timeout_override = override
      self._ensure_ready()
      if self._sock is None or self._snapshot is None:
        return

      for x in messaging.drain_sock(self._sock, wait_for_one=False):
        frames = [(y.address, y.dat, y.src) for y in x.can]
        touched = self._snapshot.ingest([(x.logMonoTime, frames)])
        self._dirty.update(touched)

        if self._graph_key is not None and self._graph_key in touched:
          row = self._snapshot.rows[self._graph_key]
          if row.value is not None:
            self._graph.add_sample(time.monotonic(), row.value)

      now = time.monotonic()
      if now - self._last_refresh > REFRESH_INTERVAL:
        self._refresh_display(now)
        self._last_refresh = now
    except Exception:
      self._fault("_update_state")

  def _refresh_display(self, now: float):
    assert self._snapshot is not None
    for key in sorted(self._dirty, key=lambda k: (k[0], k[1], k[2] or '')):
      row = self._snapshot.rows.get(key)
      if row is None:
        continue
      if key not in self._items:
        item = self._make_item(key, row)
        self._apply_bus_visibility(item, key[0])
        self._items[key] = item
        self._scroller.add_widget(item)
    self._dirty.clear()

  def _make_item(self, key: tuple[int, int, str | None], row) -> ListItem:
    # Title is a callable, re-evaluated every frame, so the bus prefix can appear/disappear
    # as the filter changes without needing to rebuild the item.
    def title() -> str:
      return self._row_title(key)

    if key[2] is None:
      # Raw/undecoded fallback rows have nothing to graph - plain display only.
      return text_item(title=title, value=self._make_value_getter(key), description=row.details())
    item = ListItem(title=title, action_item=SignalGraphAction("Graph"),
                    description=row.details(), callback=lambda k=key: self._open_graph(k))
    item.action_item.set_value(self._make_value_getter(key))
    return item

  def _row_title(self, key: tuple[int, int, str | None]) -> str:
    row = self._snapshot.rows.get(key) if self._snapshot else None
    base = row.label() if row is not None else ""
    # Only prefix with the bus name when viewing "All" - redundant once already filtered to one bus.
    if BUS_FILTER_VALUES[self._bus_filter_index] is None:
      base = f"[{key[0]}] {base}"
    item = self._items.get(key)
    width = self._scroller.rect.width-100-(item.action_item.get_width_hint() if item else 250)
    font = gui_app.font(FontWeight.NORMAL)
    if width > 0 and measure_text_cached(font, base, 50).x > width:
      while base and measure_text_cached(font, base+'...', 50).x > width:
        base = base[:-1]
      base += '...'
    return base

  def _make_value_getter(self, key: tuple[int, int, str | None]):
    def _get() -> str:
      row = self._snapshot.rows.get(key) if self._snapshot else None
      return row.text if row is not None else ""
    return _get

  def _apply_bus_visibility(self, item: ListItem, bus: int):
    selected = BUS_FILTER_VALUES[self._bus_filter_index]
    item.set_visible(selected is None or selected == bus)

  def _on_bus_filter_changed(self, index: int):
    self._bus_filter_index = index
    for key, item in self._items.items():
      self._apply_bus_visibility(item, key[0])

  def _open_graph(self, key: tuple[int, int, str | None]):
    if self._snapshot is None:
      return
    row = self._snapshot.rows.get(key)
    if row is None or row.signal is None:
      return  # raw/undecoded rows have nothing numeric to plot
    self._graph_key = key
    self._graph.reset(row.label() + (f' [{row.unit}]' if row.unit else ''))
    if row.value is not None:
      self._graph.add_sample(row.last_updated, row.value)

  def _close_graph(self):
    self._graph_key = None

  def _render(self, rect: rl.Rectangle):
    if self._faulted:
      rl.draw_text_ex(gui_app.font(FontWeight.NORMAL), "CAN Diagnostics unavailable (see logs)",
                       rl.Vector2(rect.x + 20, rect.y + 20), 50, 0, TEXT_COLOR)
      return
    try:
      if self._graph_key is not None:
        self._graph.render(rect)
        return

      if self._snapshot is None:
        rl.draw_text_ex(gui_app.font(FontWeight.NORMAL), "Waiting for car...", rl.Vector2(rect.x + 20, rect.y + 20), 50, 0, TEXT_COLOR)
        return

      filter_rect = rl.Rectangle(rect.x, rect.y, rect.width, ITEM_BASE_HEIGHT)
      # ListItem._render dereferences _parent_rect unconditionally (viewport-culling check) -
      # since this item lives outside a Scroller now, nothing else ever sets this for it.
      self._bus_filter.set_parent_rect(filter_rect)
      self._bus_filter.render(filter_rect)

      decoded = sum(r.signal is not None for r in self._snapshot.rows.values())
      raw = len(self._snapshot.rows)-decoded
      status = f'{decoded} decoded signals | {raw} raw messages | tap a row for full message / signal names'
      rl.draw_text_ex(gui_app.font(FontWeight.NORMAL), status, rl.Vector2(rect.x+20, rect.y+ITEM_BASE_HEIGHT), 28, 0, TEXT_COLOR)
      list_rect = rl.Rectangle(rect.x, rect.y + ITEM_BASE_HEIGHT + 50, rect.width, rect.height - ITEM_BASE_HEIGHT - 50)
      self._scroller.render(list_rect)
    except Exception:
      self._fault("_render")
