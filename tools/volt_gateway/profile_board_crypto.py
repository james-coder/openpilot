"""Off-device scheduling probe using the actual board ELF's crypto instructions.

Ephemeral test keys only. No hardware IO, flash, device secrets or signature
bypass. Counts executed Thumb code bytes between cooperative callbacks, NOT
cycles or elapsed target time. Callback is a returning stub, not a CAN model.
"""
import argparse
import json
from pathlib import Path
import struct
from bisect import bisect_right
from collections import Counter


def profile(path):
  from Crypto.PublicKey import ECC
  from Crypto.Hash import SHA256
  from Crypto.Signature import DSS
  from elftools.elf.elffile import ELFFile
  from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_BLOCK, UC_HOOK_CODE
  from unicorn.arm_const import (UC_CPU_ARM_CORTEX_M4, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_R0,
                                 UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)
  cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
  cpu.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M4)
  cpu.mem_map(0x08000000, 0x100000)
  cpu.mem_map(0x20000000, 0x20000)
  with path.open('rb') as stream:
    elf = ELFFile(stream)
    for segment in elf.iter_segments():
      if segment['p_type'] == 'PT_LOAD':
        cpu.mem_write(segment['p_vaddr'], segment.data())
    symbols = {s.name: s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()}
    functions = sorted((s['st_value'] & ~1, s.name) for s in elf.get_section_by_name('.symtab').iter_symbols()
                       if s['st_info']['type'] == 'STT_FUNC' and s['st_size'])
  addresses = [a for a, _ in functions]
  gap_functions = Counter()
  worst_functions = Counter()
  pump, stop, ctx, public, body, signature = (0x20017000, 0x20017010, 0x20017200, 0x20017600, 0x20017700, 0x20017800)
  cpu.mem_write(pump, b'\x01\x20\x70\x47')  # movs r0,#1; bx lr
  cpu.mem_write(stop, b'\x70\x47')
  progress = {'gap': 0, 'maximum_gap_code_bytes': 0, 'services': 0, 'total_code_bytes': 0, 'complete': False}

  def block(machine, address, size, data):
    index = bisect_right(addresses, address) - 1
    gap_functions[functions[index][1] if index >= 0 else 'stub'] += size
    progress['gap'] += size
    progress['total_code_bytes'] += size
    progress['maximum_gap_code_bytes'] = max(progress['maximum_gap_code_bytes'], progress['gap'])

  def service(machine, address, size, data):
    nonlocal worst_functions
    if sum(gap_functions.values()) > sum(worst_functions.values()):
      worst_functions = gap_functions.copy()
    gap_functions.clear()
    progress['services'] += 1
    progress['gap'] = 0

  def done(machine, address, size, data):
    progress['complete'] = True
    machine.emu_stop()

  cpu.hook_add(UC_HOOK_BLOCK, block)
  cpu.hook_add(UC_HOOK_CODE, service, begin=pump, end=pump)
  cpu.hook_add(UC_HOOK_CODE, done, begin=stop, end=stop)

  def call(name, args):
    nonlocal worst_functions
    gap_functions.clear()
    worst_functions.clear()
    for field in progress:
      progress[field] = False if field == 'complete' else 0
    stack = 0x2001ffe0
    cpu.reg_write(UC_ARM_REG_SP, stack)
    cpu.reg_write(UC_ARM_REG_LR, stop | 1)
    for reg, value in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), args[:4], strict=False):
      cpu.reg_write(reg, value)
    for index, value in enumerate(args[4:]):
      cpu.mem_write(stack+index*4, struct.pack('<I', value))
    cpu.emu_start(symbols[name] | 1, 0, timeout=30_000_000, count=100_000_000)
    if not progress['complete'] or cpu.reg_read(UC_ARM_REG_R0) != 1:
      raise RuntimeError('bounded crypto probe did not complete successfully: '+name)
    if sum(gap_functions.values()) > sum(worst_functions.values()):
      worst_functions = gap_functions.copy()
    return {**{k: v for k, v in progress.items() if k not in ('complete', 'gap')},
            'longest_gap_functions': worst_functions.most_common(10)}

  key = ECC.generate(curve='P-256')
  cpu.mem_write(public, b'\x04'+int(key.pointQ.x).to_bytes(32, 'big')+int(key.pointQ.y).to_bytes(32, 'big'))
  message = b'a'*122
  signed = DSS.new(key, 'fips-186-3', encoding='binary').sign(SHA256.new(b'VOLT GW FIRMWARE MANIFEST v1\0'+message))
  cpu.mem_write(body, message)
  cpu.mem_write(signature, signed)
  result = {'target_timing_measured': False, 'can_emulated': False}
  result['cooperative_init'] = call('vgw_crypto_cooperative_init', [pump | 1, ctx])
  result['key_init'] = call('vgw_crypto_init', [ctx, public, 65])
  result['signature_verify'] = call('vgw_crypto_verify', [ctx, 1, body, 122, signature])
  return result


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('elf', type=Path)
  print(json.dumps(profile(parser.parse_args().elf), indent=2))
