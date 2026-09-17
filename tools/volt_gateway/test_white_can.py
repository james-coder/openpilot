"""bxCAN register semantics, not a claim about physical bus timing or wiring."""
import ctypes as C
from collections import deque
from pathlib import Path
import subprocess

import pytest

from openpilot.tools.volt_gateway.test_white_clock_rng import Clock, Hardware, setup
from openpilot.tools.volt_gateway.test_white_board import IO

BASES = (0x40006400, 0x40006800, 0x40006c00)


class Config(C.Structure):
  _fields_ = [('swcan', C.c_uint8), ('hscan', C.c_uint8), ('backhaul', C.c_uint8), ('response', C.c_uint16)]


class Stats(C.Structure):
  _fields_ = [(n, C.c_uint32) for n in ('received','malformed','overflow','transmitted','arbitration_lost','tx_errors','last_esr')]
  _fields_ += [('window_start',C.c_uint32*3),('window_bits',C.c_uint32*3),('peak',C.c_uint32)]
  _fields_ += [(n,C.c_uint32) for n in ('software_drops','irq_calls','queue_peak','max_queue_age_ms')]


class Raw(C.Structure):
  _fields_ = [(n,C.c_uint32) for n in ('id','dlc','lo','hi','stamp','sequence')]


class Can(C.Structure):
  _fields_ = [('clock',C.POINTER(Clock)),('config',Config),('stats',Stats*3)]
  _fields_ += [(n,C.c_uint32) for n in ('previous_ms','token_ms','pending_ms','sequence','config_check')]
  _fields_ += [('elapsed_ms',C.c_uint64),('tokens',C.c_uint8)]
  _fields_ += [(n,C.c_bool) for n in ('ready','failed','pending','tx_inhibited')]
  _fields_ += [('rx_interrupts',C.c_bool)]
  _fields_ += [(n,C.c_uint8*3) for n in ('rx_head','rx_tail','rx_count')]
  _fields_ += [('rx',(Raw*64)*3)]


class Frame(C.Structure):
  _fields_ = [('timestamp',C.c_uint64),('sequence',C.c_uint32),('address',C.c_uint32),
              ('bus',C.c_uint8),('flags',C.c_uint8),('dlc',C.c_uint8),('data',C.c_uint8*8)]


RECEIVE = C.CFUNCTYPE(None,C.c_void_p,C.POINTER(Frame))


class CanHardware(Hardware):
  def __init__(self, ignore=None, stuck=None, fifo_depth=None):
    super().__init__(ignore=ignore)
    self.fifos = {b:deque() for b in BASES}
    self.can_stuck = stuck
    # Existing algebraic backlog tests deliberately use an unbounded offered
    # queue. Physical FIFO tests opt into the RM0430 three-message locked FIFO.
    self.fifo_depth=fifo_depth
    self.lost=[0,0,0]
    self.values.update({b+8:1<<26 for b in BASES})
    self.values.update({BASES[0]+0x200:0x2a1c0e01,BASES[2]+0x200:0x2a1c0e01})

  def read(self, ctx, a):
    for b in BASES:
      if a==b+12:
        return min(3,len(self.fifos[b])) | self.values.get(a,0)
      if b+0x1b0<=a<=b+0x1bc and self.fifos[b]:
        return self.fifos[b][0][(a-b-0x1b0)//4]
    return super().read(ctx,a)

  def write(self, ctx, a, v):
    for b in BASES:
      if a==self.ignore:
        break
      if a==b:
        self.values[b+4] = (v&1) if self.can_stuck!='enter' else 0
        if not v&1 and self.values.get(b+28,0)&1023==89:
          # Undriven White SWCAN RX needs its historical recessive bias.
          pin=12 if b==BASES[1] else 3
          if self.values.get(0x4002040c,0)>>(pin*2)&3!=1:
            self.values[b+4]=1
        if self.can_stuck=='leave':
          self.values[b+4]=1
      if a==b+12:
        if v&32 and self.fifos[b]:
          self.fifos[b].popleft()
        self.values[a] = self.values.get(a,0)&~(v&16)
        self.writes.append((a,v))
        return
      if a==b+8:
        self.values[a]&=~(v&0xff)
        self.writes.append((a,v))
        return
      if a==b+0x180 and v&1:
        self.values[b+8]&=~(1<<26)
    super().write(ctx,a,v)

  def inject(self, controller, address=0x123, data=b'abcdefgh', flags=0, dlc=None):
    b=BASES[controller-1]
    if self.fifo_depth is not None and len(self.fifos[b])>=self.fifo_depth:
      self.values[b+12]=self.values.get(b+12,0)|16
      self.lost[controller-1]+=1
      return
    rir=(address<<3|4) if flags&1 else address<<21
    if flags&2:
      rir|=2
    payload=data.ljust(8,b'\0')
    self.fifos[BASES[controller-1]].append((rir,len(data) if dlc is None else dlc,
      int.from_bytes(payload[:4],'little'),int.from_bytes(payload[4:],'little')))

  def complete(self, controller, result=3):
    self.values[BASES[controller-1]+8]=(1<<26)|result


@pytest.fixture(scope='module')
def lib(tmp_path_factory):
  own=Path(__file__).parent/'firmware'
  out=tmp_path_factory.mktemp('can')/'can.so'
  subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fanalyzer','-shared','-fPIC',
                  *(str(own/(n+'.c')) for n in ('white_can','white_clock','white_watchdog','white_board','status_led')),
                  '-o',str(out)],check=True)
  dll=C.CDLL(str(out))
  from openpilot.tools.volt_gateway.test_white_startup import Startup
  for name,args in [('vgw_white_quiesce',[C.POINTER(IO)]),
                    ('vgw_white_clock_init',[C.POINTER(Clock),C.POINTER(Startup)]),
                    ('vgw_white_can_init',[C.POINTER(Can),C.POINTER(Clock),C.POINTER(Config)]),
                    ('vgw_white_can_poll',[C.POINTER(Can),RECEIVE,C.c_void_p]),
                    ('vgw_white_can_enable_rx',[C.POINTER(Can)]),
                    ('vgw_white_can_response',[C.POINTER(Can),C.c_void_p])]:
    getattr(dll,name).argtypes=args
    getattr(dll,name).restype=C.c_bool
  dll.vgw_white_can_rx_irq.argtypes=[C.POINTER(Can),C.c_uint]
  dll.vgw_white_can_rx_irq.restype=None
  return dll


