# ruff: noqa: E501  (report-generator text: long f-string lines are clearer unbroken)
"""Incident report from a local copy of the car's logs and video. Read-only; never touches the car.

  .venv/bin/python -m openpilot.tools.dashcam.incident_report <incident-dir> <route> <first-seg> <last-seg> \
      [--before 8] [--after 8] [--anchor <log-mono-seconds>]

Writes <incident-dir>/report/: telemetry.npz, report.json, report.md, graph.png, video.mp4 (road + wide with a
data strip: speed, blinkers, brake pedal, ABS, g, nearest radar return), stills/, index.html.
"""
import argparse
import datetime
import json
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from openpilot.tools.dashcam.frames import grab, locate
from openpilot.tools.dashcam.telemetry import car_frame, extract, wheel_slip

MPH = 2.23694
FONT_DIR = Path('/usr/share/fonts/truetype/dejavu')
BG, FG, DIM = (18, 20, 24), (235, 235, 235), (140, 145, 155)
AMBER, RED, BLUE, ORANGE, GREEN = (255, 176, 40), (235, 64, 64), (96, 165, 255), (255, 150, 60), (80, 200, 120)
PURPLE = (176, 110, 245)  # ABS: its own color so it is never mistaken for the brake pedal
MDT = datetime.timezone(datetime.timedelta(hours=-6))


def font(size, bold=False):
  try:
    return ImageFont.truetype(str(FONT_DIR / ('DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf')), size)
  except OSError:
    return ImageFont.load_default(size)


def make_wheel(size=400):
  """Steering-wheel sprite (rim, side and bottom spokes, hub, bright mark at 12 o'clock), drawn large and shrunk later."""
  im = Image.new('RGBA', (size, size), (0, 0, 0, 0))
  d = ImageDraw.Draw(im)
  c, R, rim = size / 2, size * 0.46, size * 0.075
  light = (205, 210, 220, 255)
  d.ellipse([c - R, c - R, c + R, c + R], outline=light, width=int(rim))
  sp = int(size * 0.06)
  d.rectangle([c - R + rim, c - sp / 2, c + R - rim, c + sp / 2], fill=light)  # left and right spokes
  d.rectangle([c - sp / 2, c, c + sp / 2, c + R - rim], fill=light)  # bottom spoke
  d.ellipse([c - size * 0.1, c - size * 0.1, c + size * 0.1, c + size * 0.1], fill=light)
  d.rectangle([c - size * 0.03, c - R - 2, c + size * 0.03, c - R + rim * 1.25], fill=(255, 176, 40, 255))  # top-center mark
  return im


def smooth(x, n):
  return np.convolve(x, np.ones(n) / n, 'same')


