"""Incident detection for the dashcam: hard braking, ABS-like wheel lock, and extreme g-force. Pure logic, no I/O.

Fed carState (vEgo, brakePressed) and accelerometer samples on the car's monotonic clock. It returns event dicts:
  {'type': 'start', 't', 'kinds', ...}   the first trigger of an episode (protect footage and show the banner now)
  {'type': 'update', ...}                 a new kind joined the open episode
  {'type': 'end', 't', 'kinds', 'peaks'}  quiet for `episode_gap_s`: write the summary

Thresholds come from 33 minutes of ordinary driving plus the 2026-10-01 near-miss (normal peaks: decel 0.51 g,
horizontal 0.1 s-smoothed 0.66 g, >10 Hz jolt 0.60 g, 0 ABS-like frames). That is a small sample, so they are
deliberately cautious and meant to be re-tuned from replays of more drives (tools/dashcam/detector_replay.py).
Not detected yet: swerves (needs its own calibration). 'impact' (a sharp horizontal jolt) is disabled until calibrated. Nothing here can affect driving.
"""
from collections import deque
from dataclasses import dataclass

G = 9.81


@dataclass(frozen=True)
class Thresholds:
  hard_brake_g: float = 0.45          # deceleration from the wheel-speed slope
  brake_window_s: float = 0.4
  hard_brake_min_speed: float = 2.5   # m/s at the start of the window
  hard_brake_sustain_s: float = 0.3
  abs_gap_g: float = 0.35             # wheel-derived decel exceeds IMU decel by this much while braking
  abs_min_decel_g: float = 0.6
  abs_window_s: float = 0.3
  abs_min_speed: float = 2.0
  abs_sustain_s: float = 0.1
  extreme_horizontal_g: float = 0.9   # horizontal acceleration, smoothed over smooth_s
  impact_jolt_g: float = 99.0         # >10 Hz horizontal jolt; disabled (99) until calibrated on stored drives
  smooth_s: float = 0.1
  gravity_tau_s: float = 5.0
  warmup_s: float = 3.0
  episode_gap_s: float = 3.0
  brake_recent_s: float = 0.3         # brake pedal counts as pressed this long after release
  max_sample_gap_s: float = 0.5       # a longer gap in either stream resets the windows


def _norm(v):
  return (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) ** 0.5


