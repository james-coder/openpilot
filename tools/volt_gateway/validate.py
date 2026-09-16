"""Off-device checks with explicit evidence gaps. Never deploys or opens hardware."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
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


def command(argv, cwd, log, timeout=600):
  started = time.monotonic()
  timed_out = False
  # Stream into a private artifact file, not an unbounded in-memory PIPE.
  with log.open('x') as output:
    with subprocess.Popen(argv, cwd=cwd, stdout=output, stderr=subprocess.STDOUT, start_new_session=True) as child:
      try:
        code = child.wait(timeout=timeout)
      except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(child.pid, signal.SIGTERM)
        try:
          code = child.wait(timeout=5)
        except subprocess.TimeoutExpired:
          os.killpg(child.pid, signal.SIGKILL)
          code = child.wait()
  return {'status': 'passed' if code == 0 and not timed_out else 'failed', 'returncode': code,
          'timed_out': timed_out, 'seconds': round(time.monotonic() - started, 3), 'log': log.name,
          'log_sha256': hashlib.sha256(log.read_bytes()).hexdigest()}


def run(output: Path):
  repo = Path(__file__).resolve().parents[2]
  output = output.resolve()
  output.mkdir(parents=True, exist_ok=False, mode=0o700)
  source = Path(__file__).parent
  hashes = {str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(source.rglob('*')) if p.is_file() and p.suffix in ('.py', '.c', '.h', '.ld', '.rs', '.json', '.txt')
            and '__pycache__' not in p.parts}
  report = {'source_sha256': hashes, 'python': sys.version, 'checks': {}, 'production_ready': False,
            'hardware': {'status': 'hardware-unverified', 'changed': False},
            'open_gates': ['simultaneous SWCAN/Object RX', 'complete target firmware/crypto/RNG/loader',
                           'chosen flash layout and direct-XIP revert', 'provisioned transport IDs and keys',
                           'hardware TX limits and host independence', 'parked validation'],
            'scope': 'offline tests/lint/Cortex-M4 object build; no certification or hardware claim'}
  destination = output / 'report.json'
  destination.write_text(json.dumps(report, indent=2) + '\n')
  steps = [
    ('pytest', [sys.executable, '-m', 'pytest', 'tools/volt_gateway', '-q', '-n', '0', '--junitxml', str(output / 'pytest.xml')]),
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
  else:
    report['checks']['mcuboot_direct_xip'] = {'status': 'incomplete', 'reason': 'pinned boot/crypto dependency absent'}
  for name in ('authority', 'observe', 'update', 'status_led'):
    steps.append(('analyze_' + name, ['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-fanalyzer', '-c',
                                    str(source / 'firmware' / (name + '.c')), '-o', str(output / (name + '-analyzed.o'))]))
  for name, argv in steps:
    print(f'Running {name}', flush=True)
    result = command(argv, repo, output / (name + '.log'))
    if name == 'pytest':
      result['tests'] = junit_summary(output / 'pytest.xml')
      if result['status'] == 'passed':
        result['status'] = result['tests']['status']
    report['checks'][name] = result
    destination.write_text(json.dumps(report, indent=2) + '\n')
  statuses = [r['status'] for r in report['checks'].values()]
  if any(not (repo / name).is_file() or hashlib.sha256((repo / name).read_bytes()).hexdigest() != digest
         for name, digest in hashes.items()):
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
