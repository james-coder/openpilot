import ctypes as C
import struct

import pytest

from openpilot.tools.volt_gateway import test_recovery_service as service
from openpilot.tools.volt_gateway import test_white_runtime as runtime
from openpilot.tools.volt_gateway.test_white_can import Frame
from openpilot.tools.volt_gateway.telemetry import TelemetryReceiver

archive=service.archive
native=service.native
service_library=service.service_library
bundle=service.bundle
built=runtime.built


def setup(service_library,native,bundle,built):
  lib,hw,state,ok=runtime.start(built,backhaul=2)
  assert ok
  app=None
  def attach(r):
    nonlocal app
    r.lib.vgw_application_size.restype=C.c_size_t
    app=C.create_string_buffer(r.lib.vgw_application_size())
    r.lib.vgw_application_init.argtypes=[C.c_void_p,C.c_void_p,C.c_void_p]
    r.lib.vgw_application_init.restype=C.c_bool
    r.lib.vgw_application_telemetry.argtypes=[C.c_void_p]
    r.lib.vgw_application_step.argtypes=[C.c_void_p,C.c_void_p]
    r.lib.vgw_application_step.restype=C.c_bool
    assert r.lib.vgw_application_init(app,C.byref(state),r.state)
  r=service.Recovery(service_library,native,bundle,before_open=attach)
  return r,app,lib,hw,state


def inject(r,state,address=0x123,payload=b'12345678',stamp=50000):
  f=Frame(stamp,0,address,3,0,len(payload),(C.c_uint8*8)(*payload))
  r.lib.vgw_observer_feed.argtypes=[C.c_void_p,C.POINTER(Frame)]
  r.lib.vgw_observer_feed.restype=C.c_bool
  assert r.lib.vgw_observer_feed(C.byref(state.observer),C.byref(f))


def test_authenticated_observe_capture_pagination(service_library,native,bundle,built):
  r,app,lib,hw,state=setup(service_library,native,bundle,built)
  assert r.command(1)[1:3]==b'\x01\1'
  assert r.command(14)==b'\0\0'
  assert r.command(5,b'\3\0\x1e')==b'\0'
  assert r.command(11,b'\x08\0\x1e')==b'\0'
  for i in range(5):
    inject(r,state,address=0x123+i,stamp=100000+i)
  page=r.command(7,b'\3\0\0')
  assert len(page)==120 and page[3]==2
  assert int.from_bytes(page[16:20],'big')==0x123
  page2=r.command(7,b'\3'+page[1:3])
  assert page2[3]==2
  page3=r.command(7,b'\3'+page2[1:3])
  assert page3[3]==1
  assert r.command(7,b'\3'+page3[1:3])==b'\0\x01\0\0'
  capture=r.command(13,b'\0\0')
  assert len(capture)==112 and capture[3]==4
  assert r.command(13,capture[1:3])[3]==1
  assert r.command(4,b'\3')==b'\0'
  assert r.command(7,b'\3\0\0')==b'\0\x01\0\0'
  assert r.slot.erases==r.slot.writes==0


def test_rx_health_capability_and_separate_counters(service_library,native,bundle,built):
  r,app,lib,hw,state=setup(service_library,native,bundle,built)
  assert int.from_bytes(r.command(1)[3:7],'big')&0x20
  counters=state.can.stats[2]
  counters.software_drops=37
  counters.irq_calls=1234
  counters.queue_peak=64
  counters.max_queue_age_ms=9
  assert r.command(16,b'\3')==b'\0'+struct.pack('>IIII',37,1234,64,9)
  assert r.command(16,b'\2')==b'\1'  # muxed-out HSCAN
  assert r.command(16)==b'\1'
  assert r.command(16,b'\3\0')==b'\1'
  assert counters.overflow==0  # hardware loss is a different counter


def test_actual_indicator_snapshot_and_disabled_peer(service_library,native,bundle,built):
  from openpilot.tools.volt_gateway.test_status_led import Status
  from openpilot.tools.volt_gateway.device_cli import indicators
  r,app,lib,hw,state=setup(service_library,native,bundle,built)
  assert r.command(15)==b'\1'  # no invented status before a renderer snapshot
  r.lib.vgw_application_indication.argtypes=[C.c_void_p,C.POINTER(Status),C.c_uint8]
  led=Status()
  led.state=1
  led.slot=0
  r.lib.vgw_application_indication(app,C.byref(led),4)
  state.can.config.backhaul=0
  value=indicators(r.command(15)[1:])
  assert value['state']=='running' and value['error']=='none' and value['can_peer']=='disabled'
  assert value['rgb_sample']==4 and value['slot']=='A'
  state.can.config.backhaul=2
  assert indicators(r.command(15)[1:])['can_peer']=='authenticated'
  state.local_control=True
  assert indicators(r.command(15)[1:])['can_peer']=='local_usb_owner'
  led.state=5
  led.error=9
  r.lib.vgw_application_indication(app,C.byref(led),1)
  assert indicators(r.command(15)[1:])['error']=='update_aborted'
  assert r.command(15,b'x')==b'\1'


