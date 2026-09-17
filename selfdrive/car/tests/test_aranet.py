import struct
import os
import pytest
import tempfile
from pathlib import Path

from openpilot.selfdrive.car.aranet import History, advertisements, decode, plot_samples


def test_background_policy(monkeypatch):
  from openpilot.selfdrive.car.aranet import background_priority
  calls = []
  monkeypatch.setattr(os, 'setpriority', lambda *args: calls.append(('nice', args)))
  monkeypatch.setattr(os, 'sched_setscheduler', lambda *args: calls.append(('scheduler', args)))
  monkeypatch.setattr('openpilot.selfdrive.car.aranet.subprocess.run', lambda *args, **kwargs: calls.append(('io', args, kwargs)))
  background_priority()
  assert calls[0][1] == (os.PRIO_PROCESS, 0, 19)
  assert calls[1][1] == (0, os.SCHED_IDLE, os.sched_param(0))
  assert calls[2][1][0] == ['/usr/bin/ionice', '-c', '3', '-p', str(os.getpid())]
  assert calls[2][2] == {'check': True, 'timeout': 5}


def test_background_policy_failure_stops_startup(monkeypatch):
  from openpilot.selfdrive.car.aranet import main
  def unavailable(*args):
    raise PermissionError('No scheduling permission')
  monkeypatch.setattr(os, 'setpriority', unavailable)
  with pytest.raises(PermissionError):
    main()


def payload(co2=1159, age=10):
  raw = bytearray(22)
  raw[0] = 0x20
  struct.pack_into('<HHHBBBHH', raw, 8, co2, 440, 10132, 45, 90, 1, 120, age)
  return bytes(raw)


def test_decode():
  assert decode(payload()) == (1159, 22, 1013.2, 45, 90, 120, 10)
  assert decode(payload(co2=0x8001)) is None
  assert decode(payload(age=500)) is None
  assert decode(b'bad') is None


def test_multiple_reports_and_truncation():
  ad = b'\x19\xff\x02\x07' + payload()
  report = b'\x00\x01' + bytes.fromhex('c2f2742924ce') + bytes([len(ad)]) + ad + b'\xc1'
  body = b'\x02\x02' + report * 2
  packet = b'\x04\x3e' + bytes([len(body)]) + body
  assert list(advertisements(packet)) == [('CE:24:29:74:F2:C2', payload(), -63)] * 2
  assert list(advertisements(packet[:-1])) == []


def test_bounded_history_restart_and_duplicates(monkeypatch):
  monkeypatch.setattr('openpilot.selfdrive.car.aranet.MAX_ROWS', 10)
  with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    history = History(root)
    for n in range(25):
      assert history.add(decode(payload()), -63, now=100000+n*120)
      assert not history.add(decode(payload(age=11)), -60, now=100001+n*120)
    assert history.db.execute('SELECT count(*) FROM readings').fetchone()[0] == 10
    assert history.db.execute('PRAGMA max_page_count').fetchone()[0] == 1024
    history.db.close()
    history = History(root)
    assert not history.add(decode(payload()), -63, now=100000+24*120)
    assert history.add(decode(payload()), -63, now=4000000)
    assert history.db.execute('SELECT count(*) FROM readings').fetchone()[0] == 1
    history.db.close()


def test_graph_bound_extrema_and_gaps():
  rows = [(i*120, 500 if i != 5000 else 3000, 20, 50, 120) for i in range(21600)]
  points = plot_samples(rows, 1)
  assert len(points) <= 1200
  assert max(row[1] for row in points) == 3000
  rows[3] = (10000, 500, 20, 50, 120)
  assert None in plot_samples(rows, 1)
