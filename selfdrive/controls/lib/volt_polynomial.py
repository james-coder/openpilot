"""Bounded polynomial stop references. Fitting lives on the workstation only."""

import math
import numpy as np
from numpy.polynomial import polynomial as poly

MODEL = 'bernstein-speed-7-v1'
# Convert Bernstein speed coefficients to ascending power coefficients once.
POWER = np.zeros((8, 8))
for k in range(8):
  for j in range(8-k):
    POWER[k+j, k] = math.comb(7, k) * math.comb(7-k, j) * (-1)**j


def split_coefficients(coefficients, u):
  row = np.asarray(coefficients)
  left, right = [row[0]], [row[-1]]
  for _ in range(len(row)-1):
    row = (1-u)*row[:-1] + u*row[1:]
    left.append(row[0])
    right.append(row[-1])
  return np.asarray(left), np.asarray(right[::-1])


SUBDIVISION = {}
for degree in (5, 6):
  matrices = [np.eye(degree+1)]
  for _ in range(3):
    matrices = [part for matrix in matrices for part in split_coefficients(matrix, .5)]
  SUBDIVISION[degree] = np.asarray(matrices)


def within_bounds(bernstein, power, low, high, lower=0.):
  restricted = split_coefficients(bernstein, lower)[1] if lower else bernstein
  coefficients = SUBDIVISION[len(bernstein)-1] @ restricted
  if coefficients.min() >= low-1e-6 and coefficients.max() <= high+1e-6:
    return True  # Convex-hull bound certifies every point, not just samples.
  values = extrema(power, lower)
  return bool(values.min() >= low-1e-6 and values.max() <= high+1e-6)


def valid_model(curve):
  try:
    shape = np.asarray(curve['shape'], dtype=float)
    return bool(curve['model'] == MODEL and curve['version'] == 2 and shape.shape == (2,)
                and np.isfinite(shape).all() and 0 <= shape[1] <= shape[0] <= 1
                and .3 < curve['max_speed'] <= 20 and 4.5 <= curve['gap'] <= 8)
  except (KeyError, TypeError, ValueError):
    return False


def extrema(coefficients, lower=0.):
  roots = poly.polyroots(poly.polyder(coefficients))
  u = [lower, 1., *(float(r.real) for r in roots if abs(r.imag) < 1e-7 and lower < r.real < 1)]
  return poly.polyval(u, coefficients)


class Polynomial:
  def __init__(self, coefficients, duration):
    self.coefficients = np.asarray(coefficients)
    self.duration = float(duration)
    self.v = POWER @ self.coefficients
    self.a = poly.polyder(self.v) / duration
    self.j = poly.polyder(self.a) / duration
    self.x = poly.polyint(self.v) * duration
    self.distance = float(poly.polyval(1., self.x))

  def evaluate(self, time):
    u = np.clip(np.asarray(time) / self.duration, 0., 1.)
    # These are exact polynomial boundary conditions, not a speed clamp hiding
    # negative velocity. Monotone Bernstein coefficients certify the interior.
    end = u >= 1
    return np.column_stack((np.where(end, self.distance, poly.polyval(u, self.x)),
                            np.where(end, 0., poly.polyval(u, self.v)),
                            np.where(end, 0., poly.polyval(u, self.a)),
                            np.where(end, 0., poly.polyval(u, self.j))))

  def feasible(self):
    a = 7*np.diff(self.coefficients)/self.duration
    j = 6*np.diff(a)/self.duration
    terminal = max(0., 1-.1/self.duration)
    return (within_bounds(a, self.a, -2.5, 0.) and within_bounds(j, self.j, -1.5, 1.5)
            and within_bounds(a, self.a, -.05, .05, terminal) and within_bounds(j, self.j, -.3, .3, terminal))


