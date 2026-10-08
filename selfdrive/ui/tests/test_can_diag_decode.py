"""Diagnostic-traffic labels and decoding for the CAN panel. Pure data layer: no pyray, no CAN socket, no vehicle.

Real frames come from opendbc_repo/opendbc/car/tests/gm_diag_sweep_fixture.json (a read-only recording of the boot-time
factory-tester sweep on the owner's Volt). These tests do not validate the on-device panel or any live vehicle behavior.
"""
import json
import random
import time
import tracemalloc
from pathlib import Path

import pytest
from opendbc.car.gm.values import CAR
from openpilot.selfdrive.ui.layouts.settings import can_diag_decode as dd
from openpilot.selfdrive.ui.layouts.settings.can_diag_decode import label, label_text, summarize
from openpilot.selfdrive.ui.layouts.settings.can_diagnostics_data import MAX_RAW_MESSAGES_PER_BUS, CanSnapshot, matches_query
from openpilot.selfdrive.ui.layouts.settings.can_inspection import InspectionSession

FIXTURE = Path(__file__).resolve().parents[3] / 'opendbc_repo/opendbc/car/tests/gm_diag_sweep_fixture.json'
ROUTES = ('fail_e1', 'fail_e5', 'good_e3')
REQUESTS = {0x241: 'BCM', 0x242: 'PSCM', 0x248: 'HVAC A26', 0x251: 'HVAC K33', 0x252: 'HMI', 0x254: 'Amplifier',
            0x7E0: 'ECM', 0x7E1: 'HPCM', 0x7E4: 'HPCM2', 0x7E5: 'EBCM', 0x7E7: 'BECM'}
# Derived replies: 0x5xx are the unsegmented ($A9 DTC report) IDs, 0x6xx the segmented ($22) IDs.
REPLIES = {0x541: 'BCM', 0x542: 'PSCM', 0x552: 'HMI', 0x5E8: 'ECM', 0x5E9: 'HPCM', 0x5EC: 'HPCM2', 0x5ED: 'EBCM', 0x5EF: 'BECM',
           0x641: 'BCM', 0x642: 'PSCM', 0x648: 'HVAC A26', 0x652: 'HMI', 0x7E9: 'HPCM'}
CONFIRMED_REPLIES = {0x651: 'HVAC K33'}  # answered on the car in the owner's HVAC testing
OBD_REPLY_MODULES = {0x7E8: 'ECM', 0x7E9: 'HPCM', 0x7EA: None, 0x7EB: None, 0x7EC: 'HPCM2', 0x7ED: 'EBCM', 0x7EE: None, 0x7EF: 'BECM'}
TABLE_IDS = {*REQUESTS, *REPLIES, *CONFIRMED_REPLIES, 0x101, 0x7DF, *OBD_REPLY_MODULES}
UNLISTED_IDS = (0x240, 0x243, 0x249, 0x24B, 0x253, 0x255, 0x540, 0x543, 0x553, 0x5EA, 0x5EB, 0x5EE, 0x640, 0x643, 0x649, 0x653,
                0x7E2, 0x7E3, 0x7E6, 0x7DE, 0x7F0, 0x100, 0x102, 0x1FFFFFFF, -1)


def frame(text):
  return bytes.fromhex(text.replace(' ', ''))


def load_fixture():
  if not FIXTURE.is_file():
    pytest.skip('opendbc submodule fixture not checked out')
  document = json.loads(FIXTURE.read_text())
  return [(ms, int(address, 16), bytes.fromhex(data)) for route in ROUTES for ms, address, data in document[route]['diag_frames']]


# --- Table ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('address,module', REQUESTS.items())
def test_request_ids_name_the_module_and_cite_the_gds2_analysis(address, module):
  ident = label(address)
  assert (ident.role, ident.module, ident.basis) == ('request', module, dd.GDS2)
  assert module in ident.text and 'GDS2' in ident.text and ident.who == f'{module} <- tester'


