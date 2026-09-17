import ctypes as C
from pathlib import Path
import subprocess

import pytest


class Status(C.Structure):
  _fields_ = [('since', C.c_uint32), ('intro_ms', C.c_uint32), ('state', C.c_uint8),
             ('slot', C.c_uint8), ('error', C.c_uint8), ('intro_active', C.c_uint8),
             ('intro_seen', C.c_uint8), ('intro_slot', C.c_uint8)]


@pytest.fixture(scope='module')
def leds(tmp_path_factory):
  own = Path(__file__).parent / 'firmware'
  binary = tmp_path_factory.mktemp('status-led') / 'led.so'
  subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-fanalyzer', '-shared', '-fPIC',
                  str(own / 'status_led.c'), '-o', str(binary)], check=True)
  lib = C.CDLL(str(binary))
  lib.vgw_led_init.argtypes = [C.POINTER(Status), C.c_uint32]
  lib.vgw_led_init.restype = None
  lib.vgw_led_set.argtypes = [C.POINTER(Status), C.c_uint, C.c_uint, C.c_uint, C.c_uint32]
  lib.vgw_led_set.restype = None
  lib.vgw_led_sample.argtypes = [C.POINTER(Status), C.c_uint32]
  lib.vgw_led_sample.restype = C.c_uint8
  lib.vgw_white_led_bsrr.argtypes = [C.c_uint8]
  lib.vgw_white_led_bsrr.restype = C.c_uint32
  return lib


@pytest.mark.parametrize('state,color', [(1, 4), (2, 2)])
@pytest.mark.parametrize('slot,count', [(0, 1), (1, 2), (255, 3)])
def test_slot_pulses(leds, state, color, slot, count):
  s = Status()
  leds.vgw_led_init(C.byref(s), 100)
  leds.vgw_led_set(C.byref(s), state, slot, 0, 100)
  for t in range(8000):
    phase = t % 4000
    expected = color if phase // 300 < count and phase % 300 < 150 else 0
    assert leds.vgw_led_sample(C.byref(s), 4100 + t) == expected


@pytest.mark.parametrize('code', range(1, 10))
def test_fault_pulses(leds, code):
  s = Status()
  leds.vgw_led_init(C.byref(s), 0)
  leds.vgw_led_set(C.byref(s), 5, 255, code, 0)
  period=code*1000+3000
  for t in range(period*2):
    phase=t%period
    assert leds.vgw_led_sample(C.byref(s), t) == (1 if phase<code*1000 and phase%1000<500 else 0)


@pytest.mark.parametrize('rgb', range(256))
def test_only_led_pins_touched(leds, rgb):
  value = leds.vgw_white_led_bsrr(rgb)
  pins = (1 << 9) | (1 << 7) | (1 << 6)
  set_bits, reset_bits = value & 0xffff, value >> 16
  assert (set_bits | reset_bits) == pins
  assert not (set_bits & reset_bits)
  for color, pin in [(1, 9), (2, 7), (4, 6)]:
    assert bool(reset_bits & (1 << pin)) == bool(rgb & color)  # active low


def test_rollover_repeated_state_and_skipped_ticks(leds):
  s = Status()
  start = 0xfffffff0
  leds.vgw_led_init(C.byref(s), start)
  leds.vgw_led_set(C.byref(s), 2, 1, 0, start)
  leds.vgw_led_set(C.byref(s), 2, 1, 0, 500)
  assert s.since == start
  assert leds.vgw_led_sample(C.byref(s), (start + 350) & 0xffffffff) == 1
  assert leds.vgw_led_sample(C.byref(s), (start + 3999) & 0xffffffff) == 0
  assert leds.vgw_led_sample(C.byref(s), (start + 8000) & 0xffffffff) == 2
  assert not s.intro_active
  assert leds.vgw_led_sample(C.byref(s), (start + 10) & 0xffffffff) == 2  # next full clock wrap, no intro replay


