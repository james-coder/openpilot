import itertools

import pytest

from openpilot.system.review.player.layout import REF_H, REF_W, SCRUB_PAD, TouchRouter, compute_layout, contains, fit_rect, hit, list_row_at
from openpilot.system.review.player.present import info_lines, list_empty_text, row_lines, speed_text
from openpilot.system.review.player.timeline import RATES

SIZES = [(2160, 1080), (1920, 1080), (1280, 720), (2160, 1200)]


def center(rect):
  return rect[0] + rect[2] / 2, rect[1] + rect[3] / 2


def overlap(a, b):
  return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


@pytest.mark.parametrize('size', SIZES)
def test_every_control_is_on_screen_and_none_overlap(size):
  L = compute_layout(*size)
  targets = L.targets()
  assert len(L.buttons) == 5 and set(L.rates) == set(RATES)
  for name, r in targets.items():
    assert r[0] >= 0 and r[1] >= 0 and r[0] + r[2] <= size[0] + 1e-6 and r[1] + r[3] <= size[1] + 1e-6, name
  for (n1, r1), (n2, r2) in itertools.combinations(targets.items(), 2):
    assert not overlap(r1, r2), (n1, n2)
  for name in ('strip', 'scrub', 'info', 'title'):
    r = getattr(L, name)
    assert r[0] + r[2] <= size[0] + 1e-6 and r[1] + r[3] <= size[1] + 1e-6


def test_touch_targets_are_large_on_the_device_screen():
  L = compute_layout(REF_W, REF_H)
  for name, r in L.targets().items():
    if name not in ('list', 'video'):
      assert r[2] >= 180 and r[3] >= 100, name       # about a fingertip and a half at the comma 3X's density
  assert L.scrub[3] + 2 * SCRUB_PAD >= 130


def test_scrub_zone_is_checked_before_what_it_overlaps_and_is_the_full_bar_width():
  L = compute_layout(REF_W, REF_H)
  x, y, w, h = L.scrub
  assert hit(L, x + 1, y + h / 2) == 'scrub' and hit(L, x + w - 1, y + 5) == 'scrub'
  assert hit(L, x + w / 2, y - SCRUB_PAD + 2) == 'scrub'           # padded above the drawn bar
  assert hit(L, x - 5, y + h / 2) != 'scrub'


def test_hit_testing_by_name():
  L = compute_layout(REF_W, REF_H)
  assert hit(L, *center(L.close)) == 'close' and hit(L, *center(L.refresh)) == 'refresh' and hit(L, *center(L.protect)) == 'protect'
  assert hit(L, *center(L.buttons['play'])) == 'btn:play' and hit(L, *center(L.rates[0.25])) == 'rate:0.25'
  assert hit(L, *center(L.video)) == 'video' and hit(L, *center(L.list)) == 'list'
  assert hit(L, 5, 5) is None and hit(L, -10, 100) is None and hit(L, REF_W + 1, 10) is None


def test_fit_rect_keeps_the_aspect_and_centres():
  x, y, w, h = fit_rect((100, 100, 1000, 560), 526 / 330)
  assert h == 560 and w == pytest.approx(560 * 526 / 330) and x == pytest.approx(100 + (1000 - w) / 2) and y == 100
  x, y, w, h = fit_rect((0, 0, 300, 600), 2.0)
  assert w == 300 and h == 150 and y == 225
  assert fit_rect((0, 0, 0, 0), 1.5) == (0, 0, 0, 0)


def test_list_row_math_with_scrolling():
  L = compute_layout(REF_W, REF_H)
  top, rh = L.list[1], L.row_h
  assert list_row_at(L, top + 1, 0, 10) == 0 and list_row_at(L, top + rh + 1, 0, 10) == 1
  assert list_row_at(L, top + 1, -rh * 3, 10) == 3
  assert list_row_at(L, top + 1, 0, 0) is None
  assert list_row_at(L, top + rh * 5 + 1, 0, 3) is None        # below the last row
  assert list_row_at(L, top - 5, 0, 10) is None                 # above the list
  assert list_row_at(L, L.list[1] + L.list[3] + 5, 0, 10) is None


# ---- touch routing -----------------------------------------------------------------------------------------------------------

@pytest.fixture
def router():
  return TouchRouter(compute_layout(REF_W, REF_H))


