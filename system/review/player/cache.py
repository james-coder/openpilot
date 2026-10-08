"""Bounded cache of decoded RGB frames for the Dashcam player.

One writer (the decode worker) calls put/clear; any thread may call get/has. Readers only do single dict lookups (atomic under the GIL),
so the render thread never waits for the worker and the worker never waits for the render thread. Memory is capped by bytes, and a
frame is evicted by its distance from the playhead (frames behind it count more, so the cache leans forward).
"""
import numpy as np

DEFAULT_MAX_BYTES = 60 << 20
BEHIND_WEIGHT = 2.5


class FrameCache:
  def __init__(self, max_bytes: int = DEFAULT_MAX_BYTES):
    self.max_bytes = int(max_bytes)
    self._frames: dict[int, np.ndarray] = {}
    self.nbytes = 0

  def __len__(self) -> int:
    return len(self._frames)

  def get(self, idx: int) -> np.ndarray | None:
    return self._frames.get(idx)

  def has(self, idx: int) -> bool:
    return idx in self._frames

  @staticmethod
  def _cost(idx: int, anchor: int) -> float:
    d = idx - anchor
    return float(d) if d >= 0 else -d * BEHIND_WEIGHT

  def put(self, idx: int, frame: np.ndarray, anchor: int) -> bool:
    """Store frame idx. False if it was not kept: bigger than the whole cache, or farther from the playhead than everything already
    held (so a full cache never trades a frame the viewer needs soon for one needed later)."""
    if idx in self._frames:
      return True  # a decoded frame never changes
    size = int(frame.nbytes)
    if size > self.max_bytes:
      return False
    while self.nbytes + size > self.max_bytes and self._frames:
      worst = max(self._frames, key=lambda k: self._cost(k, anchor))
      if self._cost(worst, anchor) <= self._cost(idx, anchor):
        return False
      self.nbytes -= int(self._frames.pop(worst).nbytes)
    self._frames[idx] = frame
    self.nbytes += size
    return True

  def clear(self) -> None:
    self._frames = {}
    self.nbytes = 0
