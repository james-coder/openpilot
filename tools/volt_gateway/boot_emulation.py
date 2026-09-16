"""Execute real bootutil/crypto and signed Thumb probes; NEVER hardware flashing.

Flash is mapped memory, not an STM32 controller. Default selected application
probe is a returning leaf function. Optional physical-binding mode executes
MSP/VTOR/interrupt handoff instructions against modeled peripheral registers and
stops at reset entry; it does not initialize or run the application runtime.
"""
from pathlib import Path
import struct


def run(elf: Path, flash: bytes, public_der: bytes, target: bytes, *, confirm=False, led=True, physical_handoff=False):
  from elftools.elf.elffile import ELFFile
  from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE, UC_HOOK_MEM_WRITE, UC_PROT_READ, UC_PROT_EXEC, UC_PROT_WRITE
  from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_PRIMASK, UC_ARM_REG_PC, UC_ARM_REG_LR, UC_CPU_ARM_CORTEX_M4

  if len(flash) != 0x180000 or len(public_der) != 91 or len(target) != 44:
    raise ValueError('boot emulator fixture bounds')
  cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
  cpu.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M4)
  cpu.mem_map(0x08000000, 0x40000, UC_PROT_READ | UC_PROT_EXEC)
  cpu.mem_map(0x08040000, 0x140000, UC_PROT_READ | UC_PROT_WRITE | UC_PROT_EXEC)
  cpu.mem_map(0x20000000, 0x20000, UC_PROT_READ | UC_PROT_WRITE | UC_PROT_EXEC)
  if physical_handoff:
    if confirm:
      raise ValueError('physical handoff cannot pre-confirm an application')
    cpu.mem_map(0x40000000,0x100000)
    cpu.mem_map(0xe000e000,0x2000)
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
  cpu.mem_write(0x20018000, public_der + target + bytes([bool(confirm), bool(led), bool(physical_handoff)]))
  cpu.mem_protect(0x20018000, 0x1000, UC_PROT_READ)
  cpu.reg_write(UC_ARM_REG_SP, 0x2001fff0)
  complete = False
  handoff = None

  def done(machine, address, size, data):
    nonlocal complete
    complete = True
    machine.emu_stop()

  end = symbols['vgw_boot_emu_done'] & ~1
  cpu.hook_add(UC_HOOK_CODE, done, begin=end, end=end)
  if physical_handoff:
    def register_write(machine,access,address,size,value,data):
      if address in (0x40020018,0x40020818):
        old = struct.unpack('<I',machine.mem_read(address-4,4))[0]
        machine.mem_write(address-4,struct.pack('<I',(old | (value&0xffff)) & ~(value>>16)))
      if address==0xe000ed0c:
        raise RuntimeError('unexpected reset during verified handoff')

    def application(machine,address,size,data):
      nonlocal complete, handoff
      handoff = {'pc':address,'sp':machine.reg_read(UC_ARM_REG_SP),
                 'primask':machine.reg_read(UC_ARM_REG_PRIMASK),
                 'vtor':struct.unpack('<I',machine.mem_read(0xe000ed08,4))[0]}
      complete=True
      machine.emu_stop()  # Stop at reset entry; app runtime itself is not tested.

    # Scope model hooks to actual modeled registers. An all-memory write hook
    # produced a stack/control-flow failure in this Unicorn harness before
    # verification; do not instrument unrelated crypto/stack writes here.
    cpu.hook_add(UC_HOOK_MEM_WRITE,register_write,begin=0x40000000,end=0x400fffff)
    cpu.hook_add(UC_HOOK_MEM_WRITE,register_write,begin=0xe000ed0c,end=0xe000ed0c)
    cpu.hook_add(UC_HOOK_CODE,application,begin=0x08040000,end=0x0817ffff)
  try:
    cpu.emu_start(entry | 1, 0, timeout=20_000_000, count=300_000_000)
  except Exception as error:
    registers = {name:hex(cpu.reg_read(reg)) for name,reg in (('PC',UC_ARM_REG_PC),('SP',UC_ARM_REG_SP),('LR',UC_ARM_REG_LR))}
    raise RuntimeError(f'boot CPU failure: {registers}') from error
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
          'handoff': handoff, 'host_crypto_hooks': 0, 'hardware_validated': False}
