"""Read-only extraction of steady-state manual following-distance samples.

Finds windows where: not standstill, openpilot disengaged, a radar lead is present and
stable (same track, confident), and the gap has been roughly constant for several
seconds — i.e. genuine driver-chosen steady following, not a cut-in, a stop, or openpilot
itself controlling speed. Writes only (vEgo, dRel) pairs plus which criteria produced them;
this script never talks to the car and never changes any runtime profile — see
volt_following_fit.py for the curve fit, and volt_following_apply.py / opendbc.car.gm.
volt_following / selfdrive.car.volt_following for how a fit result, once written, is
(re-)validated before it can ever change MPC behavior.

Run this on a workstation, not the comma device: decompressing/parsing hundreds of
one-minute rlogs is CPU-heavy, and the comma's embedded SoC has no cores to spare for it
(confirmed: ~2.75s/segment single-threaded on-device, sustained near-100% load, real
thermal cost sitting in a hot car for no benefit). First sync the raw logs off the
device, e.g.:

    rsync -az -e ssh comma@<device-ip>:/data/media/0/realdata/ ./raw/ \\
      --include='*/' --include='rlog.zst' --exclude='*'

then run this script against ./raw locally. Nothing here needs to run on the car itself
except the final, cheap Params write — see volt_following_apply.py.
"""

import argparse
import gzip
import hashlib
import io
import json
from datetime import datetime, timedelta, UTC
from pathlib import Path

import numpy as np
import zstandard
from cereal import log

from openpilot.tools.profiling.volt_braking import atomic_json

EXTRACT_VERSION = 1

# Named, reviewable constants — also recorded in the output JSON so a reviewer never
# has to read code to know what produced a given sample set.
STEADY_WINDOW_S = 1.0
MIN_V = 1.0            # m/s; excludes stop-and-go queueing, not real following behavior.
MAX_VREL_MEAN = 1.0     # m/s; not meaningfully closing or opening over the window.
MAX_VREL_STD = 0.5      # m/s
MAX_RELATIVE_DREL_STD = 0.12  # relative, not absolute: fair at both 4m and 40m gaps.
MIN_MODEL_PROB = 0.9
LOOKBACK_DAYS = 30


def criteria():
  return {
    'steady_window_s': STEADY_WINDOW_S, 'min_v': MIN_V, 'max_vrel_mean': MAX_VREL_MEAN,
    'max_vrel_std': MAX_VREL_STD, 'max_relative_drel_std': MAX_RELATIVE_DREL_STD,
    'min_model_prob': MIN_MODEL_PROB, 'lookback_days': LOOKBACK_DAYS,
  }


def extract_native(path):
  """Read one segment's rlog.zst; return per-sample rows at native carState rate."""
  compressed = path.read_bytes()
  with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(compressed)) as reader:
    raw = reader.read()
  latest = {}
  stamps = {}
  rows = []
  for e in log.Event.read_multiple_bytes(raw):
    t = e.logMonoTime / 1e9
    k = e.which()
    if k == 'selfdriveState' and e.valid:
      latest[k] = {'enabled': e.selfdriveState.enabled}
    elif k == 'radarState' and e.valid:
      lead = e.radarState.leadOne
      latest[k] = {'status': lead.status, 'dRel': lead.dRel, 'vRel': lead.vRel,
                    'radarTrackId': lead.radarTrackId, 'modelProb': lead.modelProb}
    elif k == 'carState' and e.valid:
      row = {'t': t, 'v': e.carState.vEgo, 'standstill': e.carState.standstill}
      for service, data in latest.items():
        if 0 <= t - stamps.get(service, -1e9) <= 0.3:
          row[service] = data
      rows.append(row)
    if k in latest:
      stamps[k] = t
  return {'rows': rows, 'sha256': hashlib.sha256(compressed).hexdigest()}


def steady_windows(rows):
  """Yield (vEgo, dRel) for each qualifying window's final sample.

  rows: sorted, native-rate dicts as produced by extract_native (t, v, standstill,
  optionally selfdriveState/radarState nested dicts when fresh at that sample).
  """
  t = np.array([r['t'] for r in rows])
  v = np.array([r['v'] for r in rows])
  standstill = np.array([r['standstill'] for r in rows])
  enabled = np.array([r.get('selfdriveState', {}).get('enabled', True) for r in rows])  # unknown => assume engaged, exclude
  radar = [r.get('radarState') for r in rows]
  status = np.array([bool(r['status']) if r else False for r in radar])
  dRel = np.array([r['dRel'] if r else np.nan for r in radar])
  vRel = np.array([r['vRel'] if r else np.nan for r in radar])
  track = np.array([r['radarTrackId'] if r else -1 for r in radar])
  prob = np.array([r['modelProb'] if r else 0.0 for r in radar])

  n = len(rows)
  start = 0
  for end in range(n):
    while t[end] - t[start] > STEADY_WINDOW_S:
      start += 1
    if t[end] - t[start] < STEADY_WINDOW_S - 1e-6:
      continue  # window not yet full
    window = slice(start, end + 1)
    if standstill[window].any() or (v[window] <= MIN_V).any():
      continue
    if enabled[window].any():
      continue
    if not status[window].all():
      continue
    if len(set(track[window].tolist())) != 1 or track[end] < 0:
      continue
    if prob[window].min() < MIN_MODEL_PROB:
      continue
    vrel_window = vRel[window]
    if not np.isfinite(vrel_window).all():
      continue
    if abs(vrel_window.mean()) >= MAX_VREL_MEAN or vrel_window.std() >= MAX_VREL_STD:
      continue
    drel_window = dRel[window]
    if not np.isfinite(drel_window).all() or drel_window.mean() <= 0:
      continue
    if drel_window.std() / drel_window.mean() >= MAX_RELATIVE_DREL_STD:
      continue
    yield float(v[end]), float(dRel[end]), float(t[end])