def start(lib, swcan=3, hscan=3, backhaul=0, hw=None):
  hw=hw or CanHardware()
  assert lib.vgw_white_quiesce(C.byref(hw.io))
  h,s,c,ok=setup(lib,hw)
  assert ok
  state=Can()
  cfg=Config(swcan,hscan,backhaul,0x600)  # synthetic bench ID, NOT vehicle approval
  ok=lib.vgw_white_can_init(C.byref(state),C.byref(c),C.byref(cfg))
  return h,s,c,state,ok


def poll(lib, state):
  frames=[]
  def receive(_, f):
    frames.append((f.contents.bus,f.contents.address,f.contents.flags,f.contents.dlc,
                   bytes(f.contents.data),f.contents.timestamp,f.contents.sequence))
  return lib.vgw_white_can_poll(C.byref(state),RECEIVE(receive),None),frames


@pytest.mark.parametrize('swcan,hscan',[(2,1),(2,4),(2,5),(3,1),(3,2),(3,3)])
def test_mux_filters_silent_and_bounded_receive(lib,swcan,hscan):
  h,s,c,state,ok=start(lib,swcan,hscan)
  assert ok
  assert h.values[0x40020414]&0xc000==0xc000
  assert h.values[BASES[0]+0x200]==0x2a1c0e00
  assert h.values[BASES[2]+0x200]==0x2a1c0e00  # reserved fields retained, FINIT cleared
  assert not any(BASES[1]+0x200<=a<BASES[1]+0x300 for a,_ in h.writes)
  for ctrl,b in enumerate(BASES,1):
    if ctrl==swcan or hscan&(1<<(ctrl-1)):
      # Independent F413 pin table, not inferred from the driver's choices.
      pins=([(0x40020400,8,8),(0x40020400,9,8)] if ctrl==1 else
            [(0x40020400,12,9),(0x40020400,13,9)] if ctrl==2 and ctrl==swcan else
            [(0x40020400,5,9),(0x40020400,6,9)] if ctrl==2 else
            [(0x40020400,3,11),(0x40020400,4,11)] if ctrl==swcan else
            [(0x40020000,8,11),(0x40020000,15,11)])
      for port,pin,af in pins:
        assert h.values[port+32+4*(pin//8)]>>(4*(pin%8))&15==af
        assert h.values[port+12]>>(2*pin)&3==int(ctrl==swcan and pin==pins[0][1])
      assert h.values[b+28]&0x80000000
      h.inject(ctrl,0x10734099,b'abcdefgh',1)
      h.inject(ctrl,0x123,b'\x01\x02')
      h.inject(ctrl,0x234,b'xxxx',2)
  result,frames=poll(lib,state)
  assert result and len(frames)==3*(hscan.bit_count()+1)
  assert any(f[:5]==(3,0x10734099,1,8,b'abcdefgh') for f in frames)
  assert all(f[4]==bytes(8) for f in frames if f[2]&2)
  assert not lib.vgw_white_can_response(C.byref(state),b'12345678')
  assert not any(a in {b+0x180 for b in BASES} for a,_ in h.writes)


@pytest.mark.parametrize('stuck',['enter','leave'])
def test_initialization_timeout_stops_phys(lib,stuck):
  h,s,c,state,ok=start(lib,hw=CanHardware(stuck=stuck))
  assert not ok and state.failed and not state.ready
  assert h.values[0x40023820]&(7<<25)==7<<25
  assert h.values[0x40020414]&0xc000==0


@pytest.mark.parametrize('offset',[0,20,28,0x200,0x20c,0x21c])
def test_rejected_can_register_writes(lib,offset):
  h=CanHardware(ignore=BASES[0]+offset)
  h.values[BASES[0]+offset]=0xffffffff
  *_,state,ok=start(lib,hw=h)
  assert not ok and state.failed


def test_flood_budget_and_overflow(lib):
  h,s,c,state,ok=start(lib,backhaul=2,hw=CanHardware(fifo_depth=3))
  assert ok
  for _ in range(30):
    h.inject(2)
  h.values[BASES[1]+12]=16
  good,frames=poll(lib,state)
  assert good and len(frames)==3 and state.stats[1].received==3 and h.lost[1]==27
  assert state.stats[1].overflow==1 and state.tx_inhibited
  assert not lib.vgw_white_can_response(C.byref(state),b'12345678')


@pytest.mark.parametrize('interrupts',[False,True])
def test_real_three_frame_fifo_during_delayed_processing(lib,interrupts):
  h,s,c,state,ok=start(lib,hw=CanHardware(fifo_depth=3))
  assert ok and lib.vgw_white_can_enable_rx(C.byref(state))
  for i in range(24):
    h.values[0x40000024]+=1
    for controller in (1,2,3):
      h.inject(controller,address=0x100+i)
      if interrupts:
        lib.vgw_white_can_rx_irq(C.byref(state),controller)
  frames=[]
  for _ in range(4):
    good,part=poll(lib,state)
    assert good
    frames+=part
  assert len(frames)==(72 if interrupts else 9)
  assert h.lost==([0,0,0] if interrupts else [21,21,21])
  for index,bus in enumerate((0,1,3)):
    selected=[f for f in frames if f[0]==bus]
    assert [f[1] for f in selected]==list(range(0x100,0x100+len(selected)))
    assert state.stats[index].software_drops==0
    assert state.stats[index].irq_calls==(24 if interrupts else 0)
    assert state.stats[index].overflow==(0 if interrupts else 1)
    assert all(a[5]<=b[5] for a,b in zip(selected,selected[1:],strict=False))
  assert not any(a in {b+0x180 for b in BASES} for a,_ in h.writes)


def test_irq_ring_bounded_drop_new_and_bus_isolation(lib):
  h,s,c,state,ok=start(lib,hw=CanHardware(fifo_depth=3),backhaul=2)
  assert ok and lib.vgw_white_can_enable_rx(C.byref(state))
  for i in range(100):
    h.inject(1,address=i)
    lib.vgw_white_can_rx_irq(C.byref(state),1)
  h.inject(3,address=0x123)
  lib.vgw_white_can_rx_irq(C.byref(state),3)
  assert list(state.rx_count)==[64,0,1]
  assert state.stats[0].software_drops==36 and state.stats[0].queue_peak==64
  assert state.stats[0].overflow==0 and state.tx_inhibited
  good,frames=poll(lib,state)
  assert good and [f[1] for f in frames if f[0]==0]==list(range(8))
  assert [f[1] for f in frames if f[0]==3]==[0x123]
  assert not lib.vgw_white_can_response(C.byref(state),b'12345678')


def test_irq_ring_wrap_preserves_order_and_payload(lib):
  h,s,c,state,ok=start(lib,hw=CanHardware(fifo_depth=3))
  assert ok and lib.vgw_white_can_enable_rx(C.byref(state))
  delivered=[]
  for batch in range(40):
    for offset in range(8):
      number=batch*8+offset
      h.inject(2,address=number,data=number.to_bytes(8,'little'))
      lib.vgw_white_can_rx_irq(C.byref(state),2)
    good,frames=poll(lib,state)
    assert good
    delivered+=frames
  assert [f[1] for f in delivered]==list(range(320))
  assert [int.from_bytes(f[4],'little') for f in delivered]==list(range(320))
  assert state.stats[1].software_drops==state.stats[1].overflow==0
  assert state.stats[1].queue_peak==8 and list(state.rx_count)==[0,0,0]


def test_two_busy_hscan_and_swcan_during_five_ms_foreground_stalls(lib):
  h,s,c,state,ok=start(lib,hw=CanHardware(fifo_depth=3))
  assert ok and lib.vgw_white_can_enable_rx(C.byref(state))
  delivered=[]
  offered=[0,0,0]
  origin=h.values[0x40000024]
  # Deterministic event schedule, not a CPU-cycle/WCET claim: 8-byte HSCAN
  # frames every 300 us on each bus; extended SWCAN frames every 6 ms.
  # Interrupt service runs between arrivals, foreground pauses up to 5 ms.
  for tick in range(600):
    h.values[0x40000024]=origin+tick//10
    for controller,period in ((1,3),(2,3),(3,60)):
      if tick%period==0:
        h.inject(controller,address=offered[controller-1])
        offered[controller-1]+=1
        lib.vgw_white_can_rx_irq(C.byref(state),controller)
    if tick%50==49:
      for _ in range(3):
        good,part=poll(lib,state)
        assert good
        delivered+=part
  for _ in range(8):
    good,part=poll(lib,state)
    assert good
    delivered+=part
  assert [sum(f[0]==b for f in delivered) for b in (0,1,3)]==offered==[200,200,10]
  assert h.lost==[0,0,0]
  assert all(v.software_drops==v.overflow==0 for v in state.stats)
  assert all(v.queue_peak<=17 for v in state.stats)


def test_only_fixed_response_id_one_mailbox_and_rate_limit(lib):
  h,s,c,state,ok=start(lib,backhaul=2)
  assert ok and poll(lib,state)[0]
  for _ in range(2):
    assert lib.vgw_white_can_response(C.byref(state),b'12345678')
    assert not lib.vgw_white_can_response(C.byref(state),b'12345678')
    h.complete(2)
    assert poll(lib,state)[0]
  assert not lib.vgw_white_can_response(C.byref(state),b'12345678')
  h.values[0x40000024]+=10
  assert poll(lib,state)[0]
  assert lib.vgw_white_can_response(C.byref(state),b'12345678')
  assert state.stats[1].transmitted==2
  tx=[(a,v) for a,v in h.writes if a in {b+0x180 for b in BASES}]
  assert tx==[(BASES[1]+0x180,(0x600<<21)|1)]*3
  assert h.values[BASES[1]]==24  # no auto-retry or bus-off recovery
  assert h.values[BASES[2]+28]&0x80000000


@pytest.mark.parametrize('fault',['busoff','passive','clock','btr','stale','pending'])
def test_runtime_fault_quiesces_all_can(lib,fault):
  h,s,c,state,ok=start(lib,backhaul=2)
  assert ok
  if fault in ('busoff','passive'):
    h.values[BASES[1]+24]=4 if fault=='busoff' else 2
  elif fault=='clock':
    h.values[0x40023804]=0
  elif fault=='btr':
    h.values[BASES[2]+28]=0
  elif fault=='stale':
    h.values[0x40000024]+=251
  else:
    assert lib.vgw_white_can_response(C.byref(state),b'12345678')
    h.values[0x40000024]+=21
  assert not poll(lib,state)[0] and state.failed
  assert h.values[0x40020414]&0xc000==0
  assert h.values[0x40023820]&(7<<25)==7<<25


def test_busy_window_drops_tx_no_backlog(lib):
  h,s,c,state,ok=start(lib,backhaul=2)
  assert ok
  for _ in range(16):
    h.inject(2)
  assert poll(lib,state)[0] and poll(lib,state)[0]
  assert not lib.vgw_white_can_response(C.byref(state),b'12345678')
  h.values[0x40000024]+=10
  assert poll(lib,state)[0]
  assert lib.vgw_white_can_response(C.byref(state),b'12345678')


@pytest.mark.parametrize('cfg',[(1,2,2),(2,2,0),(3,0,0),(3,3,3),(4,1,1),(3,8,0)])
def test_invalid_topology_rejected(lib,cfg):
  h,s,c,state,ok=start(lib,*cfg)
  assert not ok and not state.ready
  assert not any(a in {b+0x180 for b in BASES} for a,_ in h.writes)


@pytest.mark.parametrize('field,value',[('swcan',1),('hscan',7),('backhaul',3),('response',0x123)])
def test_corrupted_configuration_never_changes_tx_target(lib,field,value):
  h,s,c,state,ok=start(lib,backhaul=2)
  assert ok
  setattr(state.config,field,value)
  assert not lib.vgw_white_can_response(C.byref(state),b'12345678')
  assert state.failed
  assert not any(a in {b+0x180 for b in BASES} for a,_ in h.writes)


@pytest.mark.parametrize('offset',[0x184,0x188,0x18c])
def test_bad_mailbox_write_never_sets_tx_request(lib,offset):
  h,s,c,state,ok=start(lib,backhaul=2)
  assert ok
  h.ignore=BASES[1]+offset
  assert not lib.vgw_white_can_response(C.byref(state),b'12345678') and state.failed
  assert not any(a==BASES[1]+0x180 for a,_ in h.writes)
