"""Bounded, blinded A/B steering experiment for the Volt (docs/2026-09-29-steer-experiment.md).

Armed while parked by setting the VoltSteerExperiment param (JSON, see parse_config). During that one drive
it alternates mode A (exactly the stock controller) and mode B (the test settings) in one-minute blocks, in
randomized A/B pairs so the order can't be guessed. Time only counts while openpilot is steering at a steady
speed inside a band, straight-ish, with no driver steering; any break restarts the block. Every block's
start/end (CLOCK_MONOTONIC, the same clock as logMonoTime) goes to /data/steer_exp/blocks.jsonl so the drive's
logs can be split by mode afterwards. The mode is never shown to the driver, only the block counter.

Limits, whatever the param says: friction assist <= FRICTION_MAX torque units (6% of the range, about 0.18 Nm
of the 3 Nm limit), gain and feedforward scales inside narrow ranges, no effect below 5 m/s or while the driver
steers or the safety limits are active, and A/B transitions are ramped. All state is in memory; the only I/O
is a daemon thread that writes the status/block files, so nothing here can block the 100 Hz control loop. Any
error disables the experiment and the controller is stock again.
"""
import collections
import json
import math
import random
import threading
import time
from pathlib import Path

ROOT = Path('/data/steer_exp')
PARAM = 'VoltSteerExperiment'

FRICTION_MAX = 0.06            # torque units, hard cap
KP_RANGE = (0.7, 1.3)
FF_RANGE = (0.8, 1.1)
FRICTION_DEADBAND_DEG = 0.1    # about 1.5 sensor steps of 0.0625 deg: below this is noise
FRICTION_RAMP_DEG = 0.6        # full assist at and beyond this angle error
RAMP_S = 1.0                   # A<->B transitions are ramped over this long
SETTLE_S = 5.0                 # the analysis ignores the first seconds of each block
GAP_VOID_S = 0.5               # a break in steady driving longer than this restarts the block
ARM_MAX_AGE_S = 6 * 3600       # a stale arming is ignored
MAX_CURVATURE = 1.5e-3         # 1/m, straight-ish only
MAX_ACCEL = 0.8                # m/s^2, steady speed only


def _clip(x, lo, hi):
  return min(max(x, lo), hi)


def parse_config(raw, wall_now: float | None = None) -> dict | None:
  """Validate and clamp the param JSON. None if missing, malformed or stale."""
  try:
    cfg = json.loads(raw) if isinstance(raw, str | bytes) else dict(raw)
    armed = float(cfg['armed_at'])
    wall_now = time.time() if wall_now is None else wall_now  # noqa: TID251 -- compared to an epoch stamp written at arming
    if not 0 <= wall_now - armed <= ARM_MAX_AGE_S:
      return None
    b = cfg.get('B', {})
    blocks = int(_clip(int(cfg.get('blocks', 10)), 2, 20))
    min_mph = _clip(float(cfg.get('min_mph', 25)), 15., 60.)
    return {
      'friction': _clip(float(b.get('friction', 0.)), 0., FRICTION_MAX),
      'kp': _clip(float(b.get('kp', 1.)), *KP_RANGE),
      'ff': _clip(float(b.get('ff', 1.)), *FF_RANGE),
      'blocks': blocks + blocks % 2,
      'block_s': _clip(float(cfg.get('block_s', 60)), 30., 120.),
      'min_mph': min_mph,
      'max_mph': _clip(float(cfg.get('max_mph', 50)), min_mph + 5., 80.),
      'seed': int(cfg.get('seed', 0)),
      'armed_at': armed,
    }
  except (KeyError, TypeError, ValueError, OverflowError):
    return None


def friction_assist(error_deg: float, friction: float) -> float:
  """Small torque in the direction of the needed motion, to get the steering past its stiction. Zero inside the
  sensor-noise deadband, ramping linearly to `friction` at FRICTION_RAMP_DEG, odd in the error."""
  a = abs(error_deg)
  if a <= FRICTION_DEADBAND_DEG or friction <= 0 or not math.isfinite(error_deg):
    return 0.
  s = min(1., (a - FRICTION_DEADBAND_DEG) / (FRICTION_RAMP_DEG - FRICTION_DEADBAND_DEG))
  return math.copysign(min(friction, FRICTION_MAX) * s, error_deg)


