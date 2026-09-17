"""Off-device checks with explicit evidence gaps. Never deploys or opens hardware."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

from openpilot.tools.volt_gateway.target_crypto import archive_path


def junit_summary(path):
  if not path.is_file():
    return {'status': 'failed', 'reason': 'missing test report'}
  try:
    root = ET.parse(path).getroot()
  except ET.ParseError:
    return {'status': 'failed', 'reason': 'malformed test report'}
  cases = list(root.iter('testcase'))
  skipped = sum(case.find('skipped') is not None for case in cases)
  failed = sum(case.find('failure') is not None or case.find('error') is not None for case in cases)
  return {'status': 'failed' if failed or not cases else 'incomplete' if skipped else 'passed',
          'total': len(cases), 'passed': len(cases) - skipped - failed, 'failed': failed, 'skipped': skipped,
          'skipped_tests': [case.attrib.get('classname', '') + '.' + case.attrib.get('name', '')
                            for case in cases if case.find('skipped') is not None]}


def resource_limit(scratch=None):
  # WSL's ext4 free-space figure does not bound the backing Windows volume.
  for path in (Path.cwd(),Path('/mnt/c')):
    if path.exists() and shutil.disk_usage(path).free<3*1024**3:
      return 'filesystem reserve below 3 GiB'
  if scratch is not None:
    size=0
    for directory,_,files in os.walk(scratch,followlinks=False):
      for name in files:
        try:
          size+=(Path(directory)/name).stat(follow_symlinks=False).st_size
        except FileNotFoundError:
          continue
        if size>1024**3:
          return 'disposable test scratch exceeded 1 GiB'
  return None


def command(argv, cwd, log, timeout=600, scratch=None):
  started = time.monotonic()
  timed_out = False
  limit=resource_limit(scratch)
  # Stream into a private artifact file, not an unbounded in-memory PIPE.
  with log.open('x') as output:
    if limit:
      output.write('Not started: '+limit+'\n')
      return {'status':'failed','resource_limit':limit,'returncode':None,'timed_out':False,'log':log.name}
    env=os.environ.copy()
    if scratch is not None:
      env['TMPDIR']=str(scratch)
    with subprocess.Popen(argv, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT, start_new_session=True) as child:
      while True:
        remaining=timeout-(time.monotonic()-started)
        limit=resource_limit(scratch)
        if log.stat().st_size>50*1024**2:
          limit='command log exceeded 50 MiB'
        if remaining<=0 or limit:
          timed_out=remaining<=0
          break
        try:
          code=child.wait(timeout=min(2,remaining))
          break
        except subprocess.TimeoutExpired:
          continue
      if timed_out or limit:
        os.killpg(child.pid, signal.SIGTERM)
        try:
          code = child.wait(timeout=5)
        except subprocess.TimeoutExpired:
          os.killpg(child.pid, signal.SIGKILL)
          code = child.wait()
  return {'status': 'passed' if code == 0 and not timed_out and not limit else 'failed', 'returncode': code,
          'resource_limit':limit,
          'timed_out': timed_out, 'seconds': round(time.monotonic() - started, 3), 'log': log.name,
          'log_sha256': hashlib.sha256(log.read_bytes()).hexdigest()}


def source_hashes(source: Path, repo: Path):
  return {str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest()
          for p in sorted(source.rglob('*')) if p.is_file() and p.suffix in ('.py', '.c', '.h', '.S', '.ld', '.rs', '.json', '.txt', '.ps1')
          and '__pycache__' not in p.parts}


def run(output: Path):
  # Only generated test/build scratch belongs here, never working checkouts,
  # owner keys, signed images, physical readbacks or retained evidence.
  with tempfile.TemporaryDirectory(prefix='voltgw-validation-') as scratch:
    return run_with_scratch(output,Path(scratch))


def run_with_scratch(output: Path,scratch: Path):
  repo = Path(__file__).resolve().parents[2]
  output = output.resolve()
  output.mkdir(parents=True, exist_ok=False, mode=0o700)
  source = Path(__file__).parent
  hashes = source_hashes(source, repo)
  report = {'source_sha256': hashes, 'python': sys.version, 'checks': {}, 'production_ready': False,
            'hardware': {'status': 'hardware-unverified', 'changed': False},
            'open_gates': ['physical simultaneous SWCAN/HSCAN RX and verified harness',
                           'whole-image silicon timing and power-loss/recovery validation',
                           'owner-controlled provisioning/signing and loader write protection',
                           'evidence-selected transport IDs and primary-Tres compatibility',
                           'physical TX limits and host independence', 'parked validation'],
            'scope': 'offline tests/lint/Cortex-M4 object build; no certification or hardware claim'}
  destination = output / 'report.json'
  destination.write_text(json.dumps(report, indent=2) + '\n')
  steps = [
    ('pytest', [sys.executable, '-m', 'pytest', 'tools/volt_gateway', '-q', '-n', '0',
                '--basetemp', str(scratch/'pytest'), '--junitxml', str(output / 'pytest.xml')]),
    ('ruff', [sys.executable, '-m', 'ruff', 'check', 'tools/volt_gateway']),
    ('cortex_m4_build', [sys.executable, '-m', 'tools.volt_gateway.build_core', '--output', str(output / 'core')]),
  ]
  crypto_archive = archive_path()
  if crypto_archive.is_file():
    for mode in ('arm', 'native'):
      steps.append(('crypto_' + mode, [sys.executable, '-m', 'tools.volt_gateway.target_crypto',
                                      '--archive', str(crypto_archive), '--output', str(output / ('crypto-' + mode)),
                                      *(['--native'] if mode == 'native' else [])]))
  else:
    report['checks']['crypto_build'] = {'status': 'incomplete', 'reason': 'pinned Mbed TLS archive absent'}
  boot_checkout = os.environ.get('VOLTGW_MCUBOOT_CHECKOUT')
  if boot_checkout and crypto_archive.is_file():
    steps.append(('mcuboot_direct_xip', [sys.executable, '-m', 'tools.volt_gateway.mcuboot_port',
                                       '--checkout', boot_checkout, '--archive', str(crypto_archive),
                                       '--output', str(output / 'mcuboot')]))
    steps.append(('mcuboot_arm', [sys.executable, '-m', 'tools.volt_gateway.mcuboot_port',
                                 '--checkout', boot_checkout, '--archive', str(crypto_archive),
                                 '--output', str(output / 'mcuboot-arm'), '--arm']))
    history = os.environ.get('VOLTGW_PANDA_HISTORY')
    if history:
      steps.append(('board_images', [sys.executable, '-m', 'tools.volt_gateway.board_build',
                                    '--checkout', boot_checkout, '--archive', str(crypto_archive),
                                    '--usb-repository', history, '--output', str(output / 'board')]))
    else:
      report['checks']['board_images'] = {'status': 'incomplete', 'reason': 'historical White USB source absent'}
  else:
    report['checks']['mcuboot_direct_xip'] = {'status': 'incomplete', 'reason': 'pinned boot/crypto dependency absent'}
  for name in ('authority', 'observe', 'update', 'status_led', 'white_board', 'white_flash',
               'white_watchdog', 'white_startup', 'white_clock', 'white_rng', 'white_can', 'white_runtime',
               'white_safety', 'application', 'recovery_transport', 'recovery_link', 'recovery_service', 'recovery_runtime', 'recovery_flash'):
    steps.append(('analyze_' + name, ['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-fanalyzer', '-c',
                                    str(source / 'firmware' / (name + '.c')), '-o', str(output / (name + '-analyzed.o'))]))
  for name, argv in steps:
    print(f'Running {name}', flush=True)
    result = command(argv, repo, output / (name + '.log'),scratch=scratch)
    if name == 'pytest':
      result['tests'] = junit_summary(output / 'pytest.xml')
      if result['status'] == 'passed':
        result['status'] = result['tests']['status']
    report['checks'][name] = result
    destination.write_text(json.dumps(report, indent=2) + '\n')
    if result.get('resource_limit'):
      break  # Do not start more builds while the host is short on space.
  statuses = [r['status'] for r in report['checks'].values()]
  if source_hashes(source, repo) != hashes:
    report['checks']['source_consistency'] = {'status': 'failed', 'reason': 'source changed during validation'}
    statuses.append('failed')
  report['status'] = 'failed' if 'failed' in statuses else 'incomplete' if 'incomplete' in statuses else 'passed'
  report['status_meaning'] = 'selected offline checks only; production readiness is independently false'
  destination.write_text(json.dumps(report, indent=2) + '\n')
  return report


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--output', type=Path, required=True, help='new private artifact directory')
  args = parser.parse_args()
  result = run(args.output)
  print(json.dumps({'status': result['status'], 'production_ready': result['production_ready'],
                    'report': str(args.output / 'report.json')}))
  raise SystemExit(0 if result['status'] == 'passed' else 1)


if __name__ == '__main__':
  main()
