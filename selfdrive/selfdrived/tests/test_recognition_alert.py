import pytest

from openpilot.common.params import Params, ParamKeyFlag
from openpilot.selfdrive.selfdrived.events import Events, EventName, ET


@pytest.mark.parametrize('count', [0, 1, 2, 3, 8])
@pytest.mark.parametrize('event', [EventName.carUnrecognized, EventName.startupNoCar])
def test_recognition_attempt_message(count, event):
  events = Events(car_recognition_attempts=count)
  events.add(event, static=True)
  alert = events.create_alerts([ET.PERMANENT])[0]
  if 1 <= count <= 3:
    assert f"{count} Attempt" in alert.alert_text_1 + alert.alert_text_2
    assert "Failed" in alert.alert_text_1 + alert.alert_text_2
  else:
    assert 'Attempt' not in alert.alert_text_1 + alert.alert_text_2
  # Customizing one route's alert must not mutate the shared event definitions.
  fresh = Events()
  fresh.add(event)
  assert 'Attempt' not in fresh.create_alerts([ET.PERMANENT])[0].alert_text_2


@pytest.mark.parametrize('flag', [ParamKeyFlag.CLEAR_ON_MANAGER_START, ParamKeyFlag.CLEAR_ON_ONROAD_TRANSITION])
def test_attempt_count_is_typed_and_cleared(tmp_path, flag):
  params = Params(str(tmp_path))
  params.put('CarRecognitionAttempts', 3, block=True)
  assert params.get('CarRecognitionAttempts') == 3
  params.clear_all(flag)
  assert params.get('CarRecognitionAttempts') is None
