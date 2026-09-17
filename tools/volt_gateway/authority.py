"""Verifier-only update authority reference. No private key, signer, file or device I/O.

These fixed-size records are independent of the routine HMAC credential. They
are NOT an MCUboot image format or a deployed bootloader. An MCU port must use
the same records and independently validate them before any flash operation.
"""

from dataclasses import dataclass
from enum import IntEnum
import hashlib
import secrets
import struct

from Crypto.Hash import SHA256
from Crypto.PublicKey import ECC
from Crypto.Signature import DSS


IMAGE_DOMAIN = b'VOLT GW FIRMWARE MANIFEST v1\0'
AUTH_DOMAIN = b'VOLT GW UPDATE AUTHORIZATION v1\0'
SIGNATURE_BYTES = 64
MAX_IMAGE = 1024 * 1024
MAX_LEASE_MS = 60 * 60 * 1000
CHALLENGE_TTL_MS = 60_000
MANIFEST = struct.Struct('!4sB12sB32sI32s32sI')
CHALLENGE = struct.Struct('!4sBB12s32s32s')
AUTH_TAIL = struct.Struct('!32sI')


class AuthorityError(ValueError):
  pass


class Phase(IntEnum):
  ENTER = 1
  PROGRAM = 2


def fixed(value: bytes, size: int):
  if not isinstance(value, bytes) or len(value) != size:
    raise AuthorityError('invalid field length')


def public_key(encoded: bytes):
  if not isinstance(encoded, bytes) or not 1 <= len(encoded) <= 512:
    raise AuthorityError('invalid verification key')
  try:
    key = ECC.import_key(encoded)
  except (ValueError, TypeError, IndexError) as e:
    raise AuthorityError('invalid verification key') from e
  if key.has_private() or key.curve != 'NIST P-256':
    raise AuthorityError('verification requires a public-only P-256 key')
  return key


def verify(key, domain: bytes, body: bytes, signature: bytes):
  fixed(signature, SIGNATURE_BYTES)
  if key.has_private() or key.curve != 'NIST P-256' or domain not in (IMAGE_DOMAIN, AUTH_DOMAIN):
    raise AuthorityError('invalid verification parameters')
  try:
    DSS.new(key, 'fips-186-3', encoding='binary').verify(SHA256.new(domain + body), signature)
  except ValueError as e:
    raise AuthorityError('signature rejected') from e


@dataclass(frozen=True)
class ImageManifest:
  device: bytes
  hardware: int
  layout: bytes
  size: int
  digest: bytes
  build: bytes
  version: int

  def pack(self) -> bytes:
    for value, size in ((self.device, 12), (self.layout, 32), (self.digest, 32), (self.build, 32)):
      fixed(value, size)
    if self.hardware != 1 or type(self.size) is not int or not 0 < self.size <= MAX_IMAGE:
      raise AuthorityError('unsupported target/size')
    if type(self.version) is not int or not 0 <= self.version < 2**32:
      raise AuthorityError('invalid version')
    return MANIFEST.pack(b'VGIM', 1, self.device, self.hardware, self.layout, self.size, self.digest, self.build, self.version)

  @classmethod
  def unpack(cls, body: bytes):
    fixed(body, MANIFEST.size)
    magic, protocol, *fields = MANIFEST.unpack(body)
    if magic != b'VGIM' or protocol != 1:
      raise AuthorityError('manifest format')
    result = cls(*fields)
    result.pack()
    return result


@dataclass(frozen=True)
class SignedImage:
  manifest: ImageManifest
  signature: bytes

  def pack(self):
    fixed(self.signature, SIGNATURE_BYTES)
    return self.manifest.pack() + self.signature

  @classmethod
  def unpack(cls, body: bytes):
    fixed(body, MANIFEST.size + SIGNATURE_BYTES)
    return cls(ImageManifest.unpack(body[:MANIFEST.size]), body[MANIFEST.size:])

  def verify(self, key):
    verify(key, IMAGE_DOMAIN, self.manifest.pack(), self.signature)


@dataclass(frozen=True)
class Challenge:
  phase: Phase
  device: bytes
  session: bytes
  nonce: bytes

  def pack(self):
    fixed(self.device, 12)
    fixed(self.session, 32)
    fixed(self.nonce, 32)
    if self.phase not in (Phase.ENTER, Phase.PROGRAM) or not any(self.session) or not any(self.nonce):
      raise AuthorityError('invalid phase/freshness')
    return CHALLENGE.pack(b'VGCH', 1, self.phase, self.device, self.session, self.nonce)

  @classmethod
  def unpack(cls, body: bytes):
    fixed(body, CHALLENGE.size)
    magic, protocol, phase, device, session, nonce = CHALLENGE.unpack(body)
    if magic != b'VGCH' or protocol != 1:
      raise AuthorityError('challenge format')
    try:
      result = cls(Phase(phase), device, session, nonce)
    except ValueError as e:
      raise AuthorityError('invalid phase') from e
    result.pack()
    return result


