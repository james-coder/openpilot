import ctypes as C
from pathlib import Path
import subprocess

import pytest

from openpilot.tools.volt_gateway.protocol import isotp_encode, IsoTpReceiver
from openpilot.tools.volt_gateway.test_recovery_transport import Rx


class Link(C.Structure):
  _fields_=[('rx',Rx),('reply',C.c_uint8*512),('length',C.c_uint16),('offset',C.c_uint16),('separation',C.c_uint16)]
  _fields_ += [(n,C.c_uint32) for n in ('started','last','previous','rejects','timeouts','completed')]
  _fields_ += [(n,C.c_uint8) for n in ('sequence','remaining','waits')]
  _fields_ += [(n,C.c_bool) for n in ('ready','flow_pending','replying','waiting','unlimited')]


SEND=C.CFUNCTYPE(C.c_bool,C.c_void_p,C.c_void_p)


@pytest.fixture(scope='module')
def lib(tmp_path_factory):
  source=Path(__file__).parent/'firmware'
  out=tmp_path_factory.mktemp('recovery-link')/'link.so'
  subprocess.run(['cc','-Wall','-Wextra','-Werror','-fanalyzer','-shared','-fPIC',
                  str(source/'recovery_link.c'),str(source/'recovery_transport.c'),'-o',str(out)],check=True)
  dll=C.CDLL(str(out))
  for name,args,result in [
    ('init',[C.POINTER(Link),C.c_uint16],C.c_bool),
    ('feed',[C.POINTER(Link),C.c_uint32,C.c_bool,C.c_bool,C.c_void_p,C.c_size_t,C.c_uint32],C.c_int),
    ('reply',[C.POINTER(Link),C.c_void_p,C.c_size_t,C.c_uint32],C.c_bool),
    ('poll',[C.POINTER(Link),C.c_uint32,SEND,C.c_void_p],C.c_bool),
  ]:
    fn=getattr(dll,'vgw_recovery_link_'+name)
    fn.argtypes,fn.restype=args,result
  return dll


def feed(lib,s,data,now=0,address=0x600):
  return lib.vgw_recovery_link_feed(C.byref(s),address,False,False,data,len(data),now)


def poll(lib,s,now,accept=True):
  sent=[]
  def send(_,p):
    sent.append(C.string_at(p,8))
    return accept
  result=lib.vgw_recovery_link_poll(C.byref(s),now,SEND(send),None)
  return result,sent


def start(lib):
  s=Link()
  assert lib.vgw_recovery_link_init(C.byref(s),0x600)
  return s


@pytest.mark.parametrize('length',[1,7,8,82,182,186,256,512])
@pytest.mark.parametrize('block',[0,1,8])
def test_bidirectional_transport(lib,length,block):
  s=start(lib)
  data=bytes(i%256 for i in range(length))
  now=0
  for frame in isotp_encode(data):
    result=feed(lib,s,frame,now)
    assert result in (1,2,3)
    if result==3:
      assert poll(lib,s,now)==(True,[b'\x30\x08\x0a'+bytes(5)])
    now+=10
  assert s.rx.complete and bytes(s.rx.data[:length])==data
  assert lib.vgw_recovery_link_reply(C.byref(s),data,length,now)
  decoder=IsoTpReceiver()
  complete=None
  while s.replying:
    if s.waiting:
      assert feed(lib,s,bytes([0x30,block,0])+bytes(5),now)==1
    ok,frames=poll(lib,s,now)
    if ok:
      complete=decoder.feed(frames[0],now/1000)
    now+=10
    assert now<10000
  assert complete==data and s.completed==1
  assert bytes(s.reply)==bytes(512)


def replying(lib):
  s=start(lib)
  assert feed(lib,s,b'\x01x'+bytes(6))==2
  assert lib.vgw_recovery_link_reply(C.byref(s),bytes(range(100)),100,0)
  assert poll(lib,s,0)[0] and s.waiting
  return s


def test_driver_backpressure_does_not_advance(lib):
  s=replying(lib)
  assert feed(lib,s,b'\x30\x08\x0a'+bytes(5))==1
  old=s.offset
  ok,frames=poll(lib,s,10,False)
  assert not ok and s.offset==old
  assert poll(lib,s,10)==(True,frames)
  assert s.offset==old+7


@pytest.mark.parametrize('flow',[b'\x30\x08\x80',b'\x30\x08\xf0',b'\x30\x08\xfa',b'\x32\0\0',b'\x33\0\0'])
def test_bad_or_overflow_flow_aborts(lib,flow):
  s=replying(lib)
  assert feed(lib,s,flow+bytes(5))==-1
  assert not s.replying and s.rejects==1


def test_timeout_and_bounded_waits(lib):
  s=replying(lib)
  for i in range(3):
    assert feed(lib,s,b'\x31'+bytes(7),100+i)==1
  assert feed(lib,s,b'\x31'+bytes(7),103)==-1
  s=replying(lib)
  assert poll(lib,s,1001)==(False,[]) and s.timeouts==1


def test_unauthorized_block_advance_rejected(lib):
  s=start(lib)
  frames=isotp_encode(bytes(range(100)))
  assert feed(lib,s,frames[0])==3
  assert poll(lib,s,0,False)[0] is False
  assert feed(lib,s,frames[1],10)==-1 and not s.rx.active


def test_unrelated_frames_no_response_and_clock_reversal(lib):
  s=start(lib)
  assert feed(lib,s,b'\x01x'+bytes(6),100,address=0x601)==0
  assert poll(lib,s,100)==(False,[])
  assert feed(lib,s,b'\x01x'+bytes(6),99)==-1 and not s.ready