@pytest.mark.parametrize('address,module', REPLIES.items())
def test_reply_ids_are_marked_derived_everywhere(address, module):
  ident = label(address)
  assert (ident.role, ident.module, ident.basis) == ('reply', module, dd.DERIVED)
  assert 'DERIVED' in ident.text and 'not confirmed' in ident.text and '(derived)' in ident.who
  assert 'derived' in summarize(address, frame('03 7F A9 78 00 00 00 00'))


@pytest.mark.parametrize('address,module', CONFIRMED_REPLIES.items())
def test_the_one_reply_confirmed_on_the_car_drops_the_derived_wording(address, module):
  ident = label(address)
  assert (ident.role, ident.module, ident.basis) == ('reply', module, dd.CONFIRMED)
  assert ident.who == f'{module} -> tester' and 'DERIVED' not in ident.text and 'confirmed on the car' in ident.text
  # The owner's live read-only answer to a diagnostic-address query (03 5A B0 99).
  assert summarize(address, frame('03 5A B0 99 AA AA AA AA')) == 'HVAC K33 -> tester: positive 0x1A read data by 1-byte ID (5A B0 99)'


def test_replies_pair_with_their_request_and_use_the_right_id_family():
  # Unsegmented 0x5xx IDs carry the $A9 DTC reports; segmented 0x6xx IDs carry the $22 replies. Both are inferred.
  for address in (0x541, 0x542, 0x552, 0x5E8, 0x5E9, 0x5EC, 0x5ED, 0x5EF):
    assert label(address).report
    assert 'DTC report' in summarize(address, frame('81 00 00 00 FF 00 00 00'))
  for address in (0x641, 0x642, 0x648, 0x651, 0x652, 0x7E9, 0x7EF):
    assert not label(address).report
    assert 'DTC report' not in summarize(address, frame('81 00 00 00 FF 00 00 00'))
  assert summarize(0x641, frame('05 62 90 FA 0C D5 80 44')) == 'BCM (derived) -> tester: positive 0x22 read data by ID (62 90 FA 0C D5)'
  assert summarize(0x648, frame('03 7F 22 31')).startswith('HVAC A26 (derived) -> tester: negative reply to read data by ID: request out of range')
  assert summarize(0x7E7, frame('02 1A A0')) == 'BECM <- tester: read data by 1-byte ID (1A A0)'
  assert summarize(0x7EF, frame('03 5A A0 00')) == 'BECM (derived) -> tester: positive 0x1A read data by 1-byte ID (5A A0 00)'


def test_obd_response_range_follows_request_plus_eight_and_never_guesses_a_module():
  for address, module in OBD_REPLY_MODULES.items():
    ident = label(address)
    assert ident.role == 'reply' and ident.module == module
    assert ident.basis == (dd.DERIVED if module else dd.STANDARD)
  assert label_text(0x7EA) == 'OBD-II response ID (request 0x7E2 + 8; module not in table)'
  assert summarize(0x7EA, frame('03 5A A0 00')).startswith('unknown ECU reply: positive 0x1A')


def test_broadcast_and_functional_ids():
  assert label(0x101).role == 'broadcast' and 'tester-present/broadcast request' in label_text(0x101)
  assert label(0x7DF).role == 'functional' and 'OBD-II functional request' in label_text(0x7DF)


