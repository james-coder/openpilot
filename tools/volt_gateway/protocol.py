"""Experimental, bounded wire codecs. Not production firmware or a CAN driver.

No arbitration IDs are assigned here. ISO-TP flow-control scheduling belongs to
the simulator, not this reassembler. Streaming frames are untrusted observations.
"""

from dataclasses import dataclass
import binascii
import hashlib
import hmac
import math
import struct
import zlib

MAX_PDU = 512
MAJOR, MINOR = 1, 0
HEADER = struct.Struct("!4sBBBB8sQII")
TAG_SIZE = 16
FRAME = struct.Struct("!BBBBIQI8s")
COMPACT = struct.Struct("!HIIB8s")


class ProtocolError(ValueError):
  pass


def isotp_encode(payload: bytes) -> list[bytes]:
  if not 1 <= len(payload) <= MAX_PDU:
    raise ProtocolError("PDU length")
  if len(payload) <= 7:
    return [(bytes([len(payload)]) + payload).ljust(8, b"\0")]
  result = [(bytes([0x10 | len(payload) >> 8, len(payload) & 255]) + payload[:6])]
  for i, offset in enumerate(range(6, len(payload), 7), 1):
    result.append((bytes([0x20 | (i & 15)]) + payload[offset:offset + 7]).ljust(8, b"\0"))
  return result


def flow_frames(data_frames: int, block_size: int = 8) -> int:
  if block_size < 1 or data_frames < 1:
    raise ProtocolError("flow-control arguments")
  return (data_frames - 2) // block_size + 1 if data_frames > 1 else 0


class IsoTpReceiver:
  def __init__(self):
    self.reset()

  def reset(self):
    self.buffer = bytearray()
    self.length = 0
    self.expected = 1
    self.started = self.last = None

  def feed(self, frame: bytes, now: float) -> bytes | None:
    try:
      return self._feed(frame, now)
    except (ValueError, OverflowError):
      self.reset()
      raise

  def _feed(self, frame: bytes, now: float) -> bytes | None:
    if not math.isfinite(now) or now < 0 or len(frame) != 8:
      raise ProtocolError("DLC must be eight")
    if self.last is not None and (now < self.last or now - self.last > 1 or now - self.started > 10):
      raise ProtocolError("transfer deadline")
    kind = frame[0] >> 4
    if kind == 0:
      size = frame[0] & 15
      if self.length or not 1 <= size <= 7 or any(frame[1 + size:]):
        raise ProtocolError("single frame")
      return frame[1:1 + size]
    if kind == 1:
      size = ((frame[0] & 15) << 8) | frame[1]
      if self.length or not 8 <= size <= MAX_PDU:
        raise ProtocolError("first frame")
      self.length, self.buffer = size, bytearray(frame[2:])
      self.started = self.last = now
      return None
    if kind != 2 or not self.length or (frame[0] & 15) != self.expected:
      raise ProtocolError("fragment sequence/type")
    remaining = self.length - len(self.buffer)
    size = min(7, remaining)
    if any(frame[1 + size:]):
      raise ProtocolError("padding")
    self.buffer.extend(frame[1:1 + size])
    self.last, self.expected = now, (self.expected + 1) & 15
    if len(self.buffer) != self.length:
      return None
    result = bytes(self.buffer)
    self.reset()
    return result


@dataclass(frozen=True)
class Observation:
  bus: int
  address: int
  extended: bool
  rtr: bool
  dlc: int
  data: bytes
  timestamp_us: int
  sequence: int

  def validate(self):
    if not 0 <= self.bus <= 3 or not 0 <= self.address <= (0x1fffffff if self.extended else 0x7ff):
      raise ProtocolError("bus/address")
    if not 0 <= self.dlc <= 8 or len(self.data) != (0 if self.rtr else self.dlc):
      raise ProtocolError("DLC/data")
    if not 0 <= self.timestamp_us < 2**64 or not 0 <= self.sequence < 2**32:
      raise ProtocolError("timestamp/sequence")

  def pack(self) -> bytes:
    self.validate()
    return FRAME.pack(MAJOR, self.bus, int(self.extended) | (int(self.rtr) << 1), self.dlc,
                      self.address, self.timestamp_us, self.sequence, self.data.ljust(8, b"\0"))

  @classmethod
  def unpack(cls, payload: bytes):
    if len(payload) != FRAME.size:
      raise ProtocolError("record size")
    major, bus, flags, dlc, address, timestamp, sequence, data = FRAME.unpack(payload)
    if major != MAJOR or flags & ~3 or dlc > 8 or any(data[0 if flags & 2 else dlc:]):
      raise ProtocolError("record flags/padding")
    result = cls(bus, address, bool(flags & 1), bool(flags & 2), dlc, data[:0 if flags & 2 else dlc], timestamp, sequence)
    result.validate()
    return result


