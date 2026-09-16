"""Classic CAN frames -> hardware driver/runtime -> authenticated recovery.

CAN peripheral behavior and flash are modeled. Cryptography/dispatch/update
are the real C implementations. No USB or vehicle access.
"""
import ctypes as C

from openpilot.tools.volt_gateway import test_recovery_service as service
from openpilot.tools.volt_gateway import test_white_runtime as runtime
from openpilot.tools.volt_gateway import test_recovery_boot as boot
from openpilot.tools.volt_gateway.protocol import IsoTpReceiver, isotp_encode
from openpilot.tools.volt_gateway.test_white_can import BASES

archive=service.archive
native=service.native
service_library=service.service_library
bundle=service.bundle
built=runtime.built
library=boot.library
key=boot.key


class CanTransport:
  def __init__(self,built):
    self.lib,self.hw,self.state,ok=runtime.start(built,backhaul=2)
    assert ok
    self.frames=[]
    self.writes_at=len(self.hw.writes)

  def step(self,recovery,incoming=None):
    recovery.now+=10
    recovery.env.now=recovery.now
    self.hw.values[0x40000024]=recovery.now
    if incoming is not None:
      self.hw.inject(2,0x600,incoming)
    handler=runtime.PROTOCOL(('vgw_recovery_runtime_step',recovery.lib))
    assert self.lib.vgw_white_runtime_step(C.byref(self.state),handler,C.cast(recovery.state,C.c_void_p))
    writes=self.hw.writes[self.writes_at:]
    self.writes_at=len(self.hw.writes)
    tx=[v for a,v in writes if a==BASES[1]+0x180]
    assert len(tx)<=1
    for v in tx:
      assert v==(0x601<<21)|1
      raw=self.hw.values[BASES[1]+0x188].to_bytes(4,'little')+self.hw.values[BASES[1]+0x18c].to_bytes(4,'little')
      self.frames.append(raw)
      self.hw.complete(2)

  def __call__(self,recovery,pdu):
    frames=isotp_encode(pdu)
    for index,frame in enumerate(frames):
      self.step(recovery,frame)
      if len(frames)>1 and index<len(frames)-1 and (index==0 or index%8==0):
        for _ in range(100):
          if self.frames:
            break
          self.step(recovery)
        assert self.frames.pop(0)==b'\x30\x08\x0a'+bytes(5)
    decoder=IsoTpReceiver()
    for _ in range(1100):
      if not self.frames:
        self.step(recovery)
        continue
      frame=self.frames.pop(0)
      result=decoder.feed(frame,recovery.now/1000)
      if result is not None:
        return result
      if frame[0]>>4==1:
        self.step(recovery,b'\x30\x00\x0a'+bytes(5))
    raise AssertionError('no bounded ISO-TP reply')


def test_authenticated_update_over_driver_and_isotp(service_library,native,bundle,built):
  bus=CanTransport(built)
  r=service.Recovery(service_library,native,bundle,transport=bus)
  r.authorize()
  assert r.command(0x82)==b'\0'
  for off in range(0,len(r.image),256):
    assert r.command(0x83,off.to_bytes(4,'big')+r.image[off:off+256])==b'\0'
  assert r.command(0x84)==b'\0'
  assert r.slot.trial is not None
  assert bus.state.can.stats[1].transmitted>100
  assert bus.state.startup.watchdog.epoch>0
  assert not bus.state.startup.watchdog.failed
  assert not any(a in {BASES[0]+0x180,BASES[2]+0x180} for a,_ in bus.hw.writes)


def test_can_wire_recovery_with_both_slots_invalid(service_library,native,library,key,built):
  bus=CanTransport(built)
  flash,r=boot.setup(library,service_library,native,key,empty=True,transport=bus)
  assert r.command(0x82)==b'\0'
  for off in range(0,len(r.image),256):
    request=r.client.request(0x83,off.to_bytes(4,'big')+r.image[off:off+256])
    lost_reply=r.wire(request)
    before=flash.snapshot()
    # Host retries a request whose acknowledgement was lost.
    reply=r.wire(r.client.retry())
    assert reply==lost_reply and flash.snapshot()==before
    assert r.client.accept(reply)==b'\0'
  assert r.command(0x84)==b'\0'
  assert library.vgw_test_boot()==1
  assert library.vgw_test_confirm()
  assert library.vgw_test_boot()==1
