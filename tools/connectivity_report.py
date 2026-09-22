#!/usr/bin/env python3
"""How often was the device actually online? Reads deviceState (2 Hz in every qlog/rlog) and
reports, per route, the share of time by network type and the outage pattern.

usage: connectivity_report.py <routes_dir> [route_prefix ...]
Works on qlogs (small) or rlogs. Uses whatever is present.
"""
import glob
import os
import sys
from collections import Counter

from openpilot.tools.lib.logreader import LogReader

GAP_S = 120.  # a jump in log time larger than this means segments are missing locally


def route_segments(root, route):
  segs = sorted(glob.glob(f'{root}/{route}--*'), key=lambda p: int(p.rsplit('--', 1)[1]))
  out = []
  for s in segs:
    for name in ('qlog.zst', 'rlog.zst', 'qlog', 'rlog'):
      if os.path.exists(f'{s}/{name}'):
        out.append(f'{s}/{name}')
        break
  return out


def analyze(files):
  types = Counter()
  online_runs, offline_runs = [], []
  state = None
  run_start = None
  t = None
  prev_t = None
  n = 0
  for f in files:
    try:
      for m in LogReader(f):
        if m.which() != 'deviceState':
          continue
        t = m.logMonoTime / 1e9
        nt = str(m.deviceState.networkType)
        types[nt] += 1
        n += 1
        online = nt != 'none'
        if prev_t is not None and t - prev_t > GAP_S:
          # missing segments between files: close the current spell, start fresh
          if state is not None:
            (online_runs if state else offline_runs).append(prev_t - run_start)
          state = None
        prev_t = t
        if state is None:
          state, run_start = online, t
        elif online != state:
          (online_runs if state else offline_runs).append(t - run_start)
          state, run_start = online, t
    except Exception as e:  # a truncated segment must not kill the whole report
      print(f'  !! {f}: {e}', file=sys.stderr)
  if state is not None and t is not None:
    (online_runs if state else offline_runs).append(t - run_start)
  return n, types, online_runs, offline_runs


def fmt_runs(runs):
  if not runs:
    return 'none'
  runs = sorted(runs)
  return f'{len(runs)} spells, median {runs[len(runs) // 2] / 60:.1f} min, longest {runs[-1] / 60:.1f} min'


def modem_log_report(path):
  """Summarize /data/log/modem_events.log (from system/modem_log.py): time by state and registration."""
  import json
  from datetime import datetime, UTC
  rows = []
  for line in open(path):
    try:
      rows.append(json.loads(line))
    except ValueError:
      pass
  rows = [r for r in rows if 'state' in r]
  by_state, by_reg = Counter(), Counter()
  for a, b in zip(rows, rows[1:], strict=False):
    dt = max(0., min(b['t'] - a['t'], 3600.))
    by_state[(a.get('state'), bool(a.get('connected')))] += dt
    by_reg[a.get('registration')] += dt
  tot = sum(by_state.values()) or 1
  span = (rows[0]['t'], rows[-1]['t']) if rows else (0, 0)
  print(f"modem log {datetime.fromtimestamp(span[0], UTC):%Y-%m-%d %H:%M} -> {datetime.fromtimestamp(span[1], UTC):%H:%M} UTC, {tot / 3600:.1f} h, {len(rows)} entries")
  for (st, up), v in by_state.most_common():
    print(f"    {st:14s} {'online ' if up else 'offline'} {100 * v / tot:5.1f}%")
  print('  registration:')
  for k, v in by_reg.most_common():
    print(f"    {str(k):14s} {100 * v / tot:5.1f}%")
  ups = sum(1 for a, b in zip(rows, rows[1:], strict=False) if not a.get('connected') and b.get('connected'))
  print(f"  link came up {ups} times")


def main():
  if len(sys.argv) > 2 and sys.argv[1] == '--modem-log':
    modem_log_report(sys.argv[2])
    return
  root = sys.argv[1]
  prefixes = sys.argv[2:]
  routes = sorted({os.path.basename(p).rsplit('--', 1)[0] for p in glob.glob(f'{root}/*--*--*')})
  if prefixes:
    routes = [r for r in routes if any(r.startswith(p) for p in prefixes)]
  grand = Counter()
  for r in routes:
    files = route_segments(root, r)
    if not files:
      continue
    n, types, on, off = analyze(files)
    if n == 0:
      continue
    grand.update(types)
    online = 100 * sum(v for k, v in types.items() if k != 'none') / n
    print(f'{r}: {len(files)} segments, {n / 2 / 60:.0f} min logged, online {online:.0f}% of the time')
    for k, v in types.most_common():
      print(f'    {k:10s} {100 * v / n:5.1f}%')
    print(f'    online spells : {fmt_runs(on)}')
    print(f'    offline spells: {fmt_runs(off)}')
  if grand:
    tot = sum(grand.values())
    print(f'\nALL: {tot / 2 / 3600:.1f} h logged, online {100 * sum(v for k, v in grand.items() if k != "none") / tot:.0f}%')


if __name__ == '__main__':
  main()
