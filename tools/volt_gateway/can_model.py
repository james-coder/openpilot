"""Deterministic Classic CAN behavioral model, not electrical/MMIO emulation.

Models arbitration, non-preemptible frames, ACK retries, finite mailboxes and RX
queues. No device APIs. Error-frame costs and stuffed bit counts are conservative
approximations; bus-off is injected, not a modeled TEC/REC implementation.
"""

from collections import Counter, deque
from dataclasses import dataclass, field

from openpilot.tools.volt_gateway.traffic_replay import frame_bits


@dataclass(frozen=True)
class Frame:
  address: int
  data: bytes
  extended: bool = False

  def __post_init__(self):
    if (type(self.extended) is not bool or type(self.address) is not int
        or not 0 <= self.address <= (0x1fffffff if self.extended else 0x7ff)
        or not isinstance(self.data, bytes) or len(self.data) > 8):
      raise ValueError('invalid Classic CAN data frame')

  @property
  def arbitration(self):
    # Data frames only: standard RTR=0 wins over extended SRR=1 at same base ID.
    return (self.address >> 18, 1, self.address & 0x3ffff) if self.extended else (self.address, 0, 0)

  @property
  def bits(self):
    # Preserve IDE even when an extended frame has a numerically small ID.
    return frame_bits(0x800 if self.extended else self.address, len(self.data))


@dataclass
class Pending:
  frame: Frame
  expires_us: int
  attempts: int = 0


@dataclass
class Node:
  name: str
  mailbox_limit: int = 3
  rx_limit: int = 3
  max_attempts: int = 4
  silent: bool = False
  bus_off: bool = False
  pending: list = field(default_factory=list)
  received: deque = field(default_factory=deque)
  counters: Counter = field(default_factory=Counter)

  def __post_init__(self):
    if not 1 <= self.mailbox_limit <= 3 or not 1 <= self.rx_limit <= 4096 or not 1 <= self.max_attempts <= 4:
      raise ValueError('model queue/retry bounds')

  def offer(self, frame, now_us, ttl_us=250000):
    if not isinstance(frame, Frame) or type(now_us) is not int or now_us < 0 or not 0 < ttl_us <= 10000000:
      raise ValueError('invalid submission')
    if self.silent or self.bus_off or len(self.pending) >= self.mailbox_limit:
      self.counters['rejected'] += 1
      return False
    self.pending.append(Pending(frame, now_us + ttl_us))
    return True

  def fail_silent(self):
    self.silent = True
    self.pending.clear()

  def enter_bus_off(self):
    self.bus_off = True
    self.pending.clear()
    self.counters['bus_off'] += 1


class Bus:
  def __init__(self, nodes, bitrate=500000):
    if bitrate not in (500000, 33333) or not 1 <= len(nodes) <= 32 or len({n.name for n in nodes}) != len(nodes):
      raise ValueError('model bus configuration')
    self.nodes, self.bitrate = tuple(nodes), bitrate
    self.now_us = 0
    self.occupied_until = 0
    self.active = None
    self.counters = Counter()

  def advance(self, now_us, *, corrupt=False):
    if type(now_us) is not int or now_us < self.now_us:
      raise ValueError('nonmonotonic model clock')
    self.now_us = now_us
    if self.active and now_us < self.occupied_until:
      return None
    if self.active:
      senders, frame, success = self.active
      self.active = None
      for node, packet in senders:
        if packet not in node.pending:
          continue
        if success or packet.attempts >= node.max_attempts or packet.expires_us <= now_us:
          node.pending.remove(packet)
        node.counters['success' if success else 'tx_error'] += 1
      if success:
        for node in self.nodes:
          if node.bus_off or any(node is sender for sender, _ in senders):
            continue
          if len(node.received) >= node.rx_limit:
            node.counters['rx_overflow'] += 1
          else:
            node.received.append((self.occupied_until, frame))
      # Caller gets a completion event before a new arbitration opportunity.
      return frame if success else None
    contenders = []
    for node in self.nodes:
      expired = [p for p in node.pending if p.expires_us <= now_us]
      node.counters['expired'] += len(expired)
      node.pending[:] = [p for p in node.pending if p.expires_us > now_us]
      if not node.silent and not node.bus_off and node.pending:
        packet = min(node.pending, key=lambda p: p.frame.arbitration)
        contenders.append((node, packet))
    if not contenders:
      return None
    priority = min(p.frame.arbitration for _, p in contenders)
    senders = [(n, p) for n, p in contenders if p.frame.arbitration == priority]
    for node, packet in contenders:
      if packet.frame.arbitration != priority:
        node.counters['arbitration_lost'] += 1
    frame = senders[0][1].frame
    collision = any(p.frame != frame for _, p in senders)
    ack = any(not n.silent and not n.bus_off and all(n is not sender for sender, _ in senders) for n in self.nodes)
    success = ack and not corrupt and not collision
    for _, packet in senders:
      packet.attempts += 1
    bits = max(p.frame.bits for _, p in senders) + (0 if success else 23)
    self.occupied_until = now_us + (bits * 1000000 + self.bitrate - 1) // self.bitrate
    self.active = senders, frame, success
    self.counters['attempts'] += 1
    self.counters['occupied_bits'] += bits
    self.counters['id_conflicts'] += collision
    return None
