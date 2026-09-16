from openpilot.tools.volt_gateway.can_model import Bus, Frame, Node


def complete(bus, now=0, corrupt=False):
  bus.advance(now, corrupt=corrupt)
  return bus.advance(bus.occupied_until)


def test_arbitration_and_nonpreemption():
  gateway, oem = Node('gateway'), Node('oem')
  bus = Bus([gateway, oem])
  low, high = Frame(0x650, b'12345678'), Frame(0x100, b'12345678')
  gateway.offer(low, 0)
  bus.advance(0)
  oem.offer(high, 1)
  assert bus.advance(1) is None
  assert bus.advance(270) == low
  assert complete(bus, 270) == high
  gateway.offer(low, 540)
  oem.offer(high, 540)
  assert complete(bus, 540) == high
  assert gateway.counters['arbitration_lost'] == 1


def test_missing_ack_has_bounded_attempts_not_unbounded_retries():
  node = Node('only-transmitter')
  bus = Bus([node, Node('silent-listener', silent=True)])
  node.offer(Frame(1, b'x'), 0)
  for _ in range(10):
    complete(bus, bus.now_us)
  assert bus.counters['attempts'] == 4
  assert not node.pending and node.counters['tx_error'] == 4


def test_conflicting_same_identifier_rejected_not_arbitrated_by_payload():
  a, b, receiver = Node('a'), Node('b'), Node('receiver')
  bus = Bus([a, b, receiver])
  a.offer(Frame(100, b'left'), 0)
  b.offer(Frame(100, b'right'), 0)
  assert complete(bus) is None
  assert bus.counters['id_conflicts'] == 1 and not receiver.received


def test_standard_wins_matching_extended_base():
  standard, extended, receiver = Node('standard'), Node('extended'), Node('receiver')
  bus = Bus([standard, extended, receiver])
  extended.offer(Frame(0x123 << 18, b'extended', True), 0)
  standard.offer(Frame(0x123, b'standard'), 0)
  assert complete(bus).extended is False
  assert Frame(1, b'12345678', True).bits == 160


def test_rx_overflow_mailbox_limit_and_silent_reset():
  sender, receiver = Node('sender'), Node('receiver', rx_limit=1)
  bus = Bus([sender, receiver])
  for i in range(3):
    assert sender.offer(Frame(i, b'x'), 0)
  assert not sender.offer(Frame(10, b'x'), 0)
  complete(bus)
  complete(bus, bus.now_us)
  assert receiver.counters['rx_overflow'] == 1
  sender.fail_silent()
  assert not sender.pending and not sender.offer(Frame(1, b'x'), bus.now_us)


def test_bus_off_clears_queue_and_does_not_auto_resume():
  sender, receiver = Node('sender'), Node('receiver')
  bus = Bus([sender, receiver])
  sender.offer(Frame(1, b'x'), 0)
  sender.enter_bus_off()
  complete(bus)
  assert bus.counters['attempts'] == 0
  assert not sender.offer(Frame(1, b'x'), 1)


def test_starved_frames_expire_without_late_backlog():
  gateway, oem = Node('gateway'), Node('oem')
  bus = Bus([gateway, oem])
  gateway.offer(Frame(0x650, bytes(8)), 0, ttl_us=1000)
  for i in range(5):
    oem.offer(Frame(1, bytes(8)), i * 270)
    complete(bus, i * 270)
  assert gateway.counters['expired'] == 1 and not gateway.pending
