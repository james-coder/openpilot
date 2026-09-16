"""Development-machine signing tools. Never import this module in the comma relay.

No device access, remote signer, shell execution, password argument or environment
password support. Keys are unlocked through the local terminal only by the CLI.
"""

import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import resource
import stat
import sys

from Crypto.Hash import SHA256
from Crypto.PublicKey import ECC
from Crypto.Signature import DSS

from openpilot.tools.volt_gateway.authority import (
  AUTH_DOMAIN, IMAGE_DOMAIN, MAX_IMAGE, AuthorityError, Authorization, Challenge, ImageManifest, SignedImage,
)


PRIVATE_ROOT = Path(__file__).resolve().parents[2] / '.voltgw-private'


def sign_image(key, manifest: ImageManifest) -> SignedImage:
  return SignedImage(manifest, _sign(key, IMAGE_DOMAIN, manifest.pack()))


def authorize_image(key, image: SignedImage, challenge: Challenge, lease_ms: int) -> Authorization:
  image.verify(key.public_key())
  if image.manifest.device != challenge.device:
    raise AuthorityError('challenge targets a different device')
  token = Authorization(challenge, hashlib.sha256(image.manifest.pack()).digest(), lease_ms, b'')
  return Authorization(challenge, token.manifest_digest, lease_ms, _sign(key, AUTH_DOMAIN, token.body()))


def _sign(key, domain, body):
  if not key.has_private() or key.curve != 'NIST P-256' or domain not in (IMAGE_DOMAIN, AUTH_DOMAIN):
    raise AuthorityError('local P-256 signing key required')
  return DSS.new(key, 'deterministic-rfc6979', encoding='binary').sign(SHA256.new(domain + body))


def bounded_read(path: Path, limit: int, *, private=False) -> bytes:
  fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
  try:
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
      raise AuthorityError('invalid input file type/size')
    if private and (st.st_uid != os.getuid() or st.st_mode & 0o077 or st.st_nlink != 1):
      raise AuthorityError('private key ownership/permissions/link count')
    with os.fdopen(fd, 'rb', closefd=False) as f:
      result = f.read(limit + 1)
    if len(result) > limit:
      raise AuthorityError('input grew beyond limit')
    return result
  finally:
    os.close(fd)


def write_new(path: Path, body: bytes):
  fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
  with os.fdopen(fd, 'wb') as f:
    f.write(body)
    f.flush()
    os.fsync(f.fileno())


def private_directory(path: Path):
  if path.is_symlink():
    raise AuthorityError('private directory must not be a symlink')
  path.mkdir(mode=0o700, exist_ok=True)
  st = path.stat()
  if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
    raise AuthorityError('private directory ownership/permissions')


def generate_key(path: Path, password: str) -> bytes:
  if len(password) < 16:
    raise AuthorityError('use a passphrase of at least 16 characters')
  key = ECC.generate(curve='P-256')
  encrypted = key.export_key(format='PEM', use_pkcs8=True, passphrase=password,
                             protection='PBKDF2WithHMAC-SHA512AndAES256-CBC',
                             prot_params={'iteration_count':210000}).encode()
  write_new(path, encrypted)
  return key.public_key().export_key(format='DER')


def load_key(path: Path, password: str):
  encrypted = bounded_read(path, 4096, private=True)
  if not encrypted.startswith(b'-----BEGIN ENCRYPTED PRIVATE KEY-----'):
    raise AuthorityError('encrypted PKCS#8 key required')
  try:
    key = ECC.import_key(encrypted, passphrase=password)
  except (ValueError, TypeError, IndexError) as e:
    raise AuthorityError('cannot unlock signing key') from e
  if not key.has_private() or key.curve != 'NIST P-256':
    raise AuthorityError('P-256 private key required')
  return key


def hex_field(value, size):
  try:
    result = bytes.fromhex(value)
  except ValueError as e:
    raise argparse.ArgumentTypeError('hexadecimal value required') from e
  if len(result) != size or len(value) != size * 2:
    raise argparse.ArgumentTypeError(f'exactly {size * 2} hex digits required')
  return result


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  sub = parser.add_subparsers(dest='command', required=True)
  for cmd in ('keygen', 'sign', 'authorize'):
    p = sub.add_parser(cmd)
    p.add_argument('--device', required=True, type=lambda v: hex_field(v, 12))
    if cmd == 'sign':
      p.add_argument('--image', type=Path, required=True)
      p.add_argument('--layout', type=lambda v: hex_field(v, 32), required=True)
      p.add_argument('--build', type=lambda v: hex_field(v, 32), required=True)
      p.add_argument('--version', type=int, required=True)
    if cmd == 'authorize':
      p.add_argument('--manifest', type=Path, required=True)
      p.add_argument('--challenge', type=Path, required=True)
      p.add_argument('--lease-seconds', type=int, default=1800)
    if cmd != 'keygen':
      p.add_argument('--output', type=Path, required=True)
  args = parser.parse_args()
  # A signer must never silently become a remotely accessible password/signing API.
  if not sys.stdin.isatty():
    parser.error('local interactive terminal required; no stdin/environment password input')
  resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
  private_directory(PRIVATE_ROOT)
  device_dir = PRIVATE_ROOT / args.device.hex()
  private_directory(device_dir)
  key_path = device_dir / 'firmware-key.pem'
  if args.command == 'keygen':
    password = getpass.getpass('New local signing-key passphrase: ')
    if password != getpass.getpass('Repeat passphrase: '):
      parser.error('passphrases differ')
    public = generate_key(key_path, password)
    write_new(device_dir / 'firmware-public.der', public)
    print('Created encrypted local key. Back it up encrypted off-device before provisioning.')
    return
  if args.command == 'sign':
    image_bytes = bounded_read(args.image, MAX_IMAGE)
    manifest = ImageManifest(args.device, 1, args.layout, len(image_bytes), hashlib.sha256(image_bytes).digest(), args.build, args.version)
    manifest.pack()
    print(json.dumps({'device':args.device.hex(), 'image_bytes':len(image_bytes), 'sha256':manifest.digest.hex(),
                      'layout':args.layout.hex(), 'version':args.version}))
  else:
    image = SignedImage.unpack(bounded_read(args.manifest, 512))
    challenge = Challenge.unpack(bounded_read(args.challenge, 512))
    if image.manifest.device != args.device or challenge.device != args.device:
      parser.error('selected device does not match inputs')
    print(json.dumps({'device':args.device.hex(), 'phase':challenge.phase.name, 'sha256':image.manifest.digest.hex(),
                      'layout':image.manifest.layout.hex(), 'version':image.manifest.version, 'lease_seconds':args.lease_seconds}))
  key = load_key(key_path, getpass.getpass('Unlock local signing key for this operation: '))
  result = sign_image(key, manifest) if args.command == 'sign' else authorize_image(key, image, challenge, args.lease_seconds * 1000)
  write_new(args.output, result.pack())
  print('Wrote public signed artifact; no private key was transmitted.')


if __name__ == '__main__':
  main()
