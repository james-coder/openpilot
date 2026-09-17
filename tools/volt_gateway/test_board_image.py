"""Reset/MCUboot/actual application execution. Peripherals remain modeled."""
import hashlib
import os
from pathlib import Path
import struct
import time
import zlib

from Crypto.PublicKey import ECC
from elftools.elf.elffile import ELFFile
import pytest
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE, UC_HOOK_BLOCK, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE
from unicorn.arm_const import (UC_CPU_ARM_CORTEX_M4, UC_ARM_REG_SP, UC_ARM_REG_PC, UC_ARM_REG_LR,
                              UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_PRIMASK)

from openpilot.tools.volt_gateway import bench_image, board_build, boot_image, provisioning, target_crypto
from openpilot.tools.volt_gateway.test_white_can import BASES


@pytest.fixture(scope='module')
def images(tmp_path_factory):
  checkout=os.environ.get('VOLTGW_MCUBOOT_CHECKOUT')
  if not checkout or not target_crypto.archive_path().is_file():
    pytest.skip('pinned dependencies absent; full image execution NOT validated')
  out=tmp_path_factory.mktemp('board')/'build'
  repository=Path(os.environ.get('VOLTGW_PANDA_HISTORY','/home/james/diagnostics/volt-gateway/history/vntarasov-panda.git'))
  if not repository.is_dir():
    pytest.skip('historical White USB source absent; full USB image gate NOT passed')
  board_build.build(Path(checkout),target_crypto.archive_path(),out,usb_repository=repository)
  return out


def binary(path):
  with path.open('rb') as stream:
    elf=ELFFile(stream)
    segments=[(s['p_paddr'],s.data()) for s in elf.iter_segments() if s['p_type']=='PT_LOAD' and s['p_filesz']]
    symbols={s.name:s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()}
    start=elf.get_section_by_name('.vectors')['sh_addr']
  segments=[(max(a,start),d[max(0,start-a):]) for a,d in segments if a+len(d)>start]
  end=max(a+len(d) for a,d in segments)
  payload=bytearray(b'\xff'*(end-start))
  for address,data in segments:
    payload[address-start:address-start+len(data)]=data
  return start,bytes(payload),symbols


@pytest.mark.parametrize('slot',[0,1])
def test_reset_signed_boot_application_confirm(images,slot):
  run(images,slot)


@pytest.mark.parametrize('slot',[0,1])
def test_factory_confirmed_image_boots_on_usb_without_vehicle_or_flash_writes(images,slot):
  run(images,slot,confirmed=True)


def test_both_images_invalid_enters_actual_standalone_recovery(images):
  run(images,None)


@pytest.mark.parametrize('slot', [0, 1])
def test_busy_buses_through_entire_confirmed_cold_boot(images, slot):
  run(images, slot, confirmed=True, busy_traffic=True)


def test_software_recovery_enters_rom_before_can_watchdog_or_usb_window(images):
  run(images,0,rom_recovery=True)


def test_actual_application_recovery_without_vehicle_or_update_power(images):
  cpu,symbols=run(images,0,confirmed=True)
  def read(address):
    return struct.unpack('<I',cpu.mem_read(address,4))[0]
  def call(name,arg=0):
    cpu.reg_write(UC_ARM_REG_SP,0x2001e000)
    cpu.reg_write(UC_ARM_REG_LR,0x2001fff1)
    cpu.reg_write(UC_ARM_REG_R0,arg)
    cpu.emu_start(symbols[name]|1,0x2001fff0,timeout=2_000_000,count=2_000_000)
    assert cpu.reg_read(UC_ARM_REG_PC)==0x2001fff0
    return cpu.reg_read(UC_ARM_REG_R0)
  assert call('request_usb_recovery')==1
  assert call('request_usb_recovery')==0
  assert read(0x2001bfe0)==0
  mmio=call('vgw_white_physical_mmio')
  call('service_usb_recovery',mmio)
  assert read(0x2001bfe0)==0  # acknowledgement grace period precedes reset
  cpu.mem_write(0x40000024,struct.pack('<I',read(0x40000024)+251))
  with pytest.raises(AssertionError,match='unexpected board reset'):
    call('service_usb_recovery',mmio)
  assert read(0x2001bfe0)==0x56524732
  assert read(0x2001bfe4)==0x56524732^0xffffffff
  assert read(0x40020414)&0xc000==0
  assert read(0x40020814)&0x2002==0x2002
  assert read(0x40020014)&1==1


