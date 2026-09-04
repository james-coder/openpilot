import unittest

from opendbc.can.packer import CANPacker
from opendbc.car.gm.values import CAR

from openpilot.selfdrive.ui.layouts.settings.can_diagnostics_data import (
  BusStats, BUS_DBC_KEYS, CanSnapshot, GraphBuffer, build_parsers, format_value,
)


class TestBusStats(unittest.TestCase):
  def test_never_seen_is_not_alive(self):
    self.assertFalse(BusStats().alive(now=1000.0))

  def test_recent_is_alive(self):
    stats = BusStats(count=1, last_seen=1000.0)
    self.assertTrue(stats.alive(now=1000.5))

  def test_stale_is_not_alive(self):
    stats = BusStats(count=1, last_seen=1000.0)
    self.assertFalse(stats.alive(now=1002.0))


class TestFormatValue(unittest.TestCase):
  def test_integral_float_has_no_decimal(self):
    self.assertEqual(format_value(5.0), "5")

  def test_fractional_value_is_shortened(self):
    self.assertEqual(format_value(1.23456789), "1.235")


class TestBuildParsers(unittest.TestCase):
  def test_builds_one_parser_per_bus_for_volt(self):
    parsers, known_addrs = build_parsers(CAR.CHEVROLET_VOLT)
    self.assertEqual(set(parsers.keys()), set(BUS_DBC_KEYS.keys()))
    for bus in parsers:
      self.assertGreater(len(known_addrs[bus]), 0)


class TestCanSnapshot(unittest.TestCase):
  def setUp(self):
    self.snap = CanSnapshot(CAR.CHEVROLET_VOLT)
    self.packer = CANPacker('gm_global_a_object')

  def _header(self, num_targets=0, **fault):
    values = {'FLRRNumValidTargets': num_targets}
    values.update(fault)
    addr, dat, bus = self.packer.make_can_msg('F_LRR_Obj_Header', 1, values)
    return (addr, dat, bus)

  def test_known_signal_decodes_and_is_tallied(self):
    frame = self._header(FLRRSnsrBlckd=1)
    touched = self.snap.ingest([(1000, [frame])])

    self.assertIn((1, 1120, 'FLRRSnsrBlckd'), touched)
    row = self.snap.rows[(1, 1120, 'FLRRSnsrBlckd')]
    self.assertEqual(row.text, "1")
    self.assertEqual(self.snap.tally[1].count, 1)
    self.assertTrue(self.snap.tally[1].alive())

  def test_unknown_address_falls_back_to_raw_hex(self):
    touched = self.snap.ingest([(2000, [(0x7FF, b'\x01\x02\x03', 0)])])

    self.assertIn((0, 0x7FF, None), touched)
    row = self.snap.rows[(0, 0x7FF, None)]
    self.assertIsNone(row.signal)
    self.assertEqual(row.text, "01 02 03")

  def test_bus_filter_only_returns_matching_rows(self):
    self.snap.ingest([(1000, [self._header(num_targets=1)])])
    self.snap.ingest([(1000, [(0x7FF, b'\x00', 0)])])

    bus1_rows = self.snap.rows_for(bus_filter=1)
    self.assertTrue(all(r.bus == 1 for r in bus1_rows))
    self.assertTrue(len(bus1_rows) > 0)

    bus0_rows = self.snap.rows_for(bus_filter=0)
    self.assertTrue(all(r.bus == 0 for r in bus0_rows))

  def test_tally_does_not_cross_contaminate_other_buses(self):
    self.snap.ingest([(1000, [self._header()])])
    self.assertEqual(self.snap.tally[1].count, 1)
    self.assertEqual(self.snap.tally[0].count, 0)
    self.assertEqual(self.snap.tally[2].count, 0)


class TestGraphBuffer(unittest.TestCase):
  def test_add_and_bounds(self):
    buf = GraphBuffer(window_s=10.0)
    buf.add(0.0, 1.0)
    buf.add(5.0, 3.0)
    self.assertEqual(buf.bounds(), (1.0, 3.0))

  def test_old_samples_are_evicted(self):
    buf = GraphBuffer(window_s=10.0)
    buf.add(0.0, 1.0)
    buf.add(5.0, 2.0)
    buf.add(15.0, 3.0)   # evicts t=0.0 (15 - 10 = 5, cutoff excludes anything < 5)
    self.assertEqual([t for t, _ in buf.samples], [5.0, 15.0])

  def test_empty_bounds_is_none(self):
    self.assertIsNone(GraphBuffer().bounds())


if __name__ == "__main__":
  unittest.main()
