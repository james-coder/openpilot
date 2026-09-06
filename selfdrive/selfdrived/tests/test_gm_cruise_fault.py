from cereal import car
from openpilot.selfdrive.selfdrived.events import Events, EventName, ET


def test_gm_transient_banner_keeps_safety_events():
  cp = car.CarParams.new_message()
  cp.brand = 'gm'
  args = [cp, None, None, False, 0, None]
  events = Events()
  for _ in range(99):
    events.clear()
    events.add(EventName.accFaulted)
    assert not events.create_alerts([ET.PERMANENT], args)
    assert events.contains(ET.NO_ENTRY)
    assert events.contains(ET.IMMEDIATE_DISABLE)
    assert len(events.create_alerts([ET.NO_ENTRY, ET.IMMEDIATE_DISABLE], args)) == 2
  events.clear()
  events.add(EventName.accFaulted)
  assert len(events.create_alerts([ET.PERMANENT], args)) == 1
  events.clear()
  events.clear()  # clear counter once a frame passes without the fault
  events.add(EventName.accFaulted)
  assert not events.create_alerts([ET.PERMANENT], args)


def test_other_brands_banner_is_immediate():
  cp = car.CarParams.new_message()
  cp.brand = 'toyota'
  events = Events()
  events.add(EventName.accFaulted)
  assert len(events.create_alerts([ET.PERMANENT], [cp, None, None, False, 0, None])) == 1
