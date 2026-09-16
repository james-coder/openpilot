"""Build a targeted harness against pinned upstream bootsim without editing it."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess

from openpilot.tools.volt_gateway.mcuboot_test import MCUBOOT_COMMIT, MBEDTLS_COMMIT


def run(repo: Path, output: Path):
  for path, expected in ((repo, MCUBOOT_COMMIT), (repo / 'ext/mbedtls', MBEDTLS_COMMIT)):
    if subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip() != expected:
      raise ValueError('source pin mismatch')
    if subprocess.check_output(['git', '-C', str(path), 'diff', 'HEAD', '--'], text=True):
      raise ValueError('upstream source modified')
  output.mkdir(parents=True, exist_ok=False)
  env = dict(os.environ, CARGO_PROFILE_DEV_DEBUG='0', CARGO_PROFILE_TEST_DEBUG='0', CARGO_INCREMENTAL='0')
  env.pop('MCUBOOT_SKIP_SLOW_TESTS', None)
  env.pop('MCUBOOT_DEBUG_DUMP', None)
  command = ['cargo', 'build', '--locked', '-p', 'bootsim', '--features', 'sig-ecdsa', '--lib', '--message-format=json']
  built = subprocess.run(command, cwd=repo, env=env, capture_output=True, text=True, check=True, timeout=180)
  libraries = []
  for line in built.stdout.splitlines():
    artifact = json.loads(line)
    if artifact.get('reason') == 'compiler-artifact' and artifact.get('target', {}).get('name') == 'bootsim':
      libraries += [Path(p) for p in artifact['filenames'] if p.endswith('.rlib')]
  if len(libraries) != 1:
    raise RuntimeError('expected one explicitly built bootsim library')
  source = Path(__file__).with_name('mcuboot_stm32_test.rs')
  exe = output / 'stm32-sim-test'
  deps = libraries[0].parent / 'deps'
  compiled = subprocess.run(['rustc', '--edition=2021', '-C', 'opt-level=2', str(source), '--extern', f'bootsim={libraries[0]}',
                             '-L', f'dependency={deps}', '-o', str(exe)], capture_output=True, text=True, timeout=60)
  (output / 'compile.log').write_text(compiled.stdout + compiled.stderr)
  if compiled.returncode:
    raise RuntimeError('targeted simulator build failed: ' + compiled.stderr)
  with subprocess.Popen([str(exe)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, start_new_session=True) as process:
    try:
      log, _ = process.communicate(timeout=180)
    except subprocess.TimeoutExpired:
      os.killpg(process.pid, signal.SIGKILL)
      log, _ = process.communicate()
    code = process.returncode
  (output / 'simulator.log').write_text(log)
  report = {'mcuboot_commit':MCUBOOT_COMMIT, 'mbedtls_commit':MBEDTLS_COMMIT,
            'harness_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'passed':code == 0 and 'STM32F4 targeted simulator checks passed' in log, 'returncode':code,
            'hardware_tested':False, 'direct_xip_revert_tested':False,
            'scope':'upstream STM32F4 swap/scratch model, ECDSA, alignments 1/4/8; not Panda layout or CAN recovery',
            'log':log}
  (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
  return report


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--repo', type=Path, required=True)
  parser.add_argument('--output', type=Path, required=True)
  args = parser.parse_args()
  report = run(args.repo, args.output)
  print(json.dumps(report, indent=2))
  if not report['passed']:
    raise SystemExit(1)


if __name__ == '__main__':
  main()
