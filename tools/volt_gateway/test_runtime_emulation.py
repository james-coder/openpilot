"""Run reset -> physical MMIO -> composed runtime on Cortex-M4, not hardware."""
from pathlib import Path
import struct
import subprocess

import gcc_arm_none_eabi
import pytest
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_SP, UC_CPU_ARM_CORTEX_M4

from openpilot.tools.volt_gateway.test_white_can import BASES


def build_binary(tmp_path_factory,guard=False):
  source=Path(__file__).parent/'firmware'
  gcc=Path(gcc_arm_none_eabi.__file__).parent/'toolchain/bin/arm-none-eabi-gcc'
  out=tmp_path_factory.mktemp('runtime-guard' if guard else 'runtime-arm')/'NEVER_FLASH-runtime.elf'
  names=('runtime_emu','white_runtime','white_platform','white_startup','white_watchdog','white_clock',
         'white_rng','white_can','white_board','status_led','observe','recovery_link','recovery_transport')
  if guard:
    names+=('recovery_flash','flash_guard_emu','white_flash')
  subprocess.run([str(gcc),'-std=c11','-Wall','-Wextra','-Werror','-Os','-mcpu=cortex-m4','-mthumb',
                  '-mfloat-abi=soft','-ffreestanding','-fno-builtin','-fdata-sections','-ffunction-sections',
                  '-nostdlib','-DVGW_RUNTIME_TEST','-I',str(source),'-T',str(source/'reset_emu.ld'),
                  *(['-DVGW_FLASH_GUARD_TEST'] if guard else []),
                  '-Wl,--no-undefined,--gc-sections,--build-id=none',str(source/'white_reset.S'),
                  *(str(source/(n+'.c')) for n in names),'-o',str(out)],check=True,capture_output=True)
  return out


@pytest.fixture(scope='module')
def binary(tmp_path_factory):
  return build_binary(tmp_path_factory)


@pytest.fixture(scope='module')
def guard_binary(tmp_path_factory):
  return build_binary(tmp_path_factory,guard=True)


@pytest.mark.parametrize('fault',['none','identity','rng','can_init'])
def test_flash_only_composed_runtime(binary,fault):
  run(binary,fault)


@pytest.mark.parametrize('fault',['guard_ok','guard_unsafe','guard_stale','guard_busoff',
                                  'erase_ok','erase_readback','erase_busoff','erase_timeout',
                                  'program_ok','program_timeout'])
def test_flash_busy_no_flash_fetches_or_data_reads(guard_binary,fault):
  run(guard_binary,fault)


