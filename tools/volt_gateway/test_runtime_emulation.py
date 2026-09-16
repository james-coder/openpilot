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


@pytest.fixture(scope='module')
def binary(tmp_path_factory):
  source=Path(__file__).parent/'firmware'
  gcc=Path(gcc_arm_none_eabi.__file__).parent/'toolchain/bin/arm-none-eabi-gcc'
  out=tmp_path_factory.mktemp('runtime-arm')/'NEVER_FLASH-runtime.elf'
  names=('runtime_emu','white_runtime','white_platform','white_startup','white_watchdog','white_clock',
         'white_rng','white_can','white_board','status_led','observe','recovery_link','recovery_transport')
  subprocess.run([str(gcc),'-std=c11','-Wall','-Wextra','-Werror','-Os','-mcpu=cortex-m4','-mthumb',
                  '-mfloat-abi=soft','-ffreestanding','-fno-builtin','-fdata-sections','-ffunction-sections',
                  '-nostdlib','-DVGW_RUNTIME_TEST','-I',str(source),'-T',str(source/'reset_emu.ld'),
                  '-Wl,--no-undefined,--gc-sections,--build-id=none',str(source/'white_reset.S'),
                  *(str(source/(n+'.c')) for n in names),'-o',str(out)],check=True,capture_output=True)
  return out


@pytest.mark.parametrize('fault',['none','identity','rng','can_init'])
def test_flash_only_composed_runtime(binary,fault):
  cpu=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
  cpu.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M4)
  for base,size in [(0x08000000,0x40000),(0x20000000,0x20000),(0x40000000,0x100000),
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
  cpu.mem_write(0x1fff7a22,struct.pack('<H',1536))
  put(0x40023800,3)
  put(0x40023874,3)
  put(0x40007004,0x4000)
  put(0x50060804,4 if fault=='rng' else 1)
  writes=[]
  rng=[0]

  def on_write(machine,access,a,size,v,data):
    writes.append((a,v))
    if a in (0x40020018,0x40020418,0x40020818):
      put(a-4,(read32(a-4)|(v&0xffff))&~(v>>16))
    if a in BASES:
      put(a+4,0 if fault=='can_init' else v&1)

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

  stopped=[]
  def done(machine,address,size,data):
    stopped.append(True)
    machine.emu_stop()
  for lo,hi in [(0x40000000,0x400fffff),(0x50060000,0x50060fff)]:
    cpu.hook_add(UC_HOOK_MEM_WRITE,on_write,begin=lo,end=hi)
    cpu.hook_add(UC_HOOK_MEM_READ,on_read,begin=lo,end=hi)
  end=symbols['vgw_runtime_test_done']&~1
  cpu.hook_add(UC_HOOK_CODE,done,begin=end,end=end)
  stack,entry=struct.unpack('<II',cpu.mem_read(0x08000000,8))
  cpu.reg_write(UC_ARM_REG_SP,stack)
  cpu.emu_start(entry,0,count=10000000)
  assert stopped
  result=read32(symbols['vgw_runtime_test_result'])
  assert result==(3 if fault=='none' else 1)
  assert not any(a in {b+0x180 for b in BASES} for a,_ in writes)
  if fault=='none':
    assert rng[0]==9
    assert read32(0x40020414)&0xc000==0xc000
    assert all(read32(b+28)&0x80000000 for b in BASES)
  else:
    assert read32(0x40020414)&0xc000==0
    assert read32(0x40023820)&(7<<25)==7<<25