def test_a_tap_fires_the_button_it_began_on(router):
  x, y = center(router.layout.buttons['play'])
  assert router.press(x, y) == [] and router.pressed == 'btn:play'
  assert router.release(x, y) == [('button', 'btn:play')] and router.pressed is None


def test_sliding_off_a_button_cancels_it(router):
  x, y = center(router.layout.buttons['play'])
  router.press(x, y)
  assert router.release(x + 400, y) == []
  router.press(x, y)
  nx, ny = center(router.layout.buttons['fwd5'])
  assert router.release(nx, ny) == []            # a different button than the press began on


def test_releasing_without_a_press_fires_nothing(router):
  x, y = center(router.layout.buttons['play'])
  assert router.release(x, y) == [] and router.press(5, 5) == [] and router.release(*center(router.layout.close)) == []


def test_scrub_reports_start_move_end_with_the_fraction_along_the_bar(router):
  sx, sy, sw, sh = router.layout.scrub
  y = sy + sh / 2
  assert router.press(sx + sw * 0.25, y) == [('scrub', pytest.approx(0.25), 'start')]
  assert router.move(sx + sw * 0.5, y + 300) == [('scrub', pytest.approx(0.5), 'move')]     # finger may stray vertically
  assert router.move(sx - 500, y) == [('scrub', 0.0, 'move')]                               # and off the ends
  assert router.release(sx + sw * 2, y) == [('scrub', 1.0, 'end')]
  assert router.pressed is None


def test_cancel_finishes_a_scrub_and_ignores_everything_else(router):
  sx, sy, sw, sh = router.layout.scrub
  router.press(sx + 10, sy + 10)
  assert router.cancel() == [('scrub', 0.0, 'cancel')]
  assert router.cancel() == []
  router.press(*center(router.layout.close))
  assert router.cancel() == [] and router.release(*center(router.layout.close)) == []


def test_list_tap_versus_scroll(router):
  x, y = center(router.layout.list)
  router.press(x, y)
  assert router.release(x + 3, y + 4) == [('list_tap', y + 4)]
  router.press(x, y)
  router.move(x, y - 60)
  assert router.release(x, y - 60) == []            # became a scroll


# ---- text --------------------------------------------------------------------------------------------------------------------

def test_speed_units():
  assert speed_text(10.0, True) == '36 km/h' and speed_text(10.0, False) == '22 mph' and speed_text(0.0, False) == '0 mph'


def test_empty_list_messages_are_friendly():
  for state in ('loading', 'missing', 'unreadable', 'ok'):
    head, detail = list_empty_text(state)
    assert head and isinstance(detail, str)
  assert 'No saved events' in list_empty_text('missing')[0]


def test_row_text_and_info_lines_cover_the_states():
  from openpilot.system.review.player.events import EventRow, SegmentStatus
  from openpilot.system.review.player.session import View
  row = EventRow(key='k', id=1, mono=5.0, wall=1_790_000_000.0, kinds=('hard_brake',), peaks={'decel_g': 0.6}, segment='00000007--abc--4')
  title, detail, footage = row_lines(row, SegmentStatus('ready', True))
  assert title.startswith('20') and 'Hard brake' in detail and '0.60 g' in detail and 'abc--4' in footage and 'protected' in footage
  base = dict(events_state='ok', rows=(row,), statuses={}, selected=None, clip_state='none')
  assert any('Pick an event' in t for t, _ in info_lines(View(**base), True))
  v = View(**{**base, 'selected': 'k', 'clip_state': 'ready', 'segment_status': SegmentStatus('ready', False), 'event_time': None, 'buffering': True,
              'in_gap': True, 'hot': True, 'notice': 'Protecting...', 'message': 'damaged', 'position': 3.0, 'duration': 60.0,
              'now': dict(speed=10.0, brake=True, left=True, right=False)})
  texts = [t for t, _ in info_lines(v, False, wall_now=1_790_000_100.0)]
  joined = ' | '.join(texts)
  for needle in ('Event time unknown', 'BRAKE LEFT', '22 mph', 'Loading frames', 'damaged here', 'Hot', 'Protecting', '0:03.0 / 1:00.0'):
    assert needle in joined, needle
  assert contains((0, 0, 10, 10), 5, 5) and not contains((0, 0, 10, 10), 10, 5)