def test_pscm_id_is_the_radar_on_the_object_detection_bus_only():
  assert label(0x242, 0).module == label(0x242, 2).module == label(0x242).module == 'PSCM'
  assert label(0x242, dd.OBJECT_DETECTION_BUS).module == 'Radar'
  for reply in (0x542, 0x642):  # unsegmented and segmented replies are both ambiguous by bus
    assert label(reply, dd.OBJECT_DETECTION_BUS).module == 'Radar' and label(reply, 0).module == label(reply, 2).module == 'PSCM'
    assert label(reply, dd.OBJECT_DETECTION_BUS).basis == dd.DERIVED
  assert label(0x542, dd.OBJECT_DETECTION_BUS).report and not label(0x642, dd.OBJECT_DETECTION_BUS).report
  assert summarize(0x642, frame('03 7F 22 31'), 1).startswith('Radar (derived) -> tester: negative reply')
  assert label(0x648, dd.OBJECT_DETECTION_BUS).module == 'HVAC A26'
  assert label(0x7E0, dd.OBJECT_DETECTION_BUS).module == 'ECM'  # bus only changes the two shared IDs
  assert 'radar' in label_text(0x242) and 'keyless entry' in label_text(0x242)
  assert 'Long Range Radar Sensor Module' in label_text(0x242, 1) and 'Long Range Radar Sensor Module' in label_text(0x642, 1)
  assert summarize(0x242, frame('03 A9 81 9A'), 1) == 'Radar <- tester: read DTCs by status (A9 81 9A)'


def test_exactly_the_documented_ids_are_labeled_and_everything_else_stays_unlabeled():
  assert {a for a in range(0x20000) if label(a)} == TABLE_IDS
  assert len(dd._IDS) == len(TABLE_IDS)
  for address in UNLISTED_IDS:
    assert label(address) is None and label_text(address) == '' and summarize(address, frame('03 A9 81 9A')) == ''


# --- Frame classes ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('address,module', REQUESTS.items())
@pytest.mark.parametrize('padding', ['00 00 00 00', 'AA AA AA AA', '55 55 55 55'])
def test_incident_dtc_status_request_on_every_requested_module(address, module, padding):
  assert summarize(address, frame('03 A9 81 9A ' + padding)) == f'{module} <- tester: read DTCs by status (A9 81 9A)'


def test_the_documented_example_strings():
  assert summarize(0x252, frame('03 A9 81 9A 00 00 00 00')) == 'HMI <- tester: read DTCs by status (A9 81 9A)'
  assert summarize(0x7EA, frame('03 62 43 2F')) == 'unknown ECU reply: positive 0x22 read data by ID (62 43 2F)'
  assert summarize(0x7E4, frame('03 22 43 56 00 00 00 00')) == 'HPCM2 <- tester: read data by ID (22 43 56)'


def test_dtc_request_variants():
  assert summarize(0x7E0, frame('03 A9 81')) == 'ECM <- tester: malformed single frame (length 3, 2 data bytes present)'
  assert summarize(0x7E0, frame('02 A9 81')) == 'ECM <- tester: read DTCs by status, mask missing (A9 81)'
  assert summarize(0x7E0, frame('03 A9 80 9A')) == 'ECM <- tester: read diag info (A9 80 9A)'
  assert summarize(0x101, frame('FE 03 A9 81 12 00 00 00')) == 'all modules <- tester: read DTCs by status (A9 81 12)'


@pytest.mark.parametrize('sid,name', [(0x10, 'initiate diagnostic operation'), (0x19, 'read DTC information'),
                                      (0x1A, 'read data by 1-byte ID'), (0x20, 'return to normal mode'),
                                      (0x22, 'read data by ID'), (0x27, 'security access'),
                                      (0x2C, 'dynamically define message'), (0x3B, 'write data by ID'),
                                      (0x3E, 'tester present'), (0xA9, 'read diag info'),
                                      (0xAA, 'read data by packet ID'), (0xAE, 'device control'),
                                      (0x01, 'OBD-II current data'), (0x09, 'OBD-II vehicle information'),
                                      (0xFD, 'service 0xFD'), (0x77, 'service 0x77')])
def test_service_names_for_requests_and_positive_replies(sid, name):
  assert summarize(0x7E0, bytes([2, sid, 0x11])) == f'ECM <- tester: {name} ({sid:02X} 11)'
  if sid + 0x40 <= 0xFF:  # a positive reply byte cannot represent request services above 0xBF
    reply = summarize(0x7E8, bytes([2, sid+0x40, 0x11]))
    assert reply == f'ECM (derived) -> tester: positive 0x{sid:02X}{"" if name.startswith("service") else " "+name} ({sid+0x40:02X} 11)'


