"""Native state-machine/MMIO tests; not physical HVAC validation."""
import ctypes as C
from pathlib import Path
import subprocess

import pytest

from openpilot.tools.volt_gateway.test_white_can import Can, Config, CanHardware, BASES, RECEIVE
from openpilot.tools.volt_gateway.test_white_clock_rng import Clock, setup
from openpilot.tools.volt_gateway.test_white_startup import Startup
from openpilot.tools.volt_gateway.test_white_board import IO


class Trial(C.Structure):
  _fields_=[('safety',C.c_void_p),('deadline',C.c_uint64),('cooldown',C.c_uint64),('previous',C.c_uint64),
            ('state',C.c_uint8),('attempts',C.c_uint8),('allowed',C.c_bool),
            ('raw',C.c_bool),('reason',C.c_uint8),('tx_status',C.c_uint32),('error_status',C.c_uint32)]


@pytest.fixture(scope='module')
def library(tmp_path_factory):
  own=Path(__file__).parent/'firmware'
  root=tmp_path_factory.mktemp('hvac')
  # Fault-inject the already independently tested Park/voltage sampler.
  stub=root/'safety.c'
  stub.write_text('''#include "white_safety.h"
bool safe=true; bool valid=true;
bool vgw_white_safety_sample(void *s,uint64_t n,uint64_t *t,bool *a)
{((vgw_white_safety *)s)->safe[0]=safe;*t=n;*a=safe;return valid;}
''')
  out=root/'trial.so'
  subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fanalyzer','-shared','-fPIC',
                  '-DVGW_HVAC_EXPERIMENT','-I',str(own),str(stub),
                  *(str(own/(n+'.c')) for n in ('hvac_trial','white_can','white_clock','white_watchdog','white_board','status_led')),
                  '-o',str(out)],check=True)
  lib=C.CDLL(str(out))
  for name,args in [('vgw_white_quiesce',[C.POINTER(IO)]),
                    ('vgw_white_clock_init',[C.POINTER(Clock),C.POINTER(Startup)]),
                    ('vgw_white_can_init',[C.POINTER(Can),C.POINTER(Clock),C.POINTER(Config)]),
                    ('vgw_white_can_poll',[C.POINTER(Can),RECEIVE,C.c_void_p]),
                    ('vgw_hvac_trial_start',[C.POINTER(Trial),C.c_uint64])]:
    getattr(lib,name).argtypes=args
    getattr(lib,name).restype=C.c_bool
  lib.vgw_hvac_trial_init.argtypes=[C.POINTER(Trial),C.c_void_p]
  lib.vgw_hvac_trial_step.argtypes=[C.POINTER(Trial),C.c_uint64,C.c_bool]
  lib.vgw_hvac_trial_raw.argtypes=[C.POINTER(Trial),C.c_uint64,C.c_char_p,C.c_size_t]
  lib.vgw_hvac_trial_raw.restype=C.c_bool
  lib.vgw_hvac_trial_can_restart.argtypes=[C.POINTER(Trial),C.c_uint64]
  lib.vgw_hvac_trial_can_restart.restype=C.c_bool
  return lib


def start(lib):
  from openpilot.tools.volt_gateway.test_white_safety import Safety
  hw=CanHardware()
  _,startup,clock,ok=setup(lib,hw)
  assert ok
  can=Can()
  assert lib.vgw_white_can_init(C.byref(can),C.byref(clock),C.byref(Config(3,3,2,0x6f1)))
  safety=Safety()
  safety.can=C.pointer(can)
  safety.seen=7
  safety.safe[:]=[True,True,True]
  trial=Trial()
  lib.vgw_hvac_trial_init(C.byref(trial),C.byref(safety))
  C.c_bool.in_dll(lib,'safe').value=True
  C.c_bool.in_dll(lib,'valid').value=True
  # Keep ctypes owners alive through the complete fixture.
  return hw,can,trial,(startup,clock,safety)


