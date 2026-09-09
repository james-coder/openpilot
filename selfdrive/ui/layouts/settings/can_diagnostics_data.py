import time
import math
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from opendbc import DBC_PATH, get_generated_dbcs
from opendbc.can import CANParser
from opendbc.can.parser import CANDefine
from opendbc.can.dbc import DBC as DbcFile
from opendbc.car import Bus
from opendbc.car.gm.values import CanBus, DBC as GM_DBC_MAP

# Which opendbc.car.Bus DBC-map key backs each of this car's physical CAN buses.
# GM-specific and hardcoded deliberately - this is a personal single-car diagnostics
# tool, not a generic cross-brand feature.
BUS_DBC_KEYS: dict[int, Bus] = {
  CanBus.POWERTRAIN: Bus.pt,
  CanBus.OBSTACLE: Bus.radar,
  CanBus.CHASSIS: Bus.chassis,
}

BUS_LABELS: dict[int, str] = {
  CanBus.POWERTRAIN: "Powertrain",
  CanBus.OBSTACLE: "Obstacle/Radar",
  CanBus.CHASSIS: "Chassis",
}

BUS_ALIVE_TIMEOUT = 1.0  # seconds since last frame before a bus is considered dead
MAX_RAW_MESSAGES_PER_BUS = 256


def diagnostics_timeout(started: bool, enabled: bool, speed: float, car_state_valid: bool) -> int | None:
  return 300 if not started or (car_state_valid and not enabled and math.isfinite(speed) and abs(speed) < .1) else None


def graph_display_bounds(lo: float, hi: float) -> tuple[float, float]:
  padding = max((hi-lo)*.1, abs(lo)*.01, .1)
  return lo-padding, hi+padding


@dataclass
class BusStats:
  count: int = 0
  last_seen: float = 0.0
  rate_samples: deque = field(default_factory=lambda: deque(maxlen=32))

  def alive(self, now: float | None = None) -> bool:
    if self.last_seen == 0.0:
      return False
    now = time.monotonic() if now is None else now
    return (now - self.last_seen) < BUS_ALIVE_TIMEOUT

  def rate(self, now: float) -> float:
    if not self.rate_samples or now-self.rate_samples[-1][0] >= .2:
      self.rate_samples.append((now, self.count))
    while len(self.rate_samples) > 2 and now-self.rate_samples[1][0] > 2.:
      self.rate_samples.popleft()
    first_time, first_count = self.rate_samples[0]
    return (self.count-first_count)/(now-first_time) if self.alive(now) and now-first_time > .1 else 0.


@dataclass
class MessageStats(BusStats):
  data: bytes = b''
  decoded: bool = False
  last_decoded: float = 0.


@dataclass(frozen=True)
class MessageDefinition:
  bus: int
  address: int
  name: str
  size: int
  signals: tuple[str, ...]


def matches_query(query: str, bus: int, address: int, *names: str) -> bool:
  # Hex IDs match exactly, including digits-only hexadecimal such as 0135.
  # Bare decimal and hexadecimal IDs are both accepted for convenience.
  words = query.lower().split()
  identifiers = {f'{address:x}', f'{address:04x}', f'0x{address:x}', str(address)}
  haystack = ' '.join((BUS_LABELS[bus], *names)).lower()
  def matches(word):
    if word.startswith('0x'):
      try:
        return int(word, 16) == address
      except ValueError:
        return False
    return word in identifiers or word in haystack
  return all(matches(word) for word in words)


@dataclass
class SignalRow:
  bus: int
  address: int
  signal: str | None  # None means "raw/undecoded fallback for this address"
  text: str = ""
  last_updated: float = 0.0
  value: float | None = None
  message: str = ''
  unit: str = ''
  choice: str = ''
  changes: int = 0

  @property
  def key(self) -> tuple[int, int, str | None]:
    return (self.bus, self.address, self.signal)

  def label(self) -> str:
    if self.signal is not None:
      return f"{self.address:04X} {self.signal}"
    return f"{self.address:04X} (undecoded)"

  def details(self) -> str:
    message = self.message or 'No message definition in this bus DBC'
    return f"Bus {self.bus} ({BUS_LABELS[self.bus]}) | 0x{self.address:X} | {message} | {self.signal or 'raw bytes'}"


def signal_metadata(car_fingerprint):
  """Expose names, physical units and value tables already present in the matching DBC."""
  metadata = {}
  for bus, key in BUS_DBC_KEYS.items():
    name = GM_DBC_MAP[car_fingerprint][key]
    dbc = DbcFile(name)
    choices = CANDefine(name).dv
    content = get_generated_dbcs().get(name)
    if content is None:
      content = (Path(DBC_PATH)/(name+'.dbc')).read_text()
    units, address = {}, None
    for line in content.splitlines():
      if match := re.match(r'^BO_ (\d+) ', line):
        address = int(match[1])
      elif match := re.match(r'^\s*SG_ (\w+)(?:\s+\w+)?\s*:.*\]\s*"([^"]*)"', line):
        units[(address, match[1])] = match[2]
    for address, msg in dbc.msgs.items():
      for signal in msg.sigs:
        metadata[(bus, address, signal)] = (msg.name, units.get((address, signal), ''), choices.get(address, {}).get(signal, {}))
  return metadata