@pytest.mark.parametrize('seen',[False,True])
def test_peer_awaiting_vs_stale_is_observational(service_library,native,bundle,built,seen):
  from openpilot.tools.volt_gateway.test_status_led import Status
  from openpilot.tools.volt_gateway.device_cli import indicators
  r,app,lib,hw,state=setup(service_library,native,bundle,built)
  led=Status()
  led.state=1
  r.lib.vgw_application_indication.argtypes=[C.c_void_p,C.POINTER(Status),C.c_uint8]
  r.lib.vgw_application_indicator_snapshot.argtypes=[C.c_void_p,C.c_void_p]
  r.lib.vgw_application_indicator_snapshot.restype=C.c_bool
  r.lib.vgw_application_indication(app,C.byref(led),4)
  if seen:
    state.can.elapsed_ms=r.now
    assert r.lib.vgw_application_step(app,C.byref(state))
  assert r.command(0x87)==b'\0'
  before=len(hw.writes)
  snapshot=C.create_string_buffer(7)
  assert r.lib.vgw_application_indicator_snapshot(app,snapshot)
  assert indicators(snapshot.raw)['can_peer']==('stale' if seen else 'awaiting_authenticated_peer')
  assert len(hw.writes)==before and r.slot.erases==r.slot.writes==0


@pytest.mark.parametrize('op,payload',[(5,b'\2\0\x01'),(5,b'\3\0\0'),(7,b'\3\xff\xff'),
                                     (8,b'\0'*9),(11,b'\x80\0\1'),(13,b'\xff\xff'),(14,b'x'),(63,b'')])
def test_invalid_application_commands(service_library,native,bundle,built,op,payload):
  r,app,lib,hw,state=setup(service_library,native,bundle,built)
  assert r.command(op,payload)==b'\1'
  assert hw.writes[-1][0] not in (0x40006400+0x180,0x40006c00+0x180)
  assert r.slot.erases==r.slot.writes==0


def test_telemetry_real_driver_and_session_cleanup(service_library,native,bundle,built):
  r,app,lib,hw,state=setup(service_library,native,bundle,built)
  assert r.command(8,b'\0\3\0'+(0x123).to_bytes(4,'big')+b'\0\x0a')==b'\0'
  inject(r,state,stamp=40000)
  decoder=TelemetryReceiver()
  result=None
  for t in (40,50,60):
    hw.values[0x40000024]=t
    assert lib.vgw_white_runtime_step(C.byref(state),runtime.PROTOCOL(('vgw_application_step',r.lib)),app)
    start=len(hw.writes)
    r.lib.vgw_application_telemetry(app)
    assert any(a==0x40006800+0x180 for a,v in hw.writes[start:])
    raw=struct.pack('<II',hw.values[0x40006800+0x188],hw.values[0x40006800+0x18c])
    result=decoder.feed(raw,t/1000)
    hw.complete(2)
  assert result.address==0x123 and result.data==b'12345678' and result.bus==3
  r.now=60
  assert r.command(0x87)==b'\0'
  inject(r,state,stamp=70000)
  before=len(hw.writes)
  r.lib.vgw_application_telemetry(app)
  assert len(hw.writes)==before


@pytest.mark.parametrize('fault',['lost','duplicate','reordered','stale','backward','control','id','dlc'])
def test_bounded_telemetry_reassembly(fault):
  record=(123).to_bytes(4,'big')+(0x123).to_bytes(4,'big')+b'12345678'+b'\x88\0'
  if fault=='id':
    record=record[:4]+b'\xff'*4+record[8:]
  if fault=='dlc':
    record=record[:16]+b'\x8f\0'
  frames=[bytes([0xa0+i*16,7])+record[i*6:i*6+6] for i in range(3)]
  times=[0,0.01,0.02]
  if fault=='lost':
    frames.pop(1)
    times.pop(1)
  elif fault=='duplicate':
    frames[2]=frames[1]
  elif fault=='reordered':
    frames[1],frames[2]=frames[2],frames[1]
  elif fault=='stale':
    times[2]=0.2
  elif fault=='backward':
    times[2]=-1
  elif fault=='control':
    frames[1]=b'\x30'+bytes(7)
  receiver=TelemetryReceiver()
  assert all(receiver.feed(f,t) is None for f,t in zip(frames,times,strict=True))
  assert receiver.drops>0
