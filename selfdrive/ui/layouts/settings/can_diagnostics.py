"""Touch-first, read-only CAN inspection. Kept inside Settings' normal lifecycle."""
import math
import time
from bisect import bisect_left, bisect_right

import pyray as rl
import cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.ui.layouts.settings.can_diagnostics_data import (
  BUS_LABELS, ODOMETER_KEY, ODOMETER_KM_KEY, diagnostics_timeout, graph_display_bounds, matches_query, signal_timeout,
)
from openpilot.selfdrive.ui.layouts.settings.can_inspection import InspectionSession, bit_definitions, preference_document, sample_at
from openpilot.selfdrive.ui.ui_state import ui_state, device
from openpilot.system.ui.lib.application import gui_app, FontWeight, FONT_SCALE, font_fallback
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.button import Button, ButtonStyle
from openpilot.system.ui.widgets.scroller_tici import Scroller
from openpilot.system.ui.widgets.list_view import ITEM_BASE_HEIGHT, multiple_button_item

REFRESH_INTERVAL = .1
TEXT_COLOR = rl.Color(232, 239, 247, 255)
MUTED = rl.Color(175, 192, 210, 255)
CYAN = rl.Color(0, 230, 255, 255)
AMBER = rl.Color(255, 218, 106, 255)
BG = rl.Color(12, 22, 35, 255)
ROW_HEIGHT = 132
GAP = 16
BUTTON_HEIGHT = 120


def measure_live_text(font, text, size):
  return rl.measure_text_ex(font_fallback(font), str(text), size * FONT_SCALE, 0)  # noqa: TID251


def fit_label(text, width, size=46):
  text = str(text)
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


def label(text, x, y, width, size=46, color=TEXT_COLOR):
  rl.draw_text_ex(gui_app.font(FontWeight.NORMAL), fit_label(text, width, size), rl.Vector2(x, y), size, 0, color)


def draw_live_value(font, text, rect, right=False):
  text = fit_label(text, rect.width, 50)
  size = measure_live_text(font, text, 50)
  rl.draw_text_ex(font, text, rl.Vector2(rect.x+(rect.width-size.x if right else 0), rect.y+(rect.height-size.y)/2), 50, 0, TEXT_COLOR)


class InspectorRow(Widget):
  def __init__(self, owner, key, kind='signal'):
    super().__init__()
    self.owner, self.key, self.kind = owner, key, kind
    self._rect.height = ROW_HEIGHT
    self.favorite_rect = rl.Rectangle(0, 0, 0, 0)

  def set_parent_rect(self, rect):
    super().set_parent_rect(rect)
    self._rect.width = rect.width

  def _handle_mouse_release(self, pos):
    if self.kind == 'signal' and self.key[2] is not None and rl.check_collision_point_rec(pos, self.favorite_rect):
      self.owner.enqueue(lambda: self.owner.toggle_favorite(self.key))
    else:
      self.owner.enqueue(lambda: self.owner.open_row(self.key, self.kind))

  def _render(self, rect):
    if rect.y+rect.height <= self._parent_rect.y or rect.y >= self._parent_rect.y+self._parent_rect.height:
      return
    owner, key = self.owner, self.key
    rl.draw_rectangle_rounded(rect, .12, 8, BG)
    if self.kind == 'message':
      definition = owner.snapshot.definitions[key]
      seen = owner.display.messages.get(key)
      title = definition.name
      subtitle = f'Bus {key[0]} | 0x{key[1]:X} | {len(definition.signals)} signals'
      timeout = max((signal_timeout((*key, name)) for name in definition.signals), default=1.)
      value = 'Not seen' if not seen else 'Stale' if owner.display.now-seen.last_seen > timeout else 'Live'
      label(title, rect.x+20, rect.y+16, rect.width-390)
      label(subtitle, rect.x+20, rect.y+78, rect.width-390, 34, MUTED)
      label(value+'  >', rect.x+rect.width-350, rect.y+40, 330)
      return
    row = owner.display.rows.get(key)
    message, unit, _ = owner.snapshot.metadata.get(key, ('Raw / undecoded', '', {}))
    title = key[2] or f'0x{key[1]:X} raw bytes'
    subtitle = f'Bus {key[0]} | 0x{key[1]:X} | {message}'
    if key in owner.display.new and owner.filter == 'changed':
      subtitle = 'NEW | '+subtitle
    text = row.text if row else 'Not seen'
    if row and owner.display.now-row.last_updated > signal_timeout(key):
      subtitle = 'STALE | '+subtitle
    star_width = 176 if key[2] else 0
    value_width = min(620, rect.width*.36)
    label(title, rect.x+20, rect.y+16, rect.width-value_width-star_width-60)
    label(subtitle, rect.x+20, rect.y+78, rect.width-value_width-star_width-60, 34, MUTED)
    draw_live_value(gui_app.font(FontWeight.NORMAL), text,
                    rl.Rectangle(rect.x+rect.width-value_width-star_width-20, rect.y, value_width, rect.height), right=True)
    if key[2]:
      self.favorite_rect = rl.Rectangle(rect.x+rect.width-156, rect.y+6, 140, 120)
      selected = key in owner.session.favorites
      rl.draw_rectangle_rounded(self.favorite_rect, .2, 8, rl.Color(42, 65, 83, 255))
      label('Saved' if selected else 'Save', self.favorite_rect.x+12, rect.y+45, 120, 36, CYAN if selected else TEXT_COLOR)


