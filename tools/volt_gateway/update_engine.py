"""Off-device flash transaction reference with independent operator authority.

Uses an injected bounded inactive-slot interface, not STM32 hardware. It does
not implement MCUboot trailers, power-loss journaling or claim hardware safety.
"""

import hashlib
from dataclasses import dataclass

from openpilot.tools.volt_gateway.adaptive import Conditions
from openpilot.tools.volt_gateway.authority import AuthorityError, Phase, UpdateAuthority


@dataclass(frozen=True)
class Environment:
  now_ms: int
  sampled_ms: int
  conditions: Conditions


class UpdateEngine:
  MAX_SAMPLE_AGE_MS = 30

  def __init__(self, authority: UpdateAuthority, inactive_slot, environment):
    if authority.phase != Phase.PROGRAM:
      raise AuthorityError('loader authority required')
    if not callable(environment):
      raise AuthorityError('live environment callback required')
    self.authority, self.slot, self.environment = authority, inactive_slot, environment
    self.state = 'idle'
    self.offset = 0
    self._last = None
    self._digest = hashlib.sha256()

  def _check(self):
    try:
      sample = self.environment()
      if (not isinstance(sample, Environment) or type(sample.now_ms) is not int or type(sample.sampled_ms) is not int
          or not 0 <= sample.sampled_ms <= sample.now_ms < 2**63
          or sample.now_ms - sample.sampled_ms > self.MAX_SAMPLE_AGE_MS
          or not isinstance(sample.conditions, Conditions)
          or any(type(value) is not bool for value in vars(sample.conditions).values()) or not sample.conditions.allowed):
        raise AuthorityError('power/awake/stationary/transport sample invalid or stale')
      image = self.authority.require(Phase.PROGRAM, sample.now_ms)
    except Exception:
      self.abort()
      raise
    return image.manifest

  def begin(self):
    if self.state != 'idle':
      raise AuthorityError('update already begun')
    m = self._check()
    if m.size > self.slot.capacity:
      self.abort()
      raise AuthorityError('inactive slot too small')
    # Trusted slot adapter supplies erase geometry, never the wire request.
    regions = tuple(self.slot.erase_regions)
    end = 0
    for offset, size in regions:
      if type(offset) is not int or type(size) is not int or offset != end or size <= 0 or size > self.slot.capacity - end:
        self.abort()
        raise AuthorityError('invalid erase geometry')
      end += size
    if end != self.slot.capacity:
      self.abort()
      raise AuthorityError('incomplete erase geometry')
    self.state = 'erasing'
    try:
      for offset, size in regions:
        self._check()
        self.slot.erase(offset, size)
        self._check()
    except Exception:
      self.abort()
      raise
    self.state = 'receiving'

  def chunk(self, offset: int, data: bytes) -> int:
    m = self._check()
    if self.state != 'receiving' or type(offset) is not int or not isinstance(data, bytes) or not 1 <= len(data) <= 256:
      raise AuthorityError('invalid chunk/state')
    if self._last == (offset, data):
      return self.offset
    if offset != self.offset or offset + len(data) > m.size:
      raise AuthorityError('chunk order/bounds')
    try:
      self.slot.write(offset, data)
      self._check()
    except Exception:
      self.abort()
      raise
    self._digest.update(data)
    self._last = offset, data
    self.offset += len(data)
    return self.offset

  def finish(self):
    m = self._check()
    if self.state != 'receiving' or self.offset != m.size or self._digest.digest() != m.digest:
      self.abort()
      raise AuthorityError('incomplete/wrong image hash')
    # Independent readback hash catches storage corruption, not only wire errors.
    try:
      h = hashlib.sha256()
      for offset in range(0, m.size, 256):
        self._check()
        size = min(256, m.size - offset)
        data = self.slot.read(offset, size)
        self._check()
        if len(data) != size:
          raise AuthorityError('short flash read')
        h.update(data)
      if h.digest() != m.digest:
        raise AuthorityError('flash readback hash mismatch')
      self._check()
      # Atomic journal operation in the eventual target adapter. If conditions
      # fail during it, a trial record MAY exist: abort is not an undo operation.
      self.slot.mark_trial(m)
      self._check()
    except Exception:
      self.abort()
      raise
    self.state = 'trial'
    self.authority.close()

  def abort(self):
    self.state = 'aborted'
    self._last = None
    self.authority.close()
