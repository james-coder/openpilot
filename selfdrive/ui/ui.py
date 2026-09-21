#!/usr/bin/env python3
import faulthandler
import os
import time

from cereal import messaging
from openpilot.system.hardware import TICI
from openpilot.common.realtime import Priority, config_realtime_process, set_core_affinity
from openpilot.system.ui.lib.application import gui_app
from openpilot.selfdrive.ui.layouts.main import MainLayout
from openpilot.selfdrive.ui.mici.layouts.main import MiciMainLayout
from openpilot.selfdrive.ui.ui_state import ui_state

BIG_UI = gui_app.big_ui()


FAULT_LOG = '/data/log/ui_faults.log'


def enable_fault_log(path=FAULT_LOG):
  """On a fatal signal (SIGABRT/SIGSEGV) dump every thread's Python stack to a persistent file.
  The manager only records the exit code (a -6 on 2026-09-21 mid-drive left nothing else), and
  the process's stderr scrolls off the tmux pane within minutes."""
  try:
    f = open(path, 'a')  # noqa: SIM115 -- must stay open for faulthandler
    f.write(f'\n=== ui start pid={os.getpid()} t={time.time():.0f}\n')  # noqa: TID251 -- persisted wall timestamp
    f.flush()
    faulthandler.enable(file=f, all_threads=True)
    return f
  except OSError:
    return None


def main():
  enable_fault_log()
  # Keep initialization and hotplug recovery on the same core. Core 5 is
  # reserved for plannerd/radard; the UI must not migrate there while rendering.
  cores = {0}
  config_realtime_process(list(cores), Priority.CTRL_HIGH)

  gui_app.init_window("UI")
  if BIG_UI:
    MainLayout()
  else:
    MiciMainLayout()

  pm = messaging.PubMaster(['uiDebug'])
  for should_render, frame_time, cpu_time in gui_app.render():
    extra_start = time.monotonic()
    ui_state.update()

    if should_render:
      # reaffine after power save offlines our core
      if TICI and os.sched_getaffinity(0) != cores:
        try:
          set_core_affinity(list(cores))
        except OSError:
          pass

      extra_cpu = time.monotonic() - extra_start
      msg = messaging.new_message('uiDebug')
      msg.uiDebug.cpuTimeMillis = (cpu_time + extra_cpu) * 1000
      msg.uiDebug.frameTimeMillis = frame_time * 1000
      pm.send('uiDebug', msg)


if __name__ == "__main__":
  main()
