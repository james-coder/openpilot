from dataclasses import replace
import hashlib
import os

from Crypto.PublicKey import ECC
import pytest

from openpilot.tools.volt_gateway.adaptive import Conditions
from openpilot.tools.volt_gateway.authority import (
  AUTH_DOMAIN, CHALLENGE_TTL_MS, IMAGE_DOMAIN, MAX_LEASE_MS, AuthorityError, Authorization, Challenge,
  ImageManifest, Phase, SignedImage, UpdateAuthority, public_key, verify,
)
from openpilot.tools.volt_gateway.operator import authorize_image, bounded_read, generate_key, load_key, private_directory, sign_image
from openpilot.tools.volt_gateway.protocol import seal
from openpilot.tools.volt_gateway.update_engine import Environment, UpdateEngine


# Disposable, generated test keys only. Never used for real provisioning.
@pytest.fixture
def bundle():
  key = ECC.generate(curve='P-256')
  raw = bytes(range(256)) * 3
  manifest = ImageManifest(bytes.fromhex('370022000651363038363036'), 1, hashlib.sha256(b'test layout').digest(),
                           len(raw), hashlib.sha256(raw).digest(), hashlib.sha256(b'test build').digest(), 7)
  return key, raw, sign_image(key, manifest)


def guard(bundle, phase=Phase.PROGRAM):
  key, _, image = bundle
  return UpdateAuthority(key.public_key().export_key(format='DER'), image.manifest.device, image.manifest.layout, phase)


def grant(bundle, g, now=0, lease=60000):
  key, _, image = bundle
  challenge = g.challenge(image, now)
  token = authorize_image(key, image, challenge, lease)
  g.authorize(token, now)
  return token


def test_roundtrips_and_independent_phases(bundle):
  key, _, image = bundle
  assert SignedImage.unpack(image.pack()) == image
  image.verify(key.public_key())
  app = guard(bundle, Phase.ENTER)
  token = grant(bundle, app)
  assert Authorization.unpack(token.pack()) == token
  assert Challenge.unpack(token.challenge.pack()) == token.challenge
  assert app.require(Phase.ENTER, 1) == image
  with pytest.raises(AuthorityError):
    app.require(Phase.PROGRAM, 1)
  loader = guard(bundle)
  loader.challenge(image, 2)
  with pytest.raises(AuthorityError):
    loader.authorize(token, 3)
  grant(bundle, loader, 4)
  assert loader.require(Phase.PROGRAM, 5) == image


def test_routine_hmac_and_signed_image_alone_cannot_authorize(bundle):
  _, _, image = bundle
  g = guard(bundle)
  g.challenge(image, 0)
  with pytest.raises(AuthorityError):
    g.require(Phase.PROGRAM, 1)
  routine_pdu = seal(os.urandom(32), b'routine!', 0, 0, 0x40, image.pack())
  with pytest.raises(AuthorityError):
    Authorization.unpack(routine_pdu)
  with pytest.raises(AuthorityError):
    Authorization.unpack(image.pack())


@pytest.mark.parametrize('field,value', [('device', bytes(12)), ('layout', bytes(32)), ('digest', bytes(32)),
                                        ('build', bytes(32)), ('version', 8), ('size', 1)])
def test_modified_manifest_rejected(bundle, field, value):
  key, _, image = bundle
  changed = replace(image, manifest=replace(image.manifest, **{field:value}))
  with pytest.raises(AuthorityError):
    changed.verify(key.public_key())


def test_wrong_device_and_layout_even_if_signed(bundle):
  key, _, image = bundle
  for field, value in [('device', bytes(12)), ('layout', bytes(32))]:
    wrong = sign_image(key, replace(image.manifest, **{field:value}))
    with pytest.raises(AuthorityError):
      guard(bundle).challenge(wrong, 0)


def test_wrong_key_cross_domain_and_private_verifier_rejected(bundle):
  key, _, image = bundle
  with pytest.raises(AuthorityError):
    public_key(key.export_key(format='DER'))
  with pytest.raises(AuthorityError):
    public_key(ECC.generate(curve='P-384').public_key().export_key(format='DER'))
  with pytest.raises(AuthorityError):
    image.verify(ECC.generate(curve='P-256').public_key())
  with pytest.raises(AuthorityError):
    verify(key.public_key(), AUTH_DOMAIN, image.manifest.pack(), image.signature)
  token = grant(bundle, guard(bundle))
  with pytest.raises(AuthorityError):
    verify(key.public_key(), IMAGE_DOMAIN, token.body(), token.signature)


def test_replay_expiry_and_reboot(bundle):
  g = guard(bundle)
  token = grant(bundle, g, lease=1000)
  with pytest.raises(AuthorityError):
    g.authorize(token, 1)
  with pytest.raises(AuthorityError):
    g.require(Phase.PROGRAM, 1000)
  assert g.closed
  rebooted = guard(bundle)
  rebooted.challenge(bundle[2], 0)
  with pytest.raises(AuthorityError):
    rebooted.authorize(token, 1)
  g = guard(bundle)
  c = g.challenge(bundle[2], 0)
  token = authorize_image(bundle[0], bundle[2], c, 1000)
  with pytest.raises(AuthorityError):
    g.authorize(token, CHALLENGE_TTL_MS)


