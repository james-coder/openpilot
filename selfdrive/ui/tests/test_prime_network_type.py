from cereal import log
from openpilot.selfdrive.ui.lib import prime_state
from openpilot.selfdrive.ui import ui_state as ui_module


def test_worker_accepts_real_capnp_network_enum_without_retry_thread_crash(mocker):
  state = prime_state.PrimeState.__new__(prime_state.PrimeState)
  state._running = True
  state._thread = None
  state._poller = mocker.Mock()
  state._fetch_prime_status = mocker.Mock()
  device_state = log.DeviceState.new_message(networkType='wifi')
  mocker.patch.object(prime_state, 'drop_realtime')
  mocker.patch.object(ui_module.ui_state, 'started', False)
  mocker.patch.object(ui_module.ui_state, 'sm', {'deviceState': device_state})
  mocker.patch.object(ui_module.device, '_awake', True)

  def end_after_one_poll(fetch, network):
    assert network == device_state.networkType.raw
    assert type(network) is int
    state._running = False

  state._poller.poll.side_effect = end_after_one_poll
  state._worker_thread()
  state._poller.poll.assert_called_once_with(state._fetch_prime_status, device_state.networkType.raw)
  state._fetch_prime_status.assert_not_called()
