import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from openpilot.system.review import eventd
from openpilot.system.review.detector import G
from openpilot.system.review.eventd import Recorder, boot_seconds, current_segment

ROUTE = '00000005--4c4e99b08b'


def seg(root: Path, n: int, locked=False):
  d = root / f'{ROUTE}--{n}'
  d.mkdir(parents=True)
  (d / 'rlog.zst').write_bytes(b'x')
  if locked:
    (d / 'rlog.zst.lock').write_bytes(b'')
  return d


def start_event(t=100.0):
  return dict(type='start', t=t, kinds=['hard_brake'], peaks={'decel_g': 0.7})


def test_current_segment_prefers_the_one_being_written_and_sorts_numerically(tmp_path):
  assert current_segment(tmp_path) is None
  seg(tmp_path, 2)
  seg(tmp_path, 10)
  (tmp_path / 'boot').mkdir()
  (tmp_path / 'not-a-segment').write_text('x')
  assert current_segment(tmp_path) == f'{ROUTE}--10'  # numeric, not lexicographic
  seg(tmp_path, 3, locked=True)
  assert current_segment(tmp_path) == f'{ROUTE}--3'
  assert current_segment(tmp_path / 'missing') is None


def test_start_event_protects_the_current_segment_and_writes_the_log_and_badge(tmp_path):
  seg(tmp_path, 4, locked=True)
  calls = []
  rec = Recorder(tmp_path, tmp_path / 'review' / 'events.jsonl', tmp_path / 'cfg' / 'last_event.json',
                 protect_fn=lambda root, name: calls.append(name) or True)
  now = boot_seconds()
  rec.handle([start_event()], now)
  assert calls == [f'{ROUTE}--4']
  lines = [json.loads(line) for line in (tmp_path / 'review' / 'events.jsonl').read_text().splitlines()]
  assert lines[0]['phase'] == 'start' and lines[0]['kinds'] == ['hard_brake'] and lines[0]['segment'] == f'{ROUTE}--4'
  badge = json.loads((tmp_path / 'cfg' / 'last_event.json').read_text())
  assert badge['kinds'] == ['hard_brake'] and badge['boot_id'] == rec.boot and abs(badge['written_boot_s'] - now) < 5
  assert (tmp_path / 'review').stat().st_mode & 0o077 == 0  # private


def test_closing_summary_is_logged_but_does_not_re_show_the_banner(tmp_path):
  seg(tmp_path, 4, locked=True)
  rec = Recorder(tmp_path, tmp_path / 'e.jsonl', tmp_path / 'l.json', protect_fn=lambda r, n: True)
  rec.handle([start_event()], boot_seconds())
  first = json.loads((tmp_path / 'l.json').read_text())
  rec.handle([dict(type='end', t=104.0, t_start=100.0, kinds=['hard_brake'], peaks={'decel_g': 0.8})], boot_seconds() + 4)
  assert json.loads((tmp_path / 'l.json').read_text()) == first
  phases = [json.loads(line)['phase'] for line in (tmp_path / 'e.jsonl').read_text().splitlines()]
  assert phases == ['start', 'end']


def test_protection_follows_into_the_next_segment_only_while_the_episode_window_is_open(tmp_path):
  seg(tmp_path, 4, locked=True)
  calls = []
  rec = Recorder(tmp_path, tmp_path / 'e.jsonl', tmp_path / 'l.json', protect_fn=lambda root, name: calls.append(name) or True)
  now = boot_seconds()
  rec.handle([start_event()], now)
  seg(tmp_path, 5, locked=True)
  rec.ensure_protected(now + 10)
  assert calls == [f'{ROUTE}--4', f'{ROUTE}--5']
  seg(tmp_path, 6, locked=True)
  rec.ensure_protected(now + eventd.PROTECT_AHEAD_S + 5)  # window over
  assert calls[-1] == f'{ROUTE}--5'


def test_permission_denied_protecting_still_logs_and_never_raises(tmp_path):
  seg(tmp_path, 4, locked=True)

  def denied(root, name):
    raise PermissionError('injected')
  rec = Recorder(tmp_path, tmp_path / 'e.jsonl', tmp_path / 'l.json', protect_fn=denied)
  rec.handle([start_event()], boot_seconds())
  assert (tmp_path / 'e.jsonl').exists() and (tmp_path / 'l.json').exists()


def test_unusable_review_directories_still_protect_and_never_raise(tmp_path):
  seg(tmp_path, 4, locked=True)
  blocker = tmp_path / 'blocker'
  blocker.write_text('a file where a directory is needed')
  calls = []
  rec = Recorder(tmp_path, blocker / 'events.jsonl', blocker / 'last_event.json', protect_fn=lambda r, n: calls.append(n) or True)
  rec.handle([start_event(), dict(type='end', t=105.0, t_start=100.0, kinds=['hard_brake'], peaks={})], boot_seconds())
  assert calls == [f'{ROUTE}--4']


def test_real_xattr_is_set_on_the_segment_directory(tmp_path):
  d = seg(tmp_path, 4, locked=True)
  import xattr
  try:
    xattr.setxattr(str(d), 'user.probe', b'1')
  except OSError:
    pytest.skip('filesystem without user xattrs')
  assert eventd.protect(tmp_path, d.name)
  assert xattr.getxattr(str(d), 'user.preserve') == b'1'
  assert not eventd.protect(tmp_path, 'does-not-exist')  # failure is reported, not raised


