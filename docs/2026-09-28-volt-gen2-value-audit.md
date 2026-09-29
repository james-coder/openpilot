# Volt Gen 2 value audit, 2026-09-28

A read-only audit of every Volt Gen 2 number in `opendbc_repo/opendbc/car/gm/`, the panda GM safety code and
the fork's Volt modules, checked against public specs and the car's own logs (about 490 local logs,
Sep 7 to Sep 26). Nothing was wrong on the scale of a wrong unit or sign. The Volt numbers in
`values.py` / `interface.py` are upstream comma's; the fork's additions are flags, the following-distance
constants and the crosswind / low-speed brake changes.

## Fixed: wheel speed reads 1.6% low

`wheelSpeedFactor` is the default 1.0 (`opendbc/car/interfaces.py:205`). Wheel-derived `vEgo` divided by GPS
speed: median **0.9846** over 111k samples, 0.984-0.987 on every date (Sep 7, 8, 12, 21, 25-26); IQR
0.983-0.987. A factor of **1.0156** for the Volt (`gm/interface.py`, Volt branch) makes the set speed, gaps
and braking distances use the true speed (the set speed currently holds a true speed 1.6% higher). Re-measure
after a tire change. Applied 2026-09-28:

    ret.wheelSpeedFactor = 1.0156   # in the CHEVROLET_VOLT branch next to steerActuatorDelay

## Checked and not changed

- **Steering vehicle model (audit item "over-asks 20-65% of wheel angle"): not confirmed.** The offline
  comparison ignored road-roll compensation, which is large on banked highway curves (`liveParameters.roll`
  of about +/-3 deg). Measured from the logs at the actual runtime:
  - The model's commanded angle equals `VehicleModel.get_steer_from_curvature(...)` exactly (median ratio
    1.0000 over 46k samples), so the model is what the audit described.
  - In steady engaged curves the car achieves **0.94-0.97** of the curvature the driving model asks for at
    20-37 m/s (0.72-0.92 at 10-20 m/s, fewer samples), and the wheel tracks **0.93-0.95** of the commanded
    angle at 20+ m/s. So highway steering is already net-accurate; a model correction alone would under-steer.
  - The steering weave is therefore not a static gain error. Steering Response "Firmer" remains an
    experiment, default Stock. The measured 0.70 dynamic tracking (0.2-1.5 Hz band) with 0.28 s lag is
    still the leading suspect.
- **Regen assumed -1.0 m/s^2 for the -650 command (audit item: "only 0.6 at 6-10 m/s"): not supported.**
  Full regen adds about 0.9-1.2 m/s^2 beyond coasting at every speed with enough data (10-37 m/s). Below
  10 m/s there are only 15-55 regen samples, too few to say. Mapping unchanged.
- **Friction gain / `MAX_BRAKE = 400 ~ -4.0 m/s^2` (audit): the comment is optimistic, the mapping is
  unchanged.** Openpilot-commanded friction of 300-400 units delivered a median -2.7 (p90 -3.7) m/s^2
  including regen; a linear fit gives about -2.8 at 400 (87 high-command samples, mostly mid-ramp). Driver
  braking reached -4.6 m/s^2 (pedal about 100/255), so the car can do more; the ASCM command path is the
  limit. The planner's `ACCEL_MIN` of -3.5 is beyond what that path reliably delivers. No change without a
  controlled stop test (see VOLT_COLLISION_PROTECTION.md, "Measure brake response ... in a controlled
  facility").
- **Fine:** mass 1607 kg curb (1743 kg with cargo), wheelbase 2.69 m, 215/50R17 tires, 3 Nm at STEER_MAX 300
  (EPS delivered 2.88 Nm at 0.93 command), Python and firmware limits match, DBC bit positions and scales,
  brake-pressed threshold 8, EV/flag handling, steering lock 522 deg (3.0 turns), on-center steer ratio
  17.4-18.4 (do not change 17.7 to GM's 15.7).

## Also applied 2026-09-28 (approved by the driver)

- **Closest following time 0.725 s -> 0.8 s.** The audit cites ISO 15622:2018 as requiring at least 0.8 s;
  I have not verified the standard. Only the closest setting is affected (the middle is 0.875 s, max 1.25 s).
  Done in `get_T_FOLLOW` (aggressive), `long_mpc.py`.
- **`MAX_BRAKE` comment** (`gm/values.py`) now says the -4.0 figure is unproven (text above). The mapping is unchanged.

## Left alone on purpose

Inherited from upstream and unchanged by this fork: panda RX-check frequencies of 10 Hz for messages that run
at 20-80 Hz (firmware change), `centerToFrontRatio` 0.45 (no published weight split), `steerActuatorDelay`
0.2 vs a measured 0.24-0.29 s (it only seeds `lagd`), the unused torque-tune `params.toml` entry, and two
unvalidated, disabled brake profiles (default `PROFILE` vs `volt_candidate.json`).

Scripts: `.scratch/analysis/steer_model_*.py`, `steer_net_gain.py`, `steer_track_ss.py`, `regen_extract.py`.