class InfoRow(Widget):
  def __init__(self, title, value):
    super().__init__()
    self.title, self.value = title, value
    self._rect.height = 124

  def set_parent_rect(self, rect):
    super().set_parent_rect(rect)
    self._rect.width = rect.width

  def _render(self, rect):
    if rect.y+rect.height <= self._parent_rect.y or rect.y >= self._parent_rect.y+self._parent_rect.height:
      return
    label(self.title, rect.x+20, rect.y+8, rect.width-40, 36, MUTED)
    label(self.value() if callable(self.value) else self.value, rect.x+20, rect.y+60, rect.width-40, 42)


class CanSignalsLayout(Widget):
  def __init__(self):
    super().__init__()
    self.session = None
    self.display = None
    self._sock = None
    self._faulted = False
    self._active = False
    self._frozen_label = ""
    self._timeout_override = None
    self._last_refresh = 0.
    self._skipped_batches = 0
    self._display_skipped = 0
    self.on_back = None
    self._actions = []
    self._buttons = {}
    self._used_buttons = set()
    self._keyboard = None
    self._reset_navigation()

  @property
  def snapshot(self):
    return self.session.snapshot

  def _reset_navigation(self):
    self.tab, self.screen, self.filter = 'browse', 'home', 'live'
    self.bus, self.query = None, ''
    self._stack = []
    self._rows = {}
    self._list_keys = None
    self._scroller = Scroller([], spacing=16, pad_end=False)
    self._info = Scroller([], spacing=8, pad_end=False)
    self.key = self.message = None
    self._raw_size = None
    self.bit_page, self.selected_bit = 0, None
    self.highlight_signal = None
    self.dim_defined = False
    self._mapping_key, self._mappings = None, {}
    self.cursor = None
    self._pick_slot = None
    self.notice = ''
    self._graph_rects = []

  def enqueue(self, action):
    if len(self._actions) < 8:
      self._actions.append(action)

  def show_event(self):
    super().show_event()
    self._active = True
    if not self._faulted:
      try:
        self._ensure_ready()
      except Exception:
        self._fault('show')

  def hide_event(self):
    super().hide_event()
    self._active = False
    self._sock = None
    self.session = self.display = self._keyboard = None
    self._actions.clear()
    for button in self._buttons.values():
      button.hide_event()
    self._buttons.clear()
    self._reset_navigation()
    device.set_override_interactive_timeout(None)
    self._timeout_override = None

  def _fault(self, where):
    cloudlog.exception(f'CanDiagnosticsLayout: disabling after unexpected error in {where}')
    self._faulted = True
    self._sock = None
    self.session = self.display = None
    self._actions.clear()
    device.set_override_interactive_timeout(None)
    self._timeout_override = None

  def _ensure_ready(self):
    if self.session is None:
      if ui_state.CP is None:
        return
      preferences = None
      try:
        preferences = Params().get('CanDiagnosticsPreferences')
      except Exception:
        cloudlog.warning('CAN preferences unavailable; using empty favorites')
      self.session = InspectionSession(ui_state.CP.carFingerprint, preferences)
      self.tab = 'favorites' if self.session.favorites else 'browse'
      self._refresh()
    if self._sock is None:
      self._sock = messaging.sub_sock('can', conflate=False)

  def _update_state(self):
    if not self._active:
      return
    try:
      actions, self._actions = self._actions, []
      for action in actions:
        action()
        if not self._active:
          return
      if self._faulted:
        return
      override = diagnostics_timeout(ui_state.started, ui_state.engaged, ui_state.sm['carState'].vEgo,
                                     ui_state.sm.valid['carState'] and ui_state.sm.alive['carState'])
      if override != self._timeout_override:
        device.set_override_interactive_timeout(override)
        self._timeout_override = override
      # Search is embedded, so ignition transitions cannot leave a modal above the driving screen.
      if self.screen == 'search' and override is None:
        self.back()
      self._ensure_ready()
      if self.session is None:
        return
      if ui_state.CP and self.session.fingerprint != ui_state.CP.carFingerprint:
        self.hide_event()
        self._active = True
        self._ensure_ready()
      deadline = time.monotonic()+.008
      for _ in range(32):
        if self._sock is None or time.monotonic() >= deadline:
          break
        event = messaging.recv_one_or_none(self._sock)
        if event is None:
          break
        if not event.valid or not 0 <= time.monotonic()-event.logMonoTime/1e9 <= 1.:
          self._skipped_batches += 1
          continue
        self.session.ingest([(event.logMonoTime, [(f.address, f.dat, f.src) for f in event.can])])
      if time.monotonic()-self._last_refresh >= REFRESH_INTERVAL:
        self._refresh()
    except Exception:
      self._fault('update')

  def _refresh(self):
    self.display = self.session.capture()
    if self.session.frozen is None:
      self._display_skipped = self._skipped_batches
    self._last_refresh = time.monotonic()
    self._update_list()
    if self.screen == 'message' and self._message_size() != self._raw_size:
      offset = self._info.scroll_panel.offset
      self._build_info()
      self._info.scroll_panel.set_offset(offset)

  def _go(self, screen):
    if screen == self.screen:
      return
    if len(self._stack) >= 12:
      self._stack.pop(0)
    self._stack.append((self.screen, self.key, self.message, self._scroller, self._list_keys))
    self.screen = screen
    self._scroller = Scroller([], spacing=16, pad_end=False)
    self._list_keys = None
    self._reset_touches()

  def back(self):
    if self._stack:
      was_pick = self.screen == "pick"
      self.screen, self.key, self.message, self._scroller, self._list_keys = self._stack.pop()
      if was_pick:
        self._pick_slot = None
      for item in self._scroller._items:
        item.set_touch_valid_callback(self._scroller.scroll_panel.is_touch_valid)
      self._keyboard = None
      if self.screen not in ('bits', 'bit_info') and self.session and not self.session.frozen:
        self.session.bits = None
      self._update_list()
      if self.screen in ('signal', 'message', 'decoding', 'bit_info'):
        self._build_info()
    elif self.on_back:
      self.on_back()
    self._reset_touches()

  def _reset_touches(self):
    # New buttons cannot inherit a held touch from a previous screen.
    self._buttons.clear()
    self._scroller.hide_event()

  def _set_tab(self, tab):
    if tab == 'compare':
      self._go('compare')
      return
    self.tab, self.screen = tab, 'home'
    self._stack.clear()
    self._pick_slot = None
    self._list_keys = None
    self._scroller = Scroller([], spacing=16, pad_end=False)
    self._update_list()
    self._reset_touches()

  def _set_filter(self, value):
    self.back()
    self.filter = value
    self._list_keys = None
    self._scroller.scroll_panel.set_offset(0)
    self._update_list()

  def _set_bus(self, bus):
    self.back()
    self.bus = bus
    self._list_keys = None
    self._scroller.scroll_panel.set_offset(0)
    self._update_list()

  def _update_list(self):
    if self.session is None or self.display is None:
      return
    kind = 'signal'
    if self.screen == 'catalog':
      kind = 'message'
      keys = [(d.bus, d.address) for d in self.snapshot.catalog(self.bus, self.query)]
    elif self.screen == 'message_signals':
      definition = self.snapshot.definitions.get(self.message)
      keys = [(*self.message, name) for name in definition.signals] if definition else []
    elif self.screen == 'pick':
      keys = [k for k in self.session.favorites if k in self.snapshot.metadata]
      keys += [k for k in sorted(self.snapshot.metadata) if k not in keys]
      keys = [k for k in keys if k != next(iter(self.session.histories), None) or self._pick_slot == 0]
    elif self.screen == 'home' and self.tab != 'compare':
      keys = list(self.session.favorites) if self.tab == 'favorites' else sorted(self.display.rows, key=lambda k: (k[0], k[1], k[2] or ''))
      if self.tab == 'browse' and self.filter == 'changed':
        keys = [k for k in keys if k in self.display.changed or k in self.display.new]
      elif self.tab == 'browse' and self.filter == 'raw':
        keys = [k for k in keys if k[2] is None]
    else:
      return
    if kind == 'signal' and self.screen not in ('message_signals',) and not (self.screen == 'home' and self.tab == 'favorites'):
      keys = [k for k in keys if (self.bus is None or k[0] == self.bus) and
              matches_query(self.query, k[0], k[1], *(self.snapshot.metadata.get(k, ('', '', {}))[:2]), k[2] or 'raw')]
    signature = (kind, tuple(keys))
    if signature == self._list_keys:
      return
    self._list_keys = signature
    items = []
    for key in keys:
      cache_key = (kind, key)
      if cache_key not in self._rows:
        self._rows[cache_key] = InspectorRow(self, key, kind)
      items.append(self._rows[cache_key])
    self._scroller._items = []
    for item in items:
      self._scroller.add_widget(item)

  def toggle_favorite(self, key):
    if not self.session.toggle_favorite(key):
      self.notice = 'Favorites full (24). Remove a saved signal first.'
      return
    try:
      Params().put('CanDiagnosticsPreferences', preference_document(self.session.fingerprint, self.session.favorites))
      self.notice = ''
    except Exception:
      self.notice = 'Favorite changed for this session; could not save to device.'
      cloudlog.exception('CAN favorite save failed')
    self._update_list()

  def open_row(self, key, kind):
    if self.screen == 'pick':
      self.session.select_graph(key, self._pick_slot)
      self.back()
      self.cursor = None
      return
    if kind == 'message' or key[2] is None:
      self._go('message')
      self.message = key[:2]
    else:
      self._go('signal')
      self.key = key
      self.message = key[:2]
    self._build_info()

  def _build_info(self):
    rows = []
    if self.screen == 'signal':
      name, unit, choices = self.snapshot.metadata[self.key]
      rows = [('Signal', self.key[2]), ('Current value', lambda: self._value(self.key)), ('Message', name),
              ('Bus / CAN ID', f'{BUS_LABELS[self.key[0]]} / 0x{self.key[1]:X}'), ('Unit', unit or 'Unitless')]
      if choices:
        rows.append(('Named states', ', '.join(f'{v}={text}' for v, text in choices.items())))
    elif self.screen == 'decoding':
      from opendbc.can.dbc import DBC
      dbc = DBC(self.snapshot.message_dbcs[self.key[:2]])
      signal = dbc.msgs[self.key[1]].sigs[self.key[2]]
      _, unit, choices = self.snapshot.metadata[self.key]
      rows = [('Signal', self.key[2]), ('DBC', dbc.name),
              ('Bit layout', f'{signal.size} bits, start {signal.start_bit}; {"little" if signal.is_little_endian else "big"} endian'),
              ('Type', 'Signed' if signal.is_signed else 'Unsigned'),
              ('Physical value', f'raw * {signal.factor:g} + {signal.offset:g} {unit}')]
      rows.extend(('Named state', f'{v} = {text}') for v, text in choices.items())
    elif self.screen == 'message':
      definition = self.snapshot.definitions.get(self.message)
      def stats(field):
        msg = self.display.messages.get(self.message)
        if not msg:
          return 'Not seen'
        return {'rate': f'{msg.rate:.1f} frames/s | {msg.count} received',
                'age': f'{max(0, self.display.now-msg.last_seen):.2f} s since received',
                'decode': f'{max(0, self.display.now-msg.last_decoded):.2f} s since last successful decode' if msg.last_decoded else 'No successful decode yet',
                'size': f'{len(msg.data)} received / {definition.size if definition else "unknown"} expected bytes'}[field]
      rows = [('Message', definition.name if definition else 'No matching DBC definition'),
              ('Bus / CAN ID', f'{BUS_LABELS[self.message[0]]} / 0x{self.message[1]:X}'),
              ('Observed traffic', lambda: stats('rate')), ('Age', lambda: stats('age')),
              ('Payload length', lambda: stats('size')), ('Decoding', lambda: stats('decode'))]
      self._raw_size = self._message_size()
      for offset in range(0, max(1, self._raw_size), 8):
        rows.append((f'Raw bytes {offset}-{offset+7}', lambda offset=offset: self._raw_bytes(offset)))
    elif self.screen == 'bit_info':
      names = bit_definitions(self.snapshot, self.message).get(self.selected_bit, ())
      rows = [('Bit', f'Byte {self.selected_bit//8}, bit {self.selected_bit%8} (DBC index {self.selected_bit})'),
              ('Definition', ', '.join(names) if names else 'Undefined in the installed DBC'),
              ('Activity', lambda: self._bit_activity())]
      self.highlight_signal = names[0] if names else None
      rows.extend(('Signal', name) for name in names)
    self._info = Scroller([InfoRow(title, value) for title, value in rows], spacing=8, pad_end=False)

  def _message_size(self):
    observed = self.display.messages.get(self.message)
    definition = self.snapshot.definitions.get(self.message)
    return len(observed.data) if observed else definition.size if definition else 8

  def _raw_bytes(self, offset):
    msg = self.display.messages.get(self.message)
    return msg.data[offset:offset+8].hex(' ').upper() if msg and offset < len(msg.data) else '-'

  def _value(self, key):
    row = self.display.rows.get(key)
    return (row.text + (' [stale]' if self.display.now-row.last_updated > signal_timeout(key) else '')) if row else 'Not seen'

  def _bit_activity(self):
    bits = self.display.bits
    if not bits or bits.message != self.message:
      return 'Activity tracking starts when live Bits view opens'
    return f'{bits.flips[self.selected_bit]} changes since this bit view was opened/reset'

  def _open_bits(self):
    self._go('bits')
    self.session.inspect_bits(self.message)
    self.bit_page, self.selected_bit = 0, None
    self.highlight_signal = self.key[2] if self.key and self.key[:2] == self.message else None
    self._refresh()

  def _freeze(self):
    self.session.toggle_freeze()
    self._frozen_label = time.strftime("%H:%M:%S") if self.session.frozen else ""
    self.cursor = None
    self._refresh()

  def _reset_baseline(self):
    self.session.reset_baseline()
    self._refresh()

  def _open_graph(self):
    if self.session.select_graph(self.key, 0):
      self._set_tab('compare')
      self.cursor = None

  def _pick(self, slot):
    self._go('pick')
    self._pick_slot = slot
    self._update_list()

  def _open_search(self):
    if diagnostics_timeout(ui_state.started, ui_state.engaged, ui_state.sm['carState'].vEgo,
                           ui_state.sm.valid['carState'] and ui_state.sm.alive['carState']) is None:
      return
    from openpilot.system.ui.widgets.keyboard import Keyboard, ENTER_KEY
    owner = self
    class InspectorKeyboard(Keyboard):
      def _cancel_button_callback(self):
        owner.enqueue(owner.back)

      def _key_callback(self, key):
        if key == ENTER_KEY:
          text = self.text.strip()
          owner.enqueue(lambda: owner._search_finished(text))
        else:
          self.handle_key_press(key)
    self._go('search')
    self._keyboard = InspectorKeyboard(max_text_size=64, min_text_size=0)
    self._keyboard.set_title('Find CAN signals', 'Name, unit or CAN ID. Leave empty to clear.')
    self._keyboard.set_text(self.query)
    self._keyboard.show_event()

  def _search_finished(self, text):
    self.back()
    self.query = text
    self._scroller.scroll_panel.set_offset(0)
    self._list_keys = None
    self._update_list()

  def _button(self, identity, text, rect, action, enabled=True, selected=False):
    if identity not in self._buttons:
      self._buttons[identity] = Button(text, font_size=44)
    button = self._buttons[identity]
    button.set_text(text)
    button.set_enabled(enabled)
    button.set_button_style(ButtonStyle.PRIMARY if selected else ButtonStyle.NORMAL)
    button.set_click_callback(lambda: self.enqueue(action))
    self._used_buttons.add(identity)
    button.render(rect)

  def _bar(self, rect, items, prefix):
    width = (rect.width-GAP*(len(items)-1))/len(items)
    for i, item in enumerate(items):
      text, action, *options = item
      self._button(f'{prefix}-{i}', text, rl.Rectangle(rect.x+i*(width+GAP), rect.y, width, 120), action,
                   enabled=options[0] if options else True, selected=options[1] if len(options) > 1 else False)

  def _render(self, rect):
    self._used_buttons = set()
    try:
      self._draw(rect)
    except Exception:
      self._fault('render')
    for identity in list(self._buttons):
      if identity not in self._used_buttons:
        del self._buttons[identity]

  def _draw(self, rect):
    rl.draw_rectangle_rec(rect, rl.BLACK)
    if self.screen == 'search' and self._keyboard is not None and not self._faulted:
      self._keyboard.render(rect)
      return
    x, y, w = rect.x+24, rect.y+24, rect.width-48
    self._button('back', 'Back', rl.Rectangle(x, y, 240, 120), self.back)
    label('CAN inspection', x+272, y+34, 500, 52)
    if self._faulted:
      label('CAN inspection unavailable. Back returns to Settings.', x, y+180, w, 44)
      return
    if self.session is None:
      label('Waiting for car identification...', x, y+180, w)
      return
    if ODOMETER_KEY in self.snapshot.metadata:
      row = self.display.rows.get(ODOMETER_KEY)
      age = max(0., self.display.now-row.last_updated) if row else None
      reading = f'{math.floor(row.value):,} mi' if row else 'Not seen'
      status = f'{age:.0f}s ago' if age is not None else 'waiting for car'
      if row is None and ODOMETER_KEY[:2] in self.display.messages:
        reading, status = 'Unavailable', 'no valid reading'
      if age is not None and age > signal_timeout(ODOMETER_KEY):
        status = 'STALE | '+status
      label('Odometer | '+status, x+800, y+4, w-1340, 30, MUTED)
      label(reading, x+800, y+52, w-1340, 46, AMBER if age is None or age > 15 else TEXT_COLOR)
    self._button('freeze', 'Resume' if self.session.frozen else 'Freeze', rl.Rectangle(x+w-504, y, 240, 120), self._freeze,
                 selected=self.session.frozen is not None)
    self._button('more', 'More', rl.Rectangle(x+w-240, y, 240, 120), lambda: self._go('more'))
    y += 136
    if self.screen == 'home':
      self._bar(rl.Rectangle(x, y, w, 120), [(name, lambda tab=tab: self._set_tab(tab), True, self.tab == tab)
                                           for name, tab in [('Favorites', 'favorites'), ('Browse', 'browse'), ('Compare', 'compare')]], 'tabs')
      y += 136
    if self.screen in ('more', 'filters', 'buses'):
      if self.screen == 'more':
        items = [('DBC definitions', lambda: self._open_catalog()), ('Bus coverage', lambda: self._go('coverage'))]
      elif self.screen == 'filters':
        items = [(name, lambda value=value: self._set_filter(value))
                 for name, value in [('Live signals', 'live'), ('Changed since reset', 'changed'), ('Raw messages', 'raw')]]
      else:
        items = [(name, lambda bus=bus: self._set_bus(bus)) for name, bus in [('All buses', None), *[(name, bus) for bus, name in BUS_LABELS.items()]]]
      for i, (name, action) in enumerate(items):
        self._button(f'{self.screen}-{i}', name, rl.Rectangle(x, y+i*136, w, 120), action)
      return
    if self.screen == 'compare':
      self._draw_compare(rl.Rectangle(x, y, w, rect.y+rect.height-y-24))
      return
    if self.screen in ('home', 'catalog', 'pick', 'message_signals'):
      if self.screen == 'home' and self.tab == 'favorites':
        tools = [('Find signals', lambda: self._set_tab('browse'))]
      elif self.screen == 'message_signals':
        tools = [('Message details', self.back)]
      else:
        bus_name = 'All' if self.bus is None else {0:'Powertrain', 1:'Radar', 2:'Chassis'}[self.bus]
        tools = [('Find', self._open_search), ('Bus: '+bus_name, lambda: self._go('buses'))]
        if self.screen == 'home':
          tools.append(('Filter: '+self.filter.title(), lambda: self._go('filters')))
          if self.filter == 'changed':
            tools.append(('Reset baseline', self._reset_baseline, self.session.frozen is None))
      self._bar(rl.Rectangle(x, y, w, 120), tools, 'tools')
      y += 136
      self._update_list()
      count = len(self._scroller._items)
      status = f'{count} '+('messages' if self.screen == 'catalog' else 'signals / messages')
      if self.query and self.tab != 'favorites':
        status += ' | Find: '+self.query
      self._status(x, y, w, status)
      y += 40
      if not count:
        label('No matches. Use Find or change the filter.' if self.query else 'No signals here yet. Browse or open More > DBC definitions.', x, y+35, w, 42)
      else:
        self._scroller.render(rl.Rectangle(x, y, w, rect.y+rect.height-y-24))
      return
    if self.screen in ('signal', 'message', 'decoding', 'bit_info'):
      if self.screen == 'signal':
        actions = [('Graph', self._open_graph, self.session.frozen is None), ('Message', self._signal_message), ('Decoding details', self._decoding)]
      elif self.screen == 'message':
        actions = [('Signals', self._message_signals, self.message in self.snapshot.definitions), ('Bits', self._open_bits)]
      elif self.screen == 'bit_info':
        actions = [('View signal', self._bit_signal, self.highlight_signal is not None)]
      else:
        actions = [('Show bits', self._open_bits)]
      self._bar(rl.Rectangle(x, y, w, 120), actions, 'detail-tools')
      y += 136
      self._status(x, y, w, self.screen.replace('_', ' ').title())
      y += 44
      self._info.render(rl.Rectangle(x, y, w, rect.y+rect.height-y-24))
    elif self.screen == 'coverage':
      self._draw_coverage(x, y, w)
    elif self.screen == 'bits':
      self._draw_bits(rl.Rectangle(x, y, w, rect.y+rect.height-y-24))

  def _status(self, x, y, width, text):
    if self.session.frozen:
      text = 'FROZEN at '+self._frozen_label+' | '+text
    if self.notice:
      text = self.notice
    label(text, x, y, width, 32, AMBER if self.session.frozen or self.notice else MUTED)

  def _open_catalog(self):
    self.back()
    self._go('catalog')
    self._update_list()

  def _signal_message(self):
    self._go('message')
    self._build_info()

  def _decoding(self):
    self._go('decoding')
    self._build_info()

  def _message_signals(self):
    self._go('message_signals')
    self._update_list()

  def _bit_signal(self):
    name = self.highlight_signal
    self._go('signal')
    self.key = (*self.message, name)
    self._build_info()

  def _draw_coverage(self, x, y, width):
    self._status(x, y, width, 'Bus coverage | this inspection session')
    y += 52
    for row in self.display.coverage:
      rl.draw_rectangle_rounded(rl.Rectangle(x, y, width, 210), .08, 8, BG)
      label(f'Bus {row["bus"]}: {row["name"]} | {"LIVE" if row["alive"] else "QUIET"} | {row["rate"]:.0f} frames/s', x+20, y+16, width-40, 44)
      label(f'Observed {row["observed"]} | DBC matches {row["matched"]} | Decoded {row["decoded"]} | Unknown {row["unknown"]}', x+20, y+80, width-40, 38)
      label(f'{row["defined_messages"]} definitions / {row["defined_signals"]} signals | {row["dbc"]}', x+20, y+144, width-40, 34, MUTED)
      y += 226
    omitted = sum(r['omitted_raw_frames'] for r in self.display.coverage)
    label(f'Decoded = parsed at least once. Omitted unknown frames: {omitted}. Skipped batches: {self._display_skipped}',
          x, y+8, width, 32, MUTED)

  def _draw_bits(self, rect):
    msg = self.display.messages.get(self.message)
    definition = self.snapshot.definitions.get(self.message)
    data = msg.data if msg else b''
    length = max(len(data), definition.size if definition else 0, 1)
    pages = math.ceil(length/4)
    self.bit_page = min(self.bit_page, pages-1)
    self._bar(rect, [('Previous', lambda: self._page_bits(-1), self.bit_page > 0),
                     ('Next', lambda: self._page_bits(1), self.bit_page+1 < pages),
                     ('Dim defined', self._dim_bits, True, self.dim_defined),
                     ('Reset activity', self._reset_bits, self.session.frozen is None)], 'bits-tools')
    y = rect.y+136
    size_status = f' | LENGTH MISMATCH: {len(data)}/{definition.size}' if msg and definition and len(data) != definition.size else ''
    self._status(rect.x, y, rect.width, f'0x{self.message[1]:X} | bytes {self.bit_page*4}-{min(length, self.bit_page*4+4)-1}'+size_status)
    y += 48
    if self._mapping_key != self.message:
      self._mapping_key = self.message
      self._mappings = bit_definitions(self.snapshot, self.message)
    mappings = self._mappings
    bit_state = self.display.bits
    cell_width = (rect.width-300-7*16)/8
    for row in range(4):
      byte = self.bit_page*4+row
      if byte >= length:
        break
      label(f'Byte {byte}', rect.x, y+row*140+18, 270, 42)
      label(f'{data[byte]:02X}' if byte < len(data) else 'Not seen', rect.x, y+row*140+74, 270, 36, MUTED)
      for col in range(8):
        bit = byte*8+7-col
        names = mappings.get(bit, ())
        activity = max(0., 1.-(self.display.now-bit_state.changed_at[bit])/1.5) if bit_state and bit_state.message == self.message else 0.
        selected = self.highlight_signal in names if self.highlight_signal else self.selected_bit == bit
        box = rl.Rectangle(rect.x+300+col*(cell_width+16), y+row*140, cell_width, 124)
        value = str((data[byte] >> (bit%8)) & 1) if byte < len(data) else '-'
        self._button(f'bit-{row}-{col}', value, box, lambda bit=bit: self._select_bit(bit), selected=selected)
        if names:
          rl.draw_rectangle_rounded_lines_ex(box, .12, 8, 4, MUTED if self.dim_defined else CYAN)
        if activity and not (names and self.dim_defined):
          rl.draw_rectangle(int(box.x+12), int(box.y+box.height-10), int(box.width-24), 6,
                            rl.Color(AMBER.r, AMBER.g, AMBER.b, int(255*min(1., activity))))
        label(str(bit%8), box.x+10, box.y+6, 35, 25, MUTED)
    label('Outline: DBC-defined | Underline: changed recently | Tap a bit for its signal', rect.x, y+570, rect.width, 32, MUTED)
    label('Selected: '+(self.highlight_signal or 'none'), rect.x, y+612, rect.width, 36, CYAN)

  def _select_bit(self, bit):
    self._go('bit_info')
    self.selected_bit = bit
    self._build_info()

  def _page_bits(self, offset):
    self.bit_page += offset
    self._reset_touches()

  def _dim_bits(self):
    self.dim_defined = not self.dim_defined

  def _reset_bits(self):
    self.session.inspect_bits(self.message)
    self._refresh()

  def _draw_compare(self, rect):
    keys = list(self.session.histories)
    self._bar(rect, [('Signal 1', lambda: self._pick(0), self.session.frozen is None),
                     ('Signal 2', lambda: self._pick(1), self.session.frozen is None),
                     (f'{self.session.window:g} seconds', self._toggle_window)], 'compare-tools')
    y = rect.y+136
    self._status(rect.x, y, rect.width, 'Compare | tap a plot to inspect | separate units')
    y += 42
    if not keys:
      label('Choose Signal 1 to start. Saved favorites appear first.', rect.x, y+50, rect.width, 44)
      return
    bottom = 136 if self.session.frozen else 0
    height = (rect.y+rect.height-y-bottom-(len(keys)-1)*16)/len(keys)
    self._graph_rects = []
    end = self.display.now
    start = end-self.session.window
    cursor = end if self.cursor is None else max(start, min(end, self.cursor))
    for i, key in enumerate(keys):
      box = rl.Rectangle(rect.x, y+i*(height+16), rect.width, height)
      rl.draw_rectangle_rounded(box, .06, 8, BG)
      samples = list(self.session.graph_samples(key))
      values = [v for t, v in samples if start <= t <= end]
      color = CYAN if i == 0 else AMBER
      _, unit, choices = self.snapshot.metadata[key]
      label(f'{key[2]} [{unit or "unitless"}] | bus {key[0]} / 0x{key[1]:X}', box.x+20, box.y+10, box.width-40, 38, color)
      sample = sample_at(samples, cursor)
      text = 'No sample at cursor'
      if sample is not None:
        state = f' ({choices[sample[1]]})' if sample[1] in choices else ''
        number = f'{sample[1]:,.1f}' if key in (ODOMETER_KEY, ODOMETER_KM_KEY) else f'{sample[1]:.5g}'
        text = f'{number} {unit}{state} | t {cursor-end:.3f} s | age {max(0, cursor-sample[0]):.3f} s'
      label(text, box.x+20, box.y+58, box.width-40, 34)
      plot = rl.Rectangle(box.x+130, box.y+112, box.width-170, max(40, box.height-145))
      self._graph_rects.append((plot, start, end))
      lo, hi = graph_display_bounds(min(values), max(values)) if values else (0., 1.)
      for n in range(3):
        yy = plot.y+n*plot.height/2
        rl.draw_line_ex(rl.Vector2(plot.x, yy), rl.Vector2(plot.x+plot.width, yy), 1, MUTED)
        label(f'{hi-(hi-lo)*n/2:.4g}', box.x+6, yy-16, 116, 28, MUTED)
      points = [(t, v) for t, v in samples if start <= t <= end]
      # Bound drawing work to the pixel width while retaining gap boundaries.
      stride = max(1, len(points)//max(1, int(plot.width)))
      previous = None
      for n, (t, v) in enumerate(points):
        if previous and t-previous[0] > signal_timeout(key):
          previous = None
        if n % stride and n != len(points)-1:
          continue
        p = rl.Vector2(plot.x+(t-start)/(end-start)*plot.width, plot.y+plot.height*(hi-v)/(hi-lo))
        if previous:
          old = previous[2]
          if choices:
            elbow = rl.Vector2(p.x, old.y)
            rl.draw_line_ex(old, elbow, 4, color)
            rl.draw_line_ex(elbow, p, 4, color)
          else:
            rl.draw_line_ex(old, p, 4, color)
        previous = (t, v, p)
      cx = plot.x+(cursor-start)/(end-start)*plot.width
      rl.draw_line_ex(rl.Vector2(cx, plot.y), rl.Vector2(cx, plot.y+plot.height), 3, rl.WHITE)
    if self.session.frozen:
      self._bar(rl.Rectangle(rect.x, rect.y+rect.height-120, rect.width, 120),
                [('Previous sample', lambda: self._step_cursor(-1)), ('Next sample', lambda: self._step_cursor(1))], 'cursor')

  def _toggle_window(self):
    self.session.window = 10. if self.session.window == 30 else 30.

  def _step_cursor(self, direction):
    times = sorted({t for key in self.session.histories for t, _ in self.session.graph_samples(key)
                    if self.display.now-self.session.window <= t <= self.display.now})
    if not times:
      return
    current = self.display.now if self.cursor is None else self.cursor
    index = bisect_left(times, current)-1 if direction < 0 else bisect_right(times, current)
    self.cursor = times[max(0, min(index, len(times)-1))]

  def _handle_mouse_event(self, event):
    if self.screen != 'compare' or not self.session:
      return
    if event.left_pressed or event.left_down:
      for rect, start, end in self._graph_rects:
        if rl.check_collision_point_rec(event.pos, rect):
          self.cursor = start+(event.pos.x-rect.x)/rect.width*(end-start)


class CanDiagnosticsLayout(Widget):
  """Keep the established signal browser and the emissions scanner in one panel."""
  def __init__(self):
    super().__init__()
    from openpilot.selfdrive.ui.layouts.settings.obd_diagnostics import ObdDiagnosticsLayout
    self._views = [CanSignalsLayout(), ObdDiagnosticsLayout()]
    self._selected = 0
    self._tabs = multiple_button_item("", "", ["Signals", "Check engine"], 0, button_width=350, callback=self._select)

  def _select(self, index):
    if index != self._selected:
      self._views[self._selected].hide_event()
      self._selected = index
      self._views[self._selected].show_event()

  def show_event(self):
    super().show_event()
    self._tabs.show_event()
    self._views[self._selected].show_event()

  def hide_event(self):
    super().hide_event()
    self._tabs.hide_event()
    self._views[self._selected].hide_event()

  def _render(self, rect):
    tabs = rl.Rectangle(rect.x, rect.y, rect.width, ITEM_BASE_HEIGHT)
    self._tabs.set_parent_rect(tabs)
    self._tabs.render(tabs)
    self._views[self._selected].render(rl.Rectangle(rect.x, rect.y+ITEM_BASE_HEIGHT, rect.width, rect.height-ITEM_BASE_HEIGHT))