def test_positive_replies_from_the_recording():
  assert summarize(0x7EC, frame('04 62 43 2F CD AA AA AA')) == 'HPCM2 (derived) -> tester: positive 0x22 read data by ID (62 43 2F CD)'
  assert summarize(0x7E9, frame('04 62 24 97 00 AA AA AA')) == 'HPCM (derived) -> tester: positive 0x22 read data by ID (62 24 97 00)'
  assert summarize(0x7EC, frame('03 5A A0 00 AA AA AA AA')) == 'HPCM2 (derived) -> tester: positive 0x1A read data by 1-byte ID (5A A0 00)'


@pytest.mark.parametrize('nrc,text', [(0x11, 'service not supported'), (0x12, 'sub-function not supported'), (0x22, 'conditions not correct'),
                                      (0x31, 'request out of range'), (0x33, 'security access denied'), (0x78, 'response pending'),
                                      (0xEE, 'NRC 0xEE')])
def test_negative_replies_name_the_service_and_reason(nrc, text):
  assert summarize(0x652, bytes([3, 0x7F, 0xA9, nrc])) == f'HMI (derived) -> tester: negative reply to read diag info: {text} (7F A9 {nrc:02X})'


def test_incident_negative_reply_and_short_negative_reply():
  assert summarize(0x652, frame('03 7F A9 78 AA AA AA AA')) == \
    'HMI (derived) -> tester: negative reply to read diag info: response pending (7F A9 78)'
  assert summarize(0x652, frame('02 7F A9')) == 'HMI (derived) -> tester: malformed negative reply (7F A9)'
  assert summarize(0x652, frame('03 7F 99 11')) == 'HMI (derived) -> tester: negative reply to service 0x99: service not supported (7F 99 11)'


def test_reply_with_a_request_style_service_is_called_out_not_misread():
  assert summarize(0x7EC, frame('03 22 43 56')) == 'HPCM2 (derived) -> tester: unexpected service 0x22 in a reply (22 43 56)'


def test_multi_frame_first_consecutive_and_flow_control():
  assert summarize(0x7EC, frame('10 0A 62 41 C4 19 43 01')) == \
    'HPCM2 (derived) -> tester: multi-frame start, 10 bytes: positive 0x22 read data by ID (62 41 C4 19 43 01)'
  assert summarize(0x7EC, frame('21 02 03 04 AA AA AA AA')) == 'HPCM2 (derived) -> tester: multi-frame continuation #1'
  assert summarize(0x7EC, frame('2F 02 03')).endswith('continuation #15')
  assert summarize(0x7E4, frame('30 00 00 AA AA AA AA AA')) == 'HPCM2 <- tester: flow control: clear to send, block size 0, STmin 0 ms'
  assert summarize(0x7E4, frame('31 04 F5')).endswith('flow control: wait, block size 4, STmin 500 us')
  assert summarize(0x7E4, frame('32 00 7F')).endswith('flow control: overflow, block size 0, STmin 127 ms')
  assert summarize(0x7E4, frame('30 00 80')).endswith('STmin reserved')
  assert summarize(0x7E4, frame('33 00 00')).startswith('HPCM2 <- tester: malformed flow control')


