"""Generated malformed-input tests; not coverage-guided native fuzzing."""

from hypothesis import given, settings, strategies as st

from openpilot.tools.volt_gateway.protocol import IsoTpReceiver, ProtocolError, isotp_encode


@settings(max_examples=250, deadline=None, derandomize=True)
@given(st.binary(min_size=1, max_size=512), st.lists(st.integers(1, 20), min_size=1, max_size=12))
def test_generated_payloads_with_jitter_roundtrip(payload, intervals):
  receiver = IsoTpReceiver()
  now = 0.
  result = None
  for i, frame in enumerate(isotp_encode(payload)):
    now += intervals[i % len(intervals)] / 1000
    result = receiver.feed(frame, now)
  assert result == payload


@settings(max_examples=500, deadline=None, derandomize=True)
@given(st.lists(st.binary(min_size=0, max_size=12), max_size=100))
def test_generated_malformed_fragments_keep_memory_bounded(frames):
  receiver = IsoTpReceiver()
  for index, frame in enumerate(frames):
    try:
      result = receiver.feed(frame, index / 100)
      assert result is None or 1 <= len(result) <= 512
    except ProtocolError:
      pass
    assert len(receiver.buffer) <= 512


@settings(max_examples=150, deadline=None, derandomize=True)
@given(st.binary(min_size=8, max_size=512), st.integers(min_value=1, max_value=72))
def test_missing_fragment_never_delivers_complete_payload(payload, selected):
  frames = isotp_encode(payload)
  removed = selected % len(frames)
  del frames[removed]
  receiver, delivered = IsoTpReceiver(), []
  for index, frame in enumerate(frames):
    try:
      result = receiver.feed(frame, index / 1000)
      if result is not None:
        delivered.append(result)
    except ProtocolError:
      pass
  assert not delivered