def step(lib,can,trial,now,authorized=True):
  from openpilot.tools.volt_gateway.test_white_safety import Safety
  safety=C.cast(trial.safety,C.POINTER(Safety)).contents
  safety.received[:]=[now]*3
  can.elapsed_ms=now
  lib.vgw_hvac_trial_step(C.byref(trial),now,authorized)


def transmissions(hw):
  return [(a,v) for a,v in hw.writes if a==BASES[2]+0x180 and v&1]


def test_exact_pair_and_no_boot_transmission(library):
  hw,can,t,owners=start(library)
  step(library,can,t,100)
  assert not transmissions(hw)
  assert library.vgw_hvac_trial_start(C.byref(t),100)
  assert transmissions(hw)==[(BASES[2]+0x180,(0x10ad6080<<3)|5)]
  assert hw.values[BASES[2]+0x188]==0x0207070a
  assert hw.values[BASES[2]+0x18c]==0x2b
  assert not library.vgw_hvac_trial_start(C.byref(t),100)
  hw.complete(3)
  step(library,can,t,101)
  step(library,can,t,2600)
  assert len(transmissions(hw))==1
  step(library,can,t,2601)
  assert len(transmissions(hw))==2 and hw.values[BASES[2]+0x18c]==0
  hw.complete(3)
  step(library,can,t,2602)
  assert t.state==4 and can.stats[2].transmitted==2
  assert not library.vgw_hvac_trial_start(C.byref(t),2602)
  step(library,can,t,10100)
  assert library.vgw_hvac_trial_start(C.byref(t),10100)


@pytest.mark.parametrize('failure',['unsafe','unauthenticated','overflow','can_failed','wrong_bus','bus_off','silent'])
def test_interlocks_never_send(library,failure):
  hw,can,t,owners=start(library)
  if failure=='unsafe':
    C.c_bool.in_dll(library,'safe').value=False
  if failure=='overflow':
    can.tx_inhibited=True
  if failure=='can_failed':
    can.failed=True
  if failure=='wrong_bus':
    can.config.swcan=2
  if failure=='bus_off':
    hw.values[BASES[2]+24]=4
  if failure=='silent':
    hw.values[BASES[2]+28]|=1<<31
  step(library,can,t,100,failure!='unauthenticated')
  assert not library.vgw_hvac_trial_start(C.byref(t),100)
  assert not transmissions(hw)


@pytest.mark.parametrize('failure',['timeout','arbitration','error','session_loss','unsafe','late_release'])
def test_fault_latches_no_retry(library,failure):
  hw,can,t,owners=start(library)
  step(library,can,t,100)
  assert library.vgw_hvac_trial_start(C.byref(t),100)
  if failure=='timeout':
    step(library,can,t,120)
  elif failure in ('arbitration','error'):
    hw.complete(3,5 if failure=='arbitration' else 9)
    step(library,can,t,101)
  elif failure=='session_loss':
    step(library,can,t,101,False)
  elif failure=='unsafe':
    C.c_bool.in_dll(library,'safe').value=False
    step(library,can,t,101)
  else:
    hw.complete(3)
    step(library,can,t,101)
    step(library,can,t,2622)
  assert t.state==5
  step(library,can,t,100000)
  assert not library.vgw_hvac_trial_start(C.byref(t),100000)
  assert len(transmissions(hw))==1


def test_attempt_bound(library):
  hw,can,t,owners=start(library)
  t.attempts=4
  step(library,can,t,100000)
  assert not library.vgw_hvac_trial_start(C.byref(t),100000)
  assert not transmissions(hw)


@pytest.mark.parametrize('elapsed,now,allowed',[(100,101,True),(100,110,True),(100,111,False),(100,99,False)])
def test_live_clock_after_poll_entry(library,elapsed,now,allowed):
  hw,can,t,owners=start(library)
  can.elapsed_ms=elapsed
  owners[2].received[:]=[now]*3
  library.vgw_hvac_trial_step(C.byref(t),now,True)
  assert bool(library.vgw_hvac_trial_start(C.byref(t),now))==allowed
  assert len(transmissions(hw))==int(allowed)


