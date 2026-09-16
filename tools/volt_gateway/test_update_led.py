import ctypes as C

import pytest

from openpilot.tools.volt_gateway import test_native_update as existing
from openpilot.tools.volt_gateway.authority import AuthorityError

library = existing.library
bundle = existing.bundle


def indication(engine, now):
  fn = engine.lib.vgw_update_led
  fn.argtypes, fn.restype = [C.c_void_p, C.c_uint64, C.c_void_p], C.c_bool
  out = C.create_string_buffer(3)
  return list(out.raw) if fn(engine.buffer, now, out) else None


def test_states_track_real_operations(bundle, library, monkeypatch):
  engine, env, slot = existing.make(bundle, library)
  assert indication(engine, env.now) is None
  observed = []
  for name in ('erase','read','mark_trial'):
    original = getattr(slot,name)
    def wrapped(*args, method=original, name=name):
      observed.append((name,indication(engine,env.now)))
      return method(*args)
    monkeypatch.setattr(slot,name,wrapped)
  engine.begin()
  assert all(state==[10,255,0] for name,state in observed if name=='erase')
  assert indication(engine,env.now)==[6,255,0]  # waiting; not a single accepted byte yet
  for offset in range(0,len(bundle[1]),256):
    engine.chunk(offset,bundle[1][offset:offset+256])
    assert indication(engine,env.now)==[4,255,0]
  engine.finish()
  assert all(state==[7,255,0] for name,state in observed if name=='read')
  assert [state for name,state in observed if name=='mark_trial']==[[8,255,0]]
  assert indication(engine,env.now)==[9,255,0]  # verified/committed, NOT boot-confirmed
  assert slot.trial is not None


def test_waiting_read_only_and_duplicate_does_not_refresh(bundle, library):
  engine, env, slot = existing.make(bundle,library)
  engine.begin()
  first=bundle[1][:256]
  engine.chunk(0,first)
  env.now=900
  engine.chunk(0,first)
  snapshot=engine.buffer.raw
  writes=slot.writes
  assert indication(engine,1500)==[4,255,0]
  assert indication(engine,1501)==[6,255,0]
  for _ in range(10):
    indication(engine,2000)
  assert engine.buffer.raw==snapshot and slot.writes==writes


@pytest.mark.parametrize('operation', ['erase','read','mark_trial'])
def test_never_signals_ready_on_failure(bundle,library,monkeypatch,operation):
  engine,env,slot=existing.make(bundle,library)
  def failed(*args):
    raise OSError('test failure')
  monkeypatch.setattr(slot,operation,failed)
  with pytest.raises(AuthorityError):
    engine.begin()
    for offset in range(0,len(bundle[1]),256):
      engine.chunk(offset,bundle[1][offset:offset+256])
    engine.finish()
  assert indication(engine,env.now)==[5,255,9]