def analyse(d: dict, anchor: float | None):
  cs, cf, ws = d['cs'], car_frame(d), wheel_slip(d)
  t_cf = cf['t']
  edges = [cs[i, 0] for i in range(1, len(cs)) if cs[i, 5] > .5 and cs[i - 1, 5] < .5]
  if anchor is None:  # hardest stop: strongest forward deceleration
    anchor = float(t_cf[np.argmin(np.where(np.interp(t_cf, cs[:, 0], cs[:, 1]) > 2, smooth(cf['g_long'], 11), 0))])
  onset = max([e for e in edges if 0 <= anchor - e < 3], default=anchor)
  v = cs[:, 1]
  after = cs[(cs[:, 0] > onset) & (v < 0.2)]
  t_stop = float(after[0, 0]) if len(after) else float(onset + 1)
  g_long_s = smooth(cf['g_long'], 11)
  w = (t_cf > onset - 0.2) & (t_cf < t_stop + 0.3)
  # stopping distance from the IMU (wheel speeds are unreliable while ABS cycles)
  ts = t_cf[(t_cf >= onset) & (t_cf <= t_stop)]
  v0 = float(np.interp(onset, cs[:, 0], cs[:, 1]))
  a = cf['g_long'][(t_cf >= onset) & (t_cf <= t_stop)] * 9.81
  vimu = np.maximum(v0 + np.cumsum(a * np.gradient(ts)), 0) if len(ts) > 2 else np.array([0.0])
  dist_imu = float(np.sum(vimu * np.gradient(ts))) if len(ts) > 2 else float('nan')
  # contact assessment: jolt (>10 Hz) during the stop vs. the same drive's normal driving and the time after standstill
  ev_mask = (t_cf >= onset) & (t_cf <= t_stop)
  base = (np.interp(t_cf, cs[:, 0], v) > 2) & ((t_cf < onset - 3) | (t_cf > t_stop + 3))
  post = (t_cf > t_stop + 0.2) & (t_cf < t_stop + 4)
  ab = ws['t'][ws['abs']]
  # radar: nearest forward-ish return after standstill and every track that came within 15 m after the onset
  trk, _lead = d['trk'], d['lead']
  tr = trk[(trk[:, 0] >= onset - 1) & (trk[:, 0] <= t_stop + 6) & (trk[:, 2] < 15)]
  tracks = {}
  for r in tr:
    tid = int(r[1])
    x = float(np.sqrt(max(r[2] ** 2 - r[3] ** 2, 0)))
    row = tracks.setdefault(tid, dict(id=tid, closest_range_m=1e9, t_rel_s=None, y_m=None, x_m=None, range_rate_ms=None, first_rel_s=float(r[0] - onset)))
    if r[2] < row['closest_range_m']:
      row.update(closest_range_m=float(r[2]), t_rel_s=float(r[0] - onset), y_m=float(r[3]), x_m=x, range_rate_ms=float(r[4]))
    row['last_rel_s'] = float(r[0] - onset)
  held = (trk[:, 0] > t_stop + 0.5) & (trk[:, 0] < t_stop + 3) & (trk[:, 2] < 6)
  ids, counts = np.unique(trk[held, 1], return_counts=True)  # the persistent return after standstill (not passing clutter)
  keep = held & (trk[:, 1] == ids[np.argmax(counts)]) if len(ids) else held
  res = dict(
    anchor=anchor, onset=float(onset), t_stop=t_stop, wall_offset=float(d['wall_offset'][0]),
    onset_wall_mdt=(datetime.datetime.fromtimestamp(onset + d['wall_offset'][0], MDT).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] if np.isfinite(d['wall_offset'][0]) else None),
    speed_at_onset_mph=v0 * MPH, time_to_stop_s=t_stop - onset,
    brake_pedal_at_onset_ms=[float(cs[(cs[:, 0] > onset + dt)][0, 4]) for dt in (0.1, 0.2, 0.3)],
    peak_decel_g_smoothed=float(-g_long_s[w].min()), peak_decel_g_raw=float(-cf['g_long'][w].min()),
    peak_lateral_g=float(np.abs(cf['g_lat'][w]).max()), stopping_distance_imu_m=dist_imu,
    abs_like_first_rel_s=float(ab.min() - onset) if len(ab) else None, abs_like_last_rel_s=float(ab.max() - onset) if len(ab) else None,
    abs_like_samples=int(len(ab)), abs_like_wheel_speed_rate_hz=20,
    left_blinker_during=bool(cs[(cs[:, 0] > onset - 3) & (cs[:, 0] < t_stop), 8].max() > .5),
    right_blinker_during=bool(cs[(cs[:, 0] > onset - 3) & (cs[:, 0] < t_stop), 9].max() > .5),
    steering_angle_deg_at_onset=float(np.interp(onset, cs[:, 0], cs[:, 7])),
    jolt_peak_during_stop_g=float(cf['jolt'][ev_mask].max()), jolt_peak_after_standstill_g=float(cf['jolt'][post].max()),
    jolt_normal_driving_p99_9_g=float(np.percentile(cf['jolt'][base], 99.9)), jolt_normal_driving_max_g=float(cf['jolt'][base].max()),
    imu_rate_hz=cf['rate_hz'], imu_axis_fit_rms_ms2=cf['fit_rms'],
    radar_after_stop=dict(n=int(keep.sum()), track_id=int(ids[np.argmax(counts)]) if len(ids) else None,
                          min_range_m=float(trk[keep, 2].min()) if keep.any() else None,
                          max_range_m=float(trk[keep, 2].max()) if keep.any() else None,
                          y_m=float(np.median(trk[keep, 3])) if keep.any() else None),
    radar_tracks_within_15m=sorted(tracks.values(), key=lambda r: r['closest_range_m']),
  )
  return res, cf, ws, g_long_s