def test_gmlan_dtc_report_frames_from_the_incident():
  assert summarize(0x541, frame('81 47 50 03 19 00 00 00')) == 'BCM (derived) -> tester: DTC report: C0750, type 03, status 19'
  assert summarize(0x5E8, frame('81 04 01 00 39 00 00 00')) == 'ECM (derived) -> tester: DTC report: P0401, type 00, status 39'
  assert summarize(0x5ED, frame('81 C1 31 00 19 AA AA AA')) == 'EBCM (derived) -> tester: DTC report: U0131, type 00, status 19'
  assert summarize(0x542, frame('81 00 00 00 FF 00 00 00')) == 'PSCM (derived) -> tester: DTC report: end of report, status mask FF'
  assert summarize(0x5E9, frame('81 00 00 00 FF 00 00 00')).startswith('HPCM (derived) -> tester: DTC report: end of report')
  assert summarize(0x541, frame('81 00 00 03 19')).endswith('record without a code (81 00 00 03 19)')
  assert summarize(0x541, frame('81 47 50')) == 'BCM (derived) -> tester: malformed DTC report frame (81 47 50)'
  # Report frames are an 0x5xx convention: elsewhere 0x81 is not an ISO-TP frame and is not read as a DTC.
  assert summarize(0x652, frame('81 47 50 03 19')) == 'HMI (derived) -> tester: not an ISO-TP frame (first byte 81)'
  assert summarize(0x241, frame('81 47 50 03 19')) == 'BCM <- tester: not an ISO-TP frame (first byte 81)'
  assert summarize(0x641, frame('81 47 50 03 19')) == 'BCM (derived) -> tester: not an ISO-TP frame (first byte 81)'
  assert summarize(0x552, frame('81 00 00 00 FF')) == 'HMI (derived) -> tester: DTC report: end of report, status mask FF'
  assert summarize(0x5EC, frame('81 00 00 00 FF')).startswith('HPCM2 (derived) -> tester: DTC report')


def test_broadcast_target_byte_then_iso_tp():
  assert summarize(0x101, frame('FE 02 1A B0 AA AA AA AA')) == 'all modules <- tester: read data by 1-byte ID (1A B0)'
  assert summarize(0x101, frame('FE 01 3E 00 00 00 00 00')) == 'all modules <- tester: tester present (3E)'
  assert summarize(0x101, frame('99 02 1A B0')) == 'diagnostic address 0x99 <- tester: read data by 1-byte ID (1A B0)'
  assert summarize(0x101, frame('FE')) == 'tester broadcast: short frame (FE)'


def test_obd_functional_request():
  assert summarize(0x7DF, frame('02 01 05 00 00 00 00 00')) == 'all OBD-II ECUs <- tester: OBD-II current data (01 05)'
  assert summarize(0x7DF, frame('01 03')) == 'all OBD-II ECUs <- tester: OBD-II stored DTCs (03)'


# --- Malformed input never raises ---------------------------------------------------------------------------------

@pytest.mark.parametrize('data', [b'', b'\x00', b'\x01', b'\x03', b'\x03\xa9', b'\x08\xa9\x81\x9a', b'\x0f\xa9\x81\x9a\x00\x00\x00\x00',
                                  b'\x00\xa9\x81\x9a', b'\x10', b'\x10\x0a', b'\x10\x04\x62\x41', b'\x1f\xff\x62', b'\x20', b'\x30', b'\x30\x00',
                                  b'\x40\x00', b'\xff' * 8, b'\x7f', b'\x7f\xa9', b'\x81', b'\x81\x00', b'\x00' * 8])
@pytest.mark.parametrize('address', sorted(TABLE_IDS))
def test_short_and_malformed_frames_get_a_description_not_an_exception(address, data):
  text = summarize(address, data)
  assert text and text != f'{label(address).who}: undecodable frame'


@pytest.mark.parametrize('size', [9, 12, 20, 64, 65, 4096])
def test_oversized_payloads_are_left_undecoded(size):
  assert summarize(0x252, bytes(size)) == f'HMI <- tester: oversized payload ({size} bytes), not decoded'
  assert summarize(0x101, bytes(size)).endswith('not decoded')


def test_unlabeled_ids_ignore_any_payload_including_garbage():
  for address in (0x7FF, 0x24B, 0x10000):
    for data in (b'', b'\xff' * 70, frame('03 A9 81 9A')):
      assert summarize(address, data) == ''