class EventDetector:
  def __init__(self, thresholds: Thresholds | None = None):
    self.th = thresholds or Thresholds()
    self._cs = deque()           # (t, vEgo)
    self._last_brake_t = -1e9
    self._brake_since = None     # start of the continuous hard-brake condition
    self._abs_since = None
    self._acc = deque()          # (t, ax, ay, az) within smooth_s
    self._grav = None
    self._grav_t = None
    self._t_first_acc = None
    self._ah_t = -1e9            # time of the latest smoothed horizontal reading
    self._ah_g = 0.0
    self._ep = None              # open episode
    self.stats = dict(horizontal_g=0.0, jolt_g=0.0, jolt_h_g=0.0, decel_g=0.0)  # running peaks for calibration replays

  # ---- feeds -------------------------------------------------------------------------------------------------
  def car_state(self, t: float, v: float, brake_pressed: bool) -> list[dict]:
    th = self.th
    if self._cs and t - self._cs[-1][0] > th.max_sample_gap_s:
      self._cs.clear()
      self._brake_since = self._abs_since = None
    self._cs.append((t, v))
    while self._cs and t - self._cs[0][0] > th.brake_window_s + 0.1:
      self._cs.popleft()
    if brake_pressed:
      self._last_brake_t = t
    braking = (t - self._last_brake_t) <= th.brake_recent_s
    out = []

    decel = self._decel_g(t, th.brake_window_s)
    if decel is not None:
      self.stats['decel_g'] = max(self.stats['decel_g'], decel)
    if decel is not None and braking and decel >= th.hard_brake_g and self._v_at(t - th.brake_window_s) >= th.hard_brake_min_speed:
      self._brake_since = self._brake_since if self._brake_since is not None else t
      if t - self._brake_since >= th.hard_brake_sustain_s:
        out += self._trigger(t, 'hard_brake', decel_g=decel)
    else:
      self._brake_since = None

    wd = self._decel_g(t, th.abs_window_s)
    imu_fresh = (t - self._ah_t) <= 0.1
    if (wd is not None and braking and imu_fresh and wd >= th.abs_min_decel_g and wd - self._ah_g >= th.abs_gap_g
            and self._v_at(t - th.abs_window_s) >= th.abs_min_speed):
      self._abs_since = self._abs_since if self._abs_since is not None else t
      if t - self._abs_since >= th.abs_sustain_s:
        out += self._trigger(t, 'abs_stop', decel_g=wd)
    else:
      self._abs_since = None
    return out + self._maybe_end(t)

  def accel(self, t: float, ax: float, ay: float, az: float) -> list[dict]:
    th = self.th
    a = (ax, ay, az)
    if self._acc and t - self._acc[-1][0] > th.max_sample_gap_s:
      self._acc.clear()
    if self._grav is None or self._grav_t is None or t - self._grav_t > 10 * th.max_sample_gap_s:
      self._grav, self._grav_t, self._t_first_acc = list(a), t, t
    alpha = min((t - self._grav_t) / (th.gravity_tau_s + (t - self._grav_t)), 1.0)
    self._grav = [g + alpha * (x - g) for g, x in zip(self._grav, a, strict=True)]
    self._grav_t = t
    self._acc.append((t, *a))
    while self._acc and t - self._acc[0][0] > th.smooth_s:
      self._acc.popleft()
    if len(self._acc) < 3 or t - self._t_first_acc < th.warmup_s:
      return self._maybe_end(t)
    n = len(self._acc)
    mean = [sum(s[i] for s in self._acc) / n for i in (1, 2, 3)]
    gn = _norm(self._grav)
    if gn < 5.0 or gn > 15.0:  # not a believable gravity reading
      return self._maybe_end(t)
    gh = [x / gn for x in self._grav]
    mdot = sum(m * g for m, g in zip(mean, gh, strict=True))
    horiz = _norm([m - mdot * g for m, g in zip(mean, gh, strict=True)]) / G
    dev = [x - m for x, m in zip(a, mean, strict=True)]
    jolt = _norm(dev) / G                                     # all axes: potholes and bumps live here (mostly vertical)
    ddot = sum(d * g for d, g in zip(dev, gh, strict=True))
    jolt_h = _norm([d - ddot * g for d, g in zip(dev, gh, strict=True)]) / G  # horizontal only: what a collision adds
    self._ah_t, self._ah_g = t, horiz
    st = self.stats
    st['horizontal_g'], st['jolt_g'], st['jolt_h_g'] = max(st['horizontal_g'], horiz), max(st['jolt_g'], jolt), max(st['jolt_h_g'], jolt_h)
    out = []
    if horiz >= th.extreme_horizontal_g:
      out += self._trigger(t, 'extreme_g', horizontal_g=horiz)
    if jolt_h >= th.impact_jolt_g:
      out += self._trigger(t, 'impact', jolt_h_g=jolt_h)
    if self._ep is not None:
      p = self._ep['peaks']
      p['horizontal_g'] = max(p.get('horizontal_g', 0.0), horiz)
      p['jolt_g'] = max(p.get('jolt_g', 0.0), jolt)
      p['jolt_h_g'] = max(p.get('jolt_h_g', 0.0), jolt_h)
    return out + self._maybe_end(t)

  def tick(self, t: float) -> list[dict]:
    return self._maybe_end(t)

  # ---- internals ---------------------------------------------------------------------------------------------
  def _v_at(self, t_target: float) -> float:
    best = None
    for t, v in self._cs:
      if t >= t_target:
        return v if best is None else best
      best = v
    return best if best is not None else 0.0

  def _decel_g(self, now: float, window: float):
    pts = [(t, v) for t, v in self._cs if t >= now - window]
    if len(pts) < 4 or pts[-1][0] - pts[0][0] < 0.6 * window:
      return None
    n = len(pts)
    mt = sum(p[0] for p in pts) / n
    mv = sum(p[1] for p in pts) / n
    var = sum((p[0] - mt) ** 2 for p in pts)
    if var <= 0:
      return None
    slope = sum((p[0] - mt) * (p[1] - mv) for p in pts) / var
    return -slope / G

  def _trigger(self, t: float, kind: str, **peaks) -> list[dict]:
    if self._ep is None:
      self._ep = dict(t_start=t, last=t, kinds=[kind], peaks={})
      self._note(peaks)
      return [dict(type='start', t=t, kinds=list(self._ep['kinds']), peaks=dict(self._ep['peaks']))]
    self._ep['last'] = t
    self._note(peaks)
    if kind not in self._ep['kinds']:
      self._ep['kinds'].append(kind)
      return [dict(type='update', t=t, kinds=list(self._ep['kinds']), peaks=dict(self._ep['peaks']))]
    return []

  def _note(self, peaks: dict):
    for k, v in peaks.items():
      self._ep['peaks'][k] = max(self._ep['peaks'].get(k, 0.0), v)

  def _maybe_end(self, t: float) -> list[dict]:
    if self._ep is not None and t - self._ep['last'] > self.th.episode_gap_s:
      ep, self._ep = self._ep, None
      return [dict(type='end', t=ep['last'], t_start=ep['t_start'], kinds=ep['kinds'], peaks=ep['peaks'])]
    return []
