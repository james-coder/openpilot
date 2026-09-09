import math
import numpy as np
from cereal import car
from opendbc.car.gm.volt_longitudinal import enabled as volt_enabled, PROFILE, VoltFlags
from openpilot.selfdrive.controls.lib.volt_stopping import VoltStopping
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.controls.lib.drive_helpers import CONTROL_N
from openpilot.common.pid import PIDController
from openpilot.selfdrive.modeld.constants import ModelConstants

CONTROL_N_T_IDX = ModelConstants.T_IDXS[:CONTROL_N]

LongCtrlState = car.CarControl.Actuators.LongControlState


def long_control_state_trans(CP, active, long_control_state, v_ego,
                             should_stop, brake_pressed, cruise_standstill):
  stopping_condition = should_stop
  starting_condition = (not should_stop and
                        not cruise_standstill and
                        not brake_pressed)
  started_condition = v_ego > CP.vEgoStarting

  if not active:
    long_control_state = LongCtrlState.off

  else:
    if long_control_state == LongCtrlState.off:
      if not starting_condition:
        long_control_state = LongCtrlState.stopping
      else:
        if starting_condition and CP.startingState:
          long_control_state = LongCtrlState.starting
        else:
          long_control_state = LongCtrlState.pid

    elif long_control_state == LongCtrlState.stopping:
      if starting_condition and CP.startingState:
        long_control_state = LongCtrlState.starting
      elif starting_condition:
        long_control_state = LongCtrlState.pid

    elif long_control_state in [LongCtrlState.starting, LongCtrlState.pid]:
      if stopping_condition:
        long_control_state = LongCtrlState.stopping
      elif started_condition:
        long_control_state = LongCtrlState.pid
  return long_control_state

class LongControl:
  def __init__(self, CP):
    self.CP = CP
    self.long_control_state = LongCtrlState.off
    self.pid = PIDController((CP.longitudinalTuning.kpBP, CP.longitudinalTuning.kpV),
                             (CP.longitudinalTuning.kiBP, CP.longitudinalTuning.kiV),
                             rate=1 / DT_CTRL)
    self.last_output_accel = 0.0
    from openpilot.selfdrive.car.volt_profile import runtime_bundle
    bundle = runtime_bundle(CP)
    self.volt_profile = bundle['calibration'] if bundle else PROFILE
    self.volt_stopping = VoltStopping(self.volt_profile)
    self.stock_ki = self.pid._k_i

  def reset(self):
    self.pid.reset()

  def update(self, active, CS, a_target, should_stop, accel_limits, stop_trajectory_active=False):
    """Update longitudinal control. This updates the state machine and runs a PID loop"""
    self.pid.neg_limit = accel_limits[0]
    self.pid.pos_limit = accel_limits[1]

    volt_braking = volt_enabled(self.CP) and (a_target < 0. or should_stop or self.last_output_accel < 0.)
    profile = self.volt_profile
    positive_limit = profile.integral_limit * float(np.clip(CS.vEgo / 2., 0., 1.))
    self.pid._k_i = [[0.], [profile.braking_ki]] if volt_braking else self.stock_ki
    previous_state = self.long_control_state
    self.long_control_state = long_control_state_trans(self.CP, active, self.long_control_state, CS.vEgo,
                                                       should_stop, CS.brakePressed,
                                                       CS.cruiseState.standstill)
    if self.long_control_state == LongCtrlState.off:
      self.reset()
      output_accel = 0.
      self.volt_stopping.reset()

    elif self.long_control_state == LongCtrlState.stopping and volt_enabled(self.CP):
      output_accel = self.volt_stopping.update(CS, a_target, previous_state != LongCtrlState.stopping,
                                               self.last_output_accel, self.pid,
                                               trajectory_active=stop_trajectory_active and bool(self.CP.flags & VoltFlags.PERSONAL))

    elif self.long_control_state == LongCtrlState.stopping:
      output_accel = self.last_output_accel
      if output_accel > self.CP.stopAccel:
        output_accel = min(output_accel, 0.0)
        output_accel -= self.CP.stoppingDecelRate * DT_CTRL
      self.reset()

    elif self.long_control_state == LongCtrlState.starting:
      output_accel = self.CP.startAccel
      self.reset()

    else:  # LongCtrlState.pid
      if volt_braking:
        # Bumpless departure from stop control and bounded delayed correction.
        if previous_state == LongCtrlState.stopping:
          self.pid.i = float(np.clip(self.last_output_accel-a_target, -profile.integral_limit, positive_limit))
        self.pid.i = float(np.clip(self.pid.i, -profile.integral_limit, positive_limit))
      error = a_target - CS.aEgo
      output_accel = self.pid.update(error, speed=CS.vEgo,
                                     feedforward=a_target)
      if volt_braking:
        self.pid.i = float(np.clip(self.pid.i, -profile.integral_limit, positive_limit))
        output_accel = self.pid.p + self.pid.i + self.pid.f

    stationary_wheels = CS.standstill and math.isfinite(CS.vEgoRaw) and abs(CS.vEgoRaw) < .03
    if volt_braking and active and not stationary_wheels and a_target <= accel_limits[0]:
      # A positive residual integral must not soften a full-braking request.
      # Preserve stationary holding and the stock path when the candidate is off.
      output_accel = accel_limits[0]
    self.last_output_accel = np.clip(output_accel, accel_limits[0], accel_limits[1])
    return self.last_output_accel
