"""Signed MCUboot inner images for the conditional F413 A/B layout.

No device I/O or provisioning. Payload must already be linked for its slot.
This does not certify a payload as board firmware. No trial trailer is shipped:
the trusted updater must write its commit marker only after validation.
"""
import hashlib
import struct

from Crypto.Hash import SHA256
from Crypto.Signature import DSS

from openpilot.tools.volt_gateway.authority import AuthorityError, ImageManifest, SignedImage, public_key
from openpilot.tools.volt_gateway.operator import sign_image
from openpilot.tools.volt_gateway.release import package

FLASH_SIZE = 0x100000
SLOTS = (0x40000, 0xa0000)
SLOT_SIZE = 0x60000
HEADER_SIZE = 512
HEADER = struct.Struct('<IIHHIIBBHII')
MAGIC = bytes.fromhex('77c295f360d2ef7f3552500f2cb67980')


def target_fields(device, layout, slot, version):
  if (not isinstance(device, bytes) or len(device) != 12 or not isinstance(layout, bytes) or len(layout) != 32 or
      type(slot) is not int or slot not in (0, 1) or type(version) is not int or not 0 <= version < 2**32):
    raise AuthorityError('invalid image target/version')


def vectors(payload, slot):
  if not isinstance(payload, bytes) or not 8 <= len(payload) <= SLOT_SIZE - 1024:
    raise AuthorityError('payload bounds')
  sp, reset = struct.unpack_from('<II', payload)
  start = 0x08000000 + SLOTS[slot] + HEADER_SIZE
  if sp & 7 or not 0x20000000 < sp <= 0x20020000 or not reset & 1 or not start + 8 <= (reset & ~1) < start + len(payload):
    raise AuthorityError('payload vectors not linked for selected slot/conservative RAM envelope')


def sign(key, payload: bytes, device: bytes, layout: bytes, slot: int, version: int) -> bytes:
  target_fields(device, layout, slot, version)
  vectors(payload, slot)
  if not key.has_private() or key.curve != 'NIST P-256':
    raise AuthorityError('local P-256 signing key required')
  header = HEADER.pack(0x96f3b83d, SLOTS[slot], HEADER_SIZE, 52, len(payload), 0x100, 0, 0, 0, version, 0)
  protected = struct.pack('<HHHH', 0x6908, 52, 0xa0, 44) + device + layout
  signed = header.ljust(HEADER_SIZE, b'\x00') + payload + protected
  digest = SHA256.new(signed)
  signature = DSS.new(key, 'deterministic-rfc6979', encoding='der').sign(digest)
  key_hash = hashlib.sha256(key.public_key().export_key(format='DER')).digest()
  records = b''.join(struct.pack('<HH', kind, len(value)) + value for kind, value in (
    (0x10, digest.digest()), (0x01, key_hash), (0x22, signature)))
  image = signed + struct.pack('<HH', 0x6907, 4 + len(records)) + records
  return image.ljust((len(image) + 3) & ~3, b'\xff')


def verify(image: bytes, public: bytes, device: bytes, layout: bytes, slot: int, version: int):
  target_fields(device, layout, slot, version)
  key = public_key(public)
  if not isinstance(image, bytes) or not HEADER_SIZE + 8 + 52 + 4 <= len(image) <= SLOT_SIZE - 64 or len(image) % 4:
    raise AuthorityError('inner image bounds/alignment')
  header = HEADER.unpack_from(image)
  payload_size = header[4]
  expected = (0x96f3b83d, SLOTS[slot], HEADER_SIZE, 52, payload_size, 0x100, 0, 0, 0, version, 0)
  if header != expected or image[HEADER.size:HEADER_SIZE] != bytes(HEADER_SIZE - HEADER.size):
    raise AuthorityError('inner header policy')
  if payload_size > len(image) - HEADER_SIZE - 56:
    raise AuthorityError('inner payload overrun')
  end = HEADER_SIZE + payload_size
  vectors(image[HEADER_SIZE:end], slot)
  if image[end:end+52] != struct.pack('<HHHH', 0x6908, 52, 0xa0, 44) + device + layout:
    raise AuthorityError('inner target binding')
  end += 52
  digest = SHA256.new(image[:end])
  magic, total = struct.unpack_from('<HH', image, end)
  limit = end + total
  if magic != 0x6907 or total < 4 or not limit <= len(image) < limit + 4 or image[limit:] != b'\xff' * (len(image) - limit):
    raise AuthorityError('inner TLV bounds')
  pos = end + 4
  records = []
  for kind in (0x10, 0x01, 0x22):
    if pos + 4 > limit:
      raise AuthorityError('inner TLV truncated')
    actual, size = struct.unpack_from('<HH', image, pos)
    pos += 4
    if actual != kind or size > limit - pos:
      raise AuthorityError('inner TLV type/length')
    records.append(image[pos:pos+size])
    pos += size
  if (pos != limit or records[0] != digest.digest() or records[1] != hashlib.sha256(key.export_key(format='DER')).digest() or
      not 8 <= len(records[2]) <= 72):
    raise AuthorityError('inner hash/key/extra TLV')
  try:
    DSS.new(key, 'fips-186-3', encoding='der').verify(digest, records[2])
  except ValueError as e:
    raise AuthorityError('inner signature invalid') from e


def release(key, payload: bytes, device: bytes, layout: bytes, slot: int, version: int, build: bytes) -> bytes:
  """Public package with independent inner and outer signatures, no secrets."""
  image = sign(key, payload, device, layout, slot, version)
  manifest = ImageManifest(device, 1, layout, len(image), hashlib.sha256(image).digest(), build, version)
  signed = sign_image(key, manifest).pack()
  return verified_package(image, signed, key.public_key().export_key(format='DER'), slot)


def verified_package(image, signed, public, slot):
  manifest = SignedImage.unpack(signed)
  manifest.verify(public_key(public))
  m = manifest.manifest
  verify(image, public, m.device, m.layout, slot, m.version)
  return package(image, signed, public)
