"""Copy a bounded, reproducible sample of completed comma recordings over SSH."""
import argparse
import hashlib
import json
import re
import shlex
import shutil
import subprocess
from pathlib import Path

LOG_ROOT = '/data/media/0/realdata'
SEGMENT = re.compile(r'^[0-9a-f]{8}--[0-9a-f]{10}--[0-9]+$')
INVENTORY = '''
import json, os
from pathlib import Path
root = Path('/data/media/0/realdata')
assert Path('/data/params/d/IsOffroad').read_bytes().strip() == b'1', 'device must be offroad'
out = []
for d in sorted(root.iterdir()):
  if not d.is_dir() or '--' not in d.name: continue
  files = list(d.iterdir())
  if any(f.name.endswith('.lock') for f in files): continue
  selected = {f.name: f.stat().st_size for f in files if f.name in ('fcamera.hevc', 'ecamera.hevc', 'qlog.zst', 'rlog.zst')}
  if all(k in selected for k in ('fcamera.hevc', 'ecamera.hevc', 'qlog.zst')):
    out.append({'segment': d.name, 'files': selected})
print(json.dumps(out))
'''


def choose_segments(rows: list[dict], count: int) -> list[dict]:
  rows = sorted(rows, key=lambda r: (r['segment'].rsplit('--', 1)[0], int(r['segment'].rsplit('--', 1)[1])))
  if count < 1:
    raise ValueError('count must be positive')
  if count >= len(rows):
    return rows
  # Span the retained time range; selection is frozen in the manifest on first run.
  return [rows[round(i * (len(rows) - 1) / (count - 1))] for i in range(count)] if count > 1 else rows[:1]


def checksum(path: Path) -> str:
  with path.open('rb') as f:
    return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--host', default='comma@192.168.98.187')
  parser.add_argument('--host-key-alias', help='Previously verified SSH identity, e.g. [localhost]:2222')
  parser.add_argument('--output', type=Path, required=True)
  parser.add_argument('--count', type=int, default=30)
  parser.add_argument('--bwlimit', type=int, default=4096, help='rsync KiB/s limit')
  parser.add_argument('--inventory-only', action='store_true')
  args = parser.parse_args()
  if args.host.startswith('-') or not re.fullmatch(r'[\w.@:-]+', args.host):
    parser.error('invalid SSH host')
  if args.output.resolve().is_relative_to('/mnt/c'):
    parser.error('bulk recordings must not be stored on the Windows system drive')
  ssh = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=yes', '-o', 'UpdateHostKeys=no',
         '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2']
  if args.host_key_alias:
    ssh += ['-o', f'HostKeyAlias={args.host_key_alias}']

  def remote(command: str) -> str:
    return subprocess.check_output([*ssh, args.host, command], text=True, timeout=30)

  rows = json.loads(remote('python3 -c ' + shlex.quote(INVENTORY)))
  if args.inventory_only:
    print(json.dumps(rows, indent=2))
    return
  args.output.mkdir(parents=True, exist_ok=True)
  manifest_path = args.output / 'manifest.json'
  if manifest_path.exists():
    manifest = json.loads(manifest_path.read_text())
    if manifest['host'] != args.host:
      parser.error('manifest belongs to a different host')
  else:
    manifest = {'version': 1, 'host': args.host, 'segments': choose_segments(rows, args.count), 'verified': {}}
  total = sum(size for row in manifest['segments'] for size in row['files'].values())
  if shutil.disk_usage(args.output).free < total + 1024**3:
    parser.error('insufficient destination space (including 1 GiB reserve)')

  def save():
    tmp = manifest_path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(manifest, indent=2) + '\n')
    tmp.replace(manifest_path)

  save()
  for row in manifest['segments']:
    name = row['segment']
    if not SEGMENT.fullmatch(name):
      raise ValueError(f'invalid segment: {name}')
    dest = args.output / name
    dest.mkdir(exist_ok=True)
    for filename, size in row['files'].items():
      if filename not in ('fcamera.hevc', 'ecamera.hevc', 'qlog.zst', 'rlog.zst'):
        raise ValueError(f'invalid filename: {filename}')
      key = f'{name}/{filename}'
      local = dest / filename
      if local.exists() and key in manifest['verified'] and checksum(local) == manifest['verified'][key]:
        continue
      source = f'{LOG_ROOT}/{key}'
      # Recheck before each transfer; a recording deleted in the meantime is a
      # visible failure, never silently substituted by a different segment.
      guard = "test \"$(cat /data/params/d/IsOffroad)\" = 1"
      remote(guard)
      print(f'Copying {key} ({size / 1024**2:.1f} MiB)', flush=True)
      with subprocess.Popen(['rsync', '--partial', '--append-verify', f'--bwlimit={args.bwlimit}',
                             '-e', shlex.join(ssh), f'{args.host}:{source}', str(local)]) as transfer:
        try:
          while True:
            try:
              result = transfer.wait(timeout=5)
              if result:
                raise subprocess.CalledProcessError(result, transfer.args)
              break
            except subprocess.TimeoutExpired:
              remote(guard)
        except BaseException:
          transfer.terminate()
          try:
            transfer.wait(timeout=5)
          except subprocess.TimeoutExpired:
            transfer.kill()
          raise
      digest = remote(f'{guard} && sha256sum {shlex.quote(source)}').split()[0]
      if local.stat().st_size != size or checksum(local) != digest:
        raise RuntimeError(f'source changed or checksum mismatch: {key}')
      manifest['verified'][key] = digest
      save()
  print(f'Verified {len(manifest["verified"])} files in {args.output}', flush=True)


if __name__ == '__main__':
  main()
