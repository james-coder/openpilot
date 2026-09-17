import ctypes as C
from pathlib import Path
import subprocess

import pytest

from openpilot.tools.volt_gateway.protocol import isotp_encode


class Rx(C.Structure):
  _fields_ = [('data',C.c_uint8*512),('started',C.c_uint32),('last',C.c_uint32),
              ('id',C.c_uint16),('length',C.c_uint16),('offset',C.c_uint16),
              ('expected',C.c_uint8),('block',C.c_uint8),('configured',C.c_bool),
              ('active',C.c_bool),('complete',C.c_bool)]


@pytest.fixture(scope='module')
def lib(tmp_path_factory):
  source = Path(__file__).parent/'firmware/recovery_transport.c'
  dest = tmp_path_factory.mktemp('recovery-rx')/'rx.so'
  subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fanalyzer','-shared','-fPIC',str(source),'-o',str(dest)],check=True)
  lib = C.CDLL(str(dest))
  p = C.POINTER(Rx)
  for name,result,args in [('init',C.c_bool,[p,C.c_uint16]),('clear',None,[p]),
                           ('expire',C.c_bool,[p,C.c_uint32]),
                           ('feed',C.c_int,[p,C.c_uint32,C.c_bool,C.c_bool,C.c_void_p,C.c_size_t,C.c_uint32])]:
    fn = getattr(lib,'vgw_recovery_rx_'+name)
    fn.restype,fn.argtypes = result,args
  return lib


def init(lib):
  rx = Rx()
  # Arbitrary simulation address ONLY: not a vehicle ID recommendation.
  assert lib.vgw_recovery_rx_init(C.byref(rx),0x123)
  return rx


def feed(lib, rx, frame, now=0, address=0x123, ext=False, rtr=False):
  return lib.vgw_recovery_rx_feed(C.byref(rx),address,ext,rtr,frame,len(frame),now)


@pytest.mark.parametrize('size', [1,7,8,13,14,62,63,64,255,256,511,512])
@pytest.mark.parametrize('start',[0,0xffffff00])
def test_python_codec_to_c_reassembly(lib, size, start):
  rx = init(lib)
  data = bytes(i%256 for i in range(size))
  frames = isotp_encode(data)
  for i,frame in enumerate(frames):
    result = feed(lib,rx,frame,(start+i*10)&0xffffffff)
    assert result == (2 if i==len(frames)-1 else 3 if i%8==0 else 1)
  assert rx.complete and bytes(rx.data[:rx.length])==data
  before = bytes(rx)
  assert feed(lib,rx,isotp_encode(b'x')[0],0)==0
  assert bytes(rx)==before
  lib.vgw_recovery_rx_clear(C.byref(rx))
  assert not any(rx.data) and not rx.complete


@pytest.mark.parametrize('defect',['dlc','rtr','oversize','padding','sequence','duplicate','new_first','timeout','total_timeout'])
def test_malformed_discards_without_completion(lib, defect):
  rx = init(lib)
  frames = isotp_encode(bytes(512))
  assert feed(lib,rx,frames[0])==3
  frame, now, rtr = frames[1],10,False
  if defect=='dlc':
    frame=frame[:7]
  elif defect=='rtr':
    rtr=True
  elif defect=='oversize':
    frame=b'\x12\x01'+bytes(6)
  elif defect=='padding':
    lib.vgw_recovery_rx_clear(C.byref(rx))
    frame=b'\x01x'+bytes(5)+b'x'
  elif defect=='sequence':
    frame=frames[2]
  elif defect=='duplicate':
    assert feed(lib,rx,frames[1],5)==1
  elif defect=='new_first':
    frame=frames[0]
  elif defect=='timeout':
    now=1001
  else:
    # Keep inter-frame gaps legal but exceed the overall message lifetime.
    for i in range(1,13):
      result=feed(lib,rx,frames[i],i*900)
    assert result==-1 and not rx.active and not rx.complete
    return
  assert feed(lib,rx,frame,now,rtr=rtr)==-1
  assert not rx.active and not rx.complete and not any(rx.data)


def test_unrelated_frames_and_quiet_expiry(lib):
  rx = init(lib)
  frame = isotp_encode(bytes(100))[0]
  assert feed(lib,rx,frame)==3
  before = bytes(rx)
  assert feed(lib,rx,frame,10,address=0x124)==0
  assert feed(lib,rx,frame,10,ext=True)==0
  assert bytes(rx)==before
  assert lib.vgw_recovery_rx_expire(C.byref(rx),1001)
  assert not rx.active


def test_unprovisioned_id_disabled(lib):
  rx = Rx()
  assert not lib.vgw_recovery_rx_init(C.byref(rx),0x800)
  assert feed(lib,rx,isotp_encode(b'x')[0])==-1
