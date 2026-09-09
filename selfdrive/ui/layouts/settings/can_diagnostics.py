import math
import time
import pyray as rl

import cereal.messaging as messaging
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.ui.layouts.settings.can_diagnostics_data import (
  BUS_LABELS, CanSnapshot, GraphBuffer, diagnostics_timeout, graph_display_bounds,
  matches_query,
)
from openpilot.selfdrive.ui.ui_state import ui_state, device
from openpilot.system.ui.lib.application import gui_app, FontWeight, FONT_SCALE, font_fallback
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.list_view import (
  ITEM_BASE_HEIGHT, ITEM_TEXT_FONT_SIZE, BUTTON_WIDTH, BUTTON_HEIGHT, TEXT_PADDING,
  ListItem, ButtonAction, TextAction, multiple_button_item,
)
from openpilot.system.ui.widgets.scroller_tici import Scroller
from openpilot.system.ui.widgets.button import Button
from openpilot.system.ui.widgets import DialogResult

REFRESH_INTERVAL = 0.1  # seconds - matches selfdrive/debug/can_printer.py's own display cadence

TEXT_COLOR = rl.Color(225, 232, 240, 255)
GRAPH_LINE_COLOR = rl.Color(0, 230, 255, 255)
GRAPH_BG_COLOR = rl.Color(9, 17, 28, 255)

BUS_FILTER_LABELS: list[str] = ["All", "Powertrain", "Radar", "Chassis"]
BUS_FILTER_VALUES: list[int | None] = [None, *BUS_LABELS.keys()]
VIEWS = ('live', 'changed', 'raw', 'dbc', 'coverage')


def measure_live_text(font, text, size):
  # Live values/raw bytes have effectively unlimited combinations. Do not retain
  # every value in the shared UI's permanent text-measurement cache.
  return rl.measure_text_ex(font_fallback(font), text, size * FONT_SCALE, 0)  # noqa: TID251


def draw_live_value(font, text, rect, right=False):
  text = fit_label(text, rect.width, ITEM_TEXT_FONT_SIZE)
  size = measure_live_text(font, text, ITEM_TEXT_FONT_SIZE)
  x = rect.x + (rect.width-size.x if right else 0)
  rl.draw_text_ex(font, text, rl.Vector2(x, rect.y+(rect.height-size.y)/2), ITEM_TEXT_FONT_SIZE, 0, TEXT_COLOR)


def fit_label(text, width, size=50):
  font = gui_app.font(FontWeight.NORMAL)
  if width <= 0:
    return ''
  if measure_live_text(font, text, size).x <= width:
    return text
  lo, hi = 0, len(text)
  while lo < hi:
    mid = (lo+hi+1)//2
    if measure_live_text(font, text[:mid]+'...', size).x <= width:
      lo = mid
    else:
      hi = mid-1
  return text[:lo]+'...'


class SignalGraphAction(ButtonAction):
  def get_width_hint(self) -> float:
    # Leave a small margin so floating-point rectangle subtraction cannot elide
    # the last unit character when the value has exactly its measured width.
    return math.ceil(measure_live_text(self._font, self.value, ITEM_TEXT_FONT_SIZE).x) + BUTTON_WIDTH + TEXT_PADDING + 2

  def _render(self, rect):
    self._button.set_text(self.text)
    self._button.set_enabled(self.enabled)
    self._button.render(rl.Rectangle(rect.x+rect.width-BUTTON_WIDTH, rect.y+(rect.height-BUTTON_HEIGHT)/2, BUTTON_WIDTH, BUTTON_HEIGHT))
    draw_live_value(self._font, self.value, rl.Rectangle(rect.x, rect.y, rect.width-BUTTON_WIDTH-TEXT_PADDING, rect.height))
    pressed, self._pressed = self._pressed, False
    return pressed


