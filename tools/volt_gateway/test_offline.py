import hashlib
import json
import math
from pathlib import Path
import random
import struct

import pytest

from openpilot.tools.volt_gateway.benchmark import batch, contention, update_model
from openpilot.tools.volt_gateway.protocol import (MAX_PDU, AuthReceiver, Compatibility, IsoTpReceiver, Observation, ProtocolError,
                                        StreamReceiver, Subscription, compact_encode, flow_frames, hkdf, isotp_encode,
                                        negotiate, seal, session_key, stream_encode)
from openpilot.tools.volt_gateway.provenance import signing_input
from openpilot.tools.volt_gateway.simulation import Scheduler, TokenBucket


def receive(receiver, frames):
  result = None
  for i, frame in enumerate(frames):
    result = receiver.feed(frame, i * .001)
  return result


@pytest.mark.parametrize('length', [1, 7, 8, 13, 14, 111, 112, 255, 256, 511, 512])
def test_isotp_roundtrip(length):
  payload = bytes(i & 255 for i in range(length))
  assert receive(IsoTpReceiver(), isotp_encode(payload)) == payload


@pytest.mark.parametrize('length', [0, 513, 4096])
def test_isotp_bounds(length):
  with pytest.raises(ProtocolError):
    isotp_encode(bytes(length))


@pytest.mark.parametrize('bad', [b'', bytes(7), bytes(9), bytes(8), b'\x08' + bytes(7),
                                b'\x10\x00' + bytes(6), b'\x12\x01' + bytes(6), b'\x30' + bytes(7)])
def test_isotp_malformed(bad):
  r = IsoTpReceiver()
  with pytest.raises(ProtocolError):
    r.feed(bad, 0)
  assert not r.buffer and r.length == 0


def test_duplicate_reorder_timeout_padding():
  frames = isotp_encode(bytes(50))
  for bad in (frames[2], b'\x20' + bytes(7)):
    r = IsoTpReceiver()
    r.feed(frames[0], 0)
    with pytest.raises(ProtocolError):
      r.feed(bad, .01)
  r = IsoTpReceiver()
  r.feed(frames[0], 0)
  r.feed(frames[1], .01)
  with pytest.raises(ProtocolError):
    r.feed(frames[1], .02)
  r.feed(frames[0], 0)
  with pytest.raises(ProtocolError):
    r.feed(frames[1], 1.1)
  with pytest.raises(ProtocolError):
    receive(IsoTpReceiver(), [b'\x01x' + bytes(5) + b'x'])


@pytest.mark.parametrize('extended,address', [(False, 0), (False, 0x7ff), (True, 1), (True, 0x1fffffff)])
@pytest.mark.parametrize('dlc', range(9))
@pytest.mark.parametrize('rtr', [False, True])
def test_stream_roundtrip(extended, address, dlc, rtr):
  observation = Observation(3, address, extended, rtr, dlc, b'' if rtr else bytes(range(dlc)), 2**40 + 42, 0xffffffff)
  subscription = Subscription(42, 3, address, extended, 2**40)
  assert receive(StreamReceiver(), stream_encode(observation)) == observation
  assert receive(StreamReceiver((subscription,)), compact_encode(observation, subscription)) == observation
  assert Observation.unpack(observation.pack()) == observation


def test_stream_corruption_missing_unknown_mapping_and_anchor():
  observation = Observation(3, 0x123, False, False, 8, b'abcdefgh', 2000, 1)
  subscription = Subscription(1, 3, 0x123, False, 1000)
  frames = compact_encode(observation, subscription)
  with pytest.raises(ProtocolError):
    receive(StreamReceiver(), frames)
  for index in range(len(frames)):
    altered = frames.copy()
    altered[index] = altered[index][:3] + bytes([altered[index][3] ^ 1]) + altered[index][4:]
    with pytest.raises(ProtocolError):
      receive(StreamReceiver((subscription,)), altered)
  r = StreamReceiver((subscription,))
  r.feed(frames[0], 0)
  with pytest.raises(ProtocolError):
    r.feed(frames[2], .01)
  assert receive(r, frames) == observation
  with pytest.raises(ProtocolError):
    compact_encode(observation, Subscription(1, 3, 0x123, False, 3000))
  with pytest.raises(ProtocolError):
    StreamReceiver((subscription,) * 33)


def test_bounded_random_input():
  randomizer = random.Random(12345)
  for receiver in (IsoTpReceiver(), StreamReceiver()):
    for i in range(10000):
      try:
        receiver.feed(randomizer.randbytes(8), i * .001)
      except ProtocolError:
        pass
      assert len(receiver.buffer) <= MAX_PDU


@pytest.mark.parametrize('invalid_time', [math.nan, math.inf, -1.])
def test_bad_clock_fails_closed(invalid_time):
  for receiver in (IsoTpReceiver(), StreamReceiver()):
    with pytest.raises(ProtocolError):
      receiver.feed(bytes(8), invalid_time)
  with pytest.raises(ProtocolError):
    AuthReceiver(bytes(32), bytes(8), 10).accept(seal(bytes(32), bytes(8), 0, 0, 1, b''), invalid_time)


def test_hkdf_rfc5869_case1_first_block():
  assert hkdf(bytes.fromhex('0b' * 22), bytes.fromhex('000102030405060708090a0b0c'),
              bytes.fromhex('f0f1f2f3f4f5f6f7f8f9')).hex() == '3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf'


def test_key_derivation_context_separation():
  args = (bytes(32), b'h' * 32, b'g' * 32)
  key = session_key(*args, b'transcript', b'host')
  assert key != session_key(*args, b'transcript', b'gateway')
  assert key != session_key(*args, b'changed negotiation', b'host')
  assert key != session_key(bytes(32), b'i' * 32, b'g' * 32, b'transcript', b'host')