def route_age_days(rlog_path, now=None):
  """Route directory naming isn't a reliable timestamp source across devices/AGNOS
  versions (this device's own dirs are <seq>--<route_id>--<segment>, no embedded date
  at all) — use the rlog file's own mtime instead, which is always present."""
  now = now or datetime.now(UTC)
  try:
    mtime = datetime.fromtimestamp(rlog_path.stat().st_mtime, UTC)
  except OSError:
    return None
  return (now - mtime).total_seconds() / 86400


def _process_segment_with_cache(path, cache_root):
  """Worker for extract_routes' process pool: cache-or-extract one segment, then run
  steady_windows immediately and return only the small samples list (not the full row
  data) to keep inter-process serialization cheap.

  Must never let a raw exception escape this function: a malformed/truncated rlog (or
  any other extraction failure) can raise from inside the capnp/cereal C extension, and
  those exceptions are sometimes NOT themselves picklable (their args can hold a live
  Cython/cffi object) — letting one escape crashes ProcessPoolExecutor.result() with an
  unrelated-looking pickling TypeError and takes down every other in-flight segment with
  it, not just the one bad file. Catch broadly, report as a segment-level error string
  instead, and keep going.
  """
  segment_name = path.parent.name
  try:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    cached = cache_root / (segment_name + '.json.gz')
    data = json.loads(gzip.decompress(cached.read_bytes())) if cached.exists() else {}
    if data.get('extract_version') != EXTRACT_VERSION or data.get('sha256') != digest:
      data = extract_native(path)
      data['extract_version'] = EXTRACT_VERSION
      cached.write_bytes(gzip.compress(json.dumps(data, allow_nan=False).encode(), compresslevel=3))
    route = segment_name.rsplit('--', 1)[0]
    samples = [{'v': v, 'd': d, 't': t, 'route': route, 'segment': segment_name} for v, d, t in steady_windows(data['rows'])]
    return segment_name, len(data['rows']), samples, None
  except Exception as exc:  # noqa: BLE001 -- see docstring: must not let anything unpicklable escape
    return segment_name, 0, [], f'{type(exc).__name__}: {exc}'


def extract_routes(raw_root, cache_root, now=None, max_workers=None):
  """Extract every rlog.zst under raw_root newer than LOOKBACK_DAYS, memoized in
  cache_root. Each segment is fully independent (its own file, own cache entry), so this
  parallelizes across a process pool — the CPU-bound bottleneck is decompression/parsing
  in extract_native, not I/O."""
  from concurrent.futures import ProcessPoolExecutor, as_completed
  import functools

  cache_root.mkdir(parents=True, exist_ok=True)
  cutoff = timedelta(days=LOOKBACK_DAYS)
  now = now or datetime.now(UTC)
  samples = []
  skipped_old = 0
  paths = []
  for path in sorted(raw_root.glob('*/rlog.zst')):
    age = route_age_days(path, now)
    if age is None or age > cutoff.days:
      skipped_old += 1
      continue
    paths.append(path)

  errors = []
  worker = functools.partial(_process_segment_with_cache, cache_root=cache_root)
  with ProcessPoolExecutor(max_workers=max_workers) as pool:
    futures = {pool.submit(worker, path): path for path in paths}
    for future in as_completed(futures):
      segment_name, row_count, segment_samples, error = future.result()
      if error is not None:
        errors.append({'segment': segment_name, 'error': error})
        print(f'{segment_name}: FAILED ({error})', flush=True)
        continue
      samples.extend(segment_samples)
      print(f'{segment_name}: {row_count} rows -> running total {len(samples)} steady samples', flush=True)
  return samples, skipped_old, errors


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('raw', type=Path, help='Route root synced from the device, e.g. ./raw (see module docstring)')
  parser.add_argument('output', type=Path, help='Directory to write cache/ and samples.json into')
  parser.add_argument('--jobs', type=int, default=None, help='Parallel worker processes (default: os.cpu_count())')
  args = parser.parse_args()
  samples, skipped_old, errors = extract_routes(args.raw, args.output / 'cache', max_workers=args.jobs)
  result = {
    'version': EXTRACT_VERSION,
    'extracted_at': datetime.now(UTC).isoformat(),
    'criteria': criteria(),
    'samples': samples,
    'routes_skipped_older_than_lookback': skipped_old,
    'failed_segments': errors,
  }
  atomic_json(args.output / 'following-distance-samples.json', result)
  print(f'{len(samples)} steady-following samples from routes within {LOOKBACK_DAYS} days '
        f'({skipped_old} older routes skipped, {len(errors)} segments failed to extract — see failed_segments)')


if __name__ == '__main__':
  main()
