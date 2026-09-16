"""Build the pinned crypto backend off-device. No USB, signing or firmware flashing.

The optional archive is downloaded explicitly, never by tests. Every build
verifies its SHA256 and extracts fresh source; a mutable upstream checkout is
not trusted. The ARM result remains a NEVER_FLASH CPU harness, not a boot image.
"""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile

import gcc_arm_none_eabi


VERSION = '3.6.7'
ARCHIVE_SHA256 = 'a7e8bcbec0e6f761b4af24f25677626b35f762f68eef79c08677a363212d11f6'
ARCHIVE_URL = f'https://github.com/Mbed-TLS/mbedtls/releases/download/mbedtls-{VERSION}/mbedtls-{VERSION}.tar.bz2'
LIBRARIES = ('bignum', 'bignum_core', 'bignum_mod', 'bignum_mod_raw', 'constant_time', 'ecp', 'ecp_curves',
             'ecdsa', 'sha256', 'md', 'platform', 'platform_util', 'memory_buffer_alloc', 'asn1parse', 'asn1write')


def archive_path():
  return Path(os.environ.get('VOLTGW_MBEDTLS_ARCHIVE', Path.home() / '.cache/voltgw' / f'mbedtls-{VERSION}.tar.bz2'))


def extract(archive: Path, destination: Path):
  # Hash and extract the same bounded bytes, not two reads of a mutable path.
  maximum = 8 * 1024 * 1024
  with archive.open('rb') as stream:
    raw = stream.read(maximum + 1)
  if len(raw) > maximum or hashlib.sha256(raw).hexdigest() != ARCHIVE_SHA256:
    raise ValueError('crypto archive checksum mismatch')
  root = f'mbedtls-{VERSION}'
  with tarfile.open(fileobj=io.BytesIO(raw), mode='r:bz2') as source:
    members = [m for m in source.getmembers() if m.name.startswith((root + '/include/', root + '/library/'))]
    if any(not m.isfile() and not m.isdir() for m in members):
      raise ValueError('unexpected crypto archive member')
    source.extractall(destination, members=members, filter='data')
  return destination / root


def build(archive: Path, output: Path, *, native=False):
  output.mkdir(parents=True, exist_ok=False)
  own = Path(__file__).parent / 'firmware'
  toolchain = Path(gcc_arm_none_eabi.__file__).parent / 'toolchain/bin'
  compiler = 'cc' if native else str(toolchain / 'arm-none-eabi-gcc')
  flags = ['-std=c11', '-Wall', '-Wextra', '-Werror', '-Os', '-ffunction-sections', '-fdata-sections',
           '-fno-builtin', '-fstack-usage', '-DMBEDTLS_CONFIG_FILE="crypto_config.h"', '-I', str(own)]
  if native:
    flags += ['-fPIC']
  else:
    flags += ['-mcpu=cortex-m4', '-mthumb', '-mfloat-abi=soft', '-ffreestanding']
  report = {'mbedtls_version': VERSION, 'archive_sha256': ARCHIVE_SHA256, 'archive_url': ARCHIVE_URL,
            'architecture': 'native' if native else 'cortex-m4', 'crypto_linked_in_image': True,
            'host_crypto_hooks': False, 'bootable': False, 'flashed': False,
            'arena_bytes': 24576, 'flags': flags, 'objects': {},
            'adapter_headers_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                      for p in sorted(own.glob('*.h'))},
            'compiler': subprocess.check_output([compiler, '--version'], text=True).splitlines()[0],
            'limitations': ['no hardware RNG, board drivers or flash', 'no hardware timing/interrupt validation',
                            'synchronous verifier requires board scheduling/watchdog review']}
  with tempfile.TemporaryDirectory(prefix='voltgw-crypto-') as tmp:
    source = extract(archive, Path(tmp))
    flags += ['-I', str(source / 'include')]
    sources = [source / 'library' / (name + '.c') for name in LIBRARIES] + [own / 'crypto.c']
    if not native:
      flags += ['-DVGW_TARGET_CRYPTO']
      sources += [own / (name + '.c') for name in ('emu', 'authority', 'observe', 'update')]
    objects = []
    for path in sources:
      obj = output / (path.stem + '.o')
      analysis = ['-fanalyzer'] if native and path == own / 'crypto.c' else []
      subprocess.run([compiler, *flags, *analysis, '-c', str(path), '-o', str(obj)],
                     check=True, capture_output=True, text=True, timeout=60)
      objects.append(str(obj))
      report['objects'][path.stem] = {'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                     'object_sha256': hashlib.sha256(obj.read_bytes()).hexdigest(),
                                     'stack_usage': obj.with_suffix('.su').read_text()}
    binary = output / ('crypto.so' if native else 'NEVER_FLASH-target-crypto.elf')
    link = ['-shared'] if native else ['-nostdlib', '-T', str(own / 'emu.ld')]
    subprocess.run([compiler, *flags, *link, '-Wl,--gc-sections,--build-id=none', '-Wl,-Map=' + str(output / 'link.map'),
                    *objects, '-o', str(binary)],
                   check=True, capture_output=True, text=True, timeout=60)
    report['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
    report['adapter_static_analysis'] = native
    size = 'size' if native else str(toolchain / 'arm-none-eabi-size')
    report['size_output'] = subprocess.check_output([size, str(binary)], text=True)
    nm = 'nm'  # ELF symbol listing works for both host and ARM objects.
    report['undefined_symbols'] = subprocess.check_output([nm, '-u', str(binary)], text=True)
    if not native and report['undefined_symbols'].strip():
      raise RuntimeError('unresolved target crypto dependency')
    if not native:
      symbols = subprocess.check_output([nm, '--defined-only', str(binary)], text=True)
      forbidden = ('mbedtls_ecdsa_sign', 'mbedtls_ecp_gen_key', 'mbedtls_ssl_', 'mbedtls_x509_',
                   'mbedtls_pk_parse', 'vgw_emu_verify', 'vgw_emu_sha256')
      if any(s in symbols for s in forbidden):
        raise RuntimeError('unexpected private-key/network/parser/host-trap code in target image')
      report['forbidden_symbols_absent'] = list(forbidden)
  (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
  return binary


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--archive', type=Path, default=archive_path())
  parser.add_argument('--output', type=Path, required=True)
  parser.add_argument('--native', action='store_true')
  args = parser.parse_args()
  try:
    print(build(args.archive, args.output, native=args.native))
  except subprocess.CalledProcessError as error:
    print(error.stderr)
    raise


if __name__ == '__main__':
  main()