def stream_encode(record: Observation) -> list[bytes]:
  body = record.pack()
  payload = body + struct.pack("!I", zlib.crc32(body))
  return [(bytes([0x80 | i]) + payload[offset:offset + 7]).ljust(8, b"\0")
          for i, offset in enumerate(range(0, len(payload), 7))]


@dataclass(frozen=True)
class Subscription:
  """Installed only by a future authenticated control exchange; never from telemetry.

  Handle/anchor mappings are immutable within a session. Changing the anchor
  requires a new handle. Clear reassembly and mappings at a session/boot change.
  """
  handle: int
  bus: int
  address: int
  extended: bool
  anchor_us: int


def compact_encode(record: Observation, subscription: Subscription) -> list[bytes]:
  record.validate()
  if (record.bus, record.address, record.extended) != (subscription.bus, subscription.address, subscription.extended):
    raise ProtocolError("subscription mismatch")
  delta = record.timestamp_us - subscription.anchor_us
  if not 0 <= delta < 2**32 or not 0 <= subscription.handle < 2**16:
    raise ProtocolError("anchor/handle range")
  body = COMPACT.pack(subscription.handle, record.sequence, delta, record.dlc | (int(record.rtr) << 4), record.data.ljust(8, b"\0"))
  payload = body + struct.pack("!H", binascii.crc_hqx(body, 0xffff))
  return [bytes([0x90 | i]) + payload[i * 7:(i + 1) * 7] for i in range(3)]


class StreamReceiver:
  def __init__(self, subscriptions: tuple[Subscription, ...] = ()):
    if len(subscriptions) > 32 or len({s.handle for s in subscriptions}) != len(subscriptions):
      raise ProtocolError("subscription table")
    self.subscriptions = {s.handle: s for s in subscriptions}
    self.reset()

  def reset(self):
    self.buffer = bytearray()
    self.kind = self.index = 0
    self.started = self.last = None

  def feed(self, frame: bytes, now: float) -> Observation | None:
    try:
      return self._feed(frame, now)
    except (ValueError, KeyError, OverflowError):
      self.reset()
      raise ProtocolError("invalid/expired untrusted stream record") from None

  def _feed(self, frame: bytes, now: float) -> Observation | None:
    if not math.isfinite(now) or now < 0 or len(frame) != 8 or frame[0] >> 4 not in (8, 9):
      raise ProtocolError("stream DLC/type")
    kind, index = frame[0] >> 4, frame[0] & 15
    if index == 0:
      self.reset()  # bounded resynchronization; discard incomplete previous record
      self.kind, self.started = kind, now
    if (kind != self.kind or index != self.index or
        (self.last is not None and (now < self.last or now - self.last > .25 or now - self.started > 1))):
      raise ProtocolError("stream order/deadline")
    self.buffer.extend(frame[1:])
    self.last, self.index = now, index + 1
    if self.index != (5 if kind == 8 else 3):
      return None
    raw = bytes(self.buffer)
    self.reset()
    if kind == 8:
      body, crc, padding = raw[:28], raw[28:32], raw[32:]
      if any(padding) or zlib.crc32(body) != int.from_bytes(crc, "big"):
        raise ProtocolError("stream CRC/padding")
      return Observation.unpack(body)
    body, crc = raw[:19], raw[19:]
    if binascii.crc_hqx(body, 0xffff) != int.from_bytes(crc, "big"):
      raise ProtocolError("compact CRC")
    handle, sequence, delta, flags, data = COMPACT.unpack(body)
    sub = self.subscriptions[handle]
    dlc, rtr = flags & 15, bool(flags & 16)
    if flags & ~31 or dlc > 8 or any(data[0 if rtr else dlc:]):
      raise ProtocolError("compact flags/padding")
    result = Observation(sub.bus, sub.address, sub.extended, rtr, dlc, data[:0 if rtr else dlc], sub.anchor_us + delta, sequence)
    result.validate()
    return result