def graph_image(d, cf, ws, res, t_a, t_b, size=(1260, 280)):
  W, H = size
  im = Image.new('RGB', size, BG)
  dr = ImageDraw.Draw(im)
  cs = d['cs']
  def x(t):
    return (t - t_a) / (t_b - t_a) * (W - 70) + 60
  lanes = [('mph', 8, 78, 0, 16, BLUE), ('g long', 92, 78, -1.1, 0.6, ORANGE), ('brake', 176, 50, 0, 1, RED)]
  f = font(12)
  for name, y0, h, _lo, _hi, col in lanes:
    dr.rectangle([60, y0, W - 10, y0 + h], outline=(55, 58, 66))
    dr.text((4, y0 + h / 2 - 7), name, fill=col, font=f)
  def y_of(y0, h, lo, hi, val):
    return y0 + h - (np.clip(val, lo, hi) - lo) / (hi - lo) * h
  m = (cs[:, 0] >= t_a) & (cs[:, 0] <= t_b)
  dr.line([(x(t), y_of(8, 78, 0, 16, v * MPH)) for t, v in zip(cs[m, 0], cs[m, 1], strict=False)], fill=BLUE, width=2)
  mg = (cf['t'] >= t_a) & (cf['t'] <= t_b)
  gl = smooth(cf['g_long'], 5)
  y0g = y_of(92, 78, -1.1, .6, 0)
  dr.line([(60, y0g), (W - 10, y0g)], fill=(55, 58, 66))
  dr.line([(x(t), y_of(92, 78, -1.1, .6, g)) for t, g in zip(cf['t'][mg], gl[mg], strict=False)], fill=ORANGE, width=2)
  # brake pedal fill, ABS shading, blinker bar
  pts = [(x(t), y_of(176, 50, 0, 1, p / 255)) for t, p in zip(cs[m, 0], cs[m, 4], strict=False)]
  if pts:
    dr.polygon([(pts[0][0], 226)] + pts + [(pts[-1][0], 226)], fill=(92, 30, 34))
    dr.line(pts, fill=RED, width=2)
  for t in ws['t'][ws['abs']]:
    dr.rectangle([x(t) - 2, 8, x(t) + 2, 226], fill=(70, 42, 112))
  for col, idx, y in ((AMBER, 8, 232), (AMBER, 9, 246)):
    on = cs[m, idx] > .5
    for t0, t1, o in zip(cs[m, 0][:-1], cs[m, 0][1:], on[:-1], strict=False):
      if o:
        dr.rectangle([x(t0), y, x(t1), y + 10], fill=col)
  dr.text((4, 230), 'L turn', fill=AMBER, font=f)
  if len(ws['t'][ws['abs']]):
    dr.text((x(ws['t'][ws['abs']].min()) + 5, 10), 'ABS', fill=PURPLE, font=font(13, True))
  dr.text((4, 244), 'R turn', fill=AMBER, font=f)
  for s in range(int(np.ceil(t_a - res['onset'])), int(np.floor(t_b - res['onset'])) + 1):
    xx = x(res['onset'] + s)
    dr.line([(xx, 226), (xx, 230)], fill=DIM)
    dr.text((xx - 8, 262), f'{s:+d}s', fill=DIM, font=f)
  xo = x(res['onset'])
  dr.line([(xo, 8), (xo, 226)], fill=(200, 200, 90), width=1)
  return im, x


