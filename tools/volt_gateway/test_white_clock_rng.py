import ctypes as C
from pathlib import Path
import subprocess

import pytest

from openpilot.tools.volt_gateway.test_white_board import Registers, R32
from openpilot.tools.volt_gateway.test_white_startup import Startup

CR, PLL, CFGR, RNG = 0x40023800,0x40023804,0x40023808,0x50060800


class Clock(C.Structure):
  _fields_ = [('startup',C.POINTER(Startup)),('ready',C.c_bool)]


class Random(C.Structure):
  _fields_ = [('clock',C.POINTER(Clock)),('previous',C.c_uint32),('ready',C.c_bool),('failed',C.c_bool)]


@pytest.fixture(scope='module')
def lib(tmp_path_factory):
  own = Path(__file__).parent/'firmware'
  output = tmp_path_factory.mktemp('clock-rng')/'clock.so'
  subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fanalyzer','-shared','-fPIC',
                  *(str(own/(n+'.c')) for n in ('white_clock','white_rng','white_watchdog','white_board','status_led')),
                  '-o',str(output)],check=True)
  lib = C.CDLL(str(output))
  for name,args in [('vgw_white_clock_init',[C.POINTER(Clock),C.POINTER(Startup)]),
                    ('vgw_white_clock_valid',[C.POINTER(Clock)]),
                    ('vgw_white_rng_init',[C.POINTER(Random),C.POINTER(Clock)]),
                    ('vgw_white_rng_nonce',[C.POINTER(Random),C.c_void_p])]:
    fn = getattr(lib,name)
    fn.argtypes,fn.restype = args,C.c_bool
  return lib


class Hardware(Registers):
  def __init__(self, ignore=None, stuck=None):
    super().__init__(ignore=ignore)
    self.stuck = stuck
    self.words = iter(range(1,100))
    self.status = 1
    self.values.update({CR:3,CFGR:0,0x40023820:7<<25,0x40007004:0x4000,
                        0x40000000:1,0x40000024:321,0x40000028:15999})
    self.io.read32 = R32(self.read)

  def read(self, _, a):
    if a==RNG+4:
      return self.status
    if a==RNG+8:
      return next(self.words,42)
    return self.values.get(a,0)

  def write(self, ctx, a, value):
    if a==CR:
      value &= ~((1<<17)|(1<<25))
      if value & (1<<16) and self.stuck!='hse':
        value |= 1<<17
      if value & (1<<24) and self.stuck!='pll':
        value |= 1<<25
    if a==CFGR:
      value = (value&~12) | (0 if self.stuck=='switch' else (value&3)<<2)
    super().write(ctx,a,value)


def setup(lib, hardware=None):
  h = hardware or Hardware()
  s = Startup()
  s.ready=True
  s.watchdog.started=True
  s.watchdog.io=h.io
  c = Clock()
  ok = lib.vgw_white_clock_init(C.byref(c),C.byref(s))
  return h,s,c,ok


def test_profile_and_order(lib):
  h,s,c,ok = setup(lib)
  assert ok and lib.vgw_white_clock_valid(C.byref(c))
  assert h.values[PLL]==0x24401808
  assert h.values[CFGR]&0xfcff==0x940a
  assert h.values[0x40000028]==47999 and h.values[0x40000024]==321
  assert h.values[0x40023820]&7<<25==7<<25
  latency = next(i for i,(a,v) in enumerate(h.writes) if a==0x40023c00 and v&15==5)
  switch = next(i for i,(a,v) in enumerate(h.writes) if a==CFGR and v&3==2)
  assert latency<switch
  assert not any(a==0x40003000 and v==0xaaaa for a,v in h.writes)
  assert s.ready


@pytest.mark.parametrize('stuck',['hse','pll','switch'])
def test_missing_clock_fails_bounded(lib, stuck):
  h,s,c,ok = setup(lib,Hardware(stuck=stuck))
  assert not ok and not c.ready and not s.ready and s.watchdog.failed
  assert h.values[0x40023820]&7<<25==7<<25


@pytest.mark.parametrize('ignore',[PLL,0x40023840,0x40007000,0x40023c00,0x40000028])
def test_dropped_register_write_rejected(lib, ignore):
  _,s,c,ok = setup(lib,Hardware(ignore=ignore))
  assert not ok and not c.ready and s.watchdog.failed


def rng_setup(lib):
  h,s,c,ok = setup(lib)
  assert ok
  r = Random()
  assert lib.vgw_white_rng_init(C.byref(r),C.byref(c))
  return h,s,c,r


def test_fresh_nonce_and_continuous_state(lib):
  h,s,c,r = rng_setup(lib)
  out = C.create_string_buffer(32)
  assert lib.vgw_white_rng_nonce(C.byref(r),out)
  assert out.raw==b''.join(i.to_bytes(4,'little') for i in range(2,10))
  assert lib.vgw_white_rng_nonce(C.byref(r),out)
  assert out.raw==b''.join(i.to_bytes(4,'little') for i in range(10,18))
  assert r.previous==17 and s.ready and c.ready
  assert not any(a==RNG+4 for a,_ in h.writes)  # don't clear and conceal errors


@pytest.mark.parametrize('status',[0,2,4,0x20,0x40,0x67])
def test_rng_timeout_or_hardware_error_zeroes_and_latches(lib,status):
  h,s,c,r = rng_setup(lib)
  h.status=status
  out = C.create_string_buffer(b'x'*32,32)
  assert not lib.vgw_white_rng_nonce(C.byref(r),out)
  assert out.raw==bytes(32) and r.failed and h.values[RNG]==0
  h.status=1
  assert not lib.vgw_white_rng_nonce(C.byref(r),out)


def test_repeated_word_after_partial_output_clears_everything(lib):
  h,s,c,r = rng_setup(lib)
  h.words=iter([2,3,4,4])
  out=C.create_string_buffer(32)
  assert not lib.vgw_white_rng_nonce(C.byref(r),out)
  assert r.failed and out.raw==bytes(32)


@pytest.mark.parametrize('address,value',[(PLL,0),(CR,3),(CFGR,0),(0x40023894,1<<27),(RNG,0),(0x40023834,0)])
def test_clock_or_rng_configuration_loss_blocks_nonce(lib,address,value):
  h,s,c,r=rng_setup(lib)
  h.values[address]=value
  out=C.create_string_buffer(32)
  assert not lib.vgw_white_rng_nonce(C.byref(r),out)
  assert r.failed and out.raw==bytes(32)
