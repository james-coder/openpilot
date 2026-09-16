"""Public-only release packaging and staged-secret checks. No deployment/SSH.

Release contents are an exact allowlist, not a recursive repository copy. Signing
and routine provisioning keys are never package inputs. Real flash dumps must
remain outside Git and outside these packages.
"""

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile

from openpilot.tools.volt_gateway.authority import MAX_IMAGE, AuthorityError, SignedImage, public_key


PEM_PRIVATE = re.compile(rb'^-----BEGIN (?:ENCRYPTED |RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----\r?$', re.MULTILINE)
FORBIDDEN_NAMES = {'.voltgw-private', 'firmware-key.pem', 'routine-key.bin', 'pairing-secret.bin'}


def reject_secret(name: str, content: bytes):
  if FORBIDDEN_NAMES.intersection(Path(name).parts) or PEM_PRIVATE.search(content):
    raise AuthorityError('secret material/path rejected')
  # Recognize standalone DER private keys as well as PEM, even under renamed files.
  if len(content) <= 4096:
    from Crypto.PublicKey import ECC, RSA
    for importer in (ECC.import_key, RSA.import_key):
      try:
        key = importer(content)
      except (ValueError, TypeError, IndexError):
        continue
      if key.has_private():
        raise AuthorityError('private key rejected')


def package(image: bytes, signed: bytes, verification_key: bytes) -> bytes:
  if not isinstance(image, bytes) or not 1 <= len(image) <= MAX_IMAGE:
    raise AuthorityError('image bounds')
  manifest = SignedImage.unpack(signed)
  key = public_key(verification_key)
  manifest.verify(key)
  if manifest.manifest.size != len(image) or manifest.manifest.digest != hashlib.sha256(image).digest():
    raise AuthorityError('package image does not match signed manifest')
  contents = {'image.bin': image, 'manifest.bin': signed, 'firmware-public.der': key.export_key(format='DER')}
  for name, content in contents.items():
    reject_secret(name, content)
  contents['checksums.json'] = json.dumps({n:hashlib.sha256(b).hexdigest() for n, b in contents.items()},
                                         sort_keys=True).encode()
  out = io.BytesIO()
  with tarfile.open(fileobj=out, mode='w', format=tarfile.USTAR_FORMAT) as archive:
    for name, body in sorted(contents.items()):
      entry = tarfile.TarInfo(name)
      entry.size, entry.mode, entry.mtime = len(body), 0o644, 0
      archive.addfile(entry, io.BytesIO(body))
  return out.getvalue()


def check_staged(repo: Path) -> list[str]:
  paths = subprocess.check_output(['git', '-C', str(repo), 'diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z']).split(b'\0')
  rejected = []
  for path in filter(None, paths):
    name = path.decode('utf-8', errors='surrogateescape')
    # Read the index, not a possibly different working-tree file.
    spec = ':' + name
    size = int(subprocess.check_output(['git', '-C', str(repo), 'cat-file', '-s', spec]))
    if size > 2 * MAX_IMAGE:
      rejected.append(name)
      continue
    body = subprocess.check_output(['git', '-C', str(repo), 'show', spec])
    try:
      reject_secret(name, body)
    except AuthorityError:
      rejected.append(name)
  return rejected


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  sub = parser.add_subparsers(dest='command', required=True)
  staged = sub.add_parser('check-staged')
  staged.add_argument('--repo', type=Path, default=Path.cwd())
  args = parser.parse_args()
  rejected = check_staged(args.repo)
  # Do not print file contents or potential secret-bearing paths.
  if rejected:
    parser.exit(1, f'Rejected {len(rejected)} staged file(s): secret/path or size policy. Inspect the index locally.\n')
  print('Staged secret/path checks passed (not a general-purpose secret detector).')


if __name__ == '__main__':
  main()