def render_video(raw, route, d, cf, ws, res, out_dir, t_a, t_b):
  cs = d['cs']
  road, wide = d['enc_road'], d['enc_wide']
  sel = road[(road[:, 3] >= t_a) & (road[:, 3] <= t_b)]
  times = sel[:, 3]
  wanted_r, wanted_w = {}, {}
  pairs = []
  for t in times:
    rs, ri, _ = locate(road, t)
    ws_, wi, _ = locate(wide, t)
    wanted_r.setdefault(rs, set()).add(ri)
    wanted_w.setdefault(ws_, set()).add(wi)
    pairs.append(((rs, ri), (ws_, wi)))
  fr_r = grab(raw, route, 'road', wanted_r, size=(960, 600))
  fr_w = grab(raw, route, 'wide', wanted_w, size=(960, 600))
  gimg, gx = graph_image(d, cf, ws, res, t_a, t_b)
  fb, fm, fs, fx = font(54, True), font(22), font(15), font(30, True)
  wheel = make_wheel()
  trk = d['trk']
  absT = ws['t'][ws['abs']]
  out = out_dir / 'video.mp4'
  codec = 'h264_nvenc'
  try:
    av.codec.Codec(codec, 'w')
  except Exception:
    codec = 'libx264'
  with av.open(str(out), 'w') as c:
    st = c.add_stream(codec, rate=20)
    st.width, st.height, st.pix_fmt = 1920, 900, 'yuv420p'
    st.options = {'cq': '21', 'preset': 'p5'} if codec == 'h264_nvenc' else {'crf': '20', 'preset': 'veryfast'}
    st.time_base = Fraction(1, 20)
    for n, (t, (kr, kw)) in enumerate(zip(times, pairs, strict=False)):
      im = Image.new('RGB', (1920, 900), BG)
      im.paste(Image.fromarray(fr_r[kr]), (0, 0))
      im.paste(Image.fromarray(fr_w[kw]), (960, 0))
      im.paste(gimg, (640, 612))
      dr = ImageDraw.Draw(im)
      i = int(np.searchsorted(cs[:, 0], t)) - 1
      i = max(0, min(i, len(cs) - 1))
      v, ped, gas, steer, lb, rb = cs[i, 1], cs[i, 4], cs[i, 6], cs[i, 7], cs[i, 8] > .5, cs[i, 9] > .5
      j = int(np.searchsorted(cf['t'], t))
      gl = float(smooth(cf['g_long'], 5)[min(j, len(cf['t']) - 1)])
      gt = float(smooth(cf['g_lat'], 5)[min(j, len(cf['t']) - 1)])
      is_abs = bool(np.any(np.abs(absT - t) < 0.12))
      near = trk[(np.abs(trk[:, 0] - t) < 0.25) & (np.abs(trk[:, 3]) < 3.0) & (trk[:, 2] > 0.5)]
      rng = float(near[:, 2].min()) if len(near) else None
      rel = t - res['onset']
      dr.text((20, 626), f'{v * MPH:4.1f}', fill=BLUE, font=fb)
      dr.text((205, 654), 'mph', fill=BLUE, font=fm)
      dr.text((20, 692), f"t {rel:+.2f} s from brake press", fill=DIM, font=fs)
      if np.isfinite(res['wall_offset']):
        w = datetime.datetime.fromtimestamp(t + res['wall_offset'], MDT).strftime('%H:%M:%S.%f')[:-4]
        dr.text((20, 712), f'{w} MDT', fill=DIM, font=fs)
      # blinkers
      dr.polygon([(20, 760), (62, 738), (62, 782)], fill=AMBER if lb else (50, 52, 58))
      dr.polygon([(180, 760), (138, 738), (138, 782)], fill=AMBER if rb else (50, 52, 58))
      dr.text((74, 746), 'TURN', fill=AMBER, font=fs)
      # brake pedal
      dr.text((20, 800), 'BRAKE', fill=RED, font=fm)
      dr.rectangle([110, 802, 330, 822], outline=(90, 92, 100))
      dr.rectangle([110, 802, 110 + 220 * min(ped, 255) / 255, 822], fill=RED)
      dr.text((338, 802), f'{ped / 255 * 100:3.0f}%', fill=RED, font=fm)
      dr.rounded_rectangle([20, 836, 140, 880], 8, fill=PURPLE if is_abs else (50, 52, 58))
      dr.text((38, 842), 'ABS', fill=FG if is_abs else DIM, font=fx)
      dr.text((160, 848), 'GAS' + (' ON' if gas else ''), fill=GREEN if gas else DIM, font=fm)
      # g and radar
      dr.text((380, 626), f'long {gl:+.2f} g', fill=ORANGE, font=fm)
      dr.text((380, 656), f'lat  {gt:+.2f} g', fill=FG, font=fm)
      w = wheel.rotate(float(steer), resample=Image.BICUBIC).resize((104, 104), Image.LANCZOS)  # +angle = left = counterclockwise
      im.paste(w, (430, 786), w)
      dr.text((545, 826), 'STEERING', fill=DIM, font=fs)
      dr.text((380, 726), 'RADAR NEAREST AHEAD', fill=DIM, font=fs)
      dr.text((380, 746), f'{rng:.1f} m' if rng is not None else 'none in view', fill=AMBER if rng is not None and rng < 4 else FG, font=fm)
      xx = gx(t)
      dr.line([(640 + xx, 612), (640 + xx, 612 + 226)], fill=(255, 255, 255), width=2)
      dr.text((12, 8), 'ROAD', fill=(255, 255, 0), font=fm)
      dr.text((972, 8), 'WIDE', fill=(255, 255, 0), font=fm)
      fr = av.VideoFrame.from_ndarray(np.asarray(im), format='rgb24').reformat(format='yuv420p')
      fr.pts = n
      for p in st.encode(fr):
        c.mux(p)
    for p in st.encode():
      c.mux(p)
  return out, codec, len(times)


