import pytest

from openpilot.tools.volt_gateway.transfer_sim import run


@pytest.mark.parametrize('scenario', ['quiet', 'loaded', 'congestion', 'stale', 'lost_ack', 'lost_fc', 'corrupt', 'duplicate'])
def test_authenticated_transfer_recovers_without_duplicate_effects(scenario):
  result = run(scenario=scenario)
  assert result['complete'] and result['sha256_matches']
  assert result['counts']['chunk_effects'] == 32
  assert result['peak_rate_fps'] <= result['hard_fps']
  assert result['counts']['peer_0_frames'] + result['counts']['peer_1_frames'] == result['counts']['frames']
  assert result['counts']['frames'] <= result['hard_fps'] * result['elapsed_seconds'] + 2
  assert result['max_control_response_seconds'] < 15
  assert result['counts']['telemetry_suppressed'] > 0
  if scenario not in ('quiet', 'loaded'):
    assert result['counts']['retries'] >= 1
  if scenario == 'lost_ack':
    assert result['counts']['cached_responses'] >= 1


@pytest.mark.parametrize('scenario', ['expired', 'power_loss', 'reconnect', 'no_acks'])
def test_faults_fail_closed(scenario):
  result = run(scenario=scenario)
  assert not result['complete']
  assert result['failure']
  assert result['acknowledged_bytes'] < result['image_bytes']
  if scenario == 'no_acks':
    assert result['counts']['retries'] == 3
    assert result['counts']['chunk_effects'] == 1
  else:
    assert result['elapsed_seconds'] == 12


def test_slow_budget_waits_without_sending_timed_out_fragments():
  result = run(hard_fps=10, duration=10)
  assert not result['complete']
  assert result['counts'].get('frames', 0) == 0


@pytest.mark.parametrize('size', [1, 255, 256, 257, 1024])
def test_final_partial_chunk(size):
  result = run(size=size)
  assert result['complete']
  assert result['counts']['chunk_effects'] == (size + 255) // 256
