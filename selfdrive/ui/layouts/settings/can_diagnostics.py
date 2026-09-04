import time
import pyray as rl

import cereal.messaging as messaging
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.ui.layouts.settings.can_diagnostics_data import (
  BUS_LABELS, CanSnapshot, GraphBuffer,
)
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.list_view import ListItem, button_item, multiple_button_item, text_item
from openpilot.system.ui.widgets.scroller_tici import Scroller

REFRESH_INTERVAL = 0.1  # seconds - matches selfdrive/debug/can_printer.py's own display cadence

TEXT_COLOR = rl.Color(170, 170, 170, 255)
GRAPH_LINE_COLOR = rl.Color(51, 171, 76, 255)
GRAPH_BG_COLOR = rl.Color(30, 30, 30, 255)

BUS_FILTER_LABELS: list[str] = ["All", *BUS_LABELS.values()]
BUS_FILTER_VALUES: list[int | None] = [None, *BUS_LABELS.keys()]


class SignalGraph(Widget):
  """Minimal single-signal, auto-ranging, fixed-time-window line graph. Tap anywhere to dismiss."""

  def __init__(self, window_s: float = 10.0):
    super().__init__()
    self._buffer = GraphBuffer(window_s=window_s)
    self._label = ""
    self._font = gui_app.font(FontWeight.MEDIUM)
    self.on_dismiss = None

  def reset(self, label: str):
    self._label = label
    self._buffer = GraphBuffer(window_s=self._buffer.window_s)

  def add_sample(self, t: float, value: float):
    self._buffer.add(t, value)

  def _handle_mouse_release(self, _):
    if self.on_dismiss:
      self.on_dismiss()

  def _render(self, rect: rl.Rectangle):
    rl.draw_rectangle_rec(rect, GRAPH_BG_COLOR)

    title_size = measure_text_cached(self._font, self._label, 50)
    rl.draw_text_ex(self._font, self._label, rl.Vector2(rect.x + 20, rect.y + 20), 50, 0, rl.WHITE)

    plot_rect = rl.Rectangle(rect.x + 20, rect.y + 30 + title_size.y, rect.width - 40, rect.height - 80 - title_size.y)

    bounds = self._buffer.bounds()
    if bounds is None or len(self._buffer.samples) < 2:
      rl.draw_text_ex(self._font, "waiting for data...", rl.Vector2(plot_rect.x, plot_rect.y), 35, 0, TEXT_COLOR)
      return

    lo, hi = bounds
    span = (hi - lo) or 1.0
    window_s = self._buffer.window_s
    t_now = self._buffer.samples[-1][0]

    def to_px(t: float, v: float) -> rl.Vector2:
      x = plot_rect.x + plot_rect.width * (1.0 - (t_now - t) / window_s)
      y = plot_rect.y + plot_rect.height * (1.0 - (v - lo) / span)
      return rl.Vector2(x, y)

    points = [to_px(t, v) for t, v in self._buffer.samples]
    rl.draw_line_strip(points, len(points), GRAPH_LINE_COLOR)

    rl.draw_text_ex(self._font, f"{hi:.4g}", rl.Vector2(plot_rect.x, plot_rect.y - 10), 35, 0, TEXT_COLOR)
    rl.draw_text_ex(self._font, f"{lo:.4g}", rl.Vector2(plot_rect.x, plot_rect.y + plot_rect.height - 10), 35, 0, TEXT_COLOR)
    rl.draw_text_ex(self._font, "tap to close", rl.Vector2(rect.x + 20, rect.y + rect.height - 40), 30, 0, TEXT_COLOR)


class CanDiagnosticsLayout(Widget):
  def __init__(self):
    super().__init__()
    self._snapshot: CanSnapshot | None = None
    self._sock = None
    self._dirty: set[tuple[int, int, str | None]] = set()
    self._items: dict[tuple[int, int, str | None], ListItem] = {}
    self._last_refresh = 0.0
    self._bus_filter_index = 0
    # This panel is newly-written, less-proven code running in the same process as the
    # onroad alert display - the render loop (system/ui/lib/application.py) has no
    # catch-all exception handler, so an uncaught bug here would crash the whole UI
    # process. Once faulted, stop touching CAN/DBC state entirely and show a static
    # error instead of risking a crash loop every frame.
    self._faulted = False

    self._bus_filter = multiple_button_item(
      "CAN Bus", "", BUS_FILTER_LABELS, selected_index=0,
      button_width=180, callback=self._on_bus_filter_changed,
    )
    self._scroller = Scroller([self._bus_filter], line_separator=True, spacing=0)

    self._graph = SignalGraph()
    self._graph.on_dismiss = self._close_graph
    self._graph_key: tuple[int, int, str | None] | None = None

  def show_event(self):
    super().show_event()
    if self._faulted:
      return
    try:
      self._ensure_snapshot()
      if self._snapshot is not None:
        self._sock = messaging.sub_sock('can', conflate=False)
      self._scroller.show_event()
    except Exception:
      self._fault("show_event")

  def hide_event(self):
    super().hide_event()
    self._sock = None  # SubSocket closes on garbage collection
    self._close_graph()
    if not self._faulted:
      self._scroller.hide_event()

  def _fault(self, where: str):
    cloudlog.exception(f"CanDiagnosticsLayout: disabling after unexpected error in {where}")
    self._faulted = True
    self._sock = None

  def _ensure_snapshot(self):
    if self._snapshot is not None:
      return
    if ui_state.CP is None:
      return
    self._snapshot = CanSnapshot(ui_state.CP.carFingerprint)

  def _update_state(self):
    if self._faulted:
      return
    try:
      self._ensure_snapshot()
      if self._sock is None or self._snapshot is None:
        return

      for x in messaging.drain_sock(self._sock, wait_for_one=False):
        frames = [(y.address, y.dat, y.src) for y in x.can]
        touched = self._snapshot.ingest([(x.logMonoTime, frames)])
        self._dirty.update(touched)

        if self._graph_key is not None and self._graph_key in touched:
          row = self._snapshot.rows[self._graph_key]
          try:
            self._graph.add_sample(time.monotonic(), float(row.text))
          except ValueError:
            pass  # raw/undecoded rows aren't numeric, nothing to plot

      now = time.monotonic()
      if now - self._last_refresh > REFRESH_INTERVAL:
        self._refresh_display(now)
        self._last_refresh = now
    except Exception:
      self._fault("_update_state")

  def _refresh_display(self, now: float):
    assert self._snapshot is not None
    for key in self._dirty:
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
    if key[2] is None:
      # Raw/undecoded fallback rows have nothing to graph - plain display only.
      return text_item(title=row.label(), value=self._make_value_getter(key))
    item = button_item(title=row.label(), button_text="Graph", callback=lambda k=key: self._open_graph(k))
    item.action_item.set_value(self._make_value_getter(key))
    return item

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
    self._graph.reset(row.label())

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

      self._scroller.render(rect)
    except Exception:
      self._fault("_render")
