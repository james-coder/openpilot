import math

from cereal import log
from openpilot.selfdrive.controls.lib.latcontrol import LatControl
from openpilot.selfdrive.controls.lib.crosswind_hold import CrosswindHold, PARAM as HOLD_PARAM
from openpilot.common.pid import PIDController

# Volt crosswind hold (docs/2026-09-26-crosswind-analysis.md): integral only while a strong crosswind is reported.
HOLD_KI_BP = [0., 15., 40.]
HOLD_KI_V = [0., 0., 0.05]
HOLD_I_LIMIT = 0.4     # the hold term alone uses at most 40% of the (unchanged, panda-enforced) torque limit
HOLD_DECAY = 0.995     # per 10 ms once the hold turns off: fades out over ~2 s instead of letting go
HOLD_OVERRIDE_S = 1.0  # driver steering longer than this (e.g. lane change) clears the hold


class LatControlPID(LatControl):
  def __init__(self, CP, CI, dt):
    super().__init__(CP, CI, dt)
    self.pid = PIDController((CP.lateralTuning.pid.kpBP, CP.lateralTuning.pid.kpV),
                             (CP.lateralTuning.pid.kiBP, CP.lateralTuning.pid.kiV),
                             pos_limit=self.steer_max, neg_limit=-self.steer_max)
    self.ff_factor = CP.lateralTuning.pid.kf
    self.get_steer_feedforward = CI.get_steer_feedforward_function()
    self.hold = None
    if CP.carFingerprint == 'CHEVROLET_VOLT' and not any(CP.lateralTuning.pid.kiV):
      from openpilot.common.params import Params
      self.hold = CrosswindHold(enabled=Params().get(HOLD_PARAM, return_default=True) != 'off')
      self._stock_ki = (list(CP.lateralTuning.pid.kiBP), list(CP.lateralTuning.pid.kiV))
      self._override_time = 0.

  def reset(self):
    super().reset()
    if self.hold is not None:
      self.pid.reset()  # never carry a hold into the next engagement
      self._override_time = 0.

  def _update_hold(self, active, CS):
    if not active:
      self._override_time = 0.
      return False
    self._override_time = self._override_time + self.dt if CS.steeringPressed else 0.
    if self._override_time > HOLD_OVERRIDE_S:
      self.pid.i = 0.
    holding = self.hold.active
    self.pid._k_i = [HOLD_KI_BP, HOLD_KI_V] if holding else self._stock_ki
    if not holding:
      self.pid.i *= HOLD_DECAY
    return holding

  def update(self, active, CS, VM, params, steer_limited_by_safety, desired_curvature, curvature_limited, lat_delay):
    pid_log = log.ControlsState.LateralPIDState.new_message()
    pid_log.steeringAngleDeg = float(CS.steeringAngleDeg)
    pid_log.steeringRateDeg = float(CS.steeringRateDeg)

    angle_steers_des_no_offset = math.degrees(VM.get_steer_from_curvature(-desired_curvature, CS.vEgo, params.roll))
    angle_steers_des = angle_steers_des_no_offset + params.angleOffsetDeg
    error = angle_steers_des - CS.steeringAngleDeg

    pid_log.steeringAngleDesiredDeg = angle_steers_des
    pid_log.angleError = error
    if not active:
      output_torque = 0.0
      pid_log.active = False

    else:
      # offset does not contribute to resistive torque
      ff = self.ff_factor * self.get_steer_feedforward(angle_steers_des_no_offset, CS.vEgo)
      freeze_integrator = steer_limited_by_safety or CS.steeringPressed or CS.vEgo < 5
      if self.hold is not None:
        self._update_hold(active, CS)

      output_torque = self.pid.update(error,
                                feedforward=ff,
                                speed=CS.vEgo,
                                freeze_integrator=freeze_integrator)
      if self.hold is not None and abs(self.pid.i) > HOLD_I_LIMIT:
        self.pid.i = max(-HOLD_I_LIMIT, min(HOLD_I_LIMIT, self.pid.i))
        output_torque = max(-self.steer_max, min(self.steer_max, self.pid.p + self.pid.i + self.pid.d + self.pid.f))

      pid_log.active = True
      pid_log.p = float(self.pid.p)
      pid_log.i = float(self.pid.i)
      pid_log.f = float(self.pid.f)
      pid_log.output = float(output_torque)
      pid_log.saturated = bool(self._check_saturation(self.steer_max - abs(output_torque) < 1e-3, CS, steer_limited_by_safety, curvature_limited))

    return output_torque, angle_steers_des, pid_log