def test_auth_replay_duplicate_conflict_and_expiry():
  key, sid = b'k' * 32, b's' * 8
  r = AuthReceiver(key, sid, 10)
  first = seal(key, sid, 0, 123, 1, b'payload')
  assert r.accept(first, 0) == (1, 123, b'payload', False)
  assert r.accept(first, 1)[-1] is True
  with pytest.raises(ProtocolError):
    r.accept(seal(key, sid, 0, 123, 1, b'changed'), 1)
  with pytest.raises(ProtocolError):
    r.accept(seal(key, sid, 2, 123, 1, b'payload'), 1)
  r.accept(seal(key, sid, 1, 124, 1, b'next'), 1)
  with pytest.raises(ProtocolError):
    r.accept(first, 1)
  with pytest.raises(ProtocolError):
    r.accept(seal(key, sid, 2, 125, 1, b'next'), 10)


def test_invalid_mac_session_version_no_state_change_no_secret_errors():
  key, sid = b'k' * 32, b's' * 8
  canary = b'SECRET_CANARY_DO_NOT_LOG'
  pdu = seal(key, sid, 0, 1, 1, canary)
  r = AuthReceiver(key, sid, 10)
  candidates = [pdu[:-1] + bytes([pdu[-1] ^ 1]), seal(key, b'x' * 8, 0, 1, 1, canary),
                seal(key, sid, 0, 1, 1, canary, minor=1), pdu[:-1], bytes(513)]
  for candidate in candidates:
    with pytest.raises(ProtocolError) as error:
      r.accept(candidate, 0)
    assert canary.decode() not in str(error.value)
    assert r.next_sequence == 0 and r.last is None


def test_negotiation_is_fail_closed():
  good = Compatibility(1, 0, frozenset({'observe'}), 'BENCH-ONLY', 'test-build')
  assert negotiate(good, good, frozenset({'observe'}), 'BENCH-ONLY') == 0
  for bad in (Compatibility(2, 0, good.capabilities, good.safety_policy, good.build_id),
              Compatibility(1, 0, frozenset(), good.safety_policy, good.build_id),
              Compatibility(1, 0, good.capabilities, 'unknown', good.build_id)):
    with pytest.raises(ProtocolError):
      negotiate(good, bad, frozenset({'observe'}), 'BENCH-ONLY')


@pytest.mark.parametrize('invalid_time', [10., float('nan'), float('inf'), 0.])
def test_closed_session_cannot_be_revived_by_clock_rollback(invalid_time):
  key, sid = bytes(32), bytes(8)
  receiver = AuthReceiver(key, sid, 10)
  pdu = seal(key, sid, 0, 0, 1, b'')
  receiver.accept(pdu, 1)
  with pytest.raises(ProtocolError):
    receiver.accept(pdu, invalid_time)
  with pytest.raises(ProtocolError):
    receiver.accept(pdu, 2)


def test_rate_queue_expiry_priority_and_disable():
  bucket = TokenBucket(20)
  assert bucket.take(0) and bucket.take(0) and not bucket.take(0)
  assert bucket.take(.05) and not bucket.take(.05)
  s = Scheduler(queue_limit=2)
  for _ in range(10):
    s.offer([b'd' * 8], 0, control=False)
  assert len(s.data) == 2 and s.counters['telemetry_dropped'] == 8
  assert s.offer([b'c' * 8], 0, control=True)
  assert s.step(0) == (b'c' * 8, True)
  assert s.step(.3) is None
  assert s.counters['expired'] == 2
  s.offer([b'c' * 8], .3, control=True)
  s.disable()
  assert s.step(.4) is None and not s.offer([bytes(8)], .4, control=True)


def test_no_backlog_dump_after_contention():
  s = Scheduler()
  for i in range(1000):
    s.offer([bytes(8)], i / 1000, control=False)
    assert s.step(i / 1000, busy=True) is None
  sent = sum(s.step(2) is not None for _ in range(100))
  assert sent == 0


def test_wire_models_and_contention_report():
  record = Observation(3, 1, False, False, 8, bytes(8), 1, 1)
  for n, expected in [(1, 8), (2, 13), (4, 22)]:
    frames = len(isotp_encode(batch([record] * n)))
    assert frames + flow_frames(frames) == expected
  result = update_model(20, 40, 262144)
  assert result['request_pdu_bytes'] == 308 and result['ack_pdu_bytes'] == 52
  assert result['paced_transfer_minutes'] > 44
  iso, compact = contention('isotp', 10, .8), contention('compact-stream', 10, .8)
  assert compact['control_first_frame_delay_ms_max'] < iso['control_first_frame_delay_ms_max']
  assert compact['control_responses_started'] == 10


def test_historical_digest_input():
  assert signing_input(b'12345678') == b'5678VERS\x02\0\0\0'


def test_archived_inventory_consistent():
  path = Path(__file__).resolve().parents[2] / 'docs/evidence/volt-gateway/inventory-20260916.json'
  inventory = json.loads(path.read_text())
  signature = bytes.fromhex(inventory['application_signature_hex'])
  assert len(signature) == 128
  assert hashlib.sha256(signature).hexdigest() == inventory['application_signature_sha256']
  health = struct.unpack('<8I9B', bytes.fromhex(inventory['raw_control_in']['d2']))
  assert health[0:3] == (306, 4776, 4092) and health[-3:] == (0, 0, 1)
  config = bytes.fromhex(inventory['usb_configuration_descriptor_hex'])
  assert len(config) == int.from_bytes(config[2:4], 'little')