def build_parsers(car_fingerprint: str) -> tuple[dict[int, CANParser], dict[int, set[int]]]:
  """One full-coverage CANParser per bus, plus the set of addresses each bus's DBC actually defines."""
  dbc_map = GM_DBC_MAP[car_fingerprint]
  parsers: dict[int, CANParser] = {}
  known_addrs: dict[int, set[int]] = {}
  for bus, dbc_key in BUS_DBC_KEYS.items():
    dbc_name = dbc_map[dbc_key]
    dbc_file = DbcFile(dbc_name)
    parsers[bus] = CANParser(dbc_name, [(addr, 0) for addr in dbc_file.msgs], bus)
    known_addrs[bus] = set(dbc_file.msgs.keys())
  return parsers, known_addrs


def format_value(value: float) -> str:
  if not math.isfinite(value):
    return 'invalid'
  if value == int(value):
    return str(int(value))
  return f"{value:.4g}"


class CanSnapshot:
  """Stateful decoder + bus-activity tally, no pyray dependency - fully unit-testable."""

  def __init__(self, car_fingerprint: str):
    self.parsers, self._known_addrs = build_parsers(car_fingerprint)
    self.metadata = signal_metadata(car_fingerprint)
    self.tally: dict[int, BusStats] = {bus: BusStats() for bus in self.parsers}
    self.rows: dict[tuple[int, int, str | None], SignalRow] = {}
    self.dbc_names = {bus: GM_DBC_MAP[car_fingerprint][key] for bus, key in BUS_DBC_KEYS.items()}
    self.definitions = {(bus, address): MessageDefinition(bus, address, msg.name, msg.size, tuple(msg.sigs))
                        for bus, name in self.dbc_names.items() for address, msg in DbcFile(name).msgs.items()}
    self.messages: dict[tuple[int, int], MessageStats] = {}
    self.raw_counts = dict.fromkeys(self.parsers, 0)
    self.omitted_raw_frames = dict.fromkeys(self.parsers, 0)

  def catalog(self, bus_filter=None, query='') -> list[MessageDefinition]:
    return sorted((d for d in self.definitions.values() if (bus_filter is None or d.bus == bus_filter)
                   and matches_query(query, d.bus, d.address, d.name, *d.signals,
                                     *(self.metadata[(d.bus, d.address, name)][1] for name in d.signals))), key=lambda d: (d.bus, d.address))

  def coverage(self, now=None) -> list[dict]:
    now = time.monotonic() if now is None else now
    result = []
    for bus, dbc in self.dbc_names.items():
      observed = {address: stats for (src, address), stats in self.messages.items() if src == bus}
      known = set(observed) & self._known_addrs[bus]
      result.append({'bus': bus, 'name': BUS_LABELS[bus], 'dbc': dbc, 'alive': self.tally[bus].alive(now),
                     'frames': self.tally[bus].count, 'rate': self.tally[bus].rate(now),
                     'observed': len(observed), 'matched': len(known), 'decoded': sum(observed[a].decoded for a in known),
                     'unknown': len(observed)-len(known), 'defined_messages': len(self._known_addrs[bus]),
                     'defined_signals': sum(len(d.signals) for d in self.definitions.values() if d.bus == bus),
                     'omitted_raw_frames': self.omitted_raw_frames[bus]})
    return result

  def definition_row(self, key) -> SignalRow:
    message, unit, _ = self.metadata[key]
    return SignalRow(bus=key[0], address=key[1], signal=key[2], message=message, unit=unit, text='Not seen')

  def signal_details(self, key) -> str:
    row = self.rows.get(key)
    if key[2] is None:
      return row.details() if row else ''
    row = row or self.definition_row(key)
    dbc = self.dbc_names[key[0]]
    signal = DbcFile(dbc).msgs[key[1]].sigs[key[2]]
    choices = self.metadata[key][2]
    states = ', '.join(f'{v}={label}' for v, label in choices.items())
    return (f'{row.details()}<br>DBC: {dbc}<br>'
            + f'{signal.size} bits at bit {signal.start_bit}; {"signed" if signal.is_signed else "unsigned"}; '
            + f'{"little" if signal.is_little_endian else "big"} endian; '
            + f'value = raw * {signal.factor:g} + {signal.offset:g}'
            + (f' {row.unit}' if row.unit else '') + (f'<br>States: {states}' if states else ''))

  def ingest(self, batches: list[tuple[int, list[tuple[int, bytes, int]]]]) -> set[tuple[int, int, str | None]]:
    """Feed drained (timestamp_ns, [(address, dat, src), ...]) batches. Returns the set of row keys touched."""
    now = time.monotonic()
    touched: set[tuple[int, int, str | None]] = set()

    updated_by_bus: dict[int, set[int]] = {}
    value_changes, last_values = {}, {}
    for t, frames in batches:
      frames_by_bus: dict[int, list[tuple[int, bytes, int]]] = {}
      for address, dat, src in frames:
        stats = self.tally.get(src)
        if stats is None or len(dat) > 64:
          continue
        stats.count += 1
        stats.last_seen = now
        msg_key = (src, address)
        if msg_key not in self.messages:
          if address not in self._known_addrs[src]:
            if self.raw_counts[src] >= MAX_RAW_MESSAGES_PER_BUS:
              self.omitted_raw_frames[src] += 1
              continue
            self.raw_counts[src] += 1
          self.messages[msg_key] = MessageStats()
        msg = self.messages[msg_key]
        msg.count += 1
        msg.last_seen, msg.data = now, dat
        definition = self.definitions.get(msg_key)
        wrong_size = definition is not None and len(dat) != definition.size
        if definition is None or wrong_size:
          key = (src, address, None)
          touched.add(key)
          text = dat.hex(' ').upper()
          previous = self.rows.get(key)
          changes = previous.changes + (previous.text != text) if previous else 0
          reason = f'{definition.name}: {len(dat)} bytes received; expected {definition.size}' if wrong_size else ''
          self.rows[key] = SignalRow(bus=src, address=address, signal=None, text=text, last_updated=now, changes=changes, message=reason)
        if not wrong_size:
          frames_by_bus.setdefault(src, []).append((address, dat, src))

      # Only feed each parser the frames for its own bus (and only bother calling a
      # parser at all when this batch actually touched its bus) instead of handing every
      # parser the full mixed-bus frame list - CANParser.update() would filter it down to
      # its own src internally anyway, but re-scanning all frames once per bus (and paying
      # its per-call vl_all bookkeeping) for buses with nothing new here is wasted work.
      # Note: this means a parser's own can_valid/bus_timeout bookkeeping (which advances
      # _last_update_nanos unconditionally on every update() call) stops advancing entirely
      # once its bus goes silent, rather than correctly timing out - harmless today since
      # this module never reads those properties (bus liveness is tracked separately via
      # self.tally), but worth knowing before ever relying on a parser's own validity here.
      for bus, bus_frames in frames_by_bus.items():
        parser = self.parsers.get(bus)
        if parser is None:
          continue
        updated = parser.update([(t, bus_frames)])
        updated_by_bus.setdefault(bus, set()).update(updated)
        for address in updated:
          for name, values in parser.vl_all[address].items():
            key = (bus, address, name)
            row = self.rows.get(key)
            previous_value = last_values.get(key, row.value if row else None)
            for value in values:
              if previous_value is not None and previous_value != value:
                value_changes[key] = value_changes.get(key, 0)+1
              previous_value = value
            last_values[key] = previous_value

    for bus, addrs in updated_by_bus.items():
      parser = self.parsers[bus]
      for address in addrs:
        self.messages[(bus, address)].decoded = True
        self.messages[(bus, address)].last_decoded = now
        raw_key = (bus, address, None)
        if raw_key in self.rows:
          del self.rows[raw_key]
          touched.add(raw_key)
        signals = parser.vl.get(address, {})
        for name, value in signals.items():
          key = (bus, address, name)
          touched.add(key)
          message, unit, choices = self.metadata.get(key, ('', '', {}))
          choice = choices.get(value, '')
          text = format_value(value)
          if unit:
            text += ' '+unit
          if choice:
            text += ' ('+choice+')'
          previous = self.rows.get(key)
          changes = (previous.changes if previous else 0) + value_changes.get(key, 0)
          self.rows[key] = SignalRow(bus=bus, address=address, signal=name,
                                      text=text, last_updated=now, value=value, message=message, unit=unit, choice=choice, changes=changes)

    return touched

  def rows_for(self, bus_filter: int | None) -> list[SignalRow]:
    rows = self.rows.values()
    if bus_filter is not None:
      rows = (r for r in rows if r.bus == bus_filter)
    return sorted(rows, key=lambda r: (r.bus, r.address, r.signal or ""))


@dataclass
class GraphBuffer:
  window_s: float = 10.0
  # deque so evicting expired samples off the front is O(1); a plain list's pop(0) is O(n),
  # which turns a whole session's worth of high-frequency samples into O(n^2) work overall.
  samples: deque[tuple[float, float]] = field(default_factory=lambda: deque(maxlen=6000))

  def add(self, t: float, value: float) -> None:
    if not math.isfinite(t) or not math.isfinite(value):
      return
    if self.samples and t < self.samples[-1][0]:
      return  # Late data must not draw backwards or invalidate time-cursor lookup.
    self.samples.append((t, value))
    cutoff = t - self.window_s
    while self.samples and self.samples[0][0] < cutoff:
      self.samples.popleft()

  def bounds(self) -> tuple[float, float] | None:
    if not self.samples:
      return None
    values = [v for _, v in self.samples]
    return min(values), max(values)
