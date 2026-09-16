"""Operator -> untrusted relay -> framed/MAC transport -> native C gate -> flash model.

End-to-end component integration, NOT complete firmware/STM32/CAN emulation.
All credentials are disposable test fixtures. No vehicle/device access.
"""

import hashlib
from pathlib import Path
import struct

from Crypto.PublicKey import ECC
import pytest

from openpilot.tools.volt_gateway.authority import AuthorityError, Authorization, Challenge, ImageManifest, Phase, SignedImage
from openpilot.tools.volt_gateway.native_harness import NativeAuthority, compile_authority
from openpilot.tools.volt_gateway.native_update import NativeUpdateEngine
from openpilot.tools.volt_gateway.operator import authorize_image, sign_image
from openpilot.tools.volt_gateway.protocol import AuthReceiver, IsoTpReceiver, ProtocolError, isotp_encode, seal
from openpilot.tools.volt_gateway.test_authority import READY, Slot
from openpilot.tools.volt_gateway.update_engine import Environment


@pytest.fixture(scope='module')
def library(tmp_path_factory):
  path = tmp_path_factory.mktemp('system-native') / 'authority.so'
  compile_authority(path)
  return path


class Gateway:
  def __init__(self, library, public, manifest, phase):
    self.authority = NativeAuthority(library, public, manifest.device, manifest.layout, phase)
    self.slot = Slot()
    self.engine = NativeUpdateEngine(self.authority, self.slot, self.environment) if phase == Phase.PROGRAM else None
    self.now_ms = 0

  def environment(self):
    return Environment(self.now_ms, self.now_ms, READY)

  def request(self, opcode, payload):
    if opcode == 0x40:
      return self.authority.challenge(SignedImage.unpack(payload), self.now_ms).pack()
    if opcode == 0x41:
      self.authority.authorize(Authorization.unpack(payload), self.now_ms)
      if self.engine:
        self.engine.begin()
      else:
        self.authority.require(Phase.ENTER, self.now_ms)
      return b''
    if opcode == 0x42 and self.engine and len(payload) >= 5:
      offset = struct.unpack('!I', payload[:4])[0]
      return struct.pack('!I', self.engine.chunk(offset, payload[4:]))
    if opcode == 0x43 and self.engine and not payload:
      self.engine.finish()
      return b''
    raise AuthorityError('unsupported command')


class Relay:
  """Owns routine credentials only; has no reference to an operator signing key."""
  def __init__(self, gateway):
    self.gateway = gateway
    self.key = hashlib.sha256(b'PUBLIC routine fixture only').digest()
    self.rx_key = hashlib.sha256(b'PUBLIC response fixture only').digest()
    self.session = b'testonly'
    self.receiver = AuthReceiver(self.key, self.session, 3600)
    self.host_receiver = AuthReceiver(self.rx_key, self.session, 3600)
    self.sequence = 0
    self.cached = None
    self.effects = 0

  @staticmethod
  def framed(raw, fault=None, advance=None):
    frames = isotp_encode(raw)
    if fault == 'corrupt':
      frames[1] = frames[1][:-1] + bytes([frames[1][-1] ^ 1])
    elif fault == 'missing':
      del frames[1]
    elif fault == 'reorder':
      frames[1], frames[2] = frames[2], frames[1]
    elif fault == 'duplicate':
      frames.insert(2, frames[1])
    elif fault == 'burst_loss':
      del frames[1:4]
    decoder, result = IsoTpReceiver(), None
    elapsed_ms = 0
    for i, frame in enumerate(frames):
      delta = (1, 4, 2, 17, 3)[i % 5] if fault == 'jitter' else 1
      if fault == 'delay' and i == 1:
        delta += 1500
      elapsed_ms += delta
      if advance:
        advance(delta)
      result = decoder.feed(frame, elapsed_ms / 1000)
    if result is None:
      raise ProtocolError('incomplete transfer')
    return result

  def call(self, opcode, payload, fault=None):
    def advance(delta):
      self.gateway.now_ms += delta
    raw = seal(self.key, self.session, self.sequence, self.sequence, opcode, payload)
    for attempt in range(4):
      now = self.gateway.now_ms / 1000
      try:
        pdu = self.framed(raw, fault if attempt == 0 and fault != 'lost_ack' else None, advance)
        now = self.gateway.now_ms / 1000
        op, txn, body, duplicate = self.receiver.accept(pdu, now)
        if not duplicate:
          try:
            response = b'\0' + self.gateway.request(op, body)
          except AuthorityError:
            response = b'\1'  # fixed-size NACK; no untrusted exception strings on wire
          self.effects += 1
          self.cached = seal(self.rx_key, self.session, self.sequence, txn, op, response)
        if attempt == 0 and fault == 'lost_ack':
          continue
        response_pdu = self.framed(self.cached, advance=advance)
        now = self.gateway.now_ms / 1000
        rop, rtxn, result, _ = self.host_receiver.accept(response_pdu, now)
        assert rop == opcode and rtxn == self.sequence
        self.sequence += 1
        self.gateway.now_ms += 1
        if result[:1] != b'\0':
          raise AuthorityError('gateway rejected request')
        return result[1:]
      except ProtocolError:
        self.gateway.now_ms += 10
    raise ProtocolError('bounded retries exhausted')