@dataclass(frozen=True)
class Authorization:
  challenge: Challenge
  manifest_digest: bytes
  lease_ms: int
  signature: bytes

  def body(self):
    fixed(self.manifest_digest, 32)
    if type(self.lease_ms) is not int or not 1000 <= self.lease_ms <= MAX_LEASE_MS:
      raise AuthorityError('invalid lease')
    return self.challenge.pack() + AUTH_TAIL.pack(self.manifest_digest, self.lease_ms)

  def pack(self):
    fixed(self.signature, SIGNATURE_BYTES)
    return self.body() + self.signature

  @classmethod
  def unpack(cls, raw: bytes):
    fixed(raw, CHALLENGE.size + AUTH_TAIL.size + SIGNATURE_BYTES)
    digest, lease = AUTH_TAIL.unpack(raw[CHALLENGE.size:-SIGNATURE_BYTES])
    result = cls(Challenge.unpack(raw[:CHALLENGE.size]), digest, lease, raw[-SIGNATURE_BYTES:])
    result.body()
    return result


class UpdateAuthority:
  """One challenge and one image-scoped grant per boot/phase. Invalid time closes it.

  The caller must rate-limit transport ingress and source safety conditions from
  verified hardware. No routine secret or routine-authentication boolean can
  substitute for the operator signature. Duplicate token acceptance is forbidden;
  the authenticated transaction layer caches its prior response instead.
  """
  def __init__(self, verification_key: bytes, device: bytes, layout: bytes, phase: Phase, *, random_bytes=secrets.token_bytes):
    self.key = public_key(verification_key)
    fixed(device, 12)
    fixed(layout, 32)
    if phase not in (Phase.ENTER, Phase.PROGRAM):
      raise AuthorityError('invalid phase')
    self.device, self.layout, self.phase = device, layout, Phase(phase)
    self._random = random_bytes
    self.session = random_bytes(32)
    fixed(self.session, 32)
    if not any(self.session):
      raise AuthorityError('entropy failure')
    self.pending = self.grant = None
    self.last_ms = 0
    self.closed = False
    self.last_issue_ms = -CHALLENGE_TTL_MS
    self.last_nonce = self.session
    self.last_verify_ms = -1000

  def _clock(self, now_ms):
    if self.closed or type(now_ms) is not int or not 0 <= now_ms < 2**63 or now_ms < self.last_ms:
      self.close()
      raise AuthorityError('expired/invalid authority clock')
    self.last_ms = now_ms
    if self.grant and now_ms >= self.grant[1]:
      self.close()
      raise AuthorityError('update lease expired')

  def close(self):
    self.closed = True
    self.pending = self.grant = None

  def challenge(self, image: SignedImage, now_ms: int) -> Challenge:
    self._clock(now_ms)
    if self.grant:
      raise AuthorityError('update already authorized')
    if self.pending and now_ms < self.pending[2]:
      if image.pack() != self.pending[1].pack():
        raise AuthorityError('different image while challenge pending')
      return self.pending[0]
    if now_ms - self.last_issue_ms < CHALLENGE_TTL_MS:
      raise AuthorityError('challenge rate limit')
    # Limit expensive signature checks, including unsuccessful requests.
    self.last_issue_ms = now_ms
    image.verify(self.key)
    m = image.manifest
    if m.device != self.device or m.layout != self.layout or m.hardware != 1:
      raise AuthorityError('wrong device/layout')
    nonce = self._random(32)
    fixed(nonce, 32)
    if not any(nonce) or nonce == self.last_nonce or nonce == self.session:
      self.close()
      raise AuthorityError('entropy failure')
    self.last_nonce = nonce
    c = Challenge(self.phase, self.device, self.session, nonce)
    self.pending = (c, image, now_ms + CHALLENGE_TTL_MS)
    return c

  def authorize(self, token: Authorization, now_ms: int):
    self._clock(now_ms)
    if self.grant or not self.pending or now_ms >= self.pending[2]:
      raise AuthorityError('no live challenge')
    c, image, _ = self.pending
    if token.challenge != c or token.manifest_digest != hashlib.sha256(image.manifest.pack()).digest():
      raise AuthorityError('authorization scope mismatch')
    if now_ms - self.last_verify_ms < 1000:
      raise AuthorityError('authorization verification rate limit')
    self.last_verify_ms = now_ms
    verify(self.key, AUTH_DOMAIN, token.body(), token.signature)
    self.grant = (image, now_ms + token.lease_ms)
    self.pending = None

  def require(self, phase: Phase, now_ms: int) -> SignedImage:
    self._clock(now_ms)
    if self.phase != phase or not self.grant:
      raise AuthorityError('operator authorization required')
    return self.grant[0]
