import ctypes as C
from pathlib import Path
import subprocess

import pytest

from openpilot.tools.volt_gateway.test_white_board import IO, Identity, Registers
from openpilot.tools.volt_gateway.test_white_watchdog import State, CSR


class Startup(C.Structure):
  _fields_ = [('identity', Identity), ('watchdog', State), ('ready', C.c_bool)]


@pytest.fixture(scope='module')
def lib(tmp_path_factory):
  own = Path(__file__).parent/'firmware'
  dest = tmp_path_factory.mktemp('startup')/'startup.so'
  subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fanalyzer','-shared','-fPIC',
                  *(str(own/(name+'.c')) for name in ('white_startup','white_watchdog','white_board','status_led')),
                  '-o',str(dest)], check=True)
  lib = C.CDLL(str(dest))
  lib.vgw_white_startup_init.argtypes = [C.POINTER(Startup), C.POINTER(IO)]
  lib.vgw_white_startup_init.restype = C.c_bool
  lib.vgw_white_startup_now.argtypes = [C.POINTER(Startup)]
  lib.vgw_white_startup_now.restype = C.c_uint32
  return lib


def registers(ignore=None):
  r = Registers(ignore=ignore)
  r.values.update({0xe0042000:0x10000463,0x1fff7a22:1024,CSR:3,0x40023800:3})
  return r


def test_order_and_silent_startup(lib):
  r, s = registers(), Startup()
  assert lib.vgw_white_startup_init(C.byref(s),C.byref(r.io))
  writes = [addr for addr,_ in r.writes]
  assert writes.index(0x40020818)<writes.index(0x40003000)<writes.index(0x40023800)
  assert r.values[0x40023820] & (7<<25) == 7<<25  # CAN reset still held
  assert r.values[0x40020814] & ((1<<1)|(1<<13)) == ((1<<1)|(1<<13))
  assert r.values[0x40020014]&1
  assert r.values[0x4000000c]==0  # no timer interrupt/DMA
  for now in (0,1234,0xffffffff):
    r.values[0x40000024]=now
    assert lib.vgw_white_startup_now(C.byref(s))==now


@pytest.mark.parametrize('address', [0x40023840,0x40000000,0x40000028,0x4000002c])
def test_clock_timer_write_failure_latches(lib, address):
  r, s = registers(address), Startup()
  assert not lib.vgw_white_startup_init(C.byref(s),C.byref(r.io))
  assert not s.ready and s.watchdog.failed


@pytest.mark.parametrize('address,value', [(0x40023800,0),(0x40023808,8),(0xe0042000,0)])
def test_bad_clock_or_identity_bounded_failure(lib, address, value):
  r, s = registers(), Startup()
  r.values[address]=value
  assert not lib.vgw_white_startup_init(C.byref(s),C.byref(r.io))
  assert not s.ready
  assert not any(0x40006400<=a<0x40007000 for a,_ in r.writes)