def test_challenge_no_eviction_and_bad_signature_rate_limit(bundle):
  g = guard(bundle)
  c = g.challenge(bundle[2], 0)
  assert g.challenge(bundle[2], 1) == c
  different = sign_image(bundle[0], replace(bundle[2].manifest, version=8))
  with pytest.raises(AuthorityError):
    g.challenge(different, 2)
  token = authorize_image(bundle[0], bundle[2], c, 60000)
  bad = replace(token, signature=bytes(64))
  with pytest.raises(AuthorityError, match='signature'):
    g.authorize(bad, 3)
  with pytest.raises(AuthorityError, match='rate limit'):
    g.authorize(bad, 4)
  g.authorize(token, 1003)


@pytest.mark.parametrize('now', [-1, 0.5, float('nan'), 2**63, True])
def test_invalid_clocks_fail_closed(bundle, now):
  g = guard(bundle)
  grant(bundle, g, now=10)
  with pytest.raises(AuthorityError):
    g.require(Phase.PROGRAM, now)
  assert g.closed


@pytest.mark.parametrize('lease', [0, 999, MAX_LEASE_MS + 1, 1.5, True])
def test_lease_bounds(bundle, lease):
  g = guard(bundle)
  with pytest.raises(AuthorityError):
    authorize_image(bundle[0], bundle[2], g.challenge(bundle[2], 0), lease)


def test_entropy_failure_and_repeat(bundle):
  key, _, image = bundle
  for rng in (lambda n: bytes(n), lambda n: b'x' * (n-1)):
    with pytest.raises(AuthorityError):
      UpdateAuthority(key.public_key().export_key(format='DER'), image.manifest.device, image.manifest.layout, Phase.PROGRAM,
                      random_bytes=rng)
  g = UpdateAuthority(key.public_key().export_key(format='DER'), image.manifest.device, image.manifest.layout, Phase.PROGRAM,
                      random_bytes=lambda n: b'x' * n)
  with pytest.raises(AuthorityError):
    g.challenge(image, 0)


@pytest.mark.parametrize('decoder', [SignedImage.unpack, Challenge.unpack, Authorization.unpack])
def test_bounded_malformed_records(decoder):
  for size in range(520):
    with pytest.raises((AuthorityError, ValueError)):
      decoder(bytes(size))


class Slot:
  capacity = 2048
  erase_regions = tuple((offset, 256) for offset in range(0, 2048, 256))

  def __init__(self):
    self.data = bytearray(b'\xff' * self.capacity)
    self.erases = self.writes = 0
    self.trial = None

  def erase(self, offset, size):
    self.erases += 1
    self.data[offset:offset + size] = b'\xff' * size

  def write(self, offset, data):
    self.writes += 1
    self.data[offset:offset + len(data)] = data

  def read(self, offset, size):
    return bytes(self.data[offset:offset + size])

  def mark_trial(self, manifest):
    self.trial = manifest


READY = Conditions(True, True, True, True, True, True, False)


class LiveEnvironment:
  def __init__(self):
    self.now = 0
    self.conditions = READY
    self.age = 0

  def __call__(self):
    return Environment(self.now, self.now - self.age, self.conditions)


def test_update_and_duplicate_are_image_scoped(bundle):
  g, slot = guard(bundle), Slot()
  grant(bundle, g)
  env = LiveEnvironment()
  engine = UpdateEngine(g, slot, env)
  engine.begin()
  for offset in range(0, len(bundle[1]), 256):
    chunk = bundle[1][offset:offset+256]
    env.now = 2 + offset
    assert engine.chunk(offset, chunk) == offset + len(chunk)
    env.now += 1
    assert engine.chunk(offset, chunk) == offset + len(chunk)
  assert slot.erases == 8 and slot.writes == 3
  env.now = 900
  engine.finish()
  assert slot.trial == bundle[2].manifest and engine.state == 'trial'
  with pytest.raises(AuthorityError):
    engine.begin()


def test_no_erase_without_operator_or_app_phase(bundle):
  g, slot = guard(bundle), Slot()
  engine = UpdateEngine(g, slot, LiveEnvironment())
  with pytest.raises(AuthorityError):
    engine.begin()
  assert slot.erases == 0
  with pytest.raises(AuthorityError):
    UpdateEngine(guard(bundle, Phase.ENTER), slot, LiveEnvironment())


@pytest.mark.parametrize('field,value', [('stationary', False), ('offroad', False), ('power_stable', False),
                                       ('vehicle_awake', False), ('authenticated', False), ('compatible', False), ('errors', True)])
def test_gate_loss_never_marks_trial(bundle, field, value):
  g, slot = guard(bundle), Slot()
  grant(bundle, g)
  env = LiveEnvironment()
  engine = UpdateEngine(g, slot, env)
  engine.begin()
  env.conditions = replace(READY, **{field:value})
  with pytest.raises(AuthorityError):
    engine.chunk(0, bundle[1][:256])
  assert slot.writes == 0 and slot.trial is None and engine.state == 'aborted'


