"""Build-only historical Panda comparison. Never runs a historical Makefile.

Only explicitly named ARM compiler/objcopy commands run, in fresh directories.
No signing private key, USB library, DFU tool, flash target, or device operation.
Generated sources/binaries/reports are research artifacts, NOT deployment images.
"""

import argparse
import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess
import tarfile
import tempfile


REFERENCE = '3b35621671aaa6de3fc66d85d30e4208a77e2489'


def git(repo: Path, *args: str) -> bytes:
  return subprocess.check_output(['git', '-C', str(repo), *args])


def signature_digest(repo: Path, ref: str, signature: bytes) -> str:
  from Crypto.PublicKey import RSA
  key = RSA.import_key(git(repo, 'show', ref + ':certs/release.pub'))
  if len(signature) != 128 or key.size_in_bytes() != 128 or int.from_bytes(signature, 'big') >= key.n:
    raise ValueError('historical signature size/range')
  block = pow(int.from_bytes(signature, 'big'), key.e, key.n).to_bytes(128, 'big')
  if block[:-20] != b'\0\1' + b'\xff' * 105 + b'\0':
    raise ValueError('signature does not match historical release-key encoding')
  return block[-20:].hex()


def signing_input(code: bytes) -> bytes:
  if len(code) < 8:
    raise ValueError('application too short')
  return code[4:] + b'VERS' + struct.pack('<I', 2)


def build(repo: Path, ref: str, compiler: Path, output: Path, target_sha1: str) -> dict:
  commit = git(repo, 'rev-parse', '--verify', '--end-of-options', ref + '^{commit}').decode().strip()
  output.mkdir(parents=True, exist_ok=False)
  archive = git(repo, 'archive', commit)
  compiler = compiler.resolve(strict=True)
  objcopy = compiler.with_name('arm-none-eabi-objcopy')
  version = subprocess.check_output([str(compiler), '--version'], text=True).splitlines()[0]
  flags = ['-g', '-Wall', '-Wextra', '-Wstrict-prototypes', '-Werror', '-mlittle-endian', '-mthumb', '-mcpu=cortex-m4',
           '-mhard-float', '-DSTM32F4', '-DSTM32F413xx', '-mfpu=fpv4-sp-d16', '-fsingle-precision-constant',
           '-Iinc', '-I../', '-nostdlib', '-fno-builtin', '-std=gnu11', '-Os', '-DEON']
  result = {'commit': commit, 'compiler': version, 'compiler_sha256': hashlib.sha256(compiler.read_bytes()).hexdigest(),
            'objcopy_sha256': hashlib.sha256(objcopy.read_bytes()).hexdigest(),
            'source_archive_sha256': hashlib.sha256(archive).hexdigest(), 'flags': flags,
            'target_sha1': target_sha1, 'build_type': 'EON RELEASE; no ALLOW_DEBUG; unknown gitversion',
            'historical_toolchain_match': False, 'device_access': False, 'runs': []}
  with tempfile.TemporaryDirectory(prefix='voltgw-provenance-') as tmp:
    for run in range(2):
      directory = Path(tmp) / str(run)
      directory.mkdir()
      with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        source.extractall(directory, filter='data')
      board = directory / 'board'
      (board / 'obj').mkdir(exist_ok=True)
      declared_version = (directory / 'VERSION').read_text().strip()
      if declared_version != 'v1.7.3':
        raise ValueError('this reconstruction recipe only covers v1.7.3')
      (board / 'obj/gitversion.h').write_text('const uint8_t gitversion[] = "v1.7.3-EON-unknown-RELEASE";\n')
      # App main.c does not include cert.h: no signing operation is needed.
      commands = [[str(compiler), *flags, '-c', 'startup_stm32f413xx.s', '-o', 'obj/startup.o'],
                  [str(compiler), *flags, '-c', 'main.c', '-o', 'obj/main.o'],
                  [str(compiler), '-Wl,--section-start,.isr_vector=0x08004000', *flags, '-Tstm32_flash.ld',
                   '-o', 'obj/application.elf', 'obj/startup.o', 'obj/main.o'],
                  [str(objcopy), '-O', 'binary', 'obj/application.elf', 'obj/application.bin']]
      logs, success = [], True
      for command in commands:
        p = subprocess.run(command, cwd=board, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        logs.append({'argv': command, 'returncode': p.returncode, 'output': p.stdout})
        if p.returncode:
          success = False
          break
      (output / f'build-{run}.json').write_text(json.dumps(logs, indent=2) + '\n')
      row = {'success': success}
      if success:
        code = (board / 'obj/application.bin').read_bytes()
        body = signing_input(code)
        row.update({'code_bytes': len(code), 'code_sha256': hashlib.sha256(code).hexdigest(),
                    'signed_body_sha1': hashlib.sha1(body).hexdigest(), 'signed_body_sha256': hashlib.sha256(body).hexdigest(),
                    'matches_reported_signature_digest': hashlib.sha1(body).hexdigest() == target_sha1})
        (output / f'application-{run}.UNSIGNED.bin').write_bytes(code)
      result['runs'].append(row)
  result['reproducible_code'] = (all(r['success'] for r in result['runs']) and
                                 result['runs'][0]['code_sha256'] == result['runs'][1]['code_sha256'])
  (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
  return result


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--repo', type=Path, required=True)
  parser.add_argument('--ref', default=REFERENCE)
  parser.add_argument('--compiler', type=Path, required=True)
  parser.add_argument('--output', type=Path, required=True, help='new artifact directory; must not already exist')
  parser.add_argument('--inventory', type=Path, required=True)
  args = parser.parse_args()
  inventory = json.loads(args.inventory.read_text())
  target = signature_digest(args.repo, REFERENCE, bytes.fromhex(inventory['application_signature_hex']))
  print(json.dumps(build(args.repo, args.ref, args.compiler, args.output, target), indent=2))


if __name__ == '__main__':
  main()
