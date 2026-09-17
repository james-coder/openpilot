"""Execute physical binding instructions against mapped registers, not a Panda."""
from pathlib import Path
import subprocess
import struct

import gcc_arm_none_eabi
import pytest
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_PRIMASK, UC_CPU_ARM_CORTEX_M4


@pytest.fixture(scope='module')
def binary(tmp_path_factory):
  own = Path(__file__).parent/'firmware'
  compiler = Path(gcc_arm_none_eabi.__file__).parent/'toolchain/bin/arm-none-eabi-gcc'
  out = tmp_path_factory.mktemp('physical-binding')/'NEVER_FLASH-platform.elf'
  subprocess.run([str(compiler),'-std=c11','-Wall','-Wextra','-Werror','-Os','-mcpu=cortex-m4','-mthumb',
                  '-mfloat-abi=soft','-ffreestanding','-fno-builtin','-ffunction-sections','-fdata-sections',
                  '-I',str(own),'-DVGW_PLATFORM_TEST','-nostdlib','-T',str(own/'platform_emu.ld'),
                  '-Wl,--gc-sections,--no-undefined,--build-id=none',
                  *(str(own/(n+'.c')) for n in ('white_platform','white_startup','white_watchdog','white_board','status_led','platform_emu')),
                  '-o',str(out)],check=True,capture_output=True)
  return out


@pytest.mark.parametrize('mode',range(7))
@pytest.mark.parametrize('masked',[0,1])
def test_physical_binding_instructions(binary, mode, masked):
  cpu = Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
  cpu.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M4)
  cpu.mem_map(0x08000000,0x100000)
  cpu.mem_map(0x20000000,0x20000)
  cpu.mem_map(0x40000000,0x100000)
  cpu.mem_map(0xe000e000,0x2000)
  with binary.open('rb') as stream:
    elf = ELFFile(stream)
    symbols = {s.name:s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()}
    for seg in elf.iter_segments():
      if seg['p_type']=='PT_LOAD':
        cpu.mem_write(seg['p_vaddr'],seg.data())
    entry = elf['e_entry']
  cpu.mem_write(symbols['vgw_platform_mode'],struct.pack('<I',mode))
  cpu.mem_write(0x40000024,struct.pack('<I',100))
  cpu.mem_write(0x08040200,struct.pack('<II',0x2001e000,0x08040209))
  cpu.reg_write(UC_ARM_REG_SP,0x2001fff0)
  cpu.reg_write(UC_ARM_REG_PRIMASK,masked)
  done, reset, writes, app = [], [], [], []

  def stop(machine,address,size,data):
    done.append(True)
    machine.emu_stop()

  def write(machine,access,address,size,value,data):
    writes.append((address,value))
    if address in (0x40020018,0x40020418,0x40020818):
      odr = address-4
      old = struct.unpack('<I',machine.mem_read(odr,4))[0]
      machine.mem_write(odr,struct.pack('<I',(old | (value&0xffff)) & ~(value>>16)))
    if address==0xe000ed0c:
      reset.append(value)
      machine.emu_stop()  # model reset request, not MCU reset timing

  def entered(machine,address,size,data):
    app.append(True)
    machine.emu_stop()

  end = symbols['vgw_platform_done']&~1
  cpu.hook_add(UC_HOOK_CODE,stop,begin=end,end=end)
  cpu.hook_add(UC_HOOK_MEM_WRITE,write)
  cpu.hook_add(UC_HOOK_CODE,entered,begin=0x08040208,end=0x08040208)
  cpu.emu_start(entry|1,0,timeout=1000000,count=100000)
  result = struct.unpack('<I',cpu.mem_read(symbols['vgw_platform_result'],4))[0]
  if mode==5:
    assert app and not done and not reset
    assert cpu.reg_read(UC_ARM_REG_SP)==0x2001e000
    assert cpu.reg_read(UC_ARM_REG_PRIMASK)==1
    assert struct.unpack('<I',cpu.mem_read(0xe000ed08,4))[0]==0x08040200
    assert (0x40023820,7<<25) in writes
  elif mode==3:
    assert reset==[0x05fa0004] and not done and result!=98
    assert writes.index((0x40020818,(1<<1)|(1<<13)))<writes.index((0xe000ed0c,0x05fa0004))
    assert (0x40023820,7<<25) in writes
  else:
    assert done and not reset and result==1
  if mode==1:
    assert cpu.reg_read(UC_ARM_REG_PRIMASK)==masked
  if mode==2:
    assert (0x40003000,0xaaaa) in writes
  assert not any(address==0x40023c08 for address,_ in writes)
