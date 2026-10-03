import time
import threading
from collections import namedtuple
from pathlib import Path
from collections.abc import Sequence

import openpilot.system.loggerd.deleter as deleter
from openpilot.common.timeout import Timeout, TimeoutException
from openpilot.system.loggerd.tests.loggerd_tests_common import UploaderTestCase

Stats = namedtuple("Stats", ['f_bavail', 'f_blocks', 'f_frsize'])


class TestDeleter(UploaderTestCase):
  def fake_statvfs(self, d):
    return self.fake_stats

  def setup_method(self):
    self.f_type = "fcamera.hevc"
    super().setup_method()
    self.fake_stats = Stats(f_bavail=0, f_blocks=10, f_frsize=4096)
    deleter.os.statvfs = self.fake_statvfs

  def start_thread(self):
    self.end_event = threading.Event()
    self.del_thread = threading.Thread(target=deleter.deleter_thread, args=[self.end_event])
    self.del_thread.daemon = True
    self.del_thread.start()

  def join_thread(self):
    self.end_event.set()
    self.del_thread.join()

  def test_delete(self):
    f_path = self.make_file_with_data(self.seg_dir, self.f_type, 1)

    self.start_thread()

    try:
      with Timeout(2, "Timeout waiting for file to be deleted"):
        while f_path.exists():
          time.sleep(0.01)
    finally:
      self.join_thread()

  def assertDeleteOrder(self, f_paths: Sequence[Path], timeout: int = 5) -> None:
    deleted_order = []

    self.start_thread()
    try:
      with Timeout(timeout, "Timeout waiting for files to be deleted"):
        while True:
          for f in f_paths:
            if not f.exists() and f not in deleted_order:
              deleted_order.append(f)
          if len(deleted_order) == len(f_paths):
            break
          time.sleep(0.01)
    except TimeoutException:
      print("Not deleted:", [f for f in f_paths if f not in deleted_order])
      raise
    finally:
      self.join_thread()

    assert deleted_order == f_paths, "Files not deleted in expected order"

  def test_delete_order(self):
    self.assertDeleteOrder([
      self.make_file_with_data(self.seg_format.format(0), self.f_type),
      self.make_file_with_data(self.seg_format.format(1), self.f_type),
      self.make_file_with_data(self.seg_format2.format(0), self.f_type),
    ])

  def test_delete_many_preserved(self, monkeypatch):
    # room for five 0.1 MB segments of flagged footage: the 6th flagged group and its prior segment are ordinary footage
    monkeypatch.setattr(deleter, 'PRESERVE_BUDGET_BYTES', 600_000)
    self.assertDeleteOrder([
      self.make_file_with_data(self.seg_format.format(0), self.f_type),
      self.make_file_with_data(self.seg_format.format(1), self.f_type, preserve_xattr=deleter.PRESERVE_ATTR_VALUE),
      self.make_file_with_data(self.seg_format.format(2), self.f_type),
    ] + [
      self.make_file_with_data(self.seg_format2.format(i), self.f_type, preserve_xattr=deleter.PRESERVE_ATTR_VALUE)
      for i in range(5)
    ])

  def test_delete_last(self):
    self.assertDeleteOrder([
      self.make_file_with_data(self.seg_format.format(1), self.f_type),
      self.make_file_with_data(self.seg_format2.format(0), self.f_type),
      self.make_file_with_data(self.seg_format.format(0), self.f_type, preserve_xattr=deleter.PRESERVE_ATTR_VALUE),
      self.make_file_with_data("boot", self.seg_format[:-4]),
      self.make_file_with_data("crash", self.seg_format2[:-4]),
    ])

  def test_no_delete_when_available_space(self):
    f_path = self.make_file_with_data(self.seg_dir, self.f_type)

    block_size = 4096
    available = (10 * 1024 * 1024 * 1024) / block_size  # 10GB free
    self.fake_stats = Stats(f_bavail=available, f_blocks=10, f_frsize=block_size)

    self.start_thread()
    start_time = time.monotonic()
    while f_path.exists() and time.monotonic() - start_time < 2:
      time.sleep(0.01)
    self.join_thread()

    assert f_path.exists(), "File deleted with available space"

  def test_no_delete_with_lock_file(self):
    f_path = self.make_file_with_data(self.seg_dir, self.f_type, lock=True)

    self.start_thread()
    start_time = time.monotonic()
    while f_path.exists() and time.monotonic() - start_time < 2:
      time.sleep(0.01)
    self.join_thread()

    assert f_path.exists(), "File deleted when locked"

  def flagged(self, seg: int, route: str | None = None) -> Path:
    return self.make_file_with_data((route or self.seg_format2).format(seg), self.f_type, preserve_xattr=deleter.PRESERVE_ATTR_VALUE)

  def test_preserve_budget_drops_the_oldest_flagged_footage_first(self, monkeypatch):
    monkeypatch.setattr(deleter, 'PRESERVE_BUDGET_BYTES', 250_000)  # two 0.1 MB segments
    self.assertDeleteOrder([
      self.make_file_with_data(self.seg_format.format(0), self.f_type),
      self.flagged(10),   # over budget: ordinary footage again
      self.flagged(20),
      self.flagged(30),
    ])

  def test_newest_flagged_group_is_kept_even_when_it_alone_exceeds_the_budget(self, monkeypatch):
    monkeypatch.setattr(deleter, 'PRESERVE_BUDGET_BYTES', 1)
    self.assertDeleteOrder([self.flagged(10), self.flagged(20)])

  def test_flag_set_after_the_deleter_looked_is_seen(self):
    import xattr
    path = self.make_file_with_data(self.seg_dir, self.f_type).parent
    assert not deleter.has_preserve_xattr(self.seg_dir)
    xattr.setxattr(str(path), deleter.PRESERVE_ATTR_NAME, deleter.PRESERVE_ATTR_VALUE)  # as loggerd/eventd do, bypassing our cache
    assert deleter.has_preserve_xattr(self.seg_dir)
    assert self.seg_dir in deleter.get_preserved_segments([self.seg_dir])

  def test_failure_reading_flags_protects_nothing_but_keeps_deleting(self, monkeypatch):
    def denied(_d):
      raise PermissionError('injected')
    monkeypatch.setattr(deleter, 'has_preserve_xattr', denied)
    assert deleter.get_preserved_segments(['00000005--4c4e99b08b--3']) == set()
    f_path = self.make_file_with_data(self.seg_dir, self.f_type, preserve_xattr=deleter.PRESERVE_ATTR_VALUE)
    self.start_thread()
    try:
      with Timeout(2, "Timeout waiting for file to be deleted"):
        while f_path.exists():
          time.sleep(0.01)
      assert self.del_thread.is_alive(), "deleter thread died on a flag-read error"
    finally:
      self.join_thread()

