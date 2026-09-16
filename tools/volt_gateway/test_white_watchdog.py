import ctypes as C
from pathlib import Path
import subprocess

import pytest

from openpilot.tools.volt_gateway.test_white_board import IO, Registers

CSR, KR, PR, RLR, SR = 0x40023874, 0x40003000, 0x40003004, 0x40003008, 0x4000300c


class State(C.Structure):
  _fields_ = [('io', IO), ('epoch', C.c_uint32), ('seen', C.c_uint32), ('reset', C.c_uint32),
              ('started', C.c_bool), ('failed', C.c_bool)]


@pytest.fixture(scope='module')
def lib(tmp_path_factory):
  source = Path(__file__).parent/'firmware/white_watchdog.c'
  output = tmp_path_factory.mktemp('watchdog')/'watchdog.so'
  subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-fanalyzer', '-shared', '-fPIC',
                  str(source), '-o', str(output)], check=True)
  lib = C.CDLL(str(output))
  p = C.POINTER(State)
  for name, result, args in (
    ('start', C.c_bool, [p, C.POINTER(IO), C.c_uint32]),
    ('service', C.c_bool, [p, C.c_uint32]), ('progress', None, [p, C.c_uint32]),
    ('fail', None, [p]), ('was_reset', C.c_bool, [p]),
  ):
    fn = getattr(lib, 'vgw_white_watchdog_'+name)
    fn.restype, fn.argtypes = result, args
  return lib


def setup(lib, start=0, flags=0):
  regs = Registers()
  regs.values[CSR] = flags | 3
  state = State()
  assert lib.vgw_white_watchdog_start(C.byref(state), C.byref(regs.io), start)
  return state, regs


def feeds(regs):
  return regs.writes.count((KR, 0xaaaa))


def test_register_sequence_and_reset_capture(lib):
  state, regs = setup(lib, flags=1<<29)
  assert lib.vgw_white_watchdog_was_reset(C.byref(state))
  assert regs.writes == [(CSR, (1<<29)|3), (KR, 0xcccc), (KR, 0x5555), (PR, 3), (RLR, 1999), (KR, 0xaaaa)]
  assert not any(a==CSR and v & (1<<24) for a,v in regs.writes)


@pytest.mark.parametrize('missing', [1, 2, 4, 8])
def test_busy_subset_cannot_keep_watchdog_alive(lib, missing):
  state, regs = setup(lib)
  for now in range(251):
    lib.vgw_white_watchdog_progress(C.byref(state), 15 ^ missing)
    assert lib.vgw_white_watchdog_service(C.byref(state), now)
  assert feeds(regs)==1
  assert not lib.vgw_white_watchdog_service(C.byref(state), 251)
  lib.vgw_white_watchdog_progress(C.byref(state), 15)
  assert not lib.vgw_white_watchdog_service(C.byref(state), 252)
  assert feeds(regs)==1


@pytest.mark.parametrize('start', [0, 0xfffffff0])
def test_complete_quiet_service_and_wrap(lib, start):
  state, regs = setup(lib, start)
  for elapsed in range(1, 501):
    # Each loop completes all tasks, even with no packets/commands to process.
    for task in (1,2,4,8):
      lib.vgw_white_watchdog_progress(C.byref(state), task)
    assert lib.vgw_white_watchdog_service(C.byref(state), (start+elapsed)&0xffffffff)
  assert feeds(regs)==11
  assert state.seen==0


def test_frozen_clock_does_not_feed(lib):
  state, regs = setup(lib)
  for _ in range(1000):
    lib.vgw_white_watchdog_progress(C.byref(state), 15)
    assert lib.vgw_white_watchdog_service(C.byref(state), 0)
  assert feeds(regs)==1  # Independent LSI must reset hardware despite frozen TIM2.


@pytest.mark.parametrize('bits', [0, 16, 0xffffffff])
def test_bad_progress_latches(lib, bits):
  state, regs = setup(lib)
  lib.vgw_white_watchdog_progress(C.byref(state), bits)
  assert not lib.vgw_white_watchdog_service(C.byref(state), 100)
  assert feeds(regs)==1


@pytest.mark.parametrize('fault', ['lsi', 'busy', 'prescale', 'reload'])
def test_start_failure_never_claims_ready(lib, fault):
  regs = Registers(ignore={'prescale':PR, 'reload':RLR}.get(fault))
  regs.values[CSR] = 0 if fault=='lsi' else 3
  regs.values[SR] = 3 if fault=='busy' else 0
  state = State()
  assert not lib.vgw_white_watchdog_start(C.byref(state), C.byref(regs.io), 0)
  assert state.failed and feeds(regs)==0
  assert not lib.vgw_white_watchdog_service(C.byref(state), 50)


def test_late_complete_epoch_cannot_rescue_stall(lib):
  state, regs = setup(lib)
  lib.vgw_white_watchdog_progress(C.byref(state), 15)
  assert not lib.vgw_white_watchdog_service(C.byref(state), 251)
  assert feeds(regs)==1
