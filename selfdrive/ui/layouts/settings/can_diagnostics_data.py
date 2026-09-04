import time
from collections import deque
from dataclasses import dataclass, field

from opendbc.can import CANParser
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


@dataclass
class BusStats:
  count: int = 0
  last_seen: float = 0.0

  def alive(self, now: float | None = None) -> bool:
    if self.last_seen == 0.0:
      return False
    now = time.monotonic() if now is None else now
    return (now - self.last_seen) < BUS_ALIVE_TIMEOUT


@dataclass
class SignalRow:
  bus: int
  address: int
  signal: str | None  # None means "raw/undecoded fallback for this address"
  text: str = ""
  last_updated: float = 0.0

  @property
  def key(self) -> tuple[int, int, str | None]:
    return (self.bus, self.address, self.signal)

  def label(self) -> str:
    if self.signal is not None:
      return f"{self.address:04X} {self.signal}"
    return f"{self.address:04X} (undecoded)"


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
  if value == int(value):
    return str(int(value))
  return f"{value:.4g}"


class CanSnapshot:
  """Stateful decoder + bus-activity tally, no pyray dependency - fully unit-testable."""

  def __init__(self, car_fingerprint: str):
    self.parsers, self._known_addrs = build_parsers(car_fingerprint)
    self.tally: dict[int, BusStats] = {bus: BusStats() for bus in self.parsers}
    self.rows: dict[tuple[int, int, str | None], SignalRow] = {}

  def ingest(self, batches: list[tuple[int, list[tuple[int, bytes, int]]]]) -> set[tuple[int, int, str | None]]:
    """Feed drained (timestamp_ns, [(address, dat, src), ...]) batches. Returns the set of row keys touched."""
    now = time.monotonic()
    touched: set[tuple[int, int, str | None]] = set()

    updated_by_bus: dict[int, set[int]] = {}
    for t, frames in batches:
      frames_by_bus: dict[int, list[tuple[int, bytes, int]]] = {}
      for address, dat, src in frames:
        frames_by_bus.setdefault(src, []).append((address, dat, src))

        stats = self.tally.get(src)
        if stats is None:
          continue
        stats.count += 1
        stats.last_seen = now
        if address not in self._known_addrs.get(src, ()):
          key = (src, address, None)
          touched.add(key)
          self.rows[key] = SignalRow(bus=src, address=address, signal=None,
                                      text=dat.hex(' ').upper(), last_updated=now)

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

    for bus, addrs in updated_by_bus.items():
      parser = self.parsers[bus]
      for address in addrs:
        signals = parser.vl.get(address, {})
        for name, value in signals.items():
          key = (bus, address, name)
          touched.add(key)
          self.rows[key] = SignalRow(bus=bus, address=address, signal=name,
                                      text=format_value(value), last_updated=now)

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
  samples: deque[tuple[float, float]] = field(default_factory=deque)

  def add(self, t: float, value: float) -> None:
    self.samples.append((t, value))
    cutoff = t - self.window_s
    while self.samples and self.samples[0][0] < cutoff:
      self.samples.popleft()

  def bounds(self) -> tuple[float, float] | None:
    if not self.samples:
      return None
    values = [v for _, v in self.samples]
    return min(values), max(values)
