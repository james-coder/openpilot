import gc
import os
import subprocess
import sys

import pytest
from cereal import car, log
from openpilot.selfdrive.controls.radard import RadarD, get_lead


class RadarInputs:
  def __init__(self):
    self.seen = {'modelV2': True}
    self.logMonoTime = {'modelV2': 1, 'carState': 1}
    self.recv_frame = {'carState': 0}
    self.model = log.ModelDataV2.new_message()
    self.model.init('leadsV3', 2)
    self.model.velocity.x = [15.]
    for lead in self.model.leadsV3:
      lead.prob = .9
      lead.x = [25.]
      lead.y = [0.]
      lead.v = [15.]
      lead.a = [0.]
      lead.xStd = [1.]
      lead.yStd = [1.]
      lead.vStd = [1.]
    self.cs = car.CarState.new_message()
    self.cs.vEgo = 15.
    self.rr = car.RadarData.new_message()
    self.rr.init('points', 1)
    p = self.rr.points[0]
    p.trackId, p.dRel, p.yRel, p.vRel, p.measured = 1, 25., 0., 0., True

  def __getitem__(self, service):
    return self.model.as_reader() if service == 'modelV2' else self.cs.as_reader()

  def all_checks(self):
    return True


@pytest.mark.parametrize('mode', ['radar', 'vision', 'absent', 'unready', 'recovery'])
def test_lead_serialization_equivalence(mode):
  sm, rd = RadarInputs(), RadarD()
  if mode in ('vision', 'recovery'):
    sm.rr.points = []
    sm.rr.errors.radarDegraded = True
    sm.rr.errors.radarDegradedReasons = 8
  elif mode == 'absent':
    sm.rr.points = []
    for lead in sm.model.leadsV3:
      lead.prob = 0.
  elif mode == 'unready':
    sm.seen['modelV2'] = False
  rd.update(sm, sm.rr.as_reader())
  if mode == 'recovery':
    sm.rr = RadarInputs().rr
    sm.recv_frame['carState'] += 1
    rd.update(sm, sm.rr.as_reader())
  expected = log.RadarState.new_message()
  expected.mdMonoTime = sm.logMonoTime['modelV2']
  expected.carStateMonoTime = sm.logMonoTime['carState']
  expected.radarErrors = sm.rr.as_reader().errors
  for i, name in enumerate(('leadOne', 'leadTwo')):
    values = get_lead(rd.v_ego, rd.ready, rd.tracks, sm.model.leadsV3[i], 15., rd.lead_prob_filters[i].x, i == 0)
    setattr(expected, name, values)  # previous serialization path, for comparison
  # Equivalent decoded wire messages; arena layout/padding is not contractual.
  with log.RadarState.from_bytes(rd.radar_state.to_bytes()) as actual:
    assert actual.to_dict() == expected.to_dict()


def test_radar_memory_without_gc():
  # Separate process: neither pytest's GC nor allocator history can hide growth.
  result = subprocess.run([sys.executable, '-m', __name__], env=os.environ.copy(), capture_output=True, text=True)
  assert result.returncode == 0, result.stdout + result.stderr


if __name__ == '__main__':
  import psutil
  sm, rd = RadarInputs(), RadarD()
  for _ in range(1000):
    rd.update(sm, sm.rr.as_reader())
  gc.collect()
  gc.disable()
  process = psutil.Process()
  start = process.memory_info().rss
  for i in range(20000):
    sm.recv_frame['carState'] = i
    sm.rr.points[0].trackId = i % 20
    rd.update(sm, sm.rr.as_reader())
    rd.radar_state.to_bytes()
  growth = process.memory_info().rss - start
  unreachable = gc.collect()
  print(f'RSS growth: {growth} bytes; unreachable objects: {unreachable}')
  assert growth < 5 * 1024**2
  assert unreachable < 100