def generate(speed, acceleration, distance, shape, jerk=0.):
  """Project two learned shape parameters onto an exact finite-distance stop.

  Entry v/a/j and terminal v/a/j fix six of eight Bernstein coefficients.
  Integrating speed fixes the sum of the remaining two. Only a scalar clipped
  projection is needed at each of 64 bounded duration candidates.
  """
  if (not all(math.isfinite(x) for x in (speed, acceleration, distance, jerk, *shape))
      or speed <= .3 or distance <= 0 or not -2.5 <= acceleration <= 0 or abs(jerk) > 1.5):
    return None
  lower, upper = max(.2, distance/speed), min(40., 4*distance/speed + 2.)
  if upper <= lower:
    return None
  duration = np.linspace(lower, upper, 64)
  b0 = np.full(64, speed)
  b1 = b0 + acceleration * duration / 7
  b2 = 2*b1-b0 + jerk * duration**2 / 42
  total = 8*distance/duration - b0-b1-b2
  keep = (b0 >= b1) & (b1 >= b2) & (b2 >= 0) & (total >= 0) & (total <= 2*b2)
  if not keep.any():
    return None
  duration, b0, b1, b2, total = (a[keep] for a in (duration, b0, b1, b2, total))
  b3 = np.clip((total + speed*(shape[0]-shape[1]))/2, total/2, np.minimum(b2, total))
  b4 = total-b3
  coefficients = np.column_stack((b0, b1, b2, b3, b4, np.zeros((len(b0), 3))))
  v = POWER @ coefficients.T
  a = poly.polyder(v) / duration
  j = poly.polyder(a) / duration
  # Cheap vectorized rejection first. Acceptance still uses analytic extrema;
  # sampling cannot hide a violation between these points.
  u = np.linspace(0., 1., 17)
  keep = (np.min(poly.polyval(u, a), axis=1) >= -2.5-1e-6) & (np.max(abs(poly.polyval(u, j)), axis=1) <= 1.5+1e-6)
  terminal = np.maximum(0., 1-.1/duration)
  keep &= (abs(poly.polyval(terminal, a, tensor=False)) <= .05+1e-6)
  keep &= (abs(poly.polyval(terminal, j, tensor=False)) <= .3+1e-6)
  scores = (b3/speed-shape[0])**2 + (b4/speed-shape[1])**2
  for index in np.argsort(scores, kind='stable'):
    if keep[index]:
      result = Polynomial(coefficients[index], duration[index])
      if result.feasible():
        return result
  return None


class PolynomialStopTrajectory:
  def __init__(self, curve):
    if not valid_model(curve):
      raise ValueError('Invalid polynomial stopping model')
    self.curve = curve
    # Warm NumPy's numerical kernels before entering the periodic planner loop.
    generate(5., 0., 20.5, curve['shape'])
    self.reset()

  def reset(self):
    self.reference = None
    self.elapsed = self.travel = 0.
    self.anchor = None
    self.retry_in = 0.
    self.reason = 'inactive'

  def start(self, speed, acceleration, distance):
    self.reset()
    if not .3 < speed <= self.curve['max_speed'] or distance <= self.curve['gap']:
      self.reason = 'outside support'
      return False
    self.reference = generate(speed, acceleration, distance-self.curve['gap'], self.curve['shape'])
    self.anchor = distance
    self.reason = 'tracking' if self.reference is not None else 'no feasible polynomial'
    return self.reference is not None

  def update(self, times, speed, acceleration, distance, dt):
    if self.retry_in > 0:
      self.retry_in -= dt
      return None
    if self.reference is None and not self.start(speed, acceleration, distance):
      self.retry_in = .5
      return None
    # Keep one world-space endpoint. A discontinuous range correction cannot
    # become an x-only reference shift inconsistent with its v/a derivatives.
    if abs(distance + self.travel-self.anchor) > 1.:
      self.reset()
      self.reason = 'lead position changed'
      return None
    reference = self.reference.evaluate(self.elapsed + np.asarray(times))[:, :3]
    reference[:, 0] -= self.travel
    self.elapsed += dt
    self.travel += max(0., speed) * dt
    return reference

  @property
  def remaining_time(self):
    return max(0., self.reference.duration-self.elapsed) if self.reference is not None else None
