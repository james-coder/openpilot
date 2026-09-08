"""Archive completed route logs/road video over SSH and verify both ends by SHA-256.

No device settings, uploads, driver-camera files, or deletion. A verified manifest
is written only if all expected files exist and match the remote recordings.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
from openpilot.tools.profiling.volt_braking import atomic_json

NAMES = ('rlog.zst', 'qlog.zst', 'fcamera.hevc', 'ecamera.hevc')


def archive(destination, routes, host='comma@192.168.98.187', alias='[localhost]:2222', verify_only=False, local_manifest=None):
  if not routes or any(not re.fullmatch(r'[0-9a-f]{8}--[0-9a-f]{10}', route) for route in routes):
    raise ValueError('Use complete route identifiers')
  ssh = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', '-o', 'StrictHostKeyChecking=yes', '-o', f'HostKeyAlias={alias}', '-o', 'UpdateHostKeys=no']
  destination.mkdir(parents=True, exist_ok=True)
  if not verify_only and local_manifest is None:
    # Fetch logs first so video transfer cannot delay evidence extraction.
    for names in (NAMES[:2], NAMES[2:]):
      args = ['rsync', '-rt', '--partial', '--prune-empty-dirs']
      args += [f'--include=/{route}--*/' for route in routes]
      args += [f'--include={name}' for name in names]
      args += ['--exclude=*', '-e', shlex.join(ssh), host + ':/data/media/0/realdata/', str(destination) + '/']
      subprocess.run(args, check=True)
  remote_code = '''import hashlib,json,pathlib,sys
routes=json.loads(sys.argv[1]);names=json.loads(sys.argv[2]);result=[]
for route in routes:
 for segment in pathlib.Path('/data/media/0/realdata').glob(route+'--*'):
  for name in names:
   p=segment/name
   if not p.is_file():raise SystemExit('Missing required recording: '+str(p))
   h=hashlib.sha256()
   with p.open('rb') as f:
    for chunk in iter(lambda:f.read(4*1024*1024),b''):h.update(chunk)
   result.append({'path':segment.name+'/'+name,'size':p.stat().st_size,'sha256':h.hexdigest()})
print(json.dumps(result))
'''
  command = shlex.join(['python3', '-c', remote_code, json.dumps(routes), json.dumps(NAMES)])
  remote = json.loads(local_manifest.read_text()) if local_manifest else json.loads(subprocess.check_output(ssh + [host, command]))
  allowed = re.compile(r'(?:' + '|'.join(re.escape(r) for r in routes) + r')--[0-9]+/(?:' + '|'.join(re.escape(n) for n in NAMES) + r')')
  if len({f['path'] for f in remote}) != len(remote) or any(not allowed.fullmatch(f['path']) for f in remote):
    raise ValueError('Manifest contains duplicate or unexpected paths')
  if not local_manifest:
    # Preserve the remote hashes even if the device leaves before local transfer verification.
    atomic_json(destination.parent / 'remote-manifest.json', remote)
  for route in routes:
    parts = sorted({int(f['path'].split('/')[0].rsplit('--', 1)[1]) for f in remote if f['path'].startswith(route + '--')})
    if not parts or parts != list(range(parts[-1] + 1)):
      raise ValueError(f'Missing segments in route {route}')
    for part in parts:
      names = {f['path'].split('/')[1] for f in remote if f['path'].split('/')[0] == f'{route}--{part}'}
      if names != set(NAMES):
        raise ValueError('Manifest must include both road cameras and both logs for each segment')
  for f in remote:
    p = destination / f['path']
    if not p.is_file() or p.stat().st_size != f['size']:
      raise ValueError(f'Incomplete local recording: {p}')
    h = hashlib.sha256()
    with p.open('rb') as stream:
      for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
        h.update(chunk)
    if h.hexdigest() != f['sha256']:
      raise ValueError(f'Hash mismatch: {p}')
  manifest = {'version': 1, 'verified': True, 'routes': routes, 'files': remote, 'bytes': sum(f['size'] for f in remote)}
  atomic_json(destination.parent / 'archive-manifest.json', manifest)
  print(json.dumps({'verified_files': len(remote), 'bytes': manifest['bytes']}))


if __name__ == '__main__':
  p = argparse.ArgumentParser(description=__doc__)
  p.add_argument('destination', type=Path)
  p.add_argument('routes', nargs='+')
  p.add_argument('--host', default='comma@192.168.98.187')
  p.add_argument('--host-key-alias', default='[localhost]:2222')
  p.add_argument('--verify-only', action='store_true')
  p.add_argument('--local-manifest', type=Path, help='Verify locally against previously captured remote hashes')
  a = p.parse_args()
  archive(a.destination, a.routes, a.host, a.host_key_alias, a.verify_only, a.local_manifest)