def test_status_diagnostics():
  import struct
  from openpilot.tools.volt_gateway.device_cli import hvac_status
  from openpilot.tools.volt_gateway.protocol import ProtocolError
  assert not hvac_status(bytes([1,0,0,0]))['interlock_ready']
  value=bytes([2,0,0,0,7,7,1,1,0,0,1])+struct.pack('>I3H',4990,10,20,30)
  decoded=hvac_status(value)
  assert decoded['voltage_mv']==4990 and decoded['input_ages_ms']==[10,20,30]
  assert decoded['safe_mask']==7 and not decoded['stable']
  diagnostic=bytes([4,6])+value[2:]+bytes([3])+struct.pack('>II',5,0)
  decoded=hvac_status(diagnostic)
  assert decoded['state']=='failed' and decoded['failure_reason']=='arbitration_lost'
  assert decoded['tx_status']=='0x5' and decoded['error_status']=='0x0'
  for bad in (b'',value[:-1],value+b'\0',bytes([3,0,0,0]),bytes([1,6,0,0])):
    with pytest.raises(ProtocolError):
      hvac_status(bad)


def test_flash_power_failure_is_not_cabin_tx_requirement(library):
  hw,can,t,owners=start(library)
  C.c_bool.in_dll(library,'valid').value=False
  step(library,can,t,100)
  assert library.vgw_hvac_trial_start(C.byref(t),100)


@pytest.mark.parametrize('outcome',['success_delayed_poll','arbitration','timeout','error'])
def test_raw_completion_and_explicit_next_attempt(library,outcome):
  hw,can,t,owners=start(library)
  packet=bytes.fromhex('10b0209901080006070d00000001')
  step(library,can,t,100)
  assert library.vgw_hvac_trial_raw(C.byref(t),100,packet,len(packet))
  if outcome=='timeout':
    step(library,can,t,120)
  else:
    hw.complete(3,{'success_delayed_poll':3,'arbitration':5,'error':9}[outcome])
    # Intentionally don't advance the CAN poll epoch: hardware completion
    # must still be accounted for when the old 10ms guard is false.
    library.vgw_hvac_trial_step(C.byref(t),111,True)
  assert len(transmissions(hw))==1  # never automatically retry
  assert t.state=={'success_delayed_poll':4,'arbitration':6,'timeout':6,'error':5}[outcome]
  assert t.reason=={'success_delayed_poll':0,'arbitration':3,'timeout':2,'error':4}[outcome]
  assert can.stats[2].transmitted==int(outcome=='success_delayed_poll')
  assert can.stats[2].arbitration_lost==int(outcome=='arbitration')
  assert can.stats[2].tx_errors==int(outcome=='error')
  step(library,can,t,1120)
  # Model abort completion before accepting a new explicitly requested frame.
  hw.values[BASES[2]+8]|=1<<26
  assert library.vgw_hvac_trial_raw(C.byref(t),1120,packet,len(packet))==(outcome!='error')


@pytest.mark.parametrize('condition',['fresh','stale','future','moving','missing','active_press','fault'])
def test_restart_and_passive_park_evidence(library,condition):
  hw,can,t,owners=start(library)
  safety=owners[2]
  safety.received[:]=[100,100,100]
  if condition=='stale':
    safety.received[1]=0
  if condition=='future':
    safety.received[1]=301
  if condition=='moving':
    safety.safe[0]=False
  if condition=='missing':
    safety.seen=3
  if condition=='active_press':
    t.state=1
  if condition=='fault':
    t.state=5
  assert library.vgw_hvac_trial_can_restart(C.byref(t),300)==(condition in ('fresh','fault'))
  assert not transmissions(hw)
