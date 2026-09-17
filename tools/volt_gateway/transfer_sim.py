"""Offline, serialized two-peer protocol exercise; no device/flash/CAN APIs.

Uses actual codecs/MAC checks and explicitly paced ISO-TP FC frames. A shared
arbiter models the aggregate grant: distributing/enforcing grants in firmware
is still a deployment gate. This is not an electrical CAN arbitration model.
All keys and firmware bytes here are deterministic PUBLIC test fixtures.
"""

from collections import Counter, deque
import hashlib
import struct

from openpilot.tools.volt_gateway.adaptive import AdaptiveBudget, Conditions
from openpilot.tools.volt_gateway.protocol import AuthReceiver, IsoTpReceiver, ProtocolError, isotp_encode, seal


READY = Conditions(True, True, True, True, True, True, False)
HOST_KEY = hashlib.sha256(b'PUBLIC simulator host fixture').digest()
GATEWAY_KEY = hashlib.sha256(b'PUBLIC simulator gateway fixture').digest()
SESSION = b'sim-only'


def wire_steps(payload, sender):
  """BS=8, STmin=0; response-side FC uses the same aggregate token pool."""
  frames = isotp_encode(payload)
  result = []
  for i, frame in enumerate(frames):
    if i > 0 and (i - 1) % 8 == 0:
      result.append((1 - sender, b'\x30\x08\x00' + bytes(5), 'fc'))
    result.append((sender, frame, 'data'))
  return deque(result)