def hkdf(secret: bytes, salt: bytes, info: bytes) -> bytes:
  """One SHA-256 output block of RFC 5869; experimental host reference only."""
  prk = hmac.digest(salt, secret, "sha256")
  return hmac.digest(prk, info + b"\x01", "sha256")


def session_key(secret: bytes, host_nonce: bytes, gateway_nonce: bytes, transcript: bytes, direction: bytes) -> bytes:
  if len(secret) != 32 or len(host_nonce) != 32 or len(gateway_nonce) != 32 or direction not in (b"host", b"gateway"):
    raise ProtocolError("session parameters")
  return hkdf(secret, hashlib.sha256(host_nonce + gateway_nonce).digest(),
              b"voltgw-v1\0" + direction + hashlib.sha256(transcript).digest())


def seal(key: bytes, session: bytes, sequence: int, transaction: int, opcode: int, payload: bytes, *, minor: int = MINOR) -> bytes:
  if len(key) != 32 or len(session) != 8 or len(payload) > MAX_PDU - HEADER.size - TAG_SIZE:
    raise ProtocolError("authenticated envelope")
  header = HEADER.pack(b"VGW1", MAJOR, minor, 1, opcode, session, sequence, transaction, len(payload))
  body = header + payload
  return body + hmac.digest(key, body, "sha256")[:TAG_SIZE]


class AuthReceiver:
  """Checks only. Does not execute operations or implement device pairing.

  One last accepted PDU is retained to detect byte-identical retransmission.
  A dispatcher must return the corresponding cached result, not repeat effects.
  """
  def __init__(self, key: bytes, session: bytes, expires: float, minor: int = MINOR):
    if len(key) != 32 or len(session) != 8 or not math.isfinite(expires) or expires <= 0 or not 0 <= minor <= 255:
      raise ProtocolError("session parameters")
    self.key, self.session, self.expires, self.minor = key, session, expires, minor
    self.next_sequence, self.last = 0, None
    self.last_time, self.closed = 0., False

  def accept(self, pdu: bytes, now: float) -> tuple[int, int, bytes, bool]:
    if self.closed or not math.isfinite(now) or now < self.last_time or now >= self.expires:
      self.closed = True
      raise ProtocolError("expired/invalid session clock")
    self.last_time = now
    if not HEADER.size + TAG_SIZE <= len(pdu) <= MAX_PDU:
      raise ProtocolError("invalid authenticated command")
    body, mac = pdu[:-TAG_SIZE], pdu[-TAG_SIZE:]
    if not hmac.compare_digest(hmac.digest(self.key, body, "sha256")[:TAG_SIZE], mac):
      raise ProtocolError("invalid authenticated command")
    magic, major, minor, kind, opcode, session, sequence, transaction, size = HEADER.unpack(body[:HEADER.size])
    if (magic != b"VGW1" or major != MAJOR or minor != self.minor or kind != 1 or session != self.session or
        size != len(body) - HEADER.size):
      raise ProtocolError("incompatible authenticated command")
    duplicate = self.last is not None and hmac.compare_digest(pdu, self.last)
    if not duplicate:
      if sequence != self.next_sequence:
        raise ProtocolError("replayed/out-of-order command")
      self.last, self.next_sequence = pdu, sequence + 1
    return opcode, transaction, body[HEADER.size:], duplicate


@dataclass(frozen=True)
class Compatibility:
  major: int
  minor: int
  capabilities: frozenset[str]
  safety_policy: str
  build_id: str


def negotiate(host: Compatibility, gateway: Compatibility, required: frozenset[str], verified_safety_policy: str) -> int:
  if (host.major != MAJOR or gateway.major != MAJOR or not verified_safety_policy or
      not 0 <= host.minor <= 255 or not 0 <= gateway.minor <= 255 or
      host.safety_policy != verified_safety_policy or gateway.safety_policy != verified_safety_policy or
      not required <= host.capabilities & gateway.capabilities or not host.build_id or not gateway.build_id):
    raise ProtocolError("gateway disabled: incompatible protocol/capability/safety policy")
  return min(host.minor, gateway.minor)
