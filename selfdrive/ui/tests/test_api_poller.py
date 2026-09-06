from openpilot.selfdrive.ui.lib.api_poller import ApiPoller


def test_backoff_offline_recovery_and_network_change(mocker):
  poller = ApiPoller('test', 5)
  attempts = []

  def fail():
    attempts.append(1)
    raise ConnectionError('offline')

  logger = mocker.patch('openpilot.selfdrive.ui.lib.api_poller.cloudlog')
  poller.poll(fail, 0, now=0)
  assert not attempts
  poller.poll(fail, 1, now=0)
  poller.poll(fail, 1, now=4)
  assert len(attempts) == 1
  poller.poll(fail, 1, now=5)
  assert len(attempts) == 2
  assert poller.next_attempt == 15
  assert logger.warning.call_count == 1
  for _ in range(20):
    poller.poll(fail, 1, now=poller.next_attempt)
  assert poller.delay == 300
  poller.poll(lambda: attempts.append(1), 2, now=10000)
  assert not poller.failed and poller.delay == 5
  assert logger.info.call_count == 1


def test_timeout_backoff_starts_after_request_finishes(mocker):
  poller = ApiPoller('test', 5)
  mocker.patch('openpilot.selfdrive.ui.lib.api_poller.time.monotonic', side_effect=[0, 10])
  poller.poll(lambda: None, 1)
  assert poller.next_attempt == 15
