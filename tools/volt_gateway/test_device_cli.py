from collections import deque
from types import SimpleNamespace

import pytest

from openpilot.tools.volt_gateway import test_application as app
from openpilot.tools.volt_gateway.device_cli import Device, pages
from openpilot.tools.volt_gateway.protocol import IsoTpReceiver, isotp_encode, ProtocolError
from openpilot.tools.volt_gateway.telemetry import TelemetryReceiver
from openpilot.tools.volt_gateway.usb_transport import UsbTransport

archive=app.archive
native=app.native
service_library=app.service_library
bundle=app.bundle
built=app.built


class UsbTimeout(Exception):
  pass


class WireUsb:
  def __init__(self,recovery):
    self.recovery=recovery
    self.rx=IsoTpReceiver()
    self.out=deque()
    self.next=0
    self.received=0

  def bulkWrite(self,ep,data,timeout):
    assert ep==2 and len(data)==8 and timeout==1000
    if data[0]>>4==3:
      assert data[:3]==b'\x30\0\x0a'
      return 8
    self.next+=.01
    result=self.rx.feed(data,self.next)
    if data[0]>>4==1:
      self.received=0
      self.out.append(b'\x30\x08\x0a'+bytes(5))
    elif data[0]>>4==2:
      self.received+=1
      if self.received%8==0 and result is None:
        self.out.append(b'\x30\x08\x0a'+bytes(5))
    if result is not None:
      response=self.recovery.wire(result)
      assert response is not None
      self.out.extend(isotp_encode(response))
    return 8

  def bulkRead(self,ep,maximum,timeout):
    assert ep==0x81 and maximum==64 and timeout==100
    if not self.out:
      raise UsbTimeout
    return self.out.popleft()


def device(r):
  transport=UsbTransport.__new__(UsbTransport)
  transport.usb=SimpleNamespace(USBErrorTimeout=UsbTimeout)
  transport.handle=WireUsb(r)
  transport.frames=deque(maxlen=64)
  transport.telemetry=TelemetryReceiver()
  transport.observations=deque(maxlen=128)
  transport.observation_drops=0
  result=Device.__new__(Device)
  result.transport=transport
  result.client=r.client
  return result


def test_cli_paginated_real_dispatch_over_usb_wire(service_library,native,bundle,built,monkeypatch):
  monkeypatch.setattr('openpilot.tools.volt_gateway.usb_transport.time.sleep',lambda _:None)
  r,state,lib,hw,runtime=app.setup(service_library,native,bundle,built)
  d=device(r)
  assert d.command(1)[:2]==b'\1\0'
  d.command(5,b'\3\0\x1e')
  for i in range(9):
    app.inject(r,runtime,address=0x300+i,stamp=100000+i)
  records=list(pages(d,7,3))
  assert [v['address'] for v in records]==[hex(0x300+i) for i in range(9)]
  assert all(v['count']==1 and v['approximate_hz'] is None for v in records)
  with pytest.raises(ProtocolError,match='rejected'):
    d.command(14,b'x'*400)  # multiple block-sized requests, bounded rejection
  d.close()


def test_idle_session_expires_and_clears_subscriptions(service_library,native,bundle,built):
  r,state,lib,hw,runtime=app.setup(service_library,native,bundle,built)
  r.command(8,b'\0\3\0'+(0x123).to_bytes(4,'big')+b'\0\x0a')
  packet=r.client.request(2)
  assert r.wire(packet,now=r.now+20000) is None
  from openpilot.tools.volt_gateway.recovery_client import RecoveryClient, HELLO_REQUEST
  hello=r.wire(HELLO_REQUEST)
  client=RecoveryClient(r.pairing,hello,device=r.manifest.manifest.device,
    layout=r.manifest.manifest.layout,policy=r.policy,host_nonce=b'o'*32)
  client.accept_open(r.wire(client.open_request))
  r.client=client
  assert r.command(8,b'\0\3\0'+(0x123).to_bytes(4,'big')+b'\0\x0a')==b'\0'
