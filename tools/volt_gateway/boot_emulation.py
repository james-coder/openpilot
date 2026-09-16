"""Execute real bootutil/crypto and signed Thumb probes; NEVER hardware flashing.

Flash is mapped memory, not an STM32 controller. Selected application probe is
a returning leaf function; vector/interrupt/reset handoff is not simulated.
"""
from pathlib import Path
import struct


def run(elf: Path, flash: bytes, public_der: bytes, target: bytes, *, confirm=False, led=True):
  from elftools.elf.elffile import ELFFile
  from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE, UC_PROT_READ, UC_PROT_EXEC, UC_PROT_WRITE
  from unicorn.arm_const import UC_ARM_REG_SP, UC_CPU_ARM_CORTEX_M4

  if len(flash) != 0x180000 or len(public_der) != 91 or len(target) != 44:
    raise ValueError('boot emulator fixture bounds')
  cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
  cpu.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M4)
  cpu.mem_map(0x08000000, 0x40000, UC_PROT_READ | UC_PROT_EXEC)
  cpu.mem_map(0x08040000, 0x140000, UC_PROT_READ | UC_PROT_WRITE | UC_PROT_EXEC)
  cpu.mem_map(0x20000000, 0x20000, UC_PROT_READ | UC_PROT_WRITE)
  cpu.mem_write(0x08040000, flash[0x40000:])
  with elf.open('rb') as stream:
    image = ELFFile(stream)
    if image['e_machine'] != 'EM_ARM' or image.elfclass != 32:
      raise ValueError('expected ARM32 boot harness')
    for segment in image.iter_segments():
      if segment['p_type'] != 'PT_LOAD':
        continue
      start, size = segment['p_vaddr'], segment['p_memsz']
      if not (0x08000000 <= start <= start + size <= 0x08040000 or 0x20000000 <= start <= start + size <= 0x20010000):
        raise ValueError('boot ELF exceeds test mappings')
      cpu.mem_write(start, segment.data())
    symbols = {s.name: s['st_value'] for s in image.get_section_by_name('.symtab').iter_symbols()}
    entry = image['e_entry']
  cpu.mem_write(0x20018000, public_der + target + bytes([bool(confirm), bool(led)]))
  cpu.mem_protect(0x20018000, 0x1000, UC_PROT_READ)
  cpu.reg_write(UC_ARM_REG_SP, 0x2001fff0)
  complete = False

  def done(machine, address, size, data):
    nonlocal complete
    complete = True
    machine.emu_stop()

  end = symbols['vgw_boot_emu_done'] & ~1
  cpu.hook_add(UC_HOOK_CODE, done, begin=end, end=end)
  cpu.emu_start(entry | 1, 0, timeout=20_000_000, count=300_000_000)
  if not complete:
    raise RuntimeError('boot CPU harness exceeded time/instruction budget')

  def word(symbol, signed=False):
    return struct.unpack('<i' if signed else '<I', cpu.mem_read(symbols[symbol], 4))[0]

  return {'selected': word('vgw_boot_emu_result', True),
          'confirmed': word('vgw_boot_emu_confirmed'), 'services': word('vgw_boot_emu_services'),
          'status': list(cpu.mem_read(symbols['vgw_boot_emu_status'], 3)),
          'led_rgb': word('vgw_boot_emu_rgb'), 'led_bsrr': word('vgw_boot_emu_bsrr'),
          'probe': struct.unpack('<I', cpu.mem_read(0x20017000, 4))[0],
          'flash': flash[:0x40000] + bytes(cpu.mem_read(0x08040000, 0x140000)),
          'host_crypto_hooks': 0, 'hardware_validated': False}