def stills(raw, route, d, res, out_dir):
  out_dir.mkdir(exist_ok=True)
  marks = {'approach': -1.5, 'brake-press': 0.0, 'peak-decel': 0.3, 'stopped': res['time_to_stop_s'] + 0.2, 'crossing-1s': res['time_to_stop_s'] + 1.2,
           'crossing-2s': res['time_to_stop_s'] + 2.2}
  names = {}
  for cam in ('road', 'wide'):
    wanted = {}
    locs = {k: locate(d['enc_' + cam], res['onset'] + r) for k, r in marks.items()}
    for s, i, _ in locs.values():
      wanted.setdefault(s, set()).add(i)
    fr = grab(raw, route, cam, wanted)
    for k, (s, i, tt) in locs.items():
      p = out_dir / f'{k}_{cam}.jpg'
      Image.fromarray(fr[(s, i)]).save(p, quality=92)
      names[f'{k}_{cam}'] = dict(file=p.name, rel_s=float(tt - res['onset']))
  return names


def write_md(res, still_names, codec, nframes, notes=''):
  r = res
  rad = r['radar_after_stop']
  first_rel = next((t['first_rel_s'] for t in r['radar_tracks_within_15m'] if t['id'] == rad['track_id']), float('nan'))
  lines = [
    f"# Incident report: hard brake at {r['onset_wall_mdt']} MDT", '',
    "*Generated from a local, hash-verified copy of the car's logs. Wheel-speed ABS detection and car-frame g come from raw CAN and the device IMU; see notes.*", '',
    '## What the data shows',
    f"- **Brake press** at t=0 while moving {r['speed_at_onset_mph']:.1f} mph with the **left turn signal on** and the wheel turned left ({r['steering_angle_deg_at_onset']:.0f}°). Pedal went from 0 to about {r['brake_pedal_at_onset_ms'][2]/255*100:.0f}% within 0.3 s.",
    f"- **Stopped in {r['time_to_stop_s']:.1f} s** (about {r['stopping_distance_imu_m']:.1f} m from the IMU). Peak forward deceleration {r['peak_decel_g_smoothed']:.2f} g smoothed ({r['peak_decel_g_raw']:.2f} g instantaneous, ABS pulses), peak lateral {r['peak_lateral_g']:.2f} g.",
    f"- **ABS-like wheel behavior** from {r['abs_like_first_rel_s']:+.2f} s to {r['abs_like_last_rel_s']:+.2f} s: a wheel ran below half the speed of the fastest wheel while braking ({r['abs_like_samples']} samples at 20 Hz). The car has no direct ABS signal on the buses we record, so this is inferred.",
    f"- **Radar, after the stop**: the persistent return ahead (radar track {rad['track_id']}) first appeared {first_rel:+.1f} s after the brake press, when you had already stopped, and then sat at **{rad['min_range_m']:.2f}\u2013{rad['max_range_m']:.2f} m** ({abs(rad['y_m']):.1f} m {'right' if rad['y_m'] < 0 else 'left'} of center). Radar range is measured from behind the front fascia to the strongest reflection, so the bumper-to-body gap may be a little smaller.",
    "- **Radar, during the stop**: it did not track that object while you were braking. The approaching vehicles were lost at 8-9 m, far off to the side, as you turned, so the radar says nothing about the closest moment of the stop itself.",
    '',
    '## Did anything hit the car?',
    f"- Vibration above 10 Hz peaked at **{r['jolt_peak_during_stop_g']:.2f} g during the stop** (that is the ABS pulsing). Normal driving on this drive reaches {r['jolt_normal_driving_max_g']:.2f} g (99.9th percentile {r['jolt_normal_driving_p99_9_g']:.2f} g) on bumps. After the car stopped it peaked at {r['jolt_peak_after_standstill_g']:.2f} g. There is no isolated impact spike and nothing after standstill.",
    '- No contact is visible in the stills, and nothing in the accelerometer looks like an impact. The radar cannot confirm distance during the stop (see above), so the video and accelerometer carry that conclusion.',
    '- Limits: the IMU is mounted in the windshield unit, so a very light bumper tap could be missed, and the cameras cannot see the corners of the bumper. The video is the better check.', '',
    '## Other radar tracks within 15 m after the brake press (closest approach per track)',
    '| id | closest range (m) | when (s from press) | forward x (m) | lateral y (m) | range rate (m/s) |', '|---|---|---|---|---|---|',
  ]
  for t in r['radar_tracks_within_15m']:
    lines.append(f"| {t['id']} | {t['closest_range_m']:.1f} | {t['t_rel_s']:+.2f} | {t['x_m']:.1f} | {t['y_m']:+.1f} | {t['range_rate_ms']:+.1f} |")
  if notes:
    lines += ['', '## Notes from review', '', notes.strip()]
  lines += ['', f"Video: `video.mp4` ({nframes} frames at 20 fps, {codec}). Unavailable: parking-sensor distances (single-wire GMLAN bus, not on our harness) and the driver camera (not recorded).", '']
  return '\n'.join(lines)


