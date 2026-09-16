"""Offline-only CLI. Deliberately contains no live gateway commands."""

import argparse
import json
from pathlib import Path

from openpilot.tools.volt_gateway.benchmark import report
from openpilot.tools.volt_gateway.transfer_sim import report as transfer_report


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  sub = parser.add_subparsers(dest='command', required=True)
  bench = sub.add_parser('benchmark', help='native Python codec measurements and synthetic/wire-cost models')
  bench.add_argument('--iterations', type=int, default=1000)
  bench.add_argument('--output', type=Path, help='new report file; refuses overwrite')
  transfer = sub.add_parser('simulate-update', help='offline authenticated two-peer update/fault scenarios')
  transfer.add_argument('--output', type=Path, help='new report file; refuses overwrite')
  args = parser.parse_args()
  result = json.dumps(report(args.iterations) if args.command == 'benchmark' else transfer_report(), indent=2) + '\n'
  if args.output:
    with args.output.open('x') as f:
      f.write(result)
  else:
    print(result, end='')


if __name__ == '__main__':
  main()