# ---- the live loop, with fake sockets -------------------------------------------------------------------------------
def synthetic_hard_stop(t0):
  """~12 s of 100 Hz carState and 104 Hz accelerometer containing a 0.8 g stop from 14 m/s."""
  up = np.array([0.0, 0.6, 0.8]) * G
  fwd = np.array([1.0, 0.0, 0.0]) * G
  cs, acc = [], []
  for i in range(1200):
    t = i / 100
    v = 14.0 if t < 5 else max(14.0 - 0.8 * G * (t - 5), 0.0)
    cs.append(SimpleNamespace(logMonoTime=int((t0 + t) * 1e9), carState=SimpleNamespace(vEgo=v, brakePressed=t >= 5)))
  for j in range(1248):
    t = j / 104
    a = up - (0.8 * fwd if 5 <= t < 5 + 14 / (0.8 * G) else 0)
    acc.append(SimpleNamespace(logMonoTime=int((t0 + t) * 1e9), accelerometer=SimpleNamespace(acceleration=SimpleNamespace(v=list(a)))))
  return cs, acc


@pytest.fixture
def live(tmp_path, monkeypatch):
  seg(tmp_path / 'logs', 7, locked=True)
  monkeypatch.setattr(eventd, 'EVENT_LOG', tmp_path / 'review' / 'events.jsonl')
  monkeypatch.setattr(eventd, 'LAST_EVENT', tmp_path / 'cfg' / 'last_event.json')
  protected = []
  monkeypatch.setattr(eventd, 'protect', lambda root, name: protected.append(name) or True)
  from openpilot.system.hardware.hw import Paths
  monkeypatch.setattr(Paths, 'log_root', staticmethod(lambda: str(tmp_path / 'logs')))
  return SimpleNamespace(path=tmp_path, protected=protected)


def run_loop(monkeypatch, cs, acc, drain_errors=0):
  queue = {'carState': list(cs), 'accelerometer': list(acc)}
  state = {'errors': drain_errors}
  import cereal.messaging as messaging
  monkeypatch.setattr(messaging, 'sub_sock', lambda name, **kw: name)

  def drain(sock, wait_for_one=False):
    if state['errors'] > 0:
      state['errors'] -= 1
      raise RuntimeError('injected socket failure')
    out, queue[sock] = queue[sock][:50], queue[sock][50:]
    return out
  monkeypatch.setattr(messaging, 'drain_sock', drain)
  stop = threading.Event()
  th = threading.Thread(target=eventd.run, args=(stop,), daemon=True)
  th.start()
  return stop, th, queue


def wait_for(cond, timeout=10.0):
  end = time.monotonic() + timeout
  while time.monotonic() < end:
    if cond():
      return True
    time.sleep(0.02)
  return False


def test_live_loop_protects_and_records_a_hard_stop_then_stops_promptly(live, monkeypatch):
  cs, acc = synthetic_hard_stop(boot_seconds() - 15)
  stop, th, _ = run_loop(monkeypatch, cs, acc)
  assert wait_for(lambda: live.protected), 'segment was never protected'
  assert live.protected[0] == f'{ROUTE}--7'
  assert wait_for(lambda: (live.path / 'cfg' / 'last_event.json').exists())
  t = time.monotonic()
  stop.set()
  th.join(timeout=2)
  assert not th.is_alive() and time.monotonic() - t < 1.0
  phases = [json.loads(line)['phase'] for line in (live.path / 'review' / 'events.jsonl').read_text().splitlines()]
  assert phases[0] == 'start'


def test_live_loop_survives_socket_errors(live, monkeypatch):
  cs, acc = synthetic_hard_stop(boot_seconds() - 15)
  stop, th, _ = run_loop(monkeypatch, cs, acc, drain_errors=5)
  try:
    assert wait_for(lambda: live.protected), 'loop did not recover from socket errors'
    assert th.is_alive()
  finally:
    stop.set()
    th.join(timeout=3)


def test_startup_failure_is_swallowed_and_the_disable_flag_idles(live, monkeypatch):
  def boom(stop):
    raise PermissionError('injected')
  monkeypatch.setattr(eventd, 'run', boom)
  stopped = threading.Event()
  stopped.set()
  eventd.main(stopped)  # returns normally; the manager sees a clean exit, not a crash loop

  flag = live.path / 'eventd.off'
  flag.write_text('')
  monkeypatch.setattr(eventd, 'DISABLE_FLAG', flag)
  monkeypatch.setattr(eventd, 'run', lambda stop: pytest.fail('ran despite the disable flag'))
  stop = threading.Event()
  th = threading.Thread(target=eventd.main, args=(stop,), daemon=True)
  th.start()
  time.sleep(0.2)
  assert th.is_alive()
  stop.set()
  th.join(timeout=2)
  assert not th.is_alive()


def test_sigint_stops_the_real_process_quickly():
  repo = Path(__file__).resolve().parents[3]
  env = dict(os.environ, PYTHONPATH=str(repo))
  p = subprocess.Popen([sys.executable, '-m', 'openpilot.system.review.eventd'], cwd=repo, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
  try:
    time.sleep(3)
    assert p.poll() is None, 'eventd exited on its own'
    t = time.monotonic()
    p.send_signal(signal.SIGINT)
    p.wait(timeout=5)
    assert time.monotonic() - t < 2.0
  finally:
    if p.poll() is None:
      p.kill()