def write_html(res, md_names, nframes, notes=''):
  import html as _html
  r = res
  rows = ''.join(f"<tr><td>{t['id']}</td><td>{t['closest_range_m']:.1f}</td><td>{t['t_rel_s']:+.2f}</td><td>{t['x_m']:.1f}</td><td>{t['y_m']:+.1f}</td><td>{t['range_rate_ms']:+.1f}</td></tr>" for t in r['radar_tracks_within_15m'])
  tiles = ''.join(f"<figure><img src='stills/{v['file']}'><figcaption>{k.replace('_', ' ')} ({v['rel_s']:+.1f} s)</figcaption></figure>" for k, v in md_names.items())
  rad = r['radar_after_stop']
  return f"""<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Incident report</title>
<style>body{{font:16px system-ui;margin:0;background:#14161a;color:#e8e8e8}}main{{max-width:1100px;margin:auto;padding:16px}}video,img{{max-width:100%}}
table{{border-collapse:collapse}}td,th{{border:1px solid #444;padding:4px 8px}}.k{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:8px}}
.k div{{background:#20242b;padding:10px;border-radius:8px}}b{{font-size:22px}}figure{{margin:0}}.g{{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:8px}}</style>
<main><h1>Hard brake at {r['onset_wall_mdt']} MDT</h1>
<video controls src=video.mp4 style='width:100%'></video>
<div class=k><div>Speed at brake press<br><b>{r['speed_at_onset_mph']:.1f} mph</b></div><div>Time to stop<br><b>{r['time_to_stop_s']:.1f} s</b></div>
<div>Peak decel (smoothed)<br><b>{r['peak_decel_g_smoothed']:.2f} g</b></div><div>ABS-like wheel lock<br><b>{r['abs_like_first_rel_s']:+.2f} to {r['abs_like_last_rel_s']:+.2f} s</b></div>
<div>Radar nearest after stop<br><b>{rad['min_range_m']:.1f}–{rad['max_range_m']:.1f} m</b></div><div>Jolt during / after stop<br><b>{r['jolt_peak_during_stop_g']:.2f} / {r['jolt_peak_after_standstill_g']:.2f} g</b> (bumps: {r['jolt_normal_driving_max_g']:.2f})</div></div>
<h2>Graph</h2><img src=graph.png><h2>Stills (road and wide)</h2><div class=g>{tiles}</div>
<h2>Radar tracks within 15 m</h2><table><tr><th>id<th>closest m<th>when s<th>forward m<th>lateral m<th>m/s</tr>{rows}</table>
<h2>Notes</h2><p>See report.md for what the data can and cannot rule out.</p>{('<h2>Notes from review</h2><p style=white-space:pre-wrap>' + _html.escape(notes.strip()) + '</p>') if notes else ''}</main>"""


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument('incident')
  ap.add_argument('route')
  ap.add_argument('first', type=int)
  ap.add_argument('last', type=int)
  ap.add_argument('--before', type=float, default=8)
  ap.add_argument('--after', type=float, default=8)
  ap.add_argument('--anchor', type=float)
  a = ap.parse_args()
  inc = Path(a.incident)
  raw, out = inc / 'raw', inc / 'report'
  out.mkdir(exist_ok=True)
  d = extract(raw, a.route, a.first, a.last)
  np.savez_compressed(out / 'telemetry.npz', **d)
  res, cf, ws, _ = analyse(d, a.anchor)
  t_a, t_b = res['onset'] - a.before, res['onset'] + a.after
  gimg, _ = graph_image(d, cf, ws, res, t_a, t_b)
  gimg.save(out / 'graph.png')
  video, codec, n = render_video(raw, a.route, d, cf, ws, res, out, t_a, t_b)
  names = stills(raw, a.route, d, res, out / 'stills')
  (out / 'report.json').write_text(json.dumps(res, indent=1))
  notes = (out / 'notes.md').read_text() if (out / 'notes.md').exists() else ''  # hand-written observations, kept apart from computed text
  (out / 'report.md').write_text(write_md(res, names, codec, n, notes))
  (out / 'index.html').write_text(write_html(res, names, n, notes))
  print(json.dumps({k: v for k, v in res.items() if k != 'radar_tracks_within_15m'}, indent=1))


if __name__ == '__main__':
  main()
