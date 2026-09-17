"""Off-device MCUboot direct-XIP/revert C harness. Never produces a flash image."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import gcc_arm_none_eabi

from openpilot.tools.volt_gateway import target_crypto

COMMIT = '6d3b3d2c38ab20c242e5b9abb04d050086383eb2'
SOURCES = ('image_validate', 'bootutil_find_key', 'bootutil_img_hash', 'bootutil_img_security_cnt',
           'image_ecdsa', 'loader', 'swap_misc', 'swap_scratch', 'swap_move', 'swap_offset', 'caps',
           'bootutil_misc', 'bootutil_area', 'bootutil_loader', 'bootutil_public', 'tlv', 'fault_injection_hardening')


def build(checkout: Path, archive: Path, output: Path, *, arm=False):
  output.mkdir(parents=True, exist_ok=False)
  target_crypto.build(archive, output / 'crypto', native=not arm)
  compiler = str(Path(gcc_arm_none_eabi.__file__).parent / 'toolchain/bin/arm-none-eabi-gcc') if arm else 'cc'
  own = Path(__file__).parent / 'firmware'
  raw = subprocess.check_output(['git', '-C', str(checkout), 'archive', COMMIT, 'boot/bootutil'])
  with tempfile.TemporaryDirectory(prefix='voltgw-boot-') as tmp:
    root = Path(tmp)
    with tarfile.open(fileobj=io.BytesIO(raw)) as source:
      source.extractall(root, filter='data')
    crypto = target_crypto.extract(archive, root)
    upstream = root / 'boot/bootutil'
    flags = ['-std=c11', '-Wall', '-Wextra', '-Werror', '-Os', '-fstack-usage', '-ffunction-sections',
             '-fdata-sections', '-DMBEDTLS_CONFIG_FILE="crypto_config.h"']
    flags += (['-mcpu=cortex-m4', '-mthumb', '-mfloat-abi=soft', '-ffreestanding', '-fno-builtin'] if arm
              else ['-fPIC', '-DVGW_BOOT_TEST_HARNESS'])
    for path in (own / 'boot', own, upstream / 'include', upstream / 'src', crypto / 'include'):
      flags += ['-I', str(path)]
    objects = [output / 'crypto' / (name + '.o') for name in (*target_crypto.LIBRARIES, 'crypto')]
    sources = [upstream / 'src' / (name + '.c') for name in SOURCES]
    sources += [own / 'boot' / (name + '.c') for name in ('boot_port', 'boot_emu' if arm else 'boot_test')]
    if arm:
      sources += [own / (name + '.c') for name in ('status_led','white_platform','white_board','white_startup','white_watchdog')]
    hashes = {}
    for path in sources:
      obj = output / (path.stem + '.o')
      warnings = ['-Wno-unused-parameter'] if path.is_relative_to(upstream) else ([] if arm else ['-fanalyzer'])
      subprocess.run([compiler, *flags, *warnings, '-c', str(path), '-o', str(obj)], check=True, capture_output=True, text=True)
      objects.append(obj)
      hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    binary = output / ('NEVER_FLASH-boot.elf' if arm else 'boot-test.so')
    link = ['-nostdlib', '-T', str(own / 'boot/boot_emu.ld'), '-Wl,--gc-sections,--build-id=none'] if arm else ['-shared']
    subprocess.run([compiler, *flags, *link, '-Wl,--no-undefined', *map(str, objects), '-o', str(binary)],
                   check=True, capture_output=True, text=True)
  report = {'mcuboot_commit': COMMIT, 'mbedtls_archive_sha256': target_crypto.ARCHIVE_SHA256,
            'direct_xip': True, 'direct_xip_revert': True, 'production_ready': False,
            'adapter_static_analysis': not arm, 'upstream_modified': False, 'architecture': 'cortex-m4' if arm else 'native',
            'bootable': False, 'hardware_validated': False, 'source_sha256': hashes,
            'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}
  if arm:
    report['size_output'] = subprocess.check_output([str(Path(compiler).with_name('arm-none-eabi-size')), str(binary)], text=True)
    report['undefined_symbols'] = subprocess.check_output(['nm', '-u', str(binary)], text=True)
    if report['undefined_symbols'].strip():
      raise RuntimeError('unresolved boot harness symbols')
  (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
  return binary


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--checkout', type=Path, required=True)
  parser.add_argument('--archive', type=Path, default=target_crypto.archive_path())
  parser.add_argument('--output', type=Path, required=True)
  parser.add_argument('--arm', action='store_true')
  args = parser.parse_args()
  try:
    print(build(args.checkout, args.archive, args.output, arm=args.arm))
  except subprocess.CalledProcessError as error:
    print(error.stderr)
    raise


if __name__ == '__main__':
  main()
