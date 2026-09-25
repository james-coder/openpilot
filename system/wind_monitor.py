#!/usr/bin/env python3
"""windd: fetch current wind for the car's GPS position and write it to a status file for the UI.

Source: Open-Meteo (https://open-meteo.com), free for non-commercial use, no API key.
Its "current" block is the latest hour of the best available national model for the
location (HRRR/NBM in the US, 1-3 km grid), updated every 15-60 minutes. That is a
modelled 10 m wind, not a station observation, but it is available everywhere the car
can be, whereas station observations are sparse and hours old away from airports.

Network: one request (~1 KB) every FETCH_INTERVAL_S, or sooner after moving
REFETCH_DISTANCE_MI. Uses whatever link the device has (wifi or cellular). Failures keep
the previous reading; the UI decides how stale is too stale.

Only the pure helpers are unit-tested; the loop touches messaging, the network and disk.
"""
import json
import math
import time
from pathlib import Path

ROOT = Path('/data/wind')
API = 'https://api.open-meteo.com/v1/forecast'
FETCH_INTERVAL_S = 600.        # normal cadence while parked or cruising
REFETCH_DISTANCE_MI = 8.       # refetch sooner once the car has moved this far
MIN_INTERVAL_S = 120.          # never hit the API faster than this
REQUEST_TIMEOUT_S = 10.
MAX_ACCURACY_M = 2000.         # ignore GPS fixes worse than this
LOOP_SLEEP_S = 2.


def haversine_mi(lat1, lon1, lat2, lon2):
  r = 3958.8
  p1, p2 = math.radians(lat1), math.radians(lat2)
  dphi, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
  a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
  return 2 * r * math.asin(math.sqrt(a))


def should_fetch(now, last_fetch, last_lat, last_lon, lat, lon):
  """Decide whether a new API request is due. `last_fetch` is None before the first one."""
  if last_fetch is None:
    return True
  if now - last_fetch < MIN_INTERVAL_S:
    return False
  if now - last_fetch >= FETCH_INTERVAL_S:
    return True
  return haversine_mi(last_lat, last_lon, lat, lon) >= REFETCH_DISTANCE_MI


def fix_from(msg):
  """(lat, lon, bearing_deg) from a GpsLocationData reader if it is a usable fix, else None."""
  if msg is None:
    return None
  try:
    # hasFix is set by both GPS daemons. flags is ublox-only: the comma 3X's Qualcomm receiver
    # (qcomgpsd, gpsLocation) always sends flags=0 and horizontalAccuracy=0, which is "not
    # reported" rather than perfect, so only a reported accuracy is checked against the limit.
    if not msg.hasFix or not (0. <= msg.horizontalAccuracy <= MAX_ACCURACY_M):
      return None
    lat, lon = float(msg.latitude), float(msg.longitude)
    if not (math.isfinite(lat) and math.isfinite(lon)) or (lat == 0. and lon == 0.):
      return None
    return lat, lon, float(msg.bearingDeg)
  except Exception:
    return None


def parse_response(payload):
  """Validate an Open-Meteo JSON body (already decoded) into our status fields, or raise ValueError."""
  cur = payload['current']
  speed, gust, direction = cur['wind_speed_10m'], cur.get('wind_gusts_10m'), cur['wind_direction_10m']
  for v in (speed, direction):
    if type(v) not in (int, float) or not math.isfinite(v):
      raise ValueError('non-numeric wind field')
  if not (0 <= speed <= 250) or not (0 <= direction <= 360):
    raise ValueError('wind field out of range')
  if type(gust) not in (int, float) or not math.isfinite(gust) or not (0 <= gust <= 300):
    gust = None
  if payload.get('current_units', {}).get('wind_speed_10m') != 'mph':
    raise ValueError('unexpected wind unit')
  return {'wind_mph': round(float(speed), 1), 'gust_mph': None if gust is None else round(float(gust), 1),
          'dir_deg': round(float(direction)) % 360, 'valid_time': str(cur.get('time', '')),
          'interval_s': cur.get('interval')}


def fetch(lat, lon):
  import requests  # on-device dependency, imported lazily so the pure helpers stay importable anywhere
  params = {'latitude': f'{lat:.4f}', 'longitude': f'{lon:.4f}',
            'current': 'wind_speed_10m,wind_direction_10m,wind_gusts_10m',
            'wind_speed_unit': 'mph', 'timezone': 'UTC'}
  r = requests.get(API, params=params, timeout=REQUEST_TIMEOUT_S)
  r.raise_for_status()
  return parse_response(r.json())


def write_status(payload, root=ROOT):
  try:
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / 'status.tmp'
    tmp.write_text(json.dumps(payload))
    tmp.replace(root / 'status.json')
  except OSError:
    pass  # never turn a storage failure into a tight loop


def load_previous(root=ROOT, now=None):
  """(reading, reading_time) persisted by the previous run, so the badge shows the last known wind
  right at startup (marked by its age) instead of waiting for GPS + network. None if unusable."""
  now = time.time() if now is None else now  # noqa: TID251 -- persisted wall timestamps
  try:
    status = json.loads((root / 'status.json').read_text())
    r, rt = status.get('reading'), status.get('reading_time')
    if isinstance(r, dict) and type(rt) in (int, float) and 0 <= now - rt <= 24 * 3600:
      parse_response({'current': {'wind_speed_10m': r['wind_mph'], 'wind_direction_10m': r['dir_deg'],
                                  'wind_gusts_10m': r.get('gust_mph')}, 'current_units': {'wind_speed_10m': 'mph'}})
      return r, float(rt)
  except (OSError, ValueError, TypeError, KeyError):
    pass
  return None, 0.


def main():
  import cereal.messaging as messaging
  from openpilot.common.swaglog import cloudlog
  sm = messaging.SubMaster(['gpsLocation', 'gpsLocationExternal'])
  last_fetch = None
  last_lat = last_lon = 0.
  reading, reading_time = load_previous()  # last good wind reading dict and the wall time it was fetched
  failures = 0
  while True:
    sm.update(0)
    fix = None
    for svc in ('gpsLocation', 'gpsLocationExternal'):
      if sm.alive[svc] and sm.valid[svc]:
        fix = fix_from(sm[svc])
        if fix:
          break
    now = time.time()  # noqa: TID251 -- persisted wall timestamps, compared against API validity
    state = 'nofix' if fix is None else ('ok' if reading else 'waiting')
    if fix is not None and should_fetch(now, last_fetch, last_lat, last_lon, fix[0], fix[1]):
      last_fetch, last_lat, last_lon = now, fix[0], fix[1]
      try:
        reading = fetch(fix[0], fix[1])
        reading_time = now
        failures = 0
        state = 'ok'
        cloudlog.info('windd: %s mph from %s deg (gust %s) at %.3f,%.3f', reading['wind_mph'], reading['dir_deg'],
                      reading['gust_mph'], fix[0], fix[1])
      except Exception as e:
        failures += 1
        state = 'offline' if reading is None else 'stale'
        if failures in (1, 5, 30):
          cloudlog.warning('windd: fetch failed (%d in a row): %s', failures, str(e)[:160])
    write_status({'time': now, 'state': state, 'failures': failures, 'fix': fix is not None,
                  'lat': None if fix is None else round(fix[0], 3), 'lon': None if fix is None else round(fix[1], 3),
                  'reading': reading, 'reading_time': reading_time, 'source': 'open-meteo'})
    time.sleep(LOOP_SLEEP_S)


if __name__ == '__main__':
  main()