@pytest.mark.parametrize('slot,colors', [(0, [1, 2, 4]), (1, [1, 4, 2])])
def test_once_only_human_readable_all_colors(leds, slot, colors):
  s = Status()
  leds.vgw_led_init(C.byref(s), 0)
  leds.vgw_led_set(C.byref(s), 2, slot, 0, 100)
  for t in range(4000):
    expected = colors[t // 1000] if t < 3000 and t % 1000 < 800 else 0
    assert leds.vgw_led_sample(C.byref(s), t + 100) == expected
    if t == 500:
      leds.vgw_led_set(C.byref(s), 1, slot, 0, t + 100)  # confirmation must not restart/skip intro
  assert s.intro_seen
  leds.vgw_led_sample(C.byref(s), 4100)
  assert not s.intro_active
  leds.vgw_led_set(C.byref(s), 1, slot, 0, 5000)
  assert not s.intro_active


@pytest.mark.parametrize('state,code', [(5, 3), (3, 0), (4, 0)])
def test_fault_recovery_update_preempt_intro(leds, state, code):
  s = Status()
  leds.vgw_led_init(C.byref(s), 0)
  leds.vgw_led_set(C.byref(s), 2, 0, 0, 0)
  leds.vgw_led_set(C.byref(s), state, 255, code, 100)
  assert not s.intro_active
  assert leds.vgw_led_sample(C.byref(s), 100) == (4 if state == 4 else 1)
  leds.vgw_led_set(C.byref(s), 1, 0, 0, 200)
  assert not s.intro_active
  assert leds.vgw_led_sample(C.byref(s), 200) == 4


def test_probe_full_lifetime_one_rgb_cycle_then_spaced_blue_only(leds):
  s=Status()
  leds.vgw_led_init(C.byref(s),0)
  leds.vgw_led_set(C.byref(s),11,255,0,0)
  for t in range(130000):
    if t<4000:
      expected=[1,2,4][t//1000] if t<3000 and t%1000<800 else 0
    else:
      expected=4 if t%4000<150 else 0
    leds.vgw_led_set(C.byref(s),11,255,0,t)
    assert leds.vgw_led_sample(C.byref(s),t)==expected
  assert s.intro_seen and not s.intro_active


@pytest.mark.parametrize('state,slot,code', [(99, 0, 0), (1, 2, 0), (5, 0, 0), (5, 0, 10), (2, 0, 1)])
def test_invalid_status_is_internal_fault(leds, state, slot, code):
  s = Status()
  leds.vgw_led_init(C.byref(s), 0)
  leds.vgw_led_set(C.byref(s), state, slot, code, 0)
  assert (s.state, s.error) == (5, 8)
  s.state = 200
  assert leds.vgw_led_sample(C.byref(s), 2100) == 1


def test_boot_recovery_update_and_absence(leds):
  s = Status()
  leds.vgw_led_init(None, 0)
  leds.vgw_led_set(None, 1, 0, 0, 0)
  assert leds.vgw_led_sample(None, 0) == 0
  for state, expected in [(0, [4, 0, 4, 0]), (3, [1, 1, 0, 0]), (4, [4, 2, 4, 2])]:
    leds.vgw_led_init(C.byref(s), 0)
    leds.vgw_led_set(C.byref(s), state, 255, 0, 0)
    assert [leds.vgw_led_sample(C.byref(s), t) for t in (0, 500, 1000, 1500)] == expected


@pytest.mark.parametrize('state', [6, 7, 8, 9, 10])
def test_update_phase_waveforms(leds, state):
  s = Status()
  start = 0xffffff00
  leds.vgw_led_init(C.byref(s), start)
  leds.vgw_led_set(C.byref(s), 2, 0, 0, start)
  leds.vgw_led_set(C.byref(s), state, 255, 0, start)
  assert not s.intro_active
  for elapsed in range(8000):
    phase = elapsed % 4000
    expected = {
      6: 4 if elapsed % 2000 < 150 else 0,
      7: 4 if elapsed % 1000 < 800 else 0,
      8: 5,
      9: 2 if phase // 300 < 4 and phase % 300 < 150 else 0,
      10: 3 if elapsed % 1000 < 500 else 0,
    }[state]
    now = (start + elapsed) & 0xffffffff
    leds.vgw_led_set(C.byref(s), state, 255, 0, now)
    assert s.since == start
    assert leds.vgw_led_sample(C.byref(s), now) == expected
