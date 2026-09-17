"""Real Cortex-M reset code; load flash LMAs only, never preinitialize SRAM."""
from pathlib import Path
import struct
import subprocess

import gcc_arm_none_eabi
import pytest
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_PRIMASK, UC_CPU_ARM_CORTEX_M4


@pytest.fixture(scope='module')
def binary(tmp_path_factory):
  source = Path(__file__).parent/'firmware'
  gcc = Path(gcc_arm_none_eabi.__file__).parent/'toolchain/bin/arm-none-eabi-gcc'
  out = tmp_path_factory.mktemp('reset')/'NEVER_FLASH-reset.elf'
  subprocess.run([str(gcc), '-Wall', '-Wextra', '-Werror', '-Os', '-mcpu=cortex-m4', '-mthumb',
                  '-ffreestanding', '-fno-builtin', '-nostdlib', '-DVGW_RESET_TEST',
                  '-T', str(source/'reset_emu.ld'), '-Wl,--no-undefined,--build-id=none',
                  str(source/'white_reset.S'), str(source/'reset_emu.c'), '-o', str(out)],
                 check=True, capture_output=True)
  return out


@pytest.mark.parametrize('poison', [0x00, 0xa5, 0xff])
@pytest.mark.parametrize('fault', [False, True])
def test_flash_only_reset(binary, poison, fault):
  cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB|UC_MODE_MCLASS)
  cpu.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M4)
  cpu.mem_map(0x08000000, 0x40000)
  cpu.mem_map(0x20000000, 0x20000)
  cpu.mem_map(0x40000000, 0x100000)
  cpu.mem_map(0xe000e000, 0x2000)
  cpu.mem_write(0x20000000, bytes([poison])*0x20000)
  with binary.open('rb') as stream:
    elf = ELFFile(stream)
    symbols = {s.name:s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()}
    for seg in elf.iter_segments():
      if seg['p_type']=='PT_LOAD' and seg['p_filesz']:
        assert 0x08000000 <= seg['p_paddr'] < 0x08040000
        cpu.mem_write(seg['p_paddr'], seg.data())
  stack, reset = struct.unpack('<II', cpu.mem_read(0x08000000, 8))
  assert stack == 0x20020000 and reset & 1
  cpu.reg_write(UC_ARM_REG_SP, stack)
  done, writes, ram = [], [], []

  def entered(machine, address, size, data):
    done.append(True)
    machine.emu_stop()

  def in_ram(machine, address, size, data):
    ram.append(address)

  def write(machine, access, address, size, value, data):
    writes.append((address, value))
    if address==0xe000ed0c:
      machine.emu_stop()

  end = symbols['vgw_reset_done'] & ~1
  cpu.hook_add(UC_HOOK_CODE, entered, begin=end, end=end)
  cpu.hook_add(UC_HOOK_CODE, in_ram, begin=0x20000000, end=0x2001ffff)
  cpu.hook_add(UC_HOOK_MEM_WRITE, write, begin=0x40000000, end=0x400fffff)
  cpu.hook_add(UC_HOOK_MEM_WRITE, write, begin=0xe000ed0c, end=0xe000ed0f)
  cpu.emu_start(symbols['vgw_early_fault'] if fault else reset, 0, count=10000)
  assert cpu.reg_read(UC_ARM_REG_PRIMASK)==1
  if fault:
    assert not done and not ram
    assert writes[-1]==(0xe000ed0c, 0x05fa0004)
    assert (0x40023820, 7 << 25) in writes
    assert (0x40020818, 0x2002) in writes and (0x40020018, 1) in writes
  else:
    assert done and ram
    assert struct.unpack('<I', cpu.mem_read(symbols['vgw_reset_bss'], 4))[0]==0xb791f3dd
    assert struct.unpack('<I', cpu.mem_read(0xe000ed08, 4))[0]==0x08000000
    for section in ('ramfunc', 'data'):
      start, end = (symbols['__'+section+s] for s in ('_start', '_end'))
      assert cpu.mem_read(start, end-start)==cpu.mem_read(symbols['__'+section+'_load'], end-start)
    assert not writes
