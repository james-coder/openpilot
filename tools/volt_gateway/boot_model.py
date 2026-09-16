"""Persistent two-slot lifecycle model for integration tests, NOT a bootloader.

Uses real signed manifests/hash checks and a deliberately simple two-page
journal. This is NOT MCUboot metadata or an approved STM32 flash allocation.
It cannot validate executable vectors, hardware flash stalls or physical power.
"""

import hashlib
import struct
import zlib

from openpilot.tools.volt_gateway.authority import AuthorityError, SignedImage, public_key


RECORD = struct.Struct('!4sIBBB')
COMMIT = b'COMMIT!!'


class PowerCut(Exception):
  pass


class Storage:
  CAPACITY = 2048

  def __init__(self):
    self.slots = [bytearray(b'\xff' * self.CAPACITY) for _ in range(2)]
    self.headers = [None, None]
    self.journal = [bytearray(b'\xff' * 64) for _ in range(2)]
    self.cut_after = None
    self.mutations = 0

  def mutate(self, operation):
    if self.cut_after == self.mutations:
      raise PowerCut('simulated interruption before persistent mutation')
    operation()
    self.mutations += 1

  def record(self):
    records = []
    for index, page in enumerate(self.journal):
      body = bytes(page[:RECORD.size])
      if bytes(page[RECORD.size+4:RECORD.size+12]) != COMMIT:
        continue
      if zlib.crc32(body) != struct.unpack('!I', page[RECORD.size:RECORD.size+4])[0]:
        continue
      magic, generation, confirmed, trial, attempted = RECORD.unpack(body)
      if magic != b'GWJR' or confirmed not in (0, 1) or trial not in (0, 1, 255) or attempted not in (0, 1):
        continue
      if trial == confirmed or trial == 255 and attempted:
        continue
      records.append((generation, index, confirmed, trial, attempted))
    if not records:
      return None
    return max(records)

  def persist(self, confirmed, trial=255, attempted=0):
    previous = self.record()
    generation = previous[0] + 1 if previous else 1
    if generation > 0xffffffff:
      raise AuthorityError('journal exhausted; no generation wrap')
    index = 1 - previous[1] if previous else 0
    body = RECORD.pack(b'GWJR', generation, confirmed, trial, attempted)
    encoded = body + struct.pack('!I', zlib.crc32(body))
    page = self.journal[index]
    self.mutate(lambda: page.__setitem__(slice(None), b'\xff' * len(page)))
    for offset in range(0, len(encoded), 4):
      part = encoded[offset:offset+4]
      self.mutate(lambda offset=offset, part=part: page.__setitem__(slice(offset, offset+len(part)), part))
    # Marker written last; incomplete replacement leaves the old record usable.
    self.mutate(lambda: page.__setitem__(slice(len(encoded), len(encoded)+len(COMMIT)), COMMIT))


class BootModel:
  def __init__(self, storage, verification_key, device, layout):
    self.storage, self.key = storage, public_key(verification_key)
    self.device, self.layout = device, layout
    self.running = None

  def valid(self, index):
    try:
      image = SignedImage.unpack(self.storage.headers[index])
      image.verify(self.key)
      m = image.manifest
      return (m.device == self.device and m.layout == self.layout and m.size <= self.storage.CAPACITY
              and hashlib.sha256(self.storage.slots[index][:m.size]).digest() == m.digest)
    except (AuthorityError, ValueError, TypeError):
      return False

  def boot(self):
    self.running = None
    record = self.storage.record()
    if record is None:
      return 'recovery'
    _, _, confirmed, trial, attempted = record
    if trial != 255:
      if not attempted and self.valid(trial):
        # Must persist attempt BEFORE handing control to the trial application.
        self.storage.persist(confirmed, trial, 1)
        self.running = trial
        return trial
      self.storage.persist(confirmed)
    if self.valid(confirmed):
      self.running = confirmed
      return confirmed
    return 'recovery'

  def confirm(self):
    record = self.storage.record()
    if record is None or self.running is None or not self.valid(self.running):
      raise AuthorityError('no valid running image')
    _, _, confirmed, trial, attempted = record
    if self.running == trial and attempted:
      self.storage.persist(trial)
    elif self.running != confirmed:
      raise AuthorityError('not the running trial')


class InactiveSlot:
  capacity = Storage.CAPACITY
  erase_regions = tuple((i, 256) for i in range(0, Storage.CAPACITY, 256))

  def __init__(self, storage, signed):
    record = storage.record()
    if record is None or record[3] != 255:
      raise AuthorityError('confirmed baseline required, no concurrent trial')
    self.storage, self.signed = storage, signed
    self.confirmed, self.index = record[2], 1 - record[2]

  def bounds(self, offset, size):
    if not 0 <= offset <= self.capacity or not 0 < size <= self.capacity - offset:
      raise AuthorityError('slot bounds')

  def erase(self, offset, size):
    self.bounds(offset, size)
    self.storage.mutate(lambda: self.storage.slots[self.index].__setitem__(slice(offset, offset+size), b'\xff' * size))

  def write(self, offset, data):
    self.bounds(offset, len(data))
    for step in range(0, len(data), 16):
      chunk = data[step:step+16]
      start = offset + step
      old = self.read(start, len(chunk))
      if any((a & b) != b for a, b in zip(old, chunk, strict=True)):
        raise AuthorityError('program without erase')
      self.storage.mutate(lambda start=start, chunk=chunk:
                          self.storage.slots[self.index].__setitem__(slice(start, start+len(chunk)), chunk))

  def read(self, offset, size):
    self.bounds(offset, size)
    return bytes(self.storage.slots[self.index][offset:offset+size])

  def mark_trial(self, manifest):
    if manifest != self.signed.manifest:
      raise AuthorityError('trial manifest mismatch')
    # Header storage is atomic in this model; separate corruption tests cover
    # torn header bytes. Do not claim real header-write power-loss coverage.
    self.storage.mutate(lambda: self.storage.headers.__setitem__(self.index, self.signed.pack()))
    self.storage.persist(self.confirmed, self.index)