class RawValueAction(TextAction):
  def get_width_hint(self):
    return measure_live_text(self._font, self.text, ITEM_TEXT_FONT_SIZE).x + TEXT_PADDING

  def _render(self, rect):
    draw_live_value(self._font, self.text, rect, right=True)
    return False


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
    self._view = 'live'
    self._query = ''
    self._message_filter = None
    self._catalog_items = {}
    self._catalog_scroller = Scroller([], line_separator=True, spacing=0)
    self._coverage_rows = []
    self._skipped_batches = 0
    self._keyboard = None
    self._tabs = [Button(label, lambda view=view: self._set_view(view), font_size=35)
                  for label, view in zip(('Live', 'Changed', 'Raw', 'DBC', 'Coverage'), VIEWS, strict=True)]
    self._search_button = Button('Search', self._open_search, font_size=35)
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
      self._catalog_scroller.show_event()
      self._bus_filter.show_event()
      for button in [*self._tabs, self._search_button]:
        button.show_event()
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
      self._catalog_scroller.hide_event()
      self._bus_filter.hide_event()
      for button in [*self._tabs, self._search_button]:
        button.hide_event()

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
      self._build_catalog()
      self._coverage_rows = self._snapshot.coverage()
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

      # Inspector work must not monopolize the process that draws onroad alerts.
      # Leave unread batches in the socket and discard old samples on recovery.
      deadline = time.monotonic()+.008
      for _ in range(32):
        if time.monotonic() >= deadline:
          break
        x = messaging.recv_one_or_none(self._sock)
        if x is None:
          break
        if not x.valid or not 0 <= time.monotonic()-x.logMonoTime/1e9 <= 1.:
          self._skipped_batches += 1
          continue
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
        if key in self._items:
          self._items[key].set_visible(False)
        continue
      if key not in self._items:
        item = self._make_item(key, row)
        self._items[key] = item
        self._scroller.add_widget(item)
      self._apply_visibility(self._items[key], key)
    self._dirty.clear()
    self._coverage_rows = self._snapshot.coverage(now)

  def _build_catalog(self):
    for definition in self._snapshot.catalog():
      key = (definition.bus, definition.address)
      def title(k=key, d=definition):
        item = self._catalog_items.get(k)
        width = self._catalog_scroller.rect.width-100-(item.action_item.get_width_hint() if item else 250)
        return fit_label(f'[{d.bus}] {d.address:04X} {d.name}', width)
      def value(k=key, d=definition):
        seen = self._snapshot.messages.get(k)
        state = 'Live' if seen and time.monotonic()-seen.last_seen < 1. else 'Seen' if seen else 'Not seen'
        return f'{len(d.signals)} signals | {state}'
      item = ListItem(title=title, description=f'{definition.size} bytes | DBC: {self._snapshot.dbc_names[definition.bus]}',
                      action_item=SignalGraphAction('View'), callback=lambda k=key: self._inspect_message(k))
      item.action_item.set_value(value)
      self._catalog_items[key] = item
      self._catalog_scroller.add_widget(item)
    self._apply_filters()

  def _inspect_message(self, key):
    self._message_filter = key
    self._query = ''  # Selecting a message exposes all of its defined signals.
    definition = self._snapshot.definitions[key]
    for name in definition.signals:
      signal_key = (*key, name)
      if signal_key not in self._items:
        row = self._snapshot.rows.get(signal_key) or self._snapshot.definition_row(signal_key)
        item = self._make_item(signal_key, row)
        self._items[signal_key] = item
        self._scroller.add_widget(item)
    self._apply_filters()
    self._scroller.scroll_panel.set_offset(0)

  def _set_view(self, view):
    self._view, self._message_filter = view, None
    self._apply_filters()
    self._scroller.scroll_panel.set_offset(0)
    self._catalog_scroller.scroll_panel.set_offset(0)

  def _open_search(self):
    if diagnostics_timeout(ui_state.started, ui_state.engaged, ui_state.sm['carState'].vEgo,
                           ui_state.sm.valid['carState'] and ui_state.sm.alive['carState']) is None:
      return
    if self._keyboard is None:
      from openpilot.system.ui.widgets.keyboard import Keyboard
      self._keyboard = Keyboard(max_text_size=64, min_text_size=0)
    def finished(result):
      if result == DialogResult.CONFIRM:
        self._query = self._keyboard.text.strip()
        self._apply_filters()
        self._scroller.scroll_panel.set_offset(0)
        self._catalog_scroller.scroll_panel.set_offset(0)
    self._keyboard.reset(min_text_size=0)
    self._keyboard.set_title('Find CAN signals', 'Message, signal, unit or CAN ID. Leave empty to clear.')
    self._keyboard.set_text(self._query)
    self._keyboard.set_callback(finished)
    gui_app.push_widget(self._keyboard)

  def _make_item(self, key: tuple[int, int, str | None], row) -> ListItem:
    # Title is a callable, re-evaluated every frame, so the bus prefix can appear/disappear
    # as the filter changes without needing to rebuild the item.
    def title() -> str:
      return self._row_title(key)

    if key[2] is None:
      # Raw/undecoded fallback rows have nothing to graph - plain display only.
      return ListItem(title=title, action_item=RawValueAction(self._make_value_getter(key)), description=self._snapshot.signal_details(key))
    item = ListItem(title=title, action_item=SignalGraphAction("Graph"),
                    description=self._snapshot.signal_details(key), callback=lambda k=key: self._open_graph(k))
    item.action_item.set_enabled(lambda: key in self._snapshot.rows and self._snapshot.rows[key].value is not None
                                and math.isfinite(self._snapshot.rows[key].value))
    item.action_item.set_value(self._make_value_getter(key))
    return item

  def _row_title(self, key: tuple[int, int, str | None]) -> str:
    row = self._snapshot.rows.get(key) if self._snapshot else None
    if row is None and self._snapshot and key in self._snapshot.metadata:
      row = self._snapshot.definition_row(key)
    base = row.label() if row is not None else ""
    # Only prefix with the bus name when viewing "All" - redundant once already filtered to one bus.
    if BUS_FILTER_VALUES[self._bus_filter_index] is None:
      base = f"[{key[0]}] {base}"
    item = self._items.get(key)
    width = self._scroller.rect.width-100-(item.action_item.get_width_hint() if item else 250)
    return fit_label(base, width)

  def _make_value_getter(self, key: tuple[int, int, str | None]):
    def _get() -> str:
      row = self._snapshot.rows.get(key) if self._snapshot else None
      return row.text + (' [stale]' if time.monotonic()-row.last_updated > 1. else '') if row is not None else 'Not seen'
    return _get

  def _apply_visibility(self, item: ListItem, key):
    selected = BUS_FILTER_VALUES[self._bus_filter_index]
    row = self._snapshot.rows.get(key)
    if row is None and key in self._snapshot.metadata:
      row = self._snapshot.definition_row(key)
    visible = row is not None and (selected is None or selected == key[0])
    visible = visible and matches_query(self._query, key[0], key[1], row.message, row.signal or 'raw undecoded', row.unit, row.choice)
    if self._message_filter is not None:
      visible = visible and key[:2] == self._message_filter
    else:
      visible = visible and key in self._snapshot.rows
      if self._view == 'changed':
        visible = visible and row.changes > 0
      elif self._view == 'raw':
        visible = visible and key[2] is None
    item.set_visible(visible)

  def _apply_filters(self):
    if self._snapshot is None:
      return
    for key, item in self._items.items():
      self._apply_visibility(item, key)
    selected = BUS_FILTER_VALUES[self._bus_filter_index]
    matching = {(d.bus, d.address) for d in self._snapshot.catalog(selected, self._query)}
    for key, item in self._catalog_items.items():
      item.set_visible(key in matching)

  def _on_bus_filter_changed(self, index: int):
    self._bus_filter_index = index
    self._message_filter = None
    self._apply_filters()
    self._scroller.scroll_panel.set_offset(0)
    self._catalog_scroller.scroll_panel.set_offset(0)

  def _open_graph(self, key: tuple[int, int, str | None]):
    if self._snapshot is None:
      return
    row = self._snapshot.rows.get(key)
    if row is None or row.signal is None or row.value is None:
      return  # raw/undecoded rows have nothing numeric to plot
    self._graph_key = key
    self._graph.reset(row.label() + (f' [{row.unit}]' if row.unit else ''))
    if row.value is not None:
      self._graph.add_sample(row.last_updated, row.value)

  def _close_graph(self):
    self._graph_key = None

  def _render_coverage(self, rect):
    selected = BUS_FILTER_VALUES[self._bus_filter_index]
    rows = [r for r in self._coverage_rows if selected is None or r['bus'] == selected]
    font = gui_app.font(FontWeight.NORMAL)
    for i, row in enumerate(rows):
      box = rl.Rectangle(rect.x+12, rect.y+i*185, rect.width-24, 173)
      rl.draw_rectangle_rounded(box, .06, 8, GRAPH_BG_COLOR)
      state = 'LIVE' if row['alive'] else 'QUIET' if row['frames'] else 'NO DATA YET'
      color = GRAPH_LINE_COLOR if row['alive'] else TEXT_COLOR
      lines = [(f'Bus {row["bus"]} | {row["name"]} | {state} | {row["rate"]:.0f} frames/s', 32, color),
               (row['dbc'], 24, TEXT_COLOR),
               (f'Observed {row["observed"]}  |  DBC matches {row["matched"]}  |  Decoded {row["decoded"]}  |  No definition {row["unknown"]}', 28, rl.WHITE),
               (f'Available definitions: {row["defined_messages"]} messages / {row["defined_signals"]} signals', 26, TEXT_COLOR)]
      for j, (line, size, ink) in enumerate(lines):
        rl.draw_text_ex(font, fit_label(line, box.width-32, size), rl.Vector2(box.x+16, box.y+12+j*38), size, 0, ink)
    y = rect.y+len(rows)*185+10
    notes = ['DBC matches count definitions; Decoded counts messages successfully parsed at least once.',
             'Use DBC to browse definitions without traffic. Unknown messages remain raw bytes.']
    omitted = sum(r['omitted_raw_frames'] for r in rows)
    if omitted or self._skipped_batches:
      notes.append(f'Inspector limits: {omitted} extra unknown frames omitted; {self._skipped_batches} old/invalid batches skipped.')
    for line in notes:
      rl.draw_text_ex(font, fit_label(line, rect.width-30, 25), rl.Vector2(rect.x+15, y), 25, 0, TEXT_COLOR)
      y += 35

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
      width = (rect.width-5*12)/6
      for i, button in enumerate([*self._tabs, self._search_button]):
        button.render(rl.Rectangle(rect.x+i*(width+12), rect.y+ITEM_BASE_HEIGHT, width, 82))
        if i < len(VIEWS) and self._view == VIEWS[i]:
          rl.draw_rectangle(int(rect.x+i*(width+12)+15), int(rect.y+ITEM_BASE_HEIGHT+83), int(width-30), 4, GRAPH_LINE_COLOR)
      items = self._catalog_items if self._view == 'dbc' and self._message_filter is None else self._items
      count = sum(item.is_visible for item in items.values())
      status = f'{count} {"messages" if items is self._catalog_items else "signals / raw messages"}'
      if self._view == 'coverage':
        status = 'Live bus health and DBC coverage | counts cover this inspection session'
      elif self._message_filter:
        status = f'0x{self._message_filter[1]:X}: {count} signals | tap DBC to return to messages'
      elif self._view == 'changed':
        status += ' | changed at least once this session'
      if self._query and self._view != 'coverage':
        status += f' | Find: {self._query} (Search to edit/clear)'
      rl.draw_text_ex(gui_app.font(FontWeight.NORMAL), fit_label(status, rect.width-40, 28),
                      rl.Vector2(rect.x+20, rect.y+ITEM_BASE_HEIGHT+100), 28, 0, TEXT_COLOR)
      list_rect = rl.Rectangle(rect.x, rect.y+ITEM_BASE_HEIGHT+145, rect.width, rect.height-ITEM_BASE_HEIGHT-145)
      if self._view == 'coverage':
        self._render_coverage(list_rect)
      elif self._view == 'dbc' and self._message_filter is None:
        self._catalog_scroller.render(list_rect)
      else:
        self._scroller.render(list_rect)
      if self._view != 'coverage' and not count:
        text = ('No matches. Use Search to change or clear the filter.' if self._query else
                'No data in this view yet. DBC definitions are available without traffic.')
        rl.draw_text_ex(gui_app.font(FontWeight.NORMAL), fit_label(text, rect.width-40, 30),
                        rl.Vector2(list_rect.x+20, list_rect.y+40), 30, 0, TEXT_COLOR)
    except Exception:
      self._fault("_render")
