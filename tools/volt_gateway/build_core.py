"""Cross-compile portable gateway cores; no bootable image, signing or device I/O."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import gcc_arm_none_eabi


def build(output: Path):
  output.mkdir(parents=True, exist_ok=False)
  source = Path(__file__).parent / 'firmware'
  compiler = Path(gcc_arm_none_eabi.__file__).parent / 'toolchain/bin/arm-none-eabi-gcc'
  size = compiler.with_name('arm-none-eabi-size')
  flags = ['-std=c11', '-Wall', '-Wextra', '-Werror', '-Os', '-mcpu=cortex-m4', '-mthumb', '-mfloat-abi=soft',
           '-ffreestanding', '-fno-builtin', '-fstack-usage', '-fdata-sections', '-ffunction-sections']
  report = {'compiler':subprocess.check_output([str(compiler), '--version'], text=True).splitlines()[0],
            'compiler_sha256':hashlib.sha256(compiler.read_bytes()).hexdigest(), 'flags':flags, 'objects':{},
            'bootable':False, 'flashed':False,
            'limitations':['platform crypto/RNG and board integration absent', 'not full-image RAM/stack measurement',
                           'no CAN-driver or flash-driver validation']}
  for name in ('authority', 'observe', 'update', 'metrics', 'status_led', 'white_board'):
    destination = output / (name + '.o')
    command = [str(compiler), *flags, '-c', str(source / (name + '.c')), '-o', str(destination)]
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)
    report['objects'][name] = {'sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),
                               'size_output':subprocess.check_output([str(size), str(destination)], text=True),
                               'stack_usage':destination.with_suffix('.su').read_text()}
  (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
  return report


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--output', type=Path, required=True, help='new build-artifact directory')
  args = parser.parse_args()
  print(json.dumps(build(args.output), indent=2))


if __name__ == '__main__':
  main()
