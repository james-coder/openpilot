"""File-facing glue for the Volt following-distance profile.

Kept separate from selfdrive/car/volt_profile.py (the braking/stopping-approach
qualification pipeline) on purpose — this is an independent feature with its own,
much simpler gate: a fitted VoltFollowingProfile is used only if it round-trips
through JSON and passes opendbc.car.gm.volt_following.following_profile_valid()
again here, on load, regardless of what the fit script itself already checked.

Stored as a plain JSON file (PROFILE_PATH), not a Params key. Adding a brand-new
Params key requires the compiled params_pyx C++ extension to be rebuilt (it hardcodes
a key allowlist from common/params_keys.h at compile time) -- that happens naturally
through the normal update/build flow, but not through a quick file-copy deploy, and
this value is expected to get hand-tuned repeatedly before real fit data exists. A
plain file needs no rebuild ever, for this or any future tweak -- same pattern
selfdrive/car/volt_profile.py's own BUNDLE_PATH already uses for the braking system.
"""

import json
from pathlib import Path

from opendbc.car.gm.volt_following import GapParams, VoltFollowingProfile, following_enabled, following_profile_valid

PROFILE_PATH = Path("/data/volt_following_profile.json")


def _gap_params(value):
  return GapParams(t_follow=float(value["t_follow"]), comfort_brake=float(value["comfort_brake"]),
                    stop_distance=float(value["stop_distance"]))


def parse_following_profile(raw: str) -> VoltFollowingProfile | None:
  """Deserialize the Params JSON payload; None on any malformed/missing/invalid value."""
  if not raw:
    return None
  try:
    value = json.loads(raw)
    profile = VoltFollowingProfile(
      version=str(value["version"]),
      validated=bool(value["validated"]),
      fit_time=str(value["fit_time"]),
      heldout_rmse_m=float(value["heldout_rmse_m"]),
      aggressive=_gap_params(value["aggressive"]),
      standard=_gap_params(value["standard"]),
      relaxed=_gap_params(value["relaxed"]),
    )
  except (json.JSONDecodeError, KeyError, TypeError, ValueError):
    return None
  return profile if following_profile_valid(profile) else None


def load_following_profile(path: Path = PROFILE_PATH) -> VoltFollowingProfile:
  """Read+re-validate once (call at LongitudinalPlanner construction, not per-update).
  Falls back to the stock-matching default (validated=False) on any missing/invalid value,
  which makes following_enabled() below false and leaves stock get_T_FOLLOW() untouched."""
  try:
    raw = path.read_text()
  except (OSError, UnicodeDecodeError):
    # UnicodeDecodeError is a ValueError subclass, not an OSError -- read_text() raises it
    # on invalid-UTF8 bytes (disk corruption, a torn/non-atomic write, manual tampering).
    # This is called unguarded from LongitudinalPlanner.__init__; missing this left the
    # planner process crashing at construction on every restart until the file was fixed.
    raw = None
  profile = parse_following_profile(raw) if raw else None
  return profile if profile is not None else VoltFollowingProfile()


def following_gap_kwargs(CP, path: Path = PROFILE_PATH) -> dict:
  """kwargs for LongitudinalMpc(**kwargs) — empty dict (fully stock) unless a validated,
  Volt-specific fit is on file."""
  profile = load_following_profile(path)
  if not following_enabled(CP, profile):
    return {}
  return {'following_profile': profile}
