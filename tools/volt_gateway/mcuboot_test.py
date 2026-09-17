"""Pinned upstream bootloader simulator checks; no hardware or production keys.

This runs actual MCUboot C through its upstream flash simulator. Direct-XIP
revert is NOT enabled by the upstream direct-xip Cargo feature; report that gap.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time


MCUBOOT_COMMIT = '6d3b3d2c38ab20c242e5b9abb04d050086383eb2'
MBEDTLS_COMMIT = '2ca6c285a0dd3f33982dd57299012dacab1ff206'
CASES = (
  ('direct-xip-selection', 'sig-ecdsa,direct-xip', 'direct_xip_first'),
  ('bad-signature', 'sig-ecdsa', 'bad_secondary_slot'),
)


def run(repo: Path, output: Path):
  for directory, expected in ((repo, MCUBOOT_COMMIT), (repo / 'ext/mbedtls', MBEDTLS_COMMIT)):
    actual = subprocess.check_output(['git', '-C', str(directory), 'rev-parse', 'HEAD'], text=True).strip()
    dirty = subprocess.check_output(['git', '-C', str(directory), 'status', '--porcelain', '--untracked-files=no'], text=True)
    if actual != expected or dirty:
      raise ValueError('upstream simulator must match the clean pinned source/submodule')
  if Path('/mnt/c').exists() and shutil.disk_usage('/mnt/c').free < 512 * 1024 * 1024:
    raise RuntimeError('insufficient Windows backing-disk headroom; do not install/build more tools')
  output.mkdir(parents=True, exist_ok=False)
  env = dict(os.environ, CARGO_PROFILE_DEV_DEBUG='0', CARGO_PROFILE_TEST_DEBUG='0', CARGO_INCREMENTAL='0')
  env.pop('MCUBOOT_SKIP_SLOW_TESTS', None)
  env.pop('MCUBOOT_DEBUG_DUMP', None)
  env['RUST_LOG'] = 'warn'
  report = {'mcuboot_commit':MCUBOOT_COMMIT, 'mbedtls_commit':MBEDTLS_COMMIT,
            'cargo_lock_sha256':hashlib.sha256((repo / 'Cargo.lock').read_bytes()).hexdigest(),
            'cases':[], 'hardware_tested':False, 'f413_flash_geometry_tested':False, 'direct_xip_revert_tested':False,
            'limitations':['upstream test geometries, not the Panda flash layout',
                           'swap-revert coverage does not prove direct-XIP revert',
                           'CAN recovery, our relay and operator authority not part of upstream tests']}
  # The upstream CLI mixes permanent/trial fixtures for revert tests. Use the
  # separately recorded mcuboot_targeted runner for STM32F4 recovery coverage.
  for name, features, test_filter in CASES:
    command = ['cargo', 'test', '--locked', '-p', 'bootsim', '--features', features,
               '--test', 'core', test_filter, '--', '--test-threads=2']
    print(json.dumps({'starting':name}), flush=True)
    started = time.monotonic()
    with subprocess.Popen(command, cwd=repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, start_new_session=True) as process:
      timed_out = False
      try:
        log, _ = process.communicate(timeout=900)
      except subprocess.TimeoutExpired:
        timed_out = True
        # Only this newly created process group, including cargo's test child.
        os.killpg(process.pid, signal.SIGTERM)
        try:
          log, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
          os.killpg(process.pid, signal.SIGKILL)
          log, _ = process.communicate()
      returncode = process.returncode
    (output / (name + '.log')).write_text(log)
    marker = 'test result: ok.'
    passed = not timed_out and returncode == 0 and marker in log and 'running 0 tests' not in log
    report['cases'].append({'name':name, 'argv':command, 'passed':passed, 'returncode':returncode, 'timed_out':timed_out,
                            'seconds':round(time.monotonic() - started, 3),
                            'log_sha256':hashlib.sha256(log.encode()).hexdigest()})
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report['cases'][-1]), flush=True)
    if not passed:
      raise RuntimeError('upstream simulator failed; inspect preserved log')
  return report


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--repo', type=Path, required=True)
  parser.add_argument('--output', type=Path, required=True)
  args = parser.parse_args()
  print(json.dumps(run(args.repo, args.output), indent=2))


if __name__ == '__main__':
  main()