def run(size=8192, scenario='quiet', hard_fps=80, duration=180.):
  if size < 1 or size > 65536 or scenario not in ('quiet', 'loaded', 'congestion', 'lost_ack', 'lost_fc', 'corrupt',
                                                 'duplicate', 'expired', 'stale', 'power_loss', 'reconnect', 'no_acks'):
    raise ValueError('simulation parameters')
  image = bytes(i % 251 for i in range(size))
  received = bytearray()
  budget = AdaptiveBudget(hard_fps)
  expiry = 12. if scenario == 'expired' else duration + 1
  gateway_auth = AuthReceiver(HOST_KEY, SESSION, expiry)
  host_auth = AuthReceiver(GATEWAY_KEY, SESSION, expiry)
  decoders = [IsoTpReceiver(), IsoTpReceiver()]
  counts = Counter()
  sequence = offset = retries = ticks_bits = 0
  steps = deque()
  request = None
  ack_cache = None
  response_phase = False
  deadline = None
  start = control_start = None
  control_delays = []
  opcode = 0x40
  pending_control = False
  control_due = 10.
  injected = False
  failure = None
  peak_rate = 0.
  now = 0.

  def reset_transport():
    steps.clear()
    for decoder in decoders:
      decoder.reset()

  for tick in range(int(duration * 1000)):
    now = tick / 1000
    if scenario == 'power_loss' and now >= 12:
      budget.sample(now, 0, Conditions())
      failure = 'power/awake gate lost'
      break
    if scenario == 'reconnect' and now >= 12:
      failure = 'reconnect invalidates session; explicit reauthentication required'
      break
    if now >= expiry:
      failure = 'session expired'
      break
    if tick % 20 == 0:
      # RX accounting includes both simulated senders and failed attempts.
      load = .60 if scenario == 'loaded' else .20
      if scenario == 'congestion' and 12 <= now < 14:
        load = .85
      if not (scenario == 'stale' and 12 <= now < 14):
        budget.sample(now, min(10000, round(load * 10000) + ticks_bits), READY)
      ticks_bits = 0
      peak_rate = max(peak_rate, budget.rate)
    # A continuous source is deliberately suppressed during firmware transfer.
    if tick % 100 == 0:
      counts['telemetry_suppressed'] += 1
    if now >= control_due and not pending_control:
      pending_control, control_start = True, now
      control_due = now + 10.
    if deadline is not None and now >= deadline:
      if retries >= 3:
        failure = 'retry limit'
        break
      retries += 1
      counts['retries'] += 1
      reset_transport()
      response_phase = False
      deadline = None
    # Admission floor avoids starting ISO-TP PDUs at sub-timeout frame rates.
    # Slow ramp is retained; update waits instead of violating transport timers.
    if not steps and deadline is None and budget.rate >= 20:
      if request is None:
        opcode = 0x01 if pending_control else 0x40
        payload = b'' if opcode == 0x01 else struct.pack('!I', offset) + image[offset:offset + 256]
        request = seal(HOST_KEY, SESSION, sequence, sequence, opcode, payload)
        if start is None:
          start = now
      steps = wire_steps(request, 0)
      response_phase = False
      deadline = now + 15.  # bounded whole-transaction deadline, including pacing
      decoders = [IsoTpReceiver(), IsoTpReceiver()]
    if not steps or not budget.take(now):
      continue
    sender, frame, kind = steps.popleft()
    ticks_bits += 135
    counts['frames'] += 1
    counts[f'peer_{sender}_frames'] += 1
    counts[kind + '_frames'] += 1
    drop = False
    if kind == 'fc' and scenario == 'lost_fc' and not injected:
      injected = drop = True
    if response_phase and sender == 1 and kind == 'data' and (scenario == 'no_acks' or scenario == 'lost_ack' and not injected):
      injected = drop = True
    if drop:
      counts['injected_drops'] += 1
      reset_transport()  # peer stops on missing FC; lost ACK eventually retries
      deadline = now + 5.
      continue
    if kind == 'fc':
      continue
    if scenario == 'corrupt' and not injected:
      injected = True
      frame = frame[:-1] + bytes([frame[-1] ^ 1])
    try:
      pdu = decoders[1 - sender].feed(frame, now)
      if scenario == 'duplicate' and not injected:
        injected = True
        decoders[1 - sender].feed(frame, now)
      if pdu is None:
        continue
      if sender == 0:
        op, transaction, body, duplicate = gateway_auth.accept(pdu, now)
        if duplicate:
          counts['cached_responses'] += 1
        else:
          if op == 0x40:
            position = struct.unpack('!I', body[:4])[0]
            if position != len(received) or not 1 <= len(body[4:]) <= 256 or len(received) + len(body[4:]) > size:
              raise ProtocolError('image bounds')
            received.extend(body[4:])
            counts['chunk_effects'] += 1
          elif op != 0x01 or body:
            raise ProtocolError('unknown simulator opcode')
          ack_cache = seal(GATEWAY_KEY, SESSION, sequence, transaction, op, struct.pack('!I', len(received)))
        steps = wire_steps(ack_cache, 1)
        response_phase = True
      else:
        op, transaction, body, duplicate = host_auth.accept(pdu, now)
        expected = offset if opcode == 0x01 else min(size, offset + 256)
        if op != opcode or transaction != sequence or body != struct.pack('!I', expected):
          raise ProtocolError('ACK binding')
        if opcode == 0x01:
          control_delays.append(now - control_start)
          pending_control = False
        else:
          offset = expected
        sequence += 1
        retries = 0
        request = deadline = None
        reset_transport()
        if offset == size:
          break
    except ProtocolError:
      counts['rejected_pdus'] += 1
      reset_transport()
      deadline = now + 5.
  else:
    failure = 'simulation deadline'
  complete = offset == size and bytes(received) == image
  return {'scenario': scenario, 'complete': complete, 'failure': failure, 'image_bytes': size,
          'acknowledged_bytes': offset, 'received_bytes': len(received), 'elapsed_seconds': round(now, 3),
          'transfer_start_seconds': start, 'peak_rate_fps': peak_rate, 'hard_fps': hard_fps,
          'max_control_response_seconds': max(control_delays, default=None), 'control_responses': len(control_delays),
          'counts': dict(counts), 'sha256_matches': complete,
          'limitations': ['no flash programming or image signature verification', 'shared simulated grant arbiter, not firmware enforcement',
                          'no electrical CAN arbitration or hardware auto-retry model', 'public fixture keys; no pairing handshake',
                          'telemetry suppressed during update; no simultaneous high-rate telemetry benchmark',
                          'reconnect aborts; persistent resume not implemented']}


def report():
  return [run(scenario=s) for s in ('quiet', 'loaded', 'congestion', 'lost_ack', 'lost_fc', 'corrupt', 'duplicate',
                                   'expired', 'stale', 'power_loss', 'reconnect', 'no_acks')]
