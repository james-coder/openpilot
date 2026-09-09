"""Bounded, read-only inspection state. No UI, socket, file or vehicle writes."""
import math
import time
from bisect import bisect_right
from dataclasses import dataclass
from types import MappingProxyType

from opendbc.can.dbc import DBC
from openpilot.selfdrive.ui.layouts.settings.can_diagnostics_data import CanSnapshot, GraphBuffer

MAX_FAVORITES = 24


def validated_favorites(document, fingerprint, metadata):
  if not isinstance(document, dict) or document.get('version') != 1:
    return []
  cars = document.get('cars')
  entries = cars.get(fingerprint, []) if isinstance(cars, dict) else []
  result = []
  if isinstance(entries, list):
    for entry in entries[:MAX_FAVORITES]:
      if (isinstance(entry, list) and len(entry) == 3 and type(entry[0]) is int and type(entry[1]) is int
          and isinstance(entry[2], str)):
        key = tuple(entry)
        if key in metadata and key not in result:
          result.append(key)
  return result


def preference_document(fingerprint, favorites):
  # This is a single-car inspector; never retain arbitrary unvalidated JSON.
  return {'version': 1, 'cars': {fingerprint: [list(k) for k in favorites[:MAX_FAVORITES]]}}


def signal_bits(signal):
  """DBC bit indices (byte*8 + LSB-first bit number), including Motorola sawtooth."""
  if signal.is_little_endian:
    return tuple(range(signal.start_bit, signal.start_bit+signal.size))
  start = (signal.start_bit//8)*8 + 7-signal.start_bit%8
  return tuple((i//8)*8 + 7-i%8 for i in range(start, start+signal.size))


def bit_definitions(snapshot, message):
  definition = snapshot.definitions.get(message)
  if definition is None:
    return {}
  msg = DBC(snapshot.dbc_names[message[0]]).msgs[message[1]]
  result = {}
  for name, signal in msg.sigs.items():
    for bit in signal_bits(signal):
      if 0 <= bit < definition.size*8:
        result.setdefault(bit, []).append(name)
  return {bit: tuple(names) for bit, names in result.items()}


@dataclass(frozen=True)
class BitState:
  message: tuple
  data: bytes
  changed_at: tuple
  flips: tuple
  started: float


class BitTracker:
  def __init__(self, message, data=b'', now=None):
    self.message, self.data = message, data
    self.started = time.monotonic() if now is None else now
    self.changed_at = [0.]*512
    self.flips = [0]*512

  def update(self, data, now):
    for byte, (old, new) in enumerate(zip(self.data, data, strict=False)):
      difference = old ^ new
      for bit in range(8):
        if difference & (1 << bit):
          index = byte*8+bit
          self.changed_at[index] = now
          self.flips[index] += 1
    # New bytes have no baseline, not invented transitions from zero.
    self.data = data

  def freeze(self):
    return BitState(self.message, self.data, tuple(self.changed_at), tuple(self.flips), self.started)


@dataclass(frozen=True)
class RowState:
  bus: int
  address: int
  signal: str | None
  text: str
  value: float | None
  last_updated: float
  message: str
  unit: str
  choice: str
  changes: int


@dataclass(frozen=True)
class MessageState:
  count: int
  last_seen: float
  last_decoded: float
  data: bytes
  rate: float


@dataclass(frozen=True)
class DisplayState:
  now: float
  rows: object
  messages: object
  coverage: tuple
  changed: frozenset
  new: frozenset
  histories: object
  bits: BitState | None


def sample_at(samples, when):
  """Previous real sample, never an interpolated value."""
  index = bisect_right(samples, (when, math.inf))-1
  return samples[index] if index >= 0 else None


class InspectionSession:
  def __init__(self, fingerprint, preferences=None):
    self.fingerprint = fingerprint
    self.snapshot = CanSnapshot(fingerprint)
    self.favorites = validated_favorites(preferences, fingerprint, self.snapshot.metadata)
    self.baseline = {}
    self.changed, self.new = set(), set()
    self.histories = {}
    self.bits = None
    self.frozen = None
    self.window = 30.
    self.reset_baseline()

  def reset_baseline(self):
    if self.frozen is not None:
      return
    self.baseline = {k: (row.value, row.text, row.changes) for k, row in self.snapshot.rows.items()}
    self.changed.clear()
    self.new.clear()

  def toggle_favorite(self, key):
    if key in self.favorites:
      self.favorites.remove(key)
    elif key in self.snapshot.metadata and len(self.favorites) < MAX_FAVORITES:
      self.favorites.append(key)
    else:
      return False
    return True

  def select_graph(self, key, slot=0):
    if self.frozen is not None or key not in self.snapshot.metadata or slot not in (0, 1):
      return False
    keys = list(self.histories)
    if key in keys:
      return True
    if slot < len(keys):
      keys[slot] = key
    else:
      keys.append(key)
    self.histories = {k: self.histories.get(k, GraphBuffer(window_s=30)) for k in keys[:2]}
    row = self.snapshot.rows.get(key)
    if row and row.value is not None:
      self.histories[key].add(row.last_updated, row.value)
    return True

  def inspect_bits(self, message):
    if self.frozen is not None:
      return
    msg = self.snapshot.messages.get(message)
    self.bits = BitTracker(message, msg.data if msg else b'')

  def ingest(self, batches):
    touched = set()
    for timestamp, frames in batches:
      now = time.monotonic()
      if self.bits:
        for address, data, bus in frames:
          if (bus, address) == self.bits.message and len(data) <= 64:
            self.bits.update(data, now)
      changed = self.snapshot.ingest([(timestamp, frames)])
      touched.update(changed)
      for key in {(bus, address) for address, _, bus in frames}:
        message = self.snapshot.messages.get(key)
        if message:
          message.rate(now)
      for key in changed:
        row = self.snapshot.rows.get(key)
        if row is None:
          self.changed.discard(key)
          self.new.discard(key)
          continue
        if key not in self.baseline:
          self.new.add(key)
          self.baseline[key] = (row.value, row.text, row.changes)
        elif self.baseline[key] != (row.value, row.text, row.changes):
          self.changed.add(key)
        if key in self.histories and row.value is not None:
          self.histories[key].add(timestamp/1e9, row.value)
    return touched

  def capture(self, now=None, include_history=False):
    if self.frozen is not None:
      return self.frozen
    now = time.monotonic() if now is None else now
    return DisplayState(
      now,
      MappingProxyType({k: RowState(**{name: getattr(row, name) for name in RowState.__dataclass_fields__})
                        for k, row in self.snapshot.rows.items()}),
      MappingProxyType({k: MessageState(m.count, m.last_seen, m.last_decoded, m.data, m.rate(now)) for k, m in self.snapshot.messages.items()}),
      tuple(MappingProxyType(r) for r in self.snapshot.coverage(now)), frozenset(self.changed), frozenset(self.new),
      MappingProxyType({k: tuple(buf.samples) for k, buf in self.histories.items()}) if include_history else MappingProxyType({}),
      self.bits.freeze() if self.bits else None,
    )

  def toggle_freeze(self):
    self.frozen = self.capture(include_history=True) if self.frozen is None else None
    return self.frozen

  def graph_samples(self, key):
    return self.frozen.histories.get(key, ()) if self.frozen else self.histories[key].samples
