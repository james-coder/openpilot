import ctypes as C
from pathlib import Path
import subprocess

import pytest

from openpilot.tools.volt_gateway import test_white_runtime as runtime
from openpilot.tools.volt_gateway.test_white_can import Can, Frame

built=runtime.built


class Safety(C.Structure):
  _fields_=[('can',C.POINTER(Can)),('received',C.c_uint64*3),('stable_since',C.c_uint64),('previous',C.c_uint64)]
  _fields_+=[(n,C.c_uint32) for n in ('voltage_mv','divider_milli','adc_started','adc_sampled')]
  _fields_+=[('powertrain_bus',C.c_uint8),('seen',C.c_uint8),('safe',C.c_bool*3)]
  _fields_+=[(n,C.c_bool) for n in ('ready','converting','power_valid','stable')]


@pytest.fixture(scope='module')
def safety_lib(tmp_path_factory):
  path=tmp_path_factory.mktemp('safety')/'safety.so'
  source=Path(__file__).parent/'firmware/white_safety.c'
  subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fanalyzer','-shared','-fPIC',str(source),'-o',str(path)],check=True)
  lib=C.CDLL(str(path))
  lib.vgw_white_safety_init.argtypes=[C.POINTER(Safety),C.POINTER(Can),C.c_uint8,C.c_uint32]
  lib.vgw_white_safety_init.restype=C.c_bool
  lib.vgw_white_safety_receive.argtypes=[C.c_void_p,C.POINTER(Frame)]
  lib.vgw_white_safety_sample.argtypes=[C.c_void_p,C.c_uint64,C.POINTER(C.c_uint64),C.POINTER(C.c_bool)]
  lib.vgw_white_safety_sample.restype=C.c_bool
  return lib


def initialize(safety_lib,built,divider=8862):
  lib,hw,state,ok=runtime.start(built,backhaul=2)
  assert ok
  safety=Safety()
  assert safety_lib.vgw_white_safety_init(C.byref(safety),C.byref(state.can),0,divider)
  return hw,state,safety


def sample(lib,hw,state,safety,t,*,raw=1523,frames=True,fault=None):
  state.can.elapsed_ms=t
  if frames:
    for address in (1001,309,497):
      payload=bytearray(8)
      if address==497:
        payload[0]=2
      if fault=='speed' and address==1001:
        payload[1]=1
      if fault=='left_speed' and address==1001:
        payload[5]=1
      if fault=='gear' and address==309:
        payload[0]=2
      if fault=='awake' and address==497:
        payload[0]=1
      f=Frame(t*1000,0,address,1 if fault=='bus' else 0,1 if fault=='extended' else 0,
              7 if fault=='dlc' else 8,(C.c_uint8*8)(*payload))
      lib.vgw_white_safety_receive(C.byref(safety),C.byref(f))
  hw.values[0x40012000]=0 if fault=='adc_timeout' else 4
  hw.values[0x4001203c]=raw
  sampled=C.c_uint64()
  allowed=C.c_bool(True)
  ok=lib.vgw_white_safety_sample(C.byref(safety),t,C.byref(sampled),C.byref(allowed))
  return ok,allowed.value


@pytest.mark.parametrize('divider,raw',[(8862,1523),(3791,3560)])
def test_continuous_actual_inputs_required(safety_lib,built,divider,raw):
  hw,state,s=initialize(safety_lib,built,divider)
  for t in range(5010):
    ok,allowed=sample(safety_lib,hw,state,s,t,raw=raw)
    assert ok
    assert allowed==(t>=5001)
  assert 13400<s.voltage_mv<13600
  assert sample(safety_lib,hw,state,s,5010,raw=raw,fault='speed')==(True,False)
  assert sample(safety_lib,hw,state,s,5011,raw=raw)==(True,False)


@pytest.mark.parametrize('fault',['speed','left_speed','gear','awake','bus','extended','dlc','adc_timeout'])
def test_unsafe_or_missing_inputs_never_permit(safety_lib,built,fault):
  hw,state,s=initialize(safety_lib,built)
  for t in range(5100):
    ok,allowed=sample(safety_lib,hw,state,s,t,fault=fault)
    assert not allowed


@pytest.mark.parametrize('raw',[0,1,1000,1800,4095,65535])
def test_bad_power_fails_closed(safety_lib,built,raw):
  hw,state,s=initialize(safety_lib,built)
  for t in range(5100):
    assert not sample(safety_lib,hw,state,s,t,raw=raw)[1]


def test_stale_reversed_clock_and_unknown_divider(safety_lib,built):
  hw,state,s=initialize(safety_lib,built)
  for t in range(5010):
    sample(safety_lib,hw,state,s,t)
  for t in range(5010,5300):
    _,allowed=sample(safety_lib,hw,state,s,t,frames=False)
  assert not allowed
  assert sample(safety_lib,hw,state,s,1)==(False,False)
  assert not safety_lib.vgw_white_safety_init(C.byref(s),C.byref(state.can),0,8000)


def test_queued_old_safe_frames_do_not_refresh_update_interlock(safety_lib,built):
  hw,state,s=initialize(safety_lib,built)
  state.can.elapsed_ms=10000
  for address in (1001,309,497):
    data=bytearray(8)
    if address==497:
      data[0]=2
    frame=Frame(1000*1000,0,address,0,0,8,(C.c_uint8*8)(*data))
    safety_lib.vgw_white_safety_receive(C.byref(s),C.byref(frame))
  assert list(s.received)==[1000,1000,1000]
  for t in range(10000,10010):
    assert not sample(safety_lib,hw,state,s,t,frames=False)[1]