def setup(library):
  operator_key = ECC.generate(curve='P-256')
  raw = bytes(range(256)) * 4
  m = ImageManifest(b'd'*12, 1, b'l'*32, len(raw), hashlib.sha256(raw).digest(), b'b'*32, 2)
  signed = sign_image(operator_key, m)
  public = operator_key.public_key().export_key(format='DER')
  gateway = Gateway(library, public, m, Phase.ENTER)
  relay = Relay(gateway)
  challenge = Challenge.unpack(relay.call(0x40, signed.pack()))
  token = authorize_image(operator_key, signed, challenge, 60000)
  relay.call(0x41, token.pack())
  # Simulated reset into a NEW trusted-loader instance with a fresh challenge.
  loader = Gateway(library, public, m, Phase.PROGRAM)
  return operator_key, raw, signed, token, loader, Relay(loader)


@pytest.mark.parametrize('fault', [None, 'lost_ack', 'corrupt', 'missing', 'reorder', 'duplicate', 'delay', 'burst_loss', 'jitter'])
def test_complete_update_through_native_guard(library, fault):
  owner, raw, image, _, gateway, relay = setup(library)
  challenge = Challenge.unpack(relay.call(0x40, image.pack(), fault))
  token = authorize_image(owner, image, challenge, 60000)
  relay.call(0x41, token.pack(), fault)
  assert gateway.slot.erases == 8
  for offset in range(0, len(raw), 256):
    ack = relay.call(0x42, struct.pack('!I', offset) + raw[offset:offset+256], fault)
    assert struct.unpack('!I', ack)[0] == offset + 256
  relay.call(0x43, b'', 'lost_ack')
  assert gateway.slot.writes == 4
  assert gateway.slot.trial == image.manifest
  assert gateway.slot.read(0, len(raw)) == raw


def test_compromised_relay_cannot_start_install(library):
  _, raw, image, app_token, gateway, relay = setup(library)
  relay.call(0x40, image.pack())
  for opcode, body in ((0x41, app_token.pack()), (0x42, bytes(4) + raw[:256]), (0x43, b'')):
    with pytest.raises(AuthorityError):
      relay.call(opcode, body)
  assert gateway.slot.erases == gateway.slot.writes == 0 and gateway.slot.trial is None


class InterruptibleSlot(Slot):
  """Shared backing memory with erase/program boundaries; not STM32 timing."""
  def __init__(self, cut_after):
    super().__init__()
    self.backing = bytearray(b'c' * self.capacity + b'\xff' * self.capacity)
    self.data = memoryview(self.backing)[self.capacity:]
    self.cut_after, self.operations = cut_after, 0

  def mutate(self, offset, body):
    if self.operations == self.cut_after:
      raise AuthorityError('simulated power interruption')
    if offset < 0 or offset + len(body) > self.capacity:
      raise AuthorityError('flash partition bounds')
    self.data[offset:offset + len(body)] = body
    self.operations += 1

  def erase(self, offset, size):
    self.erases += 1
    self.mutate(offset, b'\xff' * size)

  def write(self, offset, body):
    self.writes += 1
    for i in range(0, len(body), 16):
      chunk = body[i:i+16]
      old = self.read(offset+i, len(chunk))
      if any((a & b) != b for a, b in zip(old, chunk, strict=True)):
        raise AuthorityError('program without erase')
      self.mutate(offset+i, chunk)

  def mark_trial(self, manifest):
    if self.operations == self.cut_after:
      raise AuthorityError('simulated interruption before trial commit')
    self.operations += 1
    self.trial = manifest


@pytest.mark.parametrize('cut_after', range(74))
def test_interruption_keeps_confirmed_image_untouched(library, cut_after):
  owner, raw, image, _, gateway, relay = setup(library)
  slot = InterruptibleSlot(cut_after)
  gateway.slot = slot
  gateway.engine = NativeUpdateEngine(gateway.authority, slot, gateway.environment)
  confirmed_hash = hashlib.sha256(slot.backing[:slot.capacity]).digest()
  challenge = Challenge.unpack(relay.call(0x40, image.pack()))
  def transfer():
    relay.call(0x41, authorize_image(owner, image, challenge, 60000).pack())
    for offset in range(0, len(raw), 256):
      relay.call(0x42, struct.pack('!I', offset) + raw[offset:offset+256])
    relay.call(0x43, b'')
  if cut_after < 73:
    with pytest.raises(AuthorityError):
      transfer()
    assert slot.trial is None
    with pytest.raises(AuthorityError):
      relay.call(0x43, b'')
  else:
    transfer()
    assert slot.trial == image.manifest
  assert hashlib.sha256(slot.backing[:slot.capacity]).digest() == confirmed_hash
  # Tests the bounded slot adapter/engine, NOT actual flash geometry or boot recovery.


def test_relay_and_verifier_sources_have_no_private_key_loader():
  source = Path(__file__).with_name('native_harness.py').read_text()
  assert 'load_key' not in source and 'getpass' not in source
