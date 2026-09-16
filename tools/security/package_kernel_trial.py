"""Build a signed trial image off-device; never writes a device partition."""
import argparse
import hashlib
from pathlib import Path
import struct
import subprocess

BASELINE = '3ade1f5da238dc4747827db5204f1dcfc0f53a6d418f50bdd47569dfd09504b1'


def package(backup, kernel, key, output):
  original = backup.read_bytes()
  if len(original) != 67108864 or hashlib.sha256(original).hexdigest() != BASELINE:
    raise RuntimeError('Not the verified Bluetooth-enabled backup')
  fields = struct.unpack_from('<10I', original, 8)
  if original[:8] != b'ANDROID!' or fields[2] or fields[4] or fields[8] or fields[7] != 4096:
    raise RuntimeError('Unexpected boot header')
  data = kernel.read_bytes()
  if data[:2] != b'\x1f\x8b' or not 5_000_000 < len(data) < 60_000_000:
    raise RuntimeError('Unexpected kernel payload')
  output.mkdir(mode=0o700, parents=True, exist_ok=False)
  page = fields[7]
  old_size = page + ((fields[0] + page - 1) // page) * page
  old_unsigned = output / 'baseline.unsigned'
  old_unsigned.write_bytes(original[:old_size])
  old_signature = output / 'baseline.signature'
  old_signature.write_bytes(original[old_size:old_size + 256])
  public = output / 'public.pem'
  public.write_bytes(subprocess.check_output(['openssl', 'pkey', '-in', str(key), '-pubout']))
  subprocess.run(['openssl', 'dgst', '-sha256', '-verify', str(public), '-signature', str(old_signature), str(old_unsigned)], check=True)
  header = bytearray(original[:page])
  struct.pack_into('<I', header, 8, len(data))
  header[576:608] = hashlib.sha1(data + struct.pack('<I', len(data)) + struct.pack('<II', 0, 0)).digest() + bytes(12)
  unsigned = output / 'candidate.unsigned'
  unsigned.write_bytes(bytes(header) + data + bytes((-len(data)) % page))
  signature = output / 'candidate.signature'
  signature.write_bytes(subprocess.check_output(['openssl', 'dgst', '-sha256', '-sign', str(key), str(unsigned)]))
  subprocess.run(['openssl', 'dgst', '-sha256', '-verify', str(public), '-signature', str(signature), str(unsigned)], check=True)
  sig = signature.read_bytes()
  if len(sig) != 256:
    raise RuntimeError('Unexpected signing key size')
  image = unsigned.read_bytes() + sig + bytes(2048 - len(sig))
  (output / 'candidate.img').write_bytes(image)
  print('candidate_bytes', len(image))
  print('candidate_sha256', hashlib.sha256(image).hexdigest())
  print('trial_partition_sha256', hashlib.sha256(image + original[len(image):]).hexdigest())


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  for name in ('backup', 'kernel', 'key', 'output'):
    parser.add_argument('--' + name, type=Path, required=True)
  args = parser.parse_args()
  package(args.backup, args.kernel, args.key, args.output)
