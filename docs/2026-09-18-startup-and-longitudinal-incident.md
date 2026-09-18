# 2026-09-18: startup crashes and following-distance revert

Real incident from a live drive attempt tonight. Three real bugs found and fixed via actual
logs from the device (not guessed), plus one feature deliberately reverted to stock pending
further investigation. Recorded here so a future session doesn't have to rediscover any of
this by re-reading SSH history.

## What actually happened, in order

1. **`card.py` crashed on every single startup.** `cloudlog.info("Volt longitudinal profile",
   version=..., mode=..., validated=...)` passed kwargs to a plain stdlib `logging.Logger.info()`,
   which doesn't accept them (only the custom `cloudlog.event(event, *args, **kwargs)` does).
   Deterministic, every boot, on a Volt with the following-distance/braking profile active.
   Fixed: `598933349`.

2. **`plannerd` crashed on every real drive** once `card.py` was fixed and the car could
   actually start. `opendbc/car/gm/volt_following.py`'s `resolve_gap_params()` used a dict
   lookup keyed by `log.LongitudinalPersonality` enum members. A directly-constructed enum
   value (as every existing unit test used) is a plain Python `int`; the same value read off
   a real capnp message (`sm['selfdriveState'].personality`, how this is actually called in
   production) is a `capnp._DynamicEnum` with a different `__hash__`. `==` treats them as
   equal, a dict `in` check does not. This meant **the following-distance personalization
   feature had never actually run on a real drive before tonight** -- it had been silently
   crashing plannerd since it was first deployed. Fixed: `30e01a1fc` (opendbc submodule commit
   `7894c8788331c2f02e1c5694a3c1a55c5b911e07`).

3. **Post-incident review** (3 parallel fresh-eyes agents) found and fixed two more real
   crash risks in the same session, neither yet triggered live: `summarize_mil()` could crash
   `selfdrived` on a malformed `ObdLastScan` value, `load_following_profile()` only caught
   `OSError` around `Path.read_text()` and not `UnicodeDecodeError`. Fixed: `3dc24186a`. Two
   more minor (non-crash, self-healing) findings fixed in `f9f0e2442`.

4. **MIL boot alert showed "?" instead of a separator character.** Same root cause as an
   earlier CO2-label bug this session: the device's UI font can't render non-ASCII glyphs
   (this time an em-dash). Fixed: `e8788ab69`.

5. **First real drive with the following-distance fix actually working (i.e. not crashing)
   produced dangerous behavior**: hard braking on nearly every engage, including with no lead
   present. Diagnosed from real telemetry (`carState`, `longitudinalPlan`, `radarState`, real
   route `00000090--ba5858fb6e`, 19 segments). `carState.vCruise` correctly settled near actual
   speed within ~1s of engage (not the cause). The common factor across every bad engage event
   was the following-distance profile's `comfort_brake` values (up to 4.8, vs. stock 2.5) --
   plausible but **not fully root-caused under time pressure** (the user had an imminent
   multi-hour trip). See "Current state" below for what was actually done about it.

## Current state (as of `e8788ab69`)

- **Following-distance personalization: disabled, not deleted.** The profile file was moved
  from `/data/volt_following_profile.json` to
  `/data/volt_following_profile.json.disabled_2026-09-18` on the device. `following_gap_kwargs()`
  falls back to fully stock `get_T_FOLLOW()`/`STOP_DISTANCE`/`COMFORT_BRAKE` when the file is
  missing -- confirmed directly on-device (`following_gap_kwargs(CP)` returns `{}`). **No code
  was reverted or removed** -- `opendbc/car/gm/volt_following.py`, `selfdrive/car/volt_following.py`,
  `gap_params.py`, and the `MANUAL_INTERIM_PROFILE` values are all still in the repo and still
  correct as far as testing could show; only the on-device *data file* that activates them is
  disabled.
- **Cut-in detection/relaxation: still active.** Not implicated in the braking reports (several
  bad engage events had `hasLead=False`, ruling out cut-in-specific logic). Verified via
  `process_replay` against real route data (see below) with zero crashes, but **has never
  experienced an actual real cut-in event on a real drive** -- only unit-tested and replay-tested
  for crash-safety, not road-verified for feel.
- Everything else from tonight (card.py fix, plannerd fix, MIL fix, the two minor hygiene fixes)
  is deployed and active.

## What's still open

- **Root cause of the hard-braking-on-engage is not fully proven**, only strongly suspected
  (the `comfort_brake` curvature change). The MANUAL_INTERIM_PROFILE values themselves were
  never actually exercised on a real drive until tonight (see incident #2 above) -- this may be
  the first real signal that those hand-reasoned values need retuning, not necessarily a code
  bug. Before re-enabling: replay the `00000090--ba5858fb6e` route's engage events specifically
  with the profile active and compare the MPC's `x_sol`/cost trajectory against the stock
  fallback at the same moments, rather than re-deploying and testing live again.
- Re-run the disabled profile through `process_replay` (`card`, `radard`, `plannerd`) against
  this exact route before ever re-enabling it, the same way tonight's other fixes were verified
  -- this specific validation step was skipped for the following-distance profile itself before
  its first real drive, which is arguably how incident #5 happened.
- `docs.comma.ai`'s `ssh.comma.ai` proxy (`ssh -i ~/.ssh/id_rsa -o StrictHostKeyChecking=accept-new
  -o ProxyCommand="ssh -i ~/.ssh/id_rsa -o StrictHostKeyChecking=accept-new -W %h:%p -p %p %h@ssh.comma.ai"
  comma@<dongle id>`) is confirmed working for remote access when the device has any internet
  connectivity (tested over WiFi tonight) -- untested over the device's own cellular modem,
  which had documented connectivity issues earlier this session (see `docs/MODEM_*.md`).

## Process note

Tonight's bugs were found because I ran `selfdrive/test/process_replay/` against the actual
route that crashed, using real recorded capnp messages instead of directly-constructed test
values -- catches an entire class of bug (real-vs-synthetic capnp typing) that unit tests alone
did not. Worth using before every future change to `card.py`/`radard.py`/`plannerd`-adjacent
code, not just after something breaks. See the `process_replay` invocation pattern:
`replay_process_with_name(('card','radard','plannerd'), msgs)` fed a `LogReader` pointed at a
local `rlog.zst` path (works without network/comma-connect auth). On this specific hardware,
`config_realtime_process`'s CPU-affinity pinning fails under an SSH session with restricted
affinity (`taskset -c 0-7` before invoking Python works around it; unrelated to any of the
actual bugs found).