@pytest.mark.parametrize('kind', [bytes, bytearray, memoryview])
def test_accepts_any_bytes_like_payload(kind):
  assert summarize(0x252, kind(frame('03 A9 81 9A 00 00 00 00'))) == 'HMI <- tester: read DTCs by status (A9 81 9A)'


def test_exhaustive_first_two_bytes_on_every_role():
  for address in (0x252, 0x7EC, 0x541, 0x101, 0x7DF):
    for first in range(256):
      for second in range(0, 256, 3):
        text = summarize(address, bytes([first, second, 0x22, 0, 0, 0, 0, 0]))
        assert text and 'undecodable' not in text


def test_random_payloads_of_every_length_on_every_id_never_raise():
  rng = random.Random(13)
  for _ in range(20000):
    address = rng.choice(sorted(TABLE_IDS))
    text = summarize(address, bytes(rng.getrandbits(8) for _ in range(rng.randrange(0, 72))), rng.choice([None, 0, 1, 2, 9]))
    assert text and 'undecodable' not in text


# --- Real recorded frames -----------------------------------------------------------------------------------------

def test_every_recorded_sweep_id_is_labeled_and_decodes_cleanly():
  frames = load_fixture()
  assert len(frames) > 150  # the recording holds 196 frames across three boots
  for _, address, data in frames:
    text = summarize(address, data, 0)
    assert address in TABLE_IDS, f'0x{address:X} is in the recording but not in the table'
    assert text and not any(word in text for word in ('malformed', 'not an ISO-TP', 'undecodable', 'oversized')), (hex(address), data.hex(), text)
  assert {address for _, address, _ in frames} == {0x241, 0x242, 0x252, 0x254, 0x541, 0x542, 0x552, 0x5E8, 0x5E9, 0x5EC, 0x5ED, 0x641, 0x652,
                                                    0x7E0, 0x7E1, 0x7E4, 0x7E5, 0x7E7, 0x7E9, 0x7EC, 0x7EF}


def test_recorded_dtc_status_requests_match_the_incident_pattern():
  sweep = [(address, data) for _, address, data in load_fixture() if data[:4] == frame('03 A9 81 9A')]
  assert {address for address, _ in sweep} >= {0x241, 0x242, 0x252, 0x254, 0x7E0, 0x7E1, 0x7E4, 0x7E5}
  for address, data in sweep:
    assert summarize(address, data, 0) == f'{REQUESTS[address]} <- tester: read DTCs by status (A9 81 9A)'


def test_recorded_module_replies_and_polling():
  by_id = {}
  for _, address, data in load_fixture():
    by_id.setdefault(address, []).append(summarize(address, data, 0))
  assert all(text.startswith('BCM (derived) -> tester: DTC report: ') for text in by_id[0x541])
  assert any('end of report' in text for text in by_id[0x541]) and any('C0750' in text for text in by_id[0x541])
  assert any('P0401' in text for text in by_id[0x5E8])
  assert all(text.startswith('PSCM (derived) -> tester: DTC report: ') for text in by_id[0x542])
  assert all(text.startswith('EBCM (derived) -> tester: DTC report: ') for text in by_id[0x5ED])
  assert by_id[0x652] == ['HMI (derived) -> tester: negative reply to read diag info: response pending (7F A9 78)'] * 2
  polling = [t for t in by_id[0x7E4] if '(22 43' in t]
  assert len(polling) > 20 and all(t.startswith('HPCM2 <- tester: read data by ID (22 43 ') for t in polling)
  assert all(t.startswith('BECM (derived) -> tester: positive 0x1A') for t in by_id[0x7EF])
  assert all(t.startswith('BECM <- tester: read data by 1-byte ID (1A ') for t in by_id[0x7E7])
  assert all(t.startswith('BCM (derived) -> tester: positive 0x22 read data by ID (62 ') for t in by_id[0x641])
  assert all(t.startswith('BCM <- tester:') for t in by_id[0x241])
  assert by_id[0x552] == ['HMI (derived) -> tester: DTC report: end of report, status mask FF'] * 2
  assert by_id[0x5EC] == ['HPCM2 (derived) -> tester: DTC report: end of report, status mask FF'] * 2


