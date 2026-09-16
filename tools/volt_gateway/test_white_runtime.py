"""Composition test of actual C startup/clock/RNG/CAN/observer/link/watchdog."""
import ctypes as C
from pathlib import Path
import subprocess

import pytest

from openpilot.tools.volt_gateway.test_white_can import CanHardware, Can, Config, BASES
from openpilot.tools.volt_gateway.test_white_clock_rng import Clock, Random
from openpilot.tools.volt_gateway.test_white_startup import Startup
from openpilot.tools.volt_gateway.test_white_board import IO
from openpilot.tools.volt_gateway.test_recovery_link import Link

PROTOCOL=C.CFUNCTYPE(C.c_bool,C.c_void_p,C.c_void_p)


@pytest.fixture(scope='module')
def built(tmp_path_factory):
  source=Path(__file__).parent/'firmware'
  out=tmp_path_factory.mktemp('runtime')/'runtime.so'
  names=('white_runtime','white_startup','white_watchdog','white_clock','white_rng','white_can',
         'white_board','status_led','observe','recovery_link','recovery_transport')
  subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fanalyzer','-shared','-fPIC',
                  *(str(source/(n+'.c')) for n in names),'-o',str(out)],check=True)
  dll=C.CDLL(str(out))
  dll.vgw_observer_size.restype=C.c_size_t
  dll.vgw_white_runtime_size.restype=C.c_size_t

  class Observer(C.Union):
    _fields_=[('aligned',C.c_uint64),('bytes',C.c_uint8*dll.vgw_observer_size())]

  class Runtime(C.Structure):
    _fields_=[('startup',Startup),('clock',Clock),('rng',Random),('can',Can),('observer',Observer),
              ('recovery',Link),('nonce',C.c_uint8*32),('ready',C.c_bool),
              ('listener',C.c_void_p),('listener_context',C.c_void_p),('local_control',C.c_bool)]

  assert C.sizeof(Runtime)==dll.vgw_white_runtime_size()
  dll.vgw_white_runtime_init.argtypes=[C.c_void_p,C.POINTER(IO),C.POINTER(Config),C.c_uint16]
  dll.vgw_white_runtime_init.restype=C.c_bool
  dll.vgw_white_runtime_step.argtypes=[C.c_void_p,PROTOCOL,C.c_void_p]
  dll.vgw_white_runtime_step.restype=C.c_bool
  dll.vgw_recovery_link_reply.argtypes=[C.POINTER(Link),C.c_void_p,C.c_size_t,C.c_uint32]
  dll.vgw_recovery_link_reply.restype=C.c_bool
  return dll,Runtime


def start(built,backhaul=0,hw=None):
  lib,Runtime=built
  hw=hw or CanHardware()
  hw.values.update({0xe0042000:0x10000463,0x1fff7a22:1024,0x40023874:3})
  state=Runtime()
  ok=lib.vgw_white_runtime_init(C.byref(state),C.byref(hw.io),C.byref(Config(3,3,backhaul,0x601)),0x600)
  return lib,hw,state,ok


def test_composed_startup_and_quiet_bus_watchdog(built):
  lib,h,s,ok=start(built)
  assert ok and s.ready and any(s.nonce)
  before=sum(a==0x40003000 and v==0xaaaa for a,v in h.writes)
  for t in range(1,1001):
    h.values[0x40000024]=t
    assert lib.vgw_white_runtime_step(C.byref(s),PROTOCOL(lambda *_:True),None)
  after=sum(a==0x40003000 and v==0xaaaa for a,v in h.writes)
  assert after-before==20
  assert not any(a in {b+0x180 for b in BASES} for a,_ in h.writes)


def test_request_and_reply_cross_actual_controller_driver(built):
  lib,h,s,ok=start(built,backhaul=2)
  assert ok
  h.inject(1,0x600,b'\x01x'+bytes(6))  # wrong physical controller
  h.inject(3,0x600,b'\x01x'+bytes(6))  # SWCAN is never control input
  assert lib.vgw_white_runtime_step(C.byref(s),PROTOCOL(lambda *_:True),None)
  assert not s.recovery.rx.complete
  h.inject(2,0x600,b'\x01x'+bytes(6))
  observed=[]
  def protocol(_,p):
    observed.append(bytes(s.recovery.rx.data[:s.recovery.rx.length]))
    # Test-only benign echo. Not a production authentication implementation.
    return lib.vgw_recovery_link_reply(C.byref(s.recovery),b'ok',2,0)
  assert lib.vgw_white_runtime_step(C.byref(s),PROTOCOL(protocol),None)
  assert observed==[b'x']
  assert h.values[BASES[1]+0x180]==(0x601<<21)|1
  assert h.values[BASES[1]+0x188]==int.from_bytes(b'\x02ok\0','little')
  assert s.can.stats[0].received==1 and s.can.stats[2].received==1


@pytest.mark.parametrize('failure',['protocol','delayed_protocol','no_protocol','clock','rng','rng_status','busoff','gap'])
def test_runtime_failure_stops_phys_and_clears_nonce(built,failure):
  lib,h,s,ok=start(built,backhaul=2)
  assert ok
  def protocol(*_):
    if failure=='delayed_protocol':
      h.values[0x40000024]=251
    return failure!='protocol'
  if failure=='clock':
    h.values[0x40023804]=0
  if failure=='rng':
    s.rng.failed=True
  if failure=='rng_status':
    h.status=0x20
  if failure=='busoff':
    h.values[BASES[1]+24]=4
  if failure=='gap':
    h.values[0x40000024]=251
  fn=PROTOCOL() if failure=='no_protocol' else PROTOCOL(protocol)
  assert not lib.vgw_white_runtime_step(C.byref(s),fn,None)
  assert not s.ready and s.startup.watchdog.failed
  assert bytes(s.nonce)==bytes(32)
  assert h.values[0x40020414]&0xc000==0 and h.values[0x40023820]&(7<<25)==7<<25


def test_rng_startup_failure_never_enables_can(built):
  h=CanHardware()
  h.status=4
  lib,h,s,ok=start(built,hw=h)
  assert not ok and not s.ready
  assert h.values[0x40023820]&(7<<25)==7<<25
  assert not any(a==0x40020418 and v==0xc000 for a,v in h.writes)
