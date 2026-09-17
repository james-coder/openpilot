"""Bounded host credential reader, deliberately independent of signing tools."""
import os
from pathlib import Path
import stat


def bounded_read(path: Path, limit: int, *, private=False) -> bytes:
  fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
  try:
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
      raise ValueError('invalid input file type/size')
    if private and (st.st_uid != os.getuid() or st.st_mode & 0o077 or st.st_nlink != 1):
      raise ValueError('private credential ownership/permissions/link count')
    with os.fdopen(fd, 'rb', closefd=False) as f:
      result = f.read(limit+1)
    if len(result) > limit:
      raise ValueError('input grew beyond limit')
    return result
  finally:
    os.close(fd)
