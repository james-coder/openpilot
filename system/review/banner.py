"""What the onroad "event saved" banner says and when. Pure logic, importable without any UI or display."""
from pathlib import Path

SHOW_SECONDS = 10.0
ORDER = ('abs_stop', 'hard_brake', 'extreme_g', 'impact')
LABELS = {'abs_stop': 'ABS stop', 'hard_brake': 'hard brake', 'extreme_g': 'extreme g-force', 'impact': 'possible impact'}


def current_boot_id() -> str:
  try:
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
  except OSError:
    return ''


def banner_text(record, boot: str, boot_now: float) -> str | None:
  """Text to show, or None when the record is absent, malformed, from another boot, or older than SHOW_SECONDS."""
  try:
    if not isinstance(record, dict) or record.get('boot_id') != boot or not boot:
      return None
    age = boot_now - float(record['written_boot_s'])
    if not 0 <= age <= SHOW_SECONDS:
      return None
    kinds = [k for k in ORDER if k in record['kinds']]
    if not kinds:
      return None
    return 'EVENT SAVED: ' + ', '.join(LABELS[k] for k in kinds).upper()
  except (KeyError, TypeError, ValueError):
    return None