def run(binary,fault):
  cpu=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
  cpu.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M4)
  for base,size in [(0x08000000,0x100000),(0x20000000,0x20000),(0x40000000,0x100000),
                    (0x50060000,0x1000),(0x1fff7000,0x1000),(0xe000e000,0x2000),(0xe0042000,0x1000)]:
    cpu.mem_map(base,size)
  cpu.mem_write(0x20000000,b'\xa5'*0x20000)
  with binary.open('rb') as stream:
    elf=ELFFile(stream)
    symbols={s.name:s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()}
    for seg in elf.iter_segments():
      if seg['p_type']=='PT_LOAD' and seg['p_filesz']:
        cpu.mem_write(seg['p_paddr'],seg.data())
  def read32(a):
    return struct.unpack('<I',cpu.mem_read(a,4))[0]
  def put(a,v):
    cpu.mem_write(a,struct.pack('<I',v))
  put(0xe0042000,0 if fault=='identity' else 0x10000463)
  cpu.mem_write(0x1fff7a22,struct.pack('<H',1024))
  put(0x40023800,3)
  put(0x40023874,3)
  put(0x40007004,0x4000)
  put(0x50060804,4 if fault=='rng' else 1)
  writes=[]
  rng=[0]
  busy=[False]
  guard_seen=[]
  fifo=[False]
  unlock=[0]
  polls=[0]
  reset=[]
  put(0x40023c10,0x80000000)
  if fault.startswith('program_'):
    cpu.mem_write(0x080a0000,b'\xff'*0x20000)

  def on_write(machine,access,a,size,v,data):
    writes.append((a,v))
    if a in (0x40020018,0x40020418,0x40020818):
      put(a-4,(read32(a-4)|(v&0xffff))&~(v>>16))
    if a in BASES:
      put(a+4,0 if fault=='can_init' else v&1)
    if a==BASES[0]+12 and v&32:
      fifo[0]=False
    if fault.startswith(('erase_','program_')):
      if a==0x40023c04:
        if unlock[0]==0 and v==0x45670123:
          unlock[0]=1
        elif unlock[0]==1 and v==0xcdef89ab:
          put(0x40023c10,0)
          unlock[0]=0
      if a==0x40023c0c:
        put(a,read32(a)&~v)
      if a==0x40023c10 and v&0x10000:
        assert v&2 and (v>>3)&31==9  # 1-MiB layout: inactive B begins at sector 9
        put(0x40023c0c,1<<16)
        busy[0]=True
        guard_seen.append(True)
        if fault=='erase_busoff':
          put(BASES[0]+24,4)
      if 0x080a0000<=a<0x080a0010:
        assert size==1 and read32(0x40023c10)==1
        busy[0]=True
        polls[0]=0
        guard_seen.append(True)
        put(0x40023c0c,1<<16)
      if a==0xe000ed0c:
        reset.append(v)
        machine.emu_stop()

  def on_read(machine,access,a,size,value,data):
    if a==0x40023800:
      v=read32(a)
      put(a,(v|2|((v&(1<<16))<<1)|((v&(1<<24))<<1)) if v&(1<<24) else (v|2|((v&(1<<16))<<1))&~(1<<25))
    elif a==0x40023808:
      v=read32(a)
      put(a,(v&~12)|((v&3)<<2))
    elif a==0x50060808:
      rng[0]+=1
      put(a,rng[0])
    elif a==BASES[0]+12:
      put(a,int(fifo[0]))
    elif a==0x40023c0c and busy[0] and fault.startswith(('erase_','program_')):
      polls[0]+=1
      put(0x40000024,read32(0x40000024)+10)
      if polls[0]>=(2 if fault.startswith('program_') else 8) and not fault.endswith('_timeout'):
        busy[0]=False
        put(a,0)
        if fault.startswith('erase_'):
          cpu.mem_write(0x080a0000,b'\xff'*0x20000)
        if fault=='erase_readback':
          cpu.mem_write(0x080a0010,b'\0')

  stopped=[]
  def done(machine,address,size,data):
    stopped.append(True)
    machine.emu_stop()
  for lo,hi in [(0x40000000,0x400fffff),(0x50060000,0x50060fff)]:
    cpu.hook_add(UC_HOOK_MEM_WRITE,on_write,begin=lo,end=hi)
    cpu.hook_add(UC_HOOK_MEM_READ,on_read,begin=lo,end=hi)
  end=symbols['vgw_runtime_test_done']&~1
  cpu.hook_add(UC_HOOK_CODE,done,begin=end,end=end)
  if fault.startswith(('guard_','erase_','program_')):
    def guard_begin(machine,address,size,data):
      busy[0]=True
      guard_seen.append(True)
      put(0x40000024,50)
      put(0x40023c0c,1<<16)
      put(symbols['vgw_guard_test_mode'],{'guard_ok':0,'guard_unsafe':1,'guard_stale':2,'guard_busoff':0}[fault])
      if fault=='guard_busoff':
        put(BASES[0]+24,4)
      else:
        fifo[0]=True
        put(BASES[0]+0x1b0,0x123<<21)
        put(BASES[0]+0x1b4,8)
    def guard_end(machine,address,size,data):
      busy[0]=False
    def flash_code(machine,address,size,data):
      assert not busy[0], f'flash instruction during busy: {address:#x}'
    def flash_read(machine,access,address,size,value,data):
      assert not busy[0], f'flash data dependency during busy: {address:#x}'
    start=symbols['vgw_guard_test_begin']&~1
    end_guard=symbols['vgw_guard_test_end']&~1
    cpu.hook_add(UC_HOOK_CODE,guard_begin,begin=start,end=start)
    cpu.hook_add(UC_HOOK_CODE,guard_end,begin=end_guard,end=end_guard)
    cpu.hook_add(UC_HOOK_CODE,flash_code,begin=0x08000000,end=0x080fffff)
    cpu.hook_add(UC_HOOK_MEM_READ,flash_read,begin=0x08000000,end=0x080fffff)
    if fault.startswith(('erase_','program_')):
      def arm_erase(machine,address,size,data):
        put(symbols['vgw_guard_test_erase'],2 if fault.startswith('program_') else 1)
      guard_entry=symbols['vgw_guard_test']&~1
      cpu.hook_add(UC_HOOK_CODE,arm_erase,begin=guard_entry,end=guard_entry)
      cpu.hook_add(UC_HOOK_MEM_WRITE,on_write,begin=0xe000ed0c,end=0xe000ed0f)
      if fault.startswith('program_'):
        cpu.hook_add(UC_HOOK_MEM_WRITE,on_write,begin=0x080a0000,end=0x080bffff)
  stack,entry=struct.unpack('<II',cpu.mem_read(0x08000000,8))
  cpu.reg_write(UC_ARM_REG_SP,stack)
  cpu.emu_start(entry,0,count=10000000)
  if fault in ('erase_busoff','erase_timeout','program_timeout'):
    assert reset==[0x05fa0004] and not stopped and guard_seen
    assert read32(0x40020414)&0xc000==0
    assert read32(0x40023820)&(7<<25)==7<<25
    if fault.endswith('_timeout'):
      assert polls[0]>=(10 if fault=='program_timeout' else 500)
    return
  assert stopped
  result=read32(symbols['vgw_runtime_test_result'])
  expected=3 if fault in ('none','guard_ok','erase_ok','program_ok') else 4 if fault.startswith(('guard_','erase_','program_')) else 1
  assert result==expected
  assert not any(a in {b+0x180 for b in BASES} for a,_ in writes)
  if fault.startswith('guard_'):
    assert guard_seen and not busy[0]
    if fault=='guard_ok':
      assert read32(symbols['vgw_guard_test_received'])==1
      assert (0x40003000,0xaaaa) in writes
  if fault.startswith(('erase_','program_')):
    assert guard_seen and not busy[0]
    assert read32(0x40023c10)==0x80000000
    assert sum(a==0x40003000 and v==0xaaaa for a,v in writes)>1
    assert not any(a in (0x40023c08,0x40023c14,0x40023c18) for a,_ in writes)
    if fault=='program_ok':
      assert cpu.mem_read(0x080a0000,16)==bytes(range(16)) and len(guard_seen)==16
    return
  if fault in ('none','guard_ok'):
    assert rng[0]==9
    assert read32(0x40020414)&0xc000==0xc000
    assert all(read32(b+28)&0x80000000 for b in BASES)
  else:
    assert read32(0x40020414)&0xc000==0
    assert read32(0x40023820)&(7<<25)==7<<25
