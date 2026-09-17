from dataclasses import replace

import pytest

from openpilot.tools.volt_gateway.authority import AuthorityError, Phase
from openpilot.tools.volt_gateway.native_harness import NativeAuthority, compile_authority
from openpilot.tools.volt_gateway.native_update import NativeUpdateEngine
from openpilot.tools.volt_gateway.test_authority import bundle as bundle, grant, LiveEnvironment, READY, Slot


@pytest.fixture(scope='module')
def library(tmp_path_factory):
  result = tmp_path_factory.mktemp('native-updater') / 'update.so'
  compile_authority(result)
  return result


def make(bundle, library, slot=None):
  key, _, image = bundle
  authority = NativeAuthority(library, key.public_key().export_key(format='DER'), image.manifest.device,
                              image.manifest.layout, Phase.PROGRAM)
  grant(bundle, authority, lease=1000)
  env = LiveEnvironment()
  slot = slot or Slot()
  return NativeUpdateEngine(authority, slot, env), env, slot


@pytest.mark.parametrize('operation', ['erase', 'write', 'read'])
@pytest.mark.parametrize('failure', ['expiry', 'stale', 'stationary', 'offroad', 'power_stable', 'vehicle_awake',
                                    'authenticated', 'compatible', 'errors'])
def test_native_fresh_checks_during_storage(bundle, library, monkeypatch, operation, failure):
  engine, env, slot = make(bundle, library)
  armed = False
  count = 0
  original = getattr(slot, operation)
  def changed(*args):
    nonlocal count
    result = original(*args)
    if armed:
      count += 1
      env.now = 1000 if failure == 'expiry' else 100
      if failure == 'stale':
        env.age = 31
      elif failure != 'expiry':
        env.conditions = replace(READY, **{failure: failure == 'errors'})
    return result
  monkeypatch.setattr(slot, operation, changed)
  armed = operation == 'erase'
  with pytest.raises(AuthorityError):
    engine.begin()
    armed = operation == 'write'
    for offset in range(0, len(bundle[1]), 256):
      engine.chunk(offset, bundle[1][offset:offset + 256])
    armed = operation == 'read'
    engine.finish()
  assert engine.state == 'aborted' and slot.trial is None and count == 1


def test_native_duplicates_and_corrupt_readback(bundle, library):
  engine, _, slot = make(bundle, library)
  engine.begin()
  for offset in range(0, len(bundle[1]), 256):
    chunk = bundle[1][offset:offset+256]
    assert engine.chunk(offset, chunk) == offset + len(chunk)
    assert engine.chunk(offset, chunk) == offset + len(chunk)
  assert slot.writes == 3
  slot.data[0] ^= 1
  with pytest.raises(AuthorityError):
    engine.finish()
  assert engine.state == 'aborted' and slot.trial is None


def test_native_callback_error_not_swallowed_as_success(bundle, library, monkeypatch):
  engine, _, slot = make(bundle, library)
  def failed(*_):
    raise OSError('storage failed')
  monkeypatch.setattr(slot, 'erase', failed)
  with pytest.raises(AuthorityError):
    engine.begin()
  assert engine.callback_failed and engine.state == 'aborted'


def test_native_wrong_geometry_no_erase(bundle, library):
  slot = Slot()
  slot.erase_regions = ((0, 256),)
  with pytest.raises(AuthorityError):
    make(bundle, library, slot)
  assert slot.erases == 0
