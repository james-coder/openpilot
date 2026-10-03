#!/usr/bin/env python3
import os
import shutil
import threading
from openpilot.system.hardware.hw import Paths
from openpilot.common.swaglog import cloudlog
from openpilot.system.loggerd.config import get_available_bytes, get_available_percent
from openpilot.system.loggerd.uploader import listdir_by_creation
from openpilot.system.loggerd.xattr_cache import getxattr_uncached

MIN_BYTES = 5 * 1024 * 1024 * 1024
MIN_PERCENT = 10

DELETE_LAST = ['boot', 'crash']

PRESERVE_ATTR_NAME = 'user.preserve'
PRESERVE_ATTR_VALUE = b'1'
# Flagged footage (flag button, dashcam event saving) is kept up to this many bytes, newest first. Beyond it the oldest
# flagged footage becomes ordinary footage again, so protection can never fill the disk (a full disk blocks engagement).
PRESERVE_BUDGET_BYTES = 15 * 1024 * 1024 * 1024


def has_preserve_xattr(d: str) -> bool:
  # Uncached: loggerd (flag button) and eventd set this while the deleter is already running.
  return getxattr_uncached(os.path.join(Paths.log_root(), d), PRESERVE_ATTR_NAME) == PRESERVE_ATTR_VALUE


def _dir_bytes(d: str) -> int:
  total = 0
  try:
    with os.scandir(os.path.join(Paths.log_root(), d)) as entries:
      for e in entries:
        try:
          total += e.stat(follow_symlinks=False).st_size
        except OSError:
          pass
  except OSError:
    return 0
  return total


def get_preserved_segments(dirs_by_creation: list[str]) -> set[str]:
  # Keep flagged segments, each with the two segments before it, newest first, until PRESERVE_BUDGET_BYTES is used.
  # The newest flagged group is always kept. Any failure here means "protect nothing": the deleter must keep
  # freeing space (it is a driving process, and a full disk raises outOfSpace).
  preserved: set[str] = set()
  used = 0
  try:
    for d in filter(has_preserve_xattr, reversed(dirs_by_creation)):
      date_str, _, seg_str = d.rpartition("--")

      # ignore non-segment directories
      if not date_str:
        continue
      try:
        seg_num = int(seg_str)
      except ValueError:
        continue

      # preserve segment and two prior
      group = [f"{date_str}--{_seg_num}" for _seg_num in range(max(0, seg_num - 2), seg_num + 1)]
      size = sum(_dir_bytes(g) for g in group if g not in preserved)
      if preserved and used + size > PRESERVE_BUDGET_BYTES:
        break
      preserved.update(group)
      used += size
  except Exception:
    cloudlog.exception("deleter: could not compute protected footage; deleting without protection")
    return set()
  return preserved


def deleter_thread(exit_event: threading.Event):
  while not exit_event.is_set():
    out_of_bytes = get_available_bytes(default=MIN_BYTES + 1) < MIN_BYTES
    out_of_percent = get_available_percent(default=MIN_PERCENT + 1) < MIN_PERCENT

    if out_of_percent or out_of_bytes:
      dirs = listdir_by_creation(Paths.log_root())
      preserved_dirs = get_preserved_segments(dirs)

      # remove the earliest directory we can
      for delete_dir in sorted(dirs, key=lambda d: (d in DELETE_LAST, d in preserved_dirs)):
        delete_path = os.path.join(Paths.log_root(), delete_dir)

        if any(name.endswith(".lock") for name in os.listdir(delete_path)):
          continue

        try:
          cloudlog.info(f"deleting {delete_path}")
          shutil.rmtree(delete_path)
          break
        except OSError:
          cloudlog.exception(f"issue deleting {delete_path}")
      exit_event.wait(.1)
    else:
      exit_event.wait(30)


def main():
  deleter_thread(threading.Event())


if __name__ == "__main__":
  main()