@pytest.mark.parametrize('failure', ['short', 'wire_corrupt', 'flash_corrupt', 'expired'])
def test_bad_or_incomplete_image_never_activates(bundle, failure):
  g, slot = guard(bundle), Slot()
  grant(bundle, g, lease=1000)
  env = LiveEnvironment()
  engine = UpdateEngine(g, slot, env)
  engine.begin()
  raw = bundle[1] if failure != 'wire_corrupt' else b'x' * len(bundle[1])
  stop = 256 if failure == 'short' else len(raw)
  for offset in range(0, stop, 256):
    engine.chunk(offset, raw[offset:offset+256])
  if failure == 'flash_corrupt':
    slot.data[0] ^= 1
  env.now = 1000 if failure == 'expired' else 2
  with pytest.raises(AuthorityError):
    engine.finish()
  assert slot.trial is None


@pytest.mark.parametrize('operation', ['erase', 'write', 'read'])
@pytest.mark.parametrize('failure', ['expiry', 'stale', 'stationary', 'offroad', 'power_stable', 'vehicle_awake',
                                    'authenticated', 'compatible', 'errors'])
def test_environment_refreshed_after_each_storage_operation(bundle, operation, failure):
  env, g = LiveEnvironment(), guard(bundle)
  grant(bundle, g, lease=1000)

  class ChangingSlot(Slot):
    armed = False
    operations = 0

    def changed(self, kind):
      if self.armed and kind == operation:
        self.operations += 1
        env.now = 1000 if failure == 'expiry' else 100
        if failure == 'stale':
          env.age = 31
        elif failure not in ('expiry', 'stale'):
          env.conditions = replace(READY, **{failure: failure == 'errors'})

    def erase(self, offset, size):
      super().erase(offset, size)
      self.changed('erase')

    def write(self, offset, data):
      super().write(offset, data)
      self.changed('write')

    def read(self, offset, size):
      result = super().read(offset, size)
      self.changed('read')
      return result

  slot = ChangingSlot()
  engine = UpdateEngine(g, slot, env)
  slot.armed = operation == 'erase'
  with pytest.raises(AuthorityError):
    engine.begin()
    slot.armed = operation == 'write'
    for offset in range(0, len(bundle[1]), 256):
      engine.chunk(offset, bundle[1][offset:offset+256])
    slot.armed = operation == 'read'
    engine.finish()
  assert slot.operations == 1  # no subsequent storage operation after failure
  assert engine.state == 'aborted' and g.closed and slot.trial is None


def test_provider_exception_fails_closed(bundle):
  g, slot = guard(bundle), Slot()
  grant(bundle, g)
  def unavailable():
    raise OSError('sensor unavailable')
  engine = UpdateEngine(g, slot, unavailable)
  with pytest.raises(OSError):
    engine.begin()
  assert g.closed and engine.state == 'aborted' and slot.erases == 0


def test_lost_conditions_during_commit_do_not_claim_commit_was_undone(bundle):
  env, g = LiveEnvironment(), guard(bundle)
  grant(bundle, g)
  class CommitSlot(Slot):
    def mark_trial(self, manifest):
      super().mark_trial(manifest)
      env.conditions = replace(READY, power_stable=False)
  slot = CommitSlot()
  engine = UpdateEngine(g, slot, env)
  engine.begin()
  for offset in range(0, len(bundle[1]), 256):
    engine.chunk(offset, bundle[1][offset:offset+256])
  with pytest.raises(AuthorityError):
    engine.finish()
  assert engine.state == 'aborted' and g.closed
  assert slot.trial == bundle[2].manifest  # loader must independently validate


def test_encrypted_key_roundtrip_permissions_and_no_overwrite(tmp_path):
  path = tmp_path / 'key.pem'
  password = 'disposable test passphrase only'
  public = generate_key(path, password)
  assert path.stat().st_mode & 0o777 == 0o600
  assert b'BEGIN ENCRYPTED PRIVATE KEY' in path.read_bytes()
  assert not public_key(public).has_private()
  assert load_key(path, password).public_key().export_key(format='DER') == public
  with pytest.raises(AuthorityError):
    load_key(path, 'incorrect')
  with pytest.raises(FileExistsError):
    generate_key(path, password)
  path.chmod(0o644)
  with pytest.raises(AuthorityError):
    load_key(path, password)


def test_private_paths_reject_symlink_and_unencrypted_key(tmp_path, bundle):
  path = tmp_path / 'plaintext.pem'
  path.write_text(bundle[0].export_key(format='PEM'))
  path.chmod(0o600)
  with pytest.raises(AuthorityError):
    load_key(path, 'unused')
  link = tmp_path / 'link'
  link.symlink_to(path)
  with pytest.raises(OSError):
    bounded_read(link, 4096, private=True)
  directory = tmp_path / 'dir'
  directory.symlink_to(tmp_path, target_is_directory=True)
  with pytest.raises(AuthorityError):
    private_directory(directory)