class SteerExperiment:
  def __init__(self, cfg: dict, root: Path = ROOT, mono=time.monotonic, wall=time.time, start_thread: bool = True,  # noqa: TID251 -- wall clock only stamps files
               on_done=None):
    self.cfg = cfg
    self.root = root
    self.mono = mono
    self.wall = wall
    self.on_done = on_done
    rng = random.Random(cfg['seed'] or int(wall()))
    self.order: list[str] = []
    for _ in range(cfg['blocks'] // 2):
      pair = ['A', 'B']
      rng.shuffle(pair)
      self.order += pair
    self.run_id = f'{int(wall()):d}'
    self.index = 0
    self.acc = 0.               # seconds of the current block accumulated so far
    self.block_start = None     # monotonic start of the current continuous stretch
    self.gap = 0.
    self.mix = 0.               # 0 = stock, 1 = test settings
    self.state = 'running'
    self.qualifying = False
    self._last = None
    self._records: collections.deque = collections.deque(maxlen=500)
    self._done_handled = False
    if start_thread:
      threading.Thread(target=self._writer, name='steer-exp-writer', daemon=True).start()

  @classmethod
  def from_params(cls, params, **kw):
    cfg = parse_config(params.get(PARAM))
    if cfg is None:
      return None
    return cls(cfg, on_done=lambda: params.remove(PARAM), **kw)

  # -- control-loop side: pure arithmetic, no I/O ---------------------------------------------------------------
  def step(self, active: bool, v_ego: float, a_ego: float, pressed: bool, curvature: float) -> float:
    """Advance the schedule. Returns mix in [0, 1] (0 = stock)."""
    now = self.mono()
    dt = 0. if self._last is None else _clip(now - self._last, 0., .1)
    self._last = now
    if self.state == 'done':
      self._ramp(0., dt)
      return self.mix
    mph = v_ego * 2.237
    q = bool(active and not pressed and self.cfg['min_mph'] <= mph <= self.cfg['max_mph'] and abs(a_ego) < MAX_ACCEL
             and abs(curvature) < MAX_CURVATURE)
    self.qualifying = q
    if not active:
      self.mix = 0.
    if q:
      if self.block_start is None:
        self.block_start = now
      self.gap = 0.
      self.acc += dt
      self._ramp(1. if self.order[self.index] == 'B' else 0., dt)
      if self.acc >= self.cfg['block_s']:
        self._record(True, now)
        self.index += 1
        self.acc = 0.
        self.block_start = None
        if self.index >= len(self.order):
          self.state = 'done'
    else:
      self.gap += dt
      if self.gap > GAP_VOID_S and self.acc > 0:
        self._record(False, now, 'gap')
        self.acc = 0.
        self.block_start = None
      if self.gap > GAP_VOID_S or not active:
        self._ramp(0., dt)
    return self.mix

  def _ramp(self, target: float, dt: float):
    step = dt / RAMP_S
    self.mix = _clip(self.mix + _clip(target - self.mix, -step, step), 0., 1.)

  def _record(self, valid: bool, now: float, reason: str = ''):
    self._records.append({'run': self.run_id, 'block': self.index, 'mode': self.order[self.index], 'valid': valid,
                          'reason': reason, 't0': self.block_start, 't1': now, 'settle_s': SETTLE_S,
                          'wall': self.wall(), 'cfg': {k: self.cfg[k] for k in ('friction', 'kp', 'ff')}})

  # -- writer thread: status and block files ---------------------------------------------------------------------
  def status(self) -> dict:
    return {'time': self.wall(), 'run': self.run_id, 'state': self.state, 'block': min(self.index + 1, len(self.order)),
            'of': len(self.order), 'qualifying': self.qualifying, 'progress_s': round(self.acc, 1)}

  def flush(self):
    self.root.mkdir(parents=True, exist_ok=True)
    if self._records:
      with (self.root / 'blocks.jsonl').open('a') as f:
        while self._records:
          f.write(json.dumps(self._records.popleft()) + '\n')
    tmp = self.root / 'status.tmp'
    tmp.write_text(json.dumps(self.status()))
    tmp.replace(self.root / 'status.json')
    if self.state == 'done' and not self._done_handled and self.on_done is not None:
      self._done_handled = True
      self.on_done()

  def _writer(self):
    while True:
      try:
        self.flush()
      except Exception:
        pass  # never turn a storage failure into anything else
      time.sleep(1.)


def arm(friction: float = 0.05, kp: float = 1.0, ff: float = 1.0, blocks: int = 10, block_s: float = 60,
        min_mph: float = 25, max_mph: float = 50, seed: int = 0, params=None, root: Path = ROOT) -> dict:
  """Arm one experiment for the next drive. Run on the car while parked. Old block records are set aside, a marker
  file stamps the arming (the analysis takes every log segment written after it), and the param is written with the
  car's own clock. The param clears itself on boot and when the experiment finishes."""
  if params is None:
    from openpilot.common.params import Params
    from openpilot.common.time_helpers import system_time_valid
    if not system_time_valid():
      raise RuntimeError('system clock not set yet; arming now would be judged stale once it syncs')
    params = Params()
  now = time.time()  # noqa: TID251 -- epoch stamp compared against the same clock at drive time
  root.mkdir(parents=True, exist_ok=True)
  old = root / 'blocks.jsonl'
  if old.exists():
    old.rename(root / f'blocks_{int(now)}.jsonl')
  (root / 'status.json').unlink(missing_ok=True)
  (root / 'armed').write_text(str(now))
  cfg = {'B': {'friction': friction, 'kp': kp, 'ff': ff}, 'blocks': blocks, 'block_s': block_s, 'min_mph': min_mph,
         'max_mph': max_mph, 'seed': seed, 'armed_at': now}
  parsed = parse_config(cfg, wall_now=now)
  if parsed is None:
    raise ValueError('invalid experiment configuration')
  params.put(PARAM, json.dumps(cfg))
  return parsed


def disarm(params=None):
  if params is None:
    from openpilot.common.params import Params
    params = Params()
  params.remove(PARAM)
