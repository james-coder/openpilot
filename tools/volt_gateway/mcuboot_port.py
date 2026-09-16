"""Off-device MCUboot direct-XIP/revert C harness. Never produces a flash image."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile

from openpilot.tools.volt_gateway import target_crypto

COMMIT = '6d3b3d2c38ab20c242e5b9abb04d050086383eb2'
SOURCES = ('image_validate', 'bootutil_find_key', 'bootutil_img_hash', 'bootutil_img_security_cnt',
           'image_ecdsa', 'loader', 'swap_misc', 'swap_scratch', 'swap_move', 'swap_offset', 'caps',
           'bootutil_misc', 'bootutil_area', 'bootutil_loader', 'bootutil_public', 'tlv', 'fault_injection_hardening')


def build(checkout: Path, archive: Path, output: Path):
  output.mkdir(parents=True, exist_ok=False)
  target_crypto.build(archive, output / 'crypto', native=True)
  own = Path(__file__).parent / 'firmware'
  raw = subprocess.check_output(['git', '-C', str(checkout), 'archive', COMMIT, 'boot/bootutil'])
  with tempfile.TemporaryDirectory(prefix='voltgw-boot-') as tmp:
    root = Path(tmp)
    with tarfile.open(fileobj=io.BytesIO(raw)) as source:
      source.extractall(root, filter='data')
    crypto = target_crypto.extract(archive, root)
    upstream = root / 'boot/bootutil'
    flags = ['-std=c11', '-Wall', '-Wextra', '-Werror', '-Os', '-fPIC', '-fstack-usage',
             '-DMBEDTLS_CONFIG_FILE="crypto_config.h"', '-DVGW_BOOT_TEST_HARNESS']
    for path in (own / 'boot', own, upstream / 'include', upstream / 'src', crypto / 'include'):
      flags += ['-I', str(path)]
    objects = list((output / 'crypto').glob('*.o'))
    sources = [upstream / 'src' / (name + '.c') for name in SOURCES]
    sources += [own / 'boot' / (name + '.c') for name in ('boot_port', 'boot_test')]
    hashes = {}
    for path in sources:
      obj = output / (path.stem + '.o')
      warnings = ['-Wno-unused-parameter'] if path.is_relative_to(upstream) else ['-fanalyzer']
      subprocess.run(['cc', *flags, *warnings, '-c', str(path), '-o', str(obj)], check=True, capture_output=True, text=True)
      objects.append(obj)
      hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    binary = output / 'boot-test.so'
    subprocess.run(['cc', '-shared', '-Wl,--no-undefined', *map(str, objects), '-o', str(binary)],
                   check=True, capture_output=True, text=True)
  report = {'mcuboot_commit': COMMIT, 'mbedtls_archive_sha256': target_crypto.ARCHIVE_SHA256,
            'direct_xip': True, 'direct_xip_revert': True, 'production_ready': False,
            'adapter_static_analysis': True, 'upstream_modified': False,
            'bootable': False, 'hardware_validated': False, 'source_sha256': hashes,
            'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}
  (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
  return binary


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--checkout', type=Path, required=True)
  parser.add_argument('--archive', type=Path, default=target_crypto.archive_path())
  parser.add_argument('--output', type=Path, required=True)
  args = parser.parse_args()
  try:
    print(build(args.checkout, args.archive, args.output))
  except subprocess.CalledProcessError as error:
    print(error.stderr)
    raise


if __name__ == '__main__':
  main()
