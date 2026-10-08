"""Geometry and touch routing for the Dashcam screen. Plain tuples and numbers: no raylib, so the layout and the touch rules are tested
headless. Laid out for the comma 3X's 2160x1080 screen and scaled to whatever rectangle it is given."""
from dataclasses import dataclass, field

from openpilot.system.review.player.timeline import RATES, scrub_fraction

Rect = tuple  # (x, y, w, h)
REF_W, REF_H = 2160, 1080
ROW_H = 150
SCRUB_PAD = 28            # the scrub bar's touch zone extends this far above and below the drawn bar (reference pixels)
DRAG_PX = 14              # movement beyond this turns a tap on the list into a scroll
BUTTONS = ('back5', 'stepback', 'play', 'stepfwd', 'fwd5')


def contains(rect: Rect, x: float, y: float) -> bool:
  return rect[0] <= x < rect[0] + rect[2] and rect[1] <= y < rect[1] + rect[3]


@dataclass(frozen=True)
class Layout:
  w: float
  h: float
  close: Rect
  refresh: Rect
  title: Rect
  list: Rect
  video: Rect
  info: Rect
  strip: Rect
  scrub: Rect
  scrub_touch: Rect
  protect: Rect
  buttons: dict = field(default_factory=dict)
  rates: dict = field(default_factory=dict)
  row_h: float = ROW_H

  def targets(self) -> dict:
    """Every touch target by name."""
    out = {'close': self.close, 'refresh': self.refresh, 'protect': self.protect, 'list': self.list, 'video': self.video}
    out.update({f'btn:{k}': v for k, v in self.buttons.items()})
    out.update({f'rate:{k}': v for k, v in self.rates.items()})
    return out


def compute_layout(w: float, h: float) -> Layout:
  sx, sy = w / REF_W, h / REF_H

  def r(x, y, rw, rh) -> Rect:
    return (x * sx, y * sy, rw * sx, rh * sy)

  buttons = {name: r(688 + i * 216, 788, 200, 120) for i, name in enumerate(BUTTONS)}
  gap, rate_w = 16, (1448 - 4 * 16) / len(RATES)
  rates = {rate: r(688 + i * (rate_w + gap), 924, rate_w, 108) for i, rate in enumerate(RATES)}
  return Layout(
    w=w, h=h,
    close=r(24, 24, 190, 110), title=r(230, 24, 240, 110), refresh=r(474, 24, 190, 110),
    list=r(24, 150, 640, 906),
    video=r(688, 24, 1000, 560), info=r(1712, 24, 424, 560),
    strip=r(688, 600, 1448, 84), scrub=r(688, 692, 1448, 80), scrub_touch=r(688, 692 - SCRUB_PAD, 1448, 80 + 2 * SCRUB_PAD),
    protect=r(1768, 788, 368, 120),
    buttons=buttons, rates=rates, row_h=ROW_H * sy,
  )


def hit(layout: Layout, x: float, y: float) -> str | None:
  """Name of the touch target at (x, y). Scrub is checked first because its zone is padded into the strip and the button row."""
  if contains(layout.scrub_touch, x, y):
    return 'scrub'
  for name, rect in layout.targets().items():
    if contains(rect, x, y):
      return name
  return None


def fit_rect(box: Rect, aspect: float) -> Rect:
  """Largest rectangle of the given width/height ratio centred in box."""
  x, y, w, h = box
  if aspect <= 0 or w <= 0 or h <= 0:
    return box
  if w / h > aspect:
    return (x + (w - h * aspect) / 2, y, h * aspect, h)
  return (x, y + (h - w / aspect) / 2, w, w / aspect)


def list_row_at(layout: Layout, y: float, offset: float, count: int) -> int | None:
  """Row under screen y when the list content is scrolled by `offset` (<= 0), or None."""
  top = layout.list[1]
  if not layout.list[1] <= y < layout.list[1] + layout.list[3] or layout.row_h <= 0:
    return None
  i = int((y - top - offset) // layout.row_h)
  return i if 0 <= i < count else None


class TouchRouter:
  """Turns press/move/release (and cancel) on one finger into actions:
       ('button', name)          released on the same target it was pressed on
       ('scrub', fraction, ph)   ph = 'start' | 'move' | 'end' while a finger is on the scrub bar
       ('list_tap', y)           a tap on the list that did not become a scroll
     A press that slides off its target does nothing, and nothing fires on a press that began elsewhere."""

  def __init__(self, layout: Layout):
    self.layout = layout
    self.down: str | None = None
    self._start = (0.0, 0.0)
    self._moved = False

  @property
  def pressed(self) -> str | None:
    return self.down

  def _frac(self, x: float) -> float:
    s = self.layout.scrub
    return scrub_fraction(x, s[0], s[2])

  def press(self, x: float, y: float) -> list:
    self.down = hit(self.layout, x, y)
    self._start, self._moved = (x, y), False
    return [('scrub', self._frac(x), 'start')] if self.down == 'scrub' else []

  def move(self, x: float, y: float) -> list:
    if self.down == 'scrub':
      return [('scrub', self._frac(x), 'move')]
    if abs(x - self._start[0]) > DRAG_PX or abs(y - self._start[1]) > DRAG_PX:
      self._moved = True
    return []

  def release(self, x: float, y: float) -> list:
    name, self.down = self.down, None
    if name is None:
      return []
    if name == 'scrub':
      return [('scrub', self._frac(x), 'end')]
    if hit(self.layout, x, y) != name:
      return []
    if name == 'list':
      return [] if self._moved else [('list_tap', y)]
    return [('button', name)]

  def cancel(self) -> list:
    """Touch lost (screen slept, view closing): finish a scrub so playback is not left held, fire nothing else."""
    name, self.down = self.down, None
    return [('scrub', 0.0, 'cancel')] if name == 'scrub' else []
