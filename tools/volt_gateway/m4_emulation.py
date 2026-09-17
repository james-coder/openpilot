"""Execute Cortex-M4 gateway-core instructions, NOT a whole White Panda model.

The ELF has no vehicle drivers/vector table and must NEVER be flashed.
ECDSA/SHA256 default to public-only host hooks; target_crypto=True executes the
pinned backend without crypto interception. Interrupts,
flash timing, CAN, GPIO/mux/SWCAN, USB and electrical effects are not emulated.
"""

import hashlib
from pathlib import Path
import struct
import subprocess

import gcc_arm_none_eabi

from openpilot.tools.volt_gateway.authority import AUTH_DOMAIN, IMAGE_DOMAIN, AuthorityError, public_key, verify


def build(output: Path):
  if output.exists():
    raise FileExistsError(output)
  source = Path(__file__).parent / 'firmware'
  compiler = Path(gcc_arm_none_eabi.__file__).parent / 'toolchain/bin/arm-none-eabi-gcc'
  subprocess.run([str(compiler), '-std=c11', '-Wall', '-Wextra', '-Werror', '-Os', '-mcpu=cortex-m4', '-mthumb',
                  '-mfloat-abi=soft', '-ffreestanding', '-fno-builtin', '-nostdlib', '-Wl,--build-id=none',
                  '-T', str(source / 'emu.ld'), str(source / 'emu.c'), str(source / 'authority.c'),
                  str(source / 'observe.c'), str(source / 'update.c'), '-o', str(output)],
                 check=True, capture_output=True, text=True, timeout=60)


def run(elf: Path, signed_image: bytes, authorization: bytes, verification_key: bytes, *, target_crypto=False):
  from elftools.elf.elffile import ELFFile
  from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE, UC_PROT_READ, UC_PROT_EXEC, UC_PROT_WRITE
  from unicorn.arm_const import (UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1,
                                  UC_ARM_REG_R2, UC_ARM_REG_R3, UC_CPU_ARM_CORTEX_M4)

  if len(signed_image) != 186 or len(authorization) != 182:
    raise AuthorityError('emulator fixture bounds')
  key = public_key(verification_key)
  cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
  cpu.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M4)
  cpu.mem_map(0x08000000, 0x10000, UC_PROT_READ | UC_PROT_EXEC)
  cpu.mem_map(0x20000000, 0x20000, UC_PROT_READ | UC_PROT_WRITE)
  with elf.open('rb') as f:
    image = ELFFile(f)
    if image['e_machine'] != 'EM_ARM' or image.elfclass != 32:
      raise ValueError('expected ARM32 test ELF')
    for segment in image.iter_segments():
      if segment['p_type'] != 'PT_LOAD':
        continue
      start, size = segment['p_vaddr'], segment['p_memsz']
      if not (0x08000000 <= start <= start+size <= 0x08010000 or 0x20000000 <= start <= start+size <= 0x20018000):
        raise ValueError('ELF exceeds test mappings')
      cpu.mem_write(start, segment.data())
    symbols = {s.name:s['st_value'] for s in image.get_section_by_name('.symtab').iter_symbols()}
    entry = image['e_entry']
  cpu.mem_write(0x20018000, signed_image + authorization)
  if target_crypto:
    if 'vgw_crypto_verify' not in symbols or 'vgw_emu_verify' in symbols:
      raise ValueError('expected linked target crypto, not host traps')
    cpu.mem_write(0x20018000 + 368, b'\x04' + int(key.pointQ.x).to_bytes(32, 'big') + int(key.pointQ.y).to_bytes(32, 'big'))
  cpu.mem_protect(0x20018000, 0x1000, UC_PROT_READ)
  cpu.reg_write(UC_ARM_REG_SP, 0x2001FFF0)
  state = {'completed':False, 'crypto_calls':0}
  stream_hash = None

  def hook(machine, address, size, data):
    nonlocal stream_hash
    if address == (symbols['vgw_emu_done'] & ~1):
      state['completed'] = True
      machine.emu_stop()
      return
    if target_crypto:
      raise RuntimeError('target crypto must not use a host hook')
    if address == (symbols['vgw_emu_verify'] & ~1):
      is_image = machine.reg_read(UC_ARM_REG_R1)
      pointer, length = machine.reg_read(UC_ARM_REG_R2), machine.reg_read(UC_ARM_REG_R3)
      sig = struct.unpack('<I', machine.mem_read(machine.reg_read(UC_ARM_REG_SP), 4))[0]
      if is_image not in (0, 1) or length not in (118, 122):
        raise AuthorityError('unexpected emulated crypto arguments')
      try:
        verify(key, IMAGE_DOMAIN if is_image else AUTH_DOMAIN, bytes(machine.mem_read(pointer, length)), bytes(machine.mem_read(sig, 64)))
        accepted = 1
      except AuthorityError:
        accepted = 0
      state['crypto_calls'] += 1
      machine.reg_write(UC_ARM_REG_R0, accepted)
      machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))
    elif address == (symbols['vgw_emu_hash_start'] & ~1):
      stream_hash = hashlib.sha256()
      machine.reg_write(UC_ARM_REG_R0, 1)
      machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))
    elif address == (symbols['vgw_emu_hash_add'] & ~1):
      pointer, length = machine.reg_read(UC_ARM_REG_R1), machine.reg_read(UC_ARM_REG_R2)
      if stream_hash is None or not 1 <= length <= 256:
        raise AuthorityError('invalid emulated streaming hash')
      stream_hash.update(bytes(machine.mem_read(pointer, length)))
      machine.reg_write(UC_ARM_REG_R0, 1)
      machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))
    elif address == (symbols['vgw_emu_hash_finish'] & ~1):
      if stream_hash is None:
        raise AuthorityError('uninitialized streaming hash')
      machine.mem_write(machine.reg_read(UC_ARM_REG_R1), stream_hash.digest())
      machine.reg_write(UC_ARM_REG_R0, 1)
      machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))
    elif address == (symbols['vgw_emu_sha256'] & ~1):
      pointer, length, dest = (machine.reg_read(reg) for reg in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
      if length != 122:
        raise AuthorityError('unexpected emulated hash length')
      machine.mem_write(dest, hashlib.sha256(bytes(machine.mem_read(pointer, length))).digest())
      machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))

  if target_crypto:
    done = symbols['vgw_emu_done'] & ~1
    cpu.hook_add(UC_HOOK_CODE, hook, begin=done, end=done)
  else:
    cpu.hook_add(UC_HOOK_CODE, hook)
  cpu.emu_start(entry | 1, 0, timeout=20_000_000 if target_crypto else 5_000_000,
                count=300_000_000 if target_crypto else 2_000_000)
  state['failure_bits'] = struct.unpack('<I', cpu.mem_read(symbols['vgw_emu_result'], 4))[0]
  state['authorized'] = bool(struct.unpack('<I', cpu.mem_read(symbols['vgw_emu_authorized'], 4))[0])
  state['updated'] = bool(struct.unpack('<I', cpu.mem_read(symbols['vgw_emu_updated'], 4))[0])
  if not state['completed']:
    raise RuntimeError(f'emulation did not complete within bounds; pc=0x{cpu.reg_read(UC_ARM_REG_PC):08x}')
  return state