def run(images,slot,*,confirmed=False,busy_traffic=False,rom_recovery=False):
  loader_start,loader,ls=binary(images/'NOT_RELEASED-loader.elf')
  start,application,app=binary(images/f'NOT_RELEASED-{"A" if slot==0 else "B"}.elf')
  key=ECC.generate(curve='P-256')  # ephemeral emulator fixture only
  device=b'test-device!'
  layout=hashlib.sha256(b'board model layout').digest()
  policy=hashlib.sha256(b'NOT vehicle IDs 600/601').digest()
  build=hashlib.sha256(application).digest()
  config=(b'VGWCFG1\0'+device+layout+policy+build+b'p'*32+key.public_key().export_key(format='DER')+
          bytes([3,3,2,0,0])+struct.pack('>HHI',0x600,0x601,8862))
  assert len(config)==252
  config+=struct.pack('>I',zlib.crc32(config))
  signed=boot_image.sign(key,application,device,layout,slot,1) if slot is not None else b''
  flash=bytearray(b'\xff'*0x100000)
  flash[:len(loader)]=loader
  flash[0x20000:0x20100]=config
  off=boot_image.SLOTS[slot or 0]
  if slot is not None:
    flash[off:off+len(signed)]=signed
    flash[off+boot_image.SLOT_SIZE-16:off+boot_image.SLOT_SIZE]=boot_image.MAGIC
    if confirmed:
      flash[off+boot_image.SLOT_SIZE-32]=1
      flash[off+boot_image.SLOT_SIZE-24]=1
  else:
    app=ls
  if confirmed:
    # Execute the actual factory packager output, not a separately assembled
    # lookalike. Both slots are signed/confirmed and CAN is listen-only.
    record,_=provisioning.passive_record(device,b'p'*32,key.public_key().export_key(format='DER'),swcan=3,divider=8862)
    parts=[]
    for name,origin in [('loader',0x08000000),('A',0x08040200),('B',0x080a0200)]:
      raw=(images/f'NOT_RELEASED-{name}.elf').read_bytes()
      parts.append(bench_image.payload(raw,origin,0x20000 if name=='loader' else 0x5fa00))
    flash=bytearray(bench_image.assemble(parts[0],tuple(parts[1:]),record,key))
    if slot==1:
      flash[boot_image.SLOTS[0]:boot_image.SLOTS[0]+4]=bytes(4)
  cpu=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
  cpu.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M4)
  for base,size in [(0x08000000,0x100000),(0x20000000,0x20000),(0x40000000,0x100000),
                    (0x50000000,0x20000),(0x50060000,0x1000),(0x1fff0000,0x8000),(0xe000e000,0x2000),(0xe0042000,0x1000)]:
    cpu.mem_map(base,size)
  cpu.mem_write(0x08000000,bytes(flash))
  cpu.mem_write(0x20000000,b'\xa5'*0x20000)
  cpu.mem_write(0x1fff7a10,device)
  def read(a):
    return struct.unpack('<I',cpu.mem_read(a,4))[0]
  def put(a,v):
    cpu.mem_write(a,struct.pack('<I',v))
  put(0xe0042000,0x10006463)  # actual labeled Panda DBGMCU_IDCODE readback
  cpu.mem_write(0x1fff7a22,struct.pack('<H',1024))
  for a,v in [(0x40023800,3),(0x40023874,3),(0x40007004,0x4000),(0x50060804,1),(0x40023c10,0x80000000)]:
    put(a,v)
  if rom_recovery:
    put(0x2001bfe0,0x56524732)
    put(0x2001bfe4,0x56524732^0xffffffff)
    put(0x40023874,1<<28)
    put(0x1fff0000,0x2001f000)
    put(0x1fff0004,0x1fff0101)
  for b in (BASES[0],BASES[2]):
    put(b+0x200,0x2a1c0e01)
  queue=[]
  queues={b: ([] if b != BASES[0] else queue) for b in BASES}
  overflow=dict.fromkeys(BASES,0)
  traffic={'bytes': 0, 'ms': 0, 'interrupt': False, 'in_irq': False, 'skip': False, 'offered': 0}
  can_instances=set()
  rng=[0]
  busy=[0]
  guard_hooks=[]
  mutations=[]
  entered=[]
  complete=[]
  if rom_recovery:
    def reached_rom(machine,address,size,data):
      complete.append('ROM')
      cpu.emu_stop()
    cpu.hook_add(UC_HOOK_CODE,reached_rom,begin=0x1fff0100,end=0x1fff0100)
    def unexpected_usb_init(*args):
      raise AssertionError('software recovery must not wait for cold USB enumeration')
    usb_init=ls['vgw_white_usb_init']&~1
    cpu.hook_add(UC_HOOK_CODE,unexpected_usb_init,begin=usb_init,end=usb_init)
  watchdog=[]
  # Independent LSI-domain visibility, including a ROM-inherited prescaler.
  # These are bounded fault/timing scenarios, not cycle-accurate silicon.
  iwdg_values={0x40003004:6,0x40003008:4095}
  iwdg_pending={}
  iwdg_busy=[3]
  usb_window=[True]
  def check_rx_loss():
    assert can_instances
    for pointer in can_instances:
      # ARM ABI: pointer + 8-byte config, then three 72-byte stats records.
      for i in range(3):
        stats=struct.unpack('<18I',cpu.mem_read(pointer+12+72*i,72))
        assert stats[2]==0, ('hardware FIFO loss', i, stats)
        assert stats[14]==0, ('software RX loss during boot', i, stats)
  def runtime_init(machine,address,size,data):
    usb_window[0]=False
    can_instances.clear()
  for symbols in (ls,app):
    a=symbols['vgw_white_runtime_init']&~1
    cpu.hook_add(UC_HOOK_CODE,runtime_init,begin=a,end=a)
  if busy_traffic:
    def handoff_check(machine,address,size,data):
      check_rx_loss()
    a=ls['vgw_white_physical_handoff']&~1
    cpu.hook_add(UC_HOOK_CODE,handoff_check,begin=a,end=a)
  def forbid(*args):
    raise AssertionError('flash accessed while busy in whole-image execution')
  def on_poll(machine,address,size,data):
    can_instances.add(cpu.reg_read(UC_ARM_REG_R0))
    if busy_traffic:
      return
    t=read(0x40000024)+1
    put(0x40000024,t)
    if not confirmed and t%10==0 and not queue:
      for identity,payload in [(1001,bytes(8)),(309,bytes(8)),(497,b'\2'+bytes(7))]:
        low,high=struct.unpack('<II',payload)
        queue.append((identity<<21,8,low,high))
  for symbols in (ls,app):
    a=symbols['vgw_white_can_poll']&~1
    cpu.hook_add(UC_HOOK_CODE,on_poll,begin=a,end=a)
  if busy_traffic:
    def fatal_entry(machine,address,size,data):
      raise AssertionError(f'boot fatal loop at {address:#x}, timer={read(0x40000024)}, '+
                           f'traffic={traffic}, phase={read(0x2001c804):#x}')
    for symbols in (ls,app):
      for name in ('vgw_crypto_panic', 'fatal'):
        if name in symbols:
          a=symbols[name]&~1
          cpu.hook_add(UC_HOOK_CODE,fatal_entry,begin=a,end=a)
  def app_init(machine,address,size,data):
    entered.append(True)
  a=app['vgw_application_init']&~1
  cpu.hook_add(UC_HOOK_CODE,app_init,begin=a,end=a)
  def usb_init(machine,address,size,data):
    if not cpu.reg_read(UC_ARM_REG_R1):
      assert entered, 'USB connected before application/crypto initialization could service enumeration'
  for symbols in (ls,app):
    a=symbols['vgw_white_usb_init']&~1
    cpu.hook_add(UC_HOOK_CODE,usb_init,begin=a,end=a)
  def running(machine,address,size,data):
    if entered:
      complete.append(True)
      machine.emu_stop()
  a=app['vgw_white_runtime_step']&~1
  cpu.hook_add(UC_HOOK_CODE,running,begin=a,end=a)
  def on_read(machine,access,a,size,value,data):
    if busy_traffic:
      for b in BASES:
        if a == b+12:
          put(a, len(queues[b]) | overflow[b])
          return
        if b+0x1b0 <= a <= b+0x1bc and queues[b]:
          put(a, queues[b][0][(a-b-0x1b0)//4])
          return
    if a==0x4000300c:
      put(a,3 if iwdg_busy[0] else 0)
      iwdg_busy[0]=max(0,iwdg_busy[0]-1)
    elif a in iwdg_values:
      if a in iwdg_pending:
        requested,remaining=iwdg_pending[a]
        if remaining:
          iwdg_pending[a]=(requested,remaining-1)
        else:
          iwdg_values[a]=requested
          del iwdg_pending[a]
      put(a,iwdg_values[a])
    elif a==0x40000024 and usb_window[0]:
      put(a,read(a)+1)
    elif a==0x50000010:
      put(a,0x80000000)  # AHB idle, bounded reset/flush completion
    elif a==0x40023800:
      v=read(a)
      put(a,(v|2|((v&(1<<16))<<1)|((v&(1<<24))<<1)) if v&(1<<24) else (v|2|((v&(1<<16))<<1))&~(1<<25))
    elif a==0x40023808:
      v=read(a)
      put(a,(v&~12)|((v&3)<<2))
    elif a==0x50060808:
      rng[0]+=1
      put(a,0x10203040+rng[0])
    elif a==0x40012000:
      if read(0x40012008)&(1<<22):
        put(a,4)
        put(0x4001203c,539 if confirmed else 1523)
    elif a==BASES[0]+12:
      put(a,len(queue))
    elif BASES[0]+0x1b0<=a<=BASES[0]+0x1bc:
      if queue:
        put(a,queue[0][(a-BASES[0]-0x1b0)//4])
    elif a==0x40023c0c and busy[0]:
      busy[0]-=1
      if not busy[0]:
        put(a,0)
        for hook in guard_hooks:
          cpu.hook_del(hook)
        guard_hooks.clear()
  unlock=[]
  def on_write(machine,access,a,size,value,data):
    if busy_traffic:
      if a == 0x40023820:
        # Peripheral reset during loader/application handoff clears pending
        # FIFO contents, overflow flags and interrupt enables on real bxCAN.
        # Do not carry a synthetic enabled receiver across that reset.
        for i,b in enumerate(BASES):
          if value & (1 << (25+i)):
            queues[b].clear()
            overflow[b]=0
            put(b+12,0)
            put(b+20,0)
      for b in BASES:
        if a == b+12:
          if value & 32 and queues[b]:
            queues[b].pop(0)
          overflow[b] &= ~(value & 16)
          return
    if a in iwdg_values:
      assert not iwdg_busy[0], 'watchdog written while LSI update busy'
      iwdg_pending[a]=(value,3)
    if a==0x50000014:
      put(a,read(a)&~value)
    if a==0x40023800 and not value&(1<<24) and read(0x50060800)&4:
      put(0x50060804,read(0x50060804)|0x22)  # running RNG loses PLL48 clock
    if a in (0x40020018,0x40020418,0x40020818):
      put(a-4,(read(a-4)|(value&0xffff))&~(value>>16))
    if a in BASES:
      put(a+4,value&1)
      put(a+8,1<<26)
    if a==BASES[0]+12 and value&32 and queue:
      queue.pop(0)
    if a in {b+0x180 for b in BASES}:
      raise AssertionError('unsolicited CAN transmission during boot')
    if a==0x40003000 and value==0xaaaa:
      watchdog.append(read(0x40000024))
    if a==0x40023c04:
      unlock.append(value)
      if unlock[-2:]==[0x45670123,0xcdef89ab]:
        put(0x40023c10,0)
    if a==0x40023c0c:
      put(a,read(a)&~value)
    if a==0xe000ed0c:
      raise AssertionError(f'unexpected board reset at PC={cpu.reg_read(UC_ARM_REG_PC):#x}, '+
                           f'timer={read(0x40000024)}, traffic={traffic}, '+
                           f'stats={[struct.unpack("<54I",cpu.mem_read(p+12,216)) for p in can_instances]}')
  def flash_write(machine,access,a,size,value,data):
    assert off+0x08000000+boot_image.SLOT_SIZE-64<=a<off+0x08000000+boot_image.SLOT_SIZE
    assert size==1 and read(0x40023c10)==1
    assert read(0x40023c0c)&0x10000==0
    mutations.append(a)
    put(0x40023c0c,0x10000)
    busy[0]=2
    guard_hooks.append(cpu.hook_add(UC_HOOK_CODE,forbid,begin=0x08000000,end=0x080fffff))
    guard_hooks.append(cpu.hook_add(UC_HOOK_MEM_READ,forbid,begin=0x08000000,end=0x080fffff))
  cpu.hook_add(UC_HOOK_MEM_READ,on_read,begin=0x40000000,end=0x5006ffff)
  cpu.hook_add(UC_HOOK_MEM_WRITE,on_write,begin=0x40000000,end=0x400fffff)
  cpu.hook_add(UC_HOOK_MEM_WRITE,on_write,begin=0x50000000,end=0x5001ffff)
  cpu.hook_add(UC_HOOK_MEM_WRITE,on_write,begin=0xe000ed0c,end=0xe000ed0c)
  cpu.hook_add(UC_HOOK_MEM_WRITE,flash_write,begin=0x08000000,end=0x080fffff)
  def traffic_block(machine, address, size, data):
    if traffic['in_irq'] or usb_window[0]:
      return
    if traffic['skip']:
      traffic['skip']=False
      return
    # Synthetic execution-to-time stress scale, NOT target cycle timing.
    # Arrivals continue during crypto, independent of cooperative callbacks.
    traffic['bytes'] += size
    if traffic['bytes'] >= 32000:
      traffic['bytes'] -= 32000
      traffic['ms'] += 1
      put(0x40000024,read(0x40000024)+1)
      for i,b in enumerate(BASES):
        if read(b+20) != 2:
          continue
        count=(3,1,int(traffic['ms'] % 10 == 0))[i]
        for _ in range(count):
          traffic['offered'] += 1
          if len(queues[b]) == 3:
            overflow[b] = 16
          else:
            queues[b].append(((0x120+i)<<21,8,0,0))
    if any(queues[b] for b in BASES) and not cpu.reg_read(UC_ARM_REG_PRIMASK) and any(queues[b] and read(b+20)==2 for b in BASES):
      traffic['interrupt']=True
      traffic['skip']=True
      cpu.emu_stop()
  if busy_traffic:
    cpu.hook_add(UC_HOOK_BLOCK,traffic_block)
  sp,entry=struct.unpack_from('<II',loader)
  cpu.reg_write(UC_ARM_REG_SP,sp)
  try:
    # Explicitly execute the linked C IRQ handlers with register preservation.
    # NVIC entry latency, flash wait states and IRQ execution time are not
    # modeled; physical validation remains mandatory.
    deadline=time.monotonic()+240
    for _ in range(100000 if busy_traffic else 1):
      assert time.monotonic()<deadline, f'busy boot wall-time bound: {traffic}, phase={read(0x2001c804):#x}'
      cpu.emu_start(entry,0,timeout=60_000_000,count=300_000_000)
      if not traffic['interrupt']:
        break
      traffic['interrupt']=False
      traffic['in_irq']=True
      saved=cpu.context_save()
      symbols=app if read(0xe000ed08)==start else ls
      for i,b in enumerate(BASES):
        if queues[b] and read(b+20)==2:
          cpu.reg_write(UC_ARM_REG_SP,(cpu.reg_read(UC_ARM_REG_SP)-128)&~7)
          cpu.reg_write(UC_ARM_REG_LR,0x2001fff1)
          cpu.emu_start(symbols[f'vgw_can{i+1}_rx0']|1,0x2001fff0,count=100000)
          assert cpu.reg_read(UC_ARM_REG_PC)==0x2001fff0
          cpu.context_restore(saved)
      traffic['in_irq']=False
      entry=cpu.reg_read(UC_ARM_REG_PC)|1
  except Exception as error:
    raise AssertionError(f'whole board execution failed at PC={cpu.reg_read(UC_ARM_REG_PC):#x}, '+
                         f'phase={read(0x2001c804):#x}') from error
  assert complete, f'whole board instruction/time limit: PC={cpu.reg_read(UC_ARM_REG_PC):#x}'
  if rom_recovery:
    assert complete==['ROM'] and not watchdog and not mutations and not can_instances
    assert read(0x2001bfe0)==read(0x2001bfe4)==0
    assert read(0x40020414)&0xc000==0  # SWCAN PHY asleep
    assert read(0x40020814)&0x2002==0x2002  # CAN1/CAN2 PHY disabled
    assert read(0x40020014)&1==1  # CAN3 differential PHY disabled
    assert read(0x40020014)&(1<<13)  # USB client switch must not depend on enumeration
    assert read(0x40020414)&4==0
    assert read(0x40020000)&(3<<26)==1<<26
    assert read(0x40020400)&(3<<4)==1<<4
    assert read(0x2001bfc4)==7  # fixed ROM branch reached
    assert read(0x40023820)&(7<<25)==7<<25
    assert cpu.reg_read(UC_ARM_REG_SP)==0x2001f000
    assert read(0xe000ed08)==0x1fff0000
    return cpu,ls
  if busy_traffic:
    assert traffic['offered'] > 1000
    check_rx_loss()
  marker,phase,inverse,end=struct.unpack('<4I',cpu.mem_read(0x2001c800,16))
  assert (marker,end)==(0x56474231,0x31424756) and phase^inverse==0xffffffff
  assert phase==(13|((slot+1)<<16) if slot is not None else 13)
  assert len(mutations)==(2 if slot is not None and not confirmed else 0)
  assert read(0xe000ed08)==(start if slot is not None else loader_start)
  assert len(watchdog)>0
  # Actual linked F413 vectors, not an assumed family IRQ numbering. USB stays
  # polling-only even though only the fixed CAN RX0 IRQs are now enabled.
  vector_base=start if slot is not None else loader_start
  for number,name in ((20,'vgw_can1_rx0'),(64,'vgw_can2_rx0'),(75,'vgw_can3_rx0')):
    assert 0x20000000<=app[name]<0x2001c000
    assert read(vector_base+4*(16+number))==app[name]|1
  assert read(0xe000e100)==1<<20
  assert read(0xe000e108)==(1<<0)|(1<<11)
  for b in BASES:
    assert read(b+20)==(2 if b in (BASES[0],BASES[1],BASES[2]) else 0)
  if slot is not None:
    assert cpu.mem_read(off+0x08000000+boot_image.SLOT_SIZE-32,1)==b'\1'
    assert cpu.mem_read(off+0x08000000+boot_image.SLOT_SIZE-24,1)==b'\1'
  return cpu,app


def test_actual_usb_setup_bulk_bounds_and_no_legacy_write(images):
  cpu,symbols=run(images,0)
  writes=[]
  def fifo(machine,access,address,size,value,data):
    writes.append((address,value.to_bytes(size,'little')))
  cpu.hook_add(UC_HOOK_MEM_WRITE,fifo,begin=0x50001000,end=0x50002003)
  def call(name):
    cpu.reg_write(UC_ARM_REG_SP,0x2001e000)
    cpu.reg_write(UC_ARM_REG_LR,0x2001fff1)
    cpu.emu_start(symbols[name]|1,0x2001fff0,timeout=2_000_000,count=2_000_000)
    assert cpu.reg_read(UC_ARM_REG_PC)==0x2001fff0
    return cpu.reg_read(UC_ARM_REG_R0)
  def setup(kind,request,value=0,index=0,length=64):
    writes.clear()
    cpu.mem_write(symbols['setup'],struct.pack('<BBHHH',kind,request,value,index,length))
    call('usb_setup')
    size=struct.unpack('<I',cpu.mem_read(0x50000910,4))[0]&0x7ffff
    return b''.join(data for address,data in writes if address==0x50001000)[:size]
  assert setup(0x80,6,0x0100, length=18)[8:12]==bytes.fromhex('aabbccdd')
  serial=setup(0x80,6,0x0303,length=50)
  assert serial[2:].decode('utf-16-le')==b'test-device!'.hex()
  assert setup(0xc0,0xd6)==b'voltgw-v1'
  indication=setup(0xc0,0xd7,length=7)
  assert len(indication)==7 and indication[:4]==bytes([1,1,0,0])
  assert setup(0xc0,0xd7,value=1,length=7)==b''
  assert setup(0xc0,0xd7,length=2)==indication[:2]
  assert not any(name.startswith('vgw_debug_') for name in symbols)
  cpu.mem_write(0x2001c400,b'KEEP')
  cpu.mem_write(0x2001f000,struct.pack('<4sIII',b'VGM1',1,0x2001c400,0x12345678))
  cpu.reg_write(UC_ARM_REG_R0,0x2001f000)
  cpu.reg_write(UC_ARM_REG_R1,16)
  cpu.reg_write(UC_ARM_REG_R2,1)
  call('usb_cb_ep2_out')
  assert cpu.mem_read(0x2001c400,4)==b'KEEP'  # DEBUG wire command has no authority in production
  assert setup(0xc0,0xc1)==b'\1'
  # Windows requests up to 255 bytes of BOS, whose actual size is exactly
  # one 64-byte packet. A terminating ZLP is mandatory; without it the real
  # host retries enumeration and reports FAILED_POST_START.
  bos=setup(0x80,6,0x0f00,length=255)
  assert len(bos)==64 and bos[:5]==bytes([5,15,64,0,3])
  assert cpu.mem_read(symbols['ep0_zlp'],1)==b'\1'
  def in_complete():
    cpu.mem_write(0x50000014,struct.pack('<I',1<<18))
    cpu.mem_write(0x50000908,struct.pack('<I',1))  # EP0 transfer complete
    cpu.mem_write(0x50000918,struct.pack('<I',64))  # FIFO available words
    call('usb_irqhandler')
  in_complete()
  assert struct.unpack('<I',cpu.mem_read(0x50000910,4))[0]==1<<19
  assert cpu.mem_read(symbols['ep0_zlp'],1)==b'\0'
  assert len(setup(0x80,6,0x0f00,length=64))==64
  assert cpu.mem_read(symbols['ep0_zlp'],1)==b'\0'  # exact host length: no ZLP
  config=setup(0x80,6,0x0200,length=255)
  assert len(config)==64
  writes.clear()
  in_complete()
  assert struct.unpack('<I',cpu.mem_read(0x50000910,4))[0]==(1<<19)|5
  assert b''.join(data for address,data in writes if address==0x50001000)[:5]==bytes([3,2,64,0,0])
  # A superseding SETUP must cancel continuation and its pending ZLP.
  setup(0x80,6,0x0f00,length=255)
  setup(0xc0,0xc1)
  assert cpu.mem_read(symbols['ep0_zlp'],1)==b'\0'
  # The old flash, safety, forwarding and CAN transmit vendor API is absent.
  for request in (0xb1,0xb2,0xd1,0xd8,0xdc,0xdd,0xde,0xdf,0xe5):
    assert setup(0x40,request,length=0)==b''
  setup(0,5,1,length=0)
  assert struct.unpack('<I',cpu.mem_read(0x50000910,4))[0]==1<<19  # one zero-length status packet
  setup(0,9,1,length=0)
  assert call('vgw_white_usb_owned')==1
  setup(0x40,0xb5,0x4757,0x5243,0)
  assert 'vgw_usb_recovery_window' not in symbols  # cold loader only, not linked into application
  assert cpu.mem_read(symbols['recover'],1)==b'\0'
  # Drive the actual FIFO parser, not the Python transport, with an oversized
  # bulk packet. It must fail before consuming attacker-controlled FIFO bytes.
  cpu.mem_write(0x50000014,struct.pack('<I',1<<4))
  cpu.mem_write(0x50000020,struct.pack('<I',(2<<17)|(65<<4)|2))
  call('usb_irqhandler')
  assert call('vgw_white_usb_poll')==0
