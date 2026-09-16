"""Offline backup verifier and allowlisted CubeProgrammer read-command builder.

No subprocess, USB, mode switching, or execution API. A future Windows runner
must first verify the single attached target/DFU identity, reviewed tool hash,
geometry and secure output directory. This module cannot establish those facts.
"""

import hashlib
from pathlib import Path, PureWindowsPath
import re
import struct


FLASH_BASE = 0x08000000


def dfuse_geometry(descriptor: str) -> int:
  """Strict main-flash descriptor parsing; never infer extent from Panda type."""
  match = re.fullmatch(r'@Internal Flash\s*/0x08000000/([0-9*, Kg]+)', descriptor)
  if not match:
    raise ValueError('unrecognized main-flash descriptor')
  total = 0
  for group in match[1].split(','):
    entry = re.fullmatch(r'\s*(\d{1,3})\*(\d{1,3})Kg\s*', group)
    if not entry or int(entry[1]) == 0 or int(entry[2]) not in (16, 64, 128):
      raise ValueError('unrecognized flash sectors')
    total += int(entry[1]) * int(entry[2]) * 1024
  if total not in (1024 * 1024, 1536 * 1024):
    raise ValueError('unreviewed main-flash size')
  return total


def linux_read_command(dfu_serial: str, flash_bytes: int, output: Path) -> list[str]:
  if (not re.fullmatch(r'[0-9A-F]{12}', dfu_serial) or flash_bytes not in (1048576, 1572864) or
      not output.is_absolute() or '..' in output.parts or output.suffix != '.bin' or output.exists() or output.is_symlink()):
    raise ValueError('invalid/occupied readback target')
  return ['/usr/bin/dfu-util', '-d', ',0483:df11', '-S', ',' + dfu_serial, '-a', '0',
          '-s', f'0x08000000:{flash_bytes}', '-U', str(output)]


def read_command(executable: str, dfu_serial: str, flash_bytes: int, output: str) -> list[str]:
  """Return argv, never shell text. Full main-flash read only, no extra flags."""
  exe, destination = PureWindowsPath(executable), PureWindowsPath(output)
  if (not exe.is_absolute() or exe.name != 'STM32_Programmer_CLI.exe' or
      not destination.is_absolute() or destination.suffix != '.bin' or
      not re.fullmatch(r'[A-Za-z]:', exe.drive) or not re.fullmatch(r'[A-Za-z]:', destination.drive) or
      ':' in str(destination)[2:] or ':' in str(exe)[2:] or
      any(c in executable + output for c in '\r\n\0"') or
      '..' in exe.parts or '..' in destination.parts or
      not re.fullmatch(r'[0-9A-Fa-f]{12,24}', dfu_serial) or
      type(flash_bytes) is not int or flash_bytes not in (1024 * 1024, 1536 * 1024)):
    raise ValueError('unverified/invalid readback arguments')
  # Geometry choices reflect the F413 family, not a claim about this device.
  return [str(exe), '-c', 'port=USB1', 'sn=' + dfu_serial, '-u',
          f'0x{FLASH_BASE:08X}', str(flash_bytes), str(destination)]


def verify_reads(first: bytes, second: bytes, flash_bytes: int) -> dict:
  """Content integrity gate, NOT proof that restoration has been tested."""
  if flash_bytes not in (1024 * 1024, 1536 * 1024) or len(first) != flash_bytes or len(second) != flash_bytes:
    raise ValueError('incomplete backup')
  if first != second:
    raise ValueError('readbacks disagree')
  if len(set(first)) <= 1:
    raise ValueError('blank/unusable backup')
  sp, reset = struct.unpack_from('<II', first)
  if not 0x20000000 < sp <= 0x20050000 or sp % 4 or not reset & 1 or not FLASH_BASE <= reset & ~1 < FLASH_BASE + flash_bytes:
    raise ValueError('implausible boot vector')
  return {'schema': 1, 'bytes': flash_bytes, 'sha256': hashlib.sha256(first).hexdigest(),
          'two_reads_identical': True, 'boot_vector_plausible': True,
          'restoration_tested': False, 'option_bytes_included': False, 'otp_included': False}


def verify_files(first: Path, second: Path, flash_bytes: int) -> dict:
  # Check lengths before allocating untrusted files.
  if flash_bytes not in (1024 * 1024, 1536 * 1024) or first.stat().st_size != flash_bytes or second.stat().st_size != flash_bytes:
    raise ValueError('incomplete backup')
  return verify_reads(first.read_bytes(), second.read_bytes(), flash_bytes)
