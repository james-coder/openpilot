import threading

import numpy as np

from openpilot.system.review.player.cache import DEFAULT_MAX_BYTES, FrameCache


def frame(n=1000):
  return np.zeros(n, dtype=np.uint8)


def test_memory_never_exceeds_the_cap_whatever_the_access_pattern():
  c = FrameCache(max_bytes=10_000)
  rng = np.random.default_rng(1)
  for _ in range(500):
    c.put(int(rng.integers(0, 400)), frame(), anchor=int(rng.integers(0, 400)))
    assert c.nbytes <= 10_000
    assert c.nbytes == sum(f.nbytes for f in c._frames.values())
  assert len(c) <= 10


def test_a_real_sized_cache_holds_at_most_sixty_megabytes_of_qcamera_frames():
  c = FrameCache()
  rgb = np.zeros((330, 526, 3), dtype=np.uint8)
  for i in range(400):
    c.put(i, rgb.copy(), anchor=i)
  assert DEFAULT_MAX_BYTES <= 60 << 20
  assert c.nbytes <= DEFAULT_MAX_BYTES and 100 <= len(c) <= 130


def test_frames_far_ahead_do_not_displace_frames_the_viewer_needs_now():
  c = FrameCache(max_bytes=5_000)
  for i in range(5):
    assert c.put(i, frame(), anchor=0)
  assert not c.put(50, frame(), anchor=0)       # farther than everything held: refused
  assert all(c.has(i) for i in range(5))
  # moving the playhead forward evicts the frame farthest from it, and behind counts more than ahead
  c2 = FrameCache(max_bytes=5_000)
  for i in (0, 1, 2, 3, 4):
    c2.put(i, frame(), anchor=2)
  assert c2.put(7, frame(), anchor=4)
  assert not c2.has(0) and c2.has(7)


def test_oversize_frame_is_refused_and_duplicates_are_free():
  c = FrameCache(max_bytes=1_000)
  assert not c.put(0, frame(2_000), anchor=0) and len(c) == 0
  f = frame(500)
  assert c.put(1, f, anchor=1) and c.put(1, frame(500), anchor=1)
  assert c.get(1) is f and c.nbytes == 500


def test_clear_and_get_missing():
  c = FrameCache()
  c.put(3, frame(), anchor=3)
  assert c.get(9) is None and not c.has(9)
  c.clear()
  assert len(c) == 0 and c.nbytes == 0 and c.get(3) is None


def test_reader_thread_never_blocks_or_errors_while_the_writer_churns():
  c = FrameCache(max_bytes=20_000)
  stop = threading.Event()
  errors = []

  def reader():
    try:
      while not stop.is_set():
        for i in range(0, 300, 7):
          c.get(i)
          c.has(i)
          len(c)
    except Exception as e:   # pragma: no cover - the assertion below reports it
      errors.append(e)

  t = threading.Thread(target=reader)
  t.start()
  try:
    for step in range(3000):
      c.put(step % 300, frame(), anchor=(step * 3) % 300)
  finally:
    stop.set()
    t.join(5)
  assert not errors and c.nbytes <= 20_000