# --- Panel data layer ---------------------------------------------------------------------------------------------

@pytest.fixture
def snapshot():
  return CanSnapshot(CAR.CHEVROLET_VOLT)


def test_no_dbc_on_the_panel_buses_defines_a_diagnostic_id(snapshot):
  for bus, addresses in snapshot._known_addrs.items():
    assert not addresses & TABLE_IDS, (bus, sorted(map(hex, addresses & TABLE_IDS)))


def test_raw_row_and_details_carry_label_and_summary(snapshot):
  snapshot.ingest([(1, [(0x252, frame('03 A9 81 9A 00 00 00 00'), 0)])])
  row = snapshot.rows[(0, 0x252, None)]
  assert row.diagnostic == 'HMI <- tester: read DTCs by status (A9 81 9A)'
  assert row.text == '03 A9 81 9A 00 00 00 00' and row.message == '' and row.signal is None
  assert 'No message definition in this bus DBC' in row.details() and row.diagnostic in row.details()
  assert snapshot.rows_for(0) == [row]


def test_bus_matters_only_for_the_shared_pscm_radar_id(snapshot):
  snapshot.ingest([(1, [(0x242, frame('03 A9 81 9A'), 0), (0x242, frame('03 A9 81 9A'), 1)])])
  assert snapshot.rows[(0, 0x242, None)].diagnostic.startswith('PSCM <- tester')
  assert snapshot.rows[(1, 0x242, None)].diagnostic.startswith('Radar <- tester')


def test_other_raw_rows_and_dbc_rows_are_unchanged(snapshot):
  snapshot.ingest([(1, [(0x7FF, b'\x01\x02\x03', 0), (0x24B, frame('05 62 90 FA 0C D5 80 44'), 0), (309, b'\x02', 0)])])
  assert snapshot.rows[(0, 0x7FF, None)].diagnostic == snapshot.rows[(0, 0x24B, None)].diagnostic == ''
  wrong_size = snapshot.rows[(0, 309, None)]
  assert wrong_size.diagnostic == '' and 'expected' in wrong_size.message and wrong_size.details().endswith('raw bytes')


def test_summary_follows_the_payload_and_is_not_stale_when_it_changes_back(snapshot):
  key = (0, 0x7E4, None)
  for payload, expected in (('03 22 43 56', '(22 43 56)'), ('03 22 43 56', '(22 43 56)'), ('03 22 43 89', '(22 43 89)'), ('03 22 43 56', '(22 43 56)')):
    snapshot.ingest([(1, [(0x7E4, frame(payload), 0)])])
    assert snapshot.rows[key].diagnostic == f'HPCM2 <- tester: read data by ID {expected}'
  assert snapshot.rows[key].changes == 2


def test_summary_is_exposed_in_the_frozen_display_state():
  session = InspectionSession(CAR.CHEVROLET_VOLT)
  session.ingest([(1, [(0x541, frame('81 47 50 03 19 00 00 00'), 0)])])
  state = session.capture().rows[(0, 0x541, None)]
  assert state.diagnostic == 'BCM (derived) -> tester: DTC report: C0750, type 03, status 19'
  session.toggle_freeze()
  session.ingest([(2, [(0x541, frame('81 00 00 00 FF 00 00 00'), 0)])])
  assert session.capture().rows[(0, 0x541, None)].diagnostic == state.diagnostic  # frozen snapshot does not change


def test_label_is_searchable_by_module_and_direction():
  text = label_text(0x252)
  assert matches_query('hmi', 0, 0x252, text, 'raw') and matches_query('0x252 tester', 0, 0x252, text, 'raw')
  assert not matches_query('hmi', 0, 0x253, label_text(0x253), 'raw')


