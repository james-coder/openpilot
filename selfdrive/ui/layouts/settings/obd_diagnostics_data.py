import json
import re
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def descriptions():
  try:
    return json.loads((Path(__file__).parent / "obd_codes.json").read_text())
  except (OSError, ValueError):
    return {}


def valid_report(report, vehicle):
  if not isinstance(report, dict) or report.get("version") != 1 or report.get("vehicle") != vehicle:
    return None
  ecus = report.get("ecus", {})
  if not isinstance(ecus, dict) or len(ecus) > 8:
    return None
  for addr, results in ecus.items():
    if not isinstance(addr, str) or not re.fullmatch(r"7E[8-F]", addr) or not isinstance(results, dict):
      return None
    for name, result in results.items():
      if name not in ("lamp", "stored", "pending", "permanent") or not isinstance(result, dict):
        return None
      if result.get("state") not in ("ok", "timeout", "error", "unsupported"):
        return None
      if result["state"] == "ok":
        if name == "lamp":
          if type(result.get("mil")) is not bool or type(result.get("count")) is not int or not 0 <= result["count"] <= 127:
            return None
        else:
          codes = result.get("codes")
          if not isinstance(codes, list) or len(codes) > 255:
            return None
          if any(not isinstance(code, str) or not re.fullmatch(r"[PCBU][0-3][0-9A-F]{3}", code) for code in codes):
            return None
  return report


def lamp_status(report):
  lamps = [ecu.get("lamp", {}) for ecu in report.get("ecus", {}).values()]
  if any(lamp.get("state") == "ok" and lamp.get("mil") for lamp in lamps):
    return "On"
  if lamps and all(lamp.get("state") == "ok" and lamp.get("mil") is False for lamp in lamps):
    return "Off"
  return "Unknown"


def result_rows(report):
  rows = []
  for addr, ecu in sorted(report.get("ecus", {}).items()):
    lamp = ecu.get("lamp", {})
    value = ("On" if lamp["mil"] else "Off") if lamp.get("state") == "ok" else "Unknown"
    detail = f"Computer at CAN address 0x{addr}. "
    detail += f"Reports {lamp['count']} confirmed emissions codes." if lamp.get("state") == "ok" else "Lamp status was not read."
    rows.append((f"ECU 0x{addr} light", value, detail))
    code_states = {}
    unread = []
    empty = []
    for name in ("stored", "pending", "permanent"):
      result = ecu.get(name, {})
      if result.get("state") == "ok":
        for code in result["codes"]:
          code_states.setdefault(code, []).append(name.title())
        if not result["codes"]:
          empty.append(name)
      else:
        unread.append((name.title(), "Not read", f"ECU 0x{addr}: {result.get('error', 'No response')}"))
    for code, states in sorted(code_states.items()):
      rows.append((code, ", ".join(states), f"ECU 0x{addr}: {descriptions().get(code, 'Description unavailable.')}"))
    if empty:
      rows.append(("No codes", ", ".join(s.title() for s in empty), f"ECU 0x{addr} returned empty lists for these categories."))
    rows.extend(unread)
  return rows