def test_recorded_sweep_through_the_panel_respects_the_unknown_id_cap(snapshot):
  frames = load_fixture()
  snapshot.ingest([(ms, [(address, data, 0)]) for ms, address, data in frames])
  labeled = {key for key, row in snapshot.rows.items() if row.diagnostic}
  assert labeled == {(0, address, None) for address in TABLE_IDS & {a for _, a, _ in frames}}
  assert {key[1] for key in snapshot.rows} == {address for _, address, _ in frames}  # every recorded ID is labeled
  # An unknown-ID flood still cannot grow the inspector, and already-labeled diagnostic rows survive it.
  snapshot.ingest([(1, [(0x10000+i, b'\x01', 0) for i in range(5000)])])
  assert len(snapshot.rows) <= MAX_RAW_MESSAGES_PER_BUS
  assert {key for key, row in snapshot.rows.items() if row.diagnostic} == labeled


# --- Performance and memory bounds --------------------------------------------------------------------------------

def corpus():
  frames = [(address, data) for _, address, data in load_fixture()]
  rng = random.Random(5)
  frames += [(rng.choice(sorted(TABLE_IDS)), bytes(rng.getrandbits(8) for _ in range(8))) for _ in range(500)]
  frames += [(0x10000 + i, bytes(8)) for i in range(50)]  # ordinary undefined IDs
  return frames


def test_decoder_is_constant_time_per_frame_with_a_wide_margin():
  frames = corpus()
  start = time.perf_counter()
  count = 0
  for _ in range(100):
    for address, data in frames:
      summarize(address, data, 0)
      count += 1
  per_frame_us = (time.perf_counter()-start)/count*1e6
  # Measured near 1 us on the dev machine; the bound is ~50x that so a loaded CI host cannot flake, yet any per-frame scan,
  # regex or growing structure would still show up. The panel's per-update budget is 8 ms for up to 32 batches.
  assert per_frame_us < 50, f'{per_frame_us:.1f} us per frame'
  start = time.perf_counter()
  for _ in range(100000):
    summarize(0x10000, bytes(8), 0)
  assert (time.perf_counter()-start)/100000*1e6 < 10  # a non-diagnostic ID is one dict miss


def test_decoder_holds_no_state_that_grows_with_traffic():
  frames = corpus()
  before = len(dd._IDS), len(dd._RADAR_BUS_IDS), len(dd._SERVICES)
  for address, data in frames:  # warm up allocator pools and interned strings
    summarize(address, data, 0)
  tracemalloc.start()
  try:
    baseline = tracemalloc.get_traced_memory()[0]
    rng = random.Random(9)
    for _ in range(200000):
      address, data = frames[rng.randrange(len(frames))]
      summarize(address, data[:rng.randrange(0, 9)], rng.choice([0, 1, 2]))
    growth = tracemalloc.get_traced_memory()[0]-baseline
  finally:
    tracemalloc.stop()
  assert growth < 64*1024, f'{growth} bytes retained after 200k frames'
  assert before == (len(dd._IDS), len(dd._RADAR_BUS_IDS), len(dd._SERVICES))


def test_ingest_batches_stay_far_inside_the_ui_update_budget_and_rows_stay_bounded(snapshot):
  frames = [(address, data, 0) for address, data in corpus()]
  timings = []
  for i in range(4000):
    batch = [frames[(i*7+j) % len(frames)] for j in range(24)]
    start = time.perf_counter()
    snapshot.ingest([(i, batch)])
    timings.append((time.perf_counter()-start)*1000)
  timings.sort()
  # The panel gives one update 8 ms across up to 32 batches. Asserting 2 ms per 24-frame batch (typically ~0.15 ms).
  assert timings[int(len(timings)*.99)] < 2, f'p99 {timings[int(len(timings)*.99)]:.3f} ms'
  assert len(snapshot.rows) <= MAX_RAW_MESSAGES_PER_BUS and len(snapshot.messages) <= MAX_RAW_MESSAGES_PER_BUS
