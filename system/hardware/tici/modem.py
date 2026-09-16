#!/usr/bin/env python3
import fcntl
import json
import logging
import os
import serial
import signal
import subprocess
import tempfile
import time

from ipaddress import IPv4Address, AddressValueError

from enum import Enum
from openpilot.system.hardware.tici.modem_input import read_at_response
from openpilot.system.hardware.tici.modem_recovery import PPPProgress, RetryPolicy

logging.basicConfig(
  level=logging.INFO,
  format="%(asctime)s.%(msecs)03d %(levelname)-7s modem: %(message)s",
  datefmt="%H:%M:%S",
)

AT_PORT = "/dev/modem_at0"
PPP_PORT = "/dev/modem_at1"
STATE_PATH = "/dev/shm/modem"
AT_LOCK = "/dev/shm/modem.lock"  # shared with LPA
AT_INIT = [
  "ATE0",       # disable command echo
  "ATV1",       # verbose result codes (CONNECT/BUSY/NO CARRIER, not numeric)
  "AT+CMEE=1",  # numeric +CME ERROR codes on failures (per 3GPP 27.007)
  "ATX4",       # extended result codes: busy + dial tone detection, line speed in CONNECT
  "AT&C1",      # DCD pin follows carrier state (V.250 default)
  "AT+CREG=2",  # registration URCs include location info
  "AT+CGREG=2", # GPRS registration URCs include location info
]
CREG = {0: "not_registered", 1: "home", 2: "searching", 3: "denied", 4: "unknown", 5: "roaming"}
# 3GPP TS 27.007 +COPS <AcT> -> network type
NETWORK_TYPE = {0: "gsm", 1: "gsm", 3: "gsm", 8: "gsm",
                2: "utran", 4: "utran", 5: "utran", 6: "utran",
                7: "lte", 9: "lte", 10: "lte",
                11: "nr", 12: "nr", 13: "nr"}

DIAL_CID = 1
WEBBING_ICCID_PREFIX = "8985235"

PPPD_CMD = [
  "sudo", "-n", "pppd", PPP_PORT, "460800", "noauth", "nodetach", "noipdefault", "usepeerdns",
  "nodefaultroute", "connect",
  "/usr/sbin/chat -v ABORT 'NO CARRIER' ABORT 'NO DIALTONE' ABORT 'BUSY' " +
  f"ABORT 'NO ANSWER' ABORT 'ERROR' TIMEOUT 5 '' AT OK ATD*99***{DIAL_CID}# CONNECT ''",
  "lcp-echo-interval", "30", "lcp-echo-failure", "4", "mtu", "1500", "mru", "1500",
  "novj", "novjccomp", "ipcp-accept-local", "ipcp-accept-remote", "nomagic",
  "user", '""', "password", '""',
]
INITIAL_STATE = {
  "seconds_since_boot": 0,
  "state": "INITIALIZING",
  "connected": False, "ip_address": "",
  "iccid": "", "mcc_mnc": "", "imei": "", "modem_version": "",
  "signal_strength": 0, "signal_quality": 0,
  "network_type": "unknown", "operator": "", "band": "", "channel": 0,
  "registration": "unknown", "temperatures": [], "extra": "",
  "tx_bytes": 0, "rx_bytes": 0,
  "retry_count": 0, "retry_reason": "none", "ppp_exit_status": None,
  "recovery_status": "idle",
}


class State(Enum):
  INITIALIZING = "INITIALIZING"
  SEARCHING = "SEARCHING"
  CONNECTING = "CONNECTING"
  CONNECTED = "CONNECTED"
  DISCONNECTING = "DISCONNECTING"


STATE_WAIT = 1.0  # seconds to wait after each state handler returns


class PPPSession:
  """Owns pppd lifecycle, fail tracking, and PPP routing."""

  def __init__(self):
    self._proc: subprocess.Popen | None = None
    self._peer = ""
    self.dns_ready = False
    self.started_at = 0.0
    self.progress = PPPProgress()

  def start(self):
    if self._proc is not None and self._proc.poll() is None:
      raise RuntimeError("PPP already running")
    if self._proc is not None and self._proc.stdout is not None:
      self._proc.stdout.close()
    self.progress = PPPProgress()
    self._proc = subprocess.Popen([*PPPD_CMD, 'logfd', '1'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    os.set_blocking(self._proc.stdout.fileno(), False)
    self.started_at = time.monotonic()
    self._peer = ""
    self.dns_ready = False
    logging.info(f"PPP dialing CID {DIAL_CID}")

  def kill(self):
    subprocess.run(["sudo", "-n", "killall", "-9", "pppd"], capture_output=True, timeout=5)
    if self._proc is not None:
      self._proc.wait(timeout=5)
      if self._proc.stdout is not None:
        self._proc.stdout.close()
      self._proc = None
    self._peer = ""
    self.dns_ready = False

  @staticmethod
  def reset_data_port():
    """Drop DTR on PPP_PORT so the modem terminates any stuck PPP session."""
    try:
      with serial.Serial(PPP_PORT, 460800, timeout=1) as s:
        s.dtr = False
        time.sleep(0.2)
        s.dtr = True
    except Exception as e:
      logging.warning(f"data port reset failed: {e}")

  def has_exited(self) -> bool:
    return self._proc is not None and self._proc.poll() is not None

  def drain_progress(self):
    if self._proc is None or self._proc.stdout is None:
      return
    # Bound CPU/memory per modem iteration; never store or forward raw logs.
    for _ in range(16):
      try:
        data = os.read(self._proc.stdout.fileno(), 4096)
      except BlockingIOError:
        break
      if not data:
        break
      self.progress.feed(data)

  def maybe_install_routes(self, ip: str, peer: str) -> bool:
    """Install routes if peer changed; kill the session on failure so the state machine reconnects."""
    if not peer:
      return False
    if peer == self._peer:
      return True
    try:
      IPv4Address(ip)
      IPv4Address(peer)
    except AddressValueError:
      logging.warning("refusing route install with invalid IPv4 address")
      self.kill()
      return False
    self.cleanup_routes()
    cmds = [
      ["sudo", "-n", "ip", "route", "add", "default", "via", peer, "dev", "ppp0", "metric", "1000"],
      ["sudo", "-n", "ip", "route", "add", "default", "via", peer, "dev", "ppp0", "table", "1000"],
      ["sudo", "-n", "ip", "rule", "add", "from", ip, "table", "1000"],
    ]
    for cmd in cmds:
      r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
      if r.returncode != 0:
        logging.warning("route install failed: exit=%d", r.returncode)
        self.cleanup_routes()
        self.kill()
        return False
    logging.info("PPP routes installed")
    self._peer = peer
    return True

  def maybe_install_dns(self, dns_servers: list[str]) -> bool:
    """Register DNS servers with systemd-resolved; kill the session on failure to force a retry."""
    if not dns_servers:
      return False
    try:
      dns_servers = [str(IPv4Address(d)) for d in dns_servers]
    except AddressValueError:
      return False
    if len(dns_servers) > 2:
      return False
    for cmd in (["sudo", "-n", "resolvectl", "dns", "ppp0", *dns_servers],
                ["sudo", "-n", "resolvectl", "default-route", "ppp0", "yes"]):
      r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
      if r.returncode != 0:
        logging.warning("PPP DNS configuration failed: exit=%d", r.returncode)
        self.kill()
        return False
    self.dns_ready = True
    logging.info("PPP DNS configured")
    return True

  @staticmethod
  def cleanup_routes():
    subprocess.run(["sudo", "-n", "ip", "route", "del", "default", "dev", "ppp0"], capture_output=True, timeout=5)
    subprocess.run(["sudo", "-n", "ip", "route", "flush", "table", "1000"], capture_output=True, timeout=5)
    # rules don't have a flush; delete until none remain
    for _ in range(16):
      if subprocess.run(["sudo", "-n", "ip", "rule", "del", "table", "1000"], capture_output=True, timeout=1).returncode != 0:
        break
    else:
      raise RuntimeError("PPP policy-rule cleanup limit exceeded")
    subprocess.run(["sudo", "-n", "resolvectl", "revert", "ppp0"], capture_output=True, timeout=5)


class Modem:
  def __init__(self):
    self._ppp = PPPSession()
    self._sim_change = False
    self._apn = ""  # blank = network-provided via PCO
    self._roaming_allowed = True
    self._retry = RetryPolicy()
    self._next_recovery_check = 0.0
    self._next_initialization = 0.0
    self._owns_ports = False
    self.running = True
    self.S = INITIAL_STATE.copy()

  @staticmethod
  def _read_param(key):
    try:
      with open(f"/data/params/d/{key}") as f:
        return f.read().strip()
    except FileNotFoundError:
      return ""

  @staticmethod
  def _parse_reg(v: str) -> str:
    try:
      return CREG.get(int(v.split(",")[1].strip('"')), "unknown")
    except (ValueError, IndexError):
      return "unknown"

  @staticmethod
  def _has_modem_manager() -> bool:
    return os.path.isfile("/lib/systemd/system/ModemManager.service")

  def _is_roaming_allowed(self) -> bool:
    if self.S["iccid"].startswith(WEBBING_ICCID_PREFIX):
      return True
    return self._read_param("GsmRoaming") == "1"

  def _publish_state(self, **kwargs):
    self.S.update(kwargs)
    self.S["seconds_since_boot"] = time.monotonic()
    with tempfile.NamedTemporaryFile(mode="w", dir="/dev/shm", delete=False) as f:
      json.dump(self.S, f, indent=2)
    os.chmod(f.name, 0o644)
    os.replace(f.name, STATE_PATH)

  def _at(self, cmd):
    """Send AT command, return response lines. [] on error or if LPA holds port."""
    fd = os.open(AT_LOCK, os.O_CREAT | os.O_RDWR, 0o666)
    try:
      fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
      os.close(fd)
      return []
    try:
      with serial.Serial(AT_PORT, 9600, timeout=5, write_timeout=5) as ser:
        ser.reset_input_buffer()
        ser.write((cmd + "\r").encode())
        return read_at_response(ser)
    except (RuntimeError, TimeoutError, OSError) as e:
      logging.info(f"AT {cmd} failed: {e}")
      return []
    finally:
      fcntl.flock(fd, fcntl.LOCK_UN)
      os.close(fd)

  def _atv(self, cmd, pfx):
    for line in self._at(cmd):
      if line.startswith(pfx) and ":" in line:
        return line.split(":", 1)[1].strip()
    return None

  def _init_at_channel(self) -> bool:
    """Run AT_INIT and confirm ATE0 took effect. Returns False if echo is still on."""
    for c in AT_INIT:
      self._at(c)
    r = self._at("AT+CGMI")
    return bool(r) and not r[0].startswith("AT")

  def _configure_modem(self, modem_version: str):
    if not modem_version.startswith("EG25"):
      return
    cmds = [
      # clear initial EPS bearer APN (some carriers reject the default)
      'AT+CGDCONT=0,"IP",""',

      # SIM hot swap
      'AT+QSIMDET=1,0',
      'AT+QSIMSTAT=1',

      # configure modem as data-centric
      'AT+QNVW=5280,0,"0102000000000000"',
      'AT+QNVFW="/nv/item_files/ims/IMS_enable",00',
      'AT+QNVFW="/nv/item_files/modem/mmode/ue_usage_setting",01',
    ]
    for c in cmds:
      self._at(c)

  def _do_initializing(self):
    now = time.monotonic()
    if now < self._next_initialization or not self._lte_initialized():
      return State.INITIALIZING
    if not os.path.exists(AT_PORT):
      return State.INITIALIZING
    self._next_initialization = now + 15
    if not self._owns_ports:
      if self._has_modem_manager():
        for action in ('mask', 'stop'):
          command = ['sudo', '-n', 'systemctl', action]
          if action == 'mask':
            command.append('--runtime')
          result = subprocess.run([*command, 'ModemManager'], capture_output=True, timeout=10)
          if result.returncode:
            return State.INITIALIZING
      self._owns_ports = True
    logging.info("port found, initializing")
    self._ppp.kill()
    self._ppp.cleanup_routes()

    if not self._init_at_channel():
      logging.warning("AT echo still on, retrying")
      return State.INITIALIZING

    identity = self._read_identity()
    if not identity["iccid"] or not identity["imei"]:
      logging.warning("identity read incomplete, retrying")
      return State.INITIALIZING

    self._configure_modem(identity["modem_version"])

    self.S.update(identity)
    self._apn = self._read_param("GsmApn")
    self._roaming_allowed = self._is_roaming_allowed()
    # blank APN lets the carrier supply one via PCO
    self._at(f'AT+CGDCONT={DIAL_CID},"IP","{self._apn}"')
    logging.info(f"APN '{self._apn or '(network-provided)'}' written to CID {DIAL_CID}, roaming={'on' if self._roaming_allowed else 'off'}")

    self._sim_change = False  # clear since we just re-read identity with the new SIM
    self._publish_state(**identity)
    return State.SEARCHING

  def _read_identity(self):
    def first_line(cmd):
      r = self._at(cmd)
      return r[0].strip() if r else ""

    imei = first_line("AT+CGSN")
    if not (imei.isdigit() and 14 <= len(imei) <= 17):  # 3GPP TS 23.003
      imei = ""

    iccid = (self._atv("AT+QCCID", "+QCCID:") or "").rstrip("F")
    if not iccid.isdigit():
      iccid = ""

    imsi = first_line("AT+CIMI")
    mcc_mnc = imsi[:6] if imsi.isdigit() and len(imsi) >= 6 else ""

    modem_version = first_line("AT+GMR")

    logging.info("modem identity read completed")
    return {"imei": imei, "iccid": iccid, "mcc_mnc": mcc_mnc, "modem_version": modem_version}

  def _do_searching(self):
    new_roaming = self._is_roaming_allowed()
    if new_roaming != self._roaming_allowed:
      logging.info(f"roaming changed: {self._roaming_allowed} -> {new_roaming}")
      self._roaming_allowed = new_roaming

    v = self._atv("AT+CREG?", "+CREG:")
    if not v:
      return self._searching_idle()

    reg = self._parse_reg(v)
    greg = self._parse_reg(self._atv("AT+CGREG?", "+CGREG:") or "")
    logging.debug(f"creg={reg} cgreg={greg} roaming_allowed={self._roaming_allowed}")

    if reg == "roaming" and not self._roaming_allowed:
      self._publish_state(registration=reg)
      return State.SEARCHING

    if reg in ("home", "roaming") and greg in ("home", "roaming"):
      self._publish_state(registration=reg)
      return State.CONNECTING

    if reg != self.S.get("registration"):
      self._publish_state(registration=reg)
    return self._searching_idle()

  def _searching_idle(self):
    if self._sim_change or not os.path.exists(AT_PORT):
      logging.info(f"-> reconnecting (sim_change={self._sim_change} port={os.path.exists(AT_PORT)})")
      return State.DISCONNECTING
    return State.SEARCHING

  def _do_connecting(self):
    if self._sim_change or not os.path.exists(AT_PORT) or self._params_changed():
      return State.DISCONNECTING
    now = time.monotonic()
    if self._ppp._proc is None:
      if not self._retry.ready(now):
        return State.CONNECTING
      if self._try_recovery(now):
        return State.DISCONNECTING
      self._ppp.start()
    self._ppp.drain_progress()
    if self._ppp.has_exited():
      return self._handle_pppd_exit()
    self._publish_state(**self._poll_iface())
    if self.S['connected']:
      self._record_connected(now)
      return State.CONNECTED
    if now - self._ppp.started_at >= 120:
      self._retry.failed(now, "negotiation_timeout")
      self._publish_retry()
      return State.DISCONNECTING
    return State.CONNECTING

  def _handle_pppd_exit(self):
    if self._sim_change or not os.path.exists(AT_PORT):
      return State.DISCONNECTING
    self._ppp.drain_progress()
    code = self._ppp._proc.poll()
    registered = all(self._parse_reg(self._atv(cmd, prefix) or '') in ('home', 'roaming')
                     for cmd, prefix in [('AT+CREG?', '+CREG:'), ('AT+CGREG?', '+CGREG:')])
    early = (code == 16 and self._ppp.progress.authenticated and not self._ppp.progress.address_assigned
             and time.monotonic() - self._ppp.started_at < 60 and registered)
    self._retry.failed(time.monotonic(), "registered_early_hangup" if early else "ppp_exit")
    self._publish_retry(code)
    return State.DISCONNECTING

  def _publish_retry(self, code=None):
    logging.warning("PPP failed: reason=%s count=%d exit=%s", self._retry.reason, self._retry.failures, code)
    self._publish_state(connected=False, ip_address='', retry_count=self._retry.failures,
                        retry_reason=self._retry.reason, ppp_exit_status=code)

  @staticmethod
  def _lte_initialized():
    # Wait inside the modem worker, never gate the driving manager on LTE.
    result = subprocess.run(['systemctl', 'show', 'lte.service', '--property=ActiveState,SubState,Result'],
                            capture_output=True, text=True, timeout=3)
    fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    # LTE is Type=simple + RemainAfterExit: active/running occurs BEFORE its
    # reset/power-on script finishes. Only active/exited is the handoff point.
    return result.returncode == 0 and all(fields.get(k) == v for k, v in
                                         [('ActiveState', 'active'), ('SubState', 'exited'), ('Result', 'success')])

  def _try_recovery(self, now):
    if not self._retry.recovery_due(now) or now < self._next_recovery_check:
      return False
    self._next_recovery_check = now + 60
    registered = all(self._parse_reg(self._atv(cmd, prefix) or '') in ('home', 'roaming')
                     for cmd, prefix in [('AT+CREG?', '+CREG:'), ('AT+CGREG?', '+CGREG:')])
    if not registered or self._sim_change or not self._roaming_allowed and self.S['registration'] == 'roaming':
      self._retry.clear_hangups()
      return False
    helper = '/usr/local/lib/comma-modem/recover.py'
    if not os.path.isfile(helper):
      self._publish_state(recovery_status='unavailable')
      return False
    try:
      result = subprocess.run(['sudo', '-n', '/usr/bin/python3', '-I', helper],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=110)
      status = {0: 'reset_requested', 2: 'deferred', 3: 'budget_exhausted'}.get(result.returncode, 'failed')
    except (OSError, subprocess.TimeoutExpired):
      status = 'failed'
    self._publish_state(recovery_status=status)
    if status in ('reset_requested', 'budget_exhausted', 'failed'):
      self._retry.clear_hangups()
    return status == 'reset_requested'

  def _params_changed(self) -> bool:
    new_apn = self._read_param("GsmApn")
    if new_apn != self._apn:
      logging.info(f"GsmApn changed: '{self._apn}' -> '{new_apn}'")
      return True
    new_roaming = self._is_roaming_allowed()
    if new_roaming != self._roaming_allowed:
      logging.info(f"roaming changed: {self._roaming_allowed} -> {new_roaming}")
      return True
    return False

  def _check_iccid(self, state):
    if state in (State.INITIALIZING, State.DISCONNECTING) or not self.S["iccid"]:
      return
    iccid = (self._atv("AT+QCCID", "+QCCID:") or "").rstrip("F")
    if iccid and iccid != self.S["iccid"]:
      logging.warning("SIM identity changed")
      self._sim_change = True

  def _do_connected(self):
    self._ppp.drain_progress()
    if self._ppp.has_exited():
      return self._handle_pppd_exit()

    if self._sim_change or not os.path.exists(AT_PORT) or self._params_changed():
      return State.DISCONNECTING

    self._poll()
    if not self.S['connected']:
      self._retry.failed(time.monotonic(), 'link_configuration_lost')
      self._publish_retry()
      return State.DISCONNECTING
    self._record_connected(time.monotonic())
    return State.CONNECTED

  def _record_connected(self, now):
    self._retry.stable(now)
    values = dict(retry_count=self._retry.failures, retry_reason=self._retry.reason)
    if self.S['recovery_status'] == 'reset_requested':
      values['recovery_status'] = 'reconnected'
    self._publish_state(**values)

  def _do_disconnecting(self):
    logging.warning("reconnecting")
    self._publish_state(connected=False, ip_address='')
    self._ppp.kill()
    self._ppp.cleanup_routes()
    self._ppp.reset_data_port()
    self._sim_change = False
    return State.INITIALIZING

  def _poll_signal(self) -> dict:
    v = self._atv("AT+CSQ", "+CSQ:")
    if not v:
      return {}
    try:
      rssi = int(v.split(",")[0])
      if rssi == 99:
        return {}
      return {"signal_strength": rssi, "signal_quality": min(100, int(rssi / 31 * 100))}
    except (ValueError, IndexError):
      return {}

  def _poll_operator(self) -> dict:
    v = self._atv("AT+COPS?", "+COPS:")
    if not v:
      return {}
    p = v.split(",")
    out: dict = {}
    try:
      if len(p) >= 3:
        out["operator"] = p[2].strip('"')
      if len(p) >= 4:
        out["network_type"] = NETWORK_TYPE.get(int(p[3]), "unknown")
    except (ValueError, IndexError):
      pass
    return out

  def _poll_band(self) -> dict:
    v = self._atv("AT+QNWINFO", "+QNWINFO:")
    if not v:
      return {}
    info = v.replace('"', '').split(",")
    try:
      if len(info) >= 4:
        return {"band": info[2], "channel": int(info[3])}
    except ValueError:
      pass
    return {}

  def _poll_extra(self) -> dict:
    v = self._atv('AT+QENG="servingcell"', "+QENG:")
    return {"extra": v.replace('"', '')} if v else {}

  def _poll_temps(self) -> dict:
    v = self._atv("AT+QTEMP", "+QTEMP:")
    if not v:
      return {}
    try:
      return {"temperatures": [t for t in (int(x) for x in v.split(",") if x.strip()) if t != 255]}
    except (ValueError, IndexError):
      return {}

  def _poll_iface(self) -> dict:
    try:
      r = subprocess.run(["ip", "-4", "addr", "show", "ppp0"], capture_output=True, text=True, timeout=2)
      ip, peer = "", ""
      for line in r.stdout.splitlines():
        # `inet 10.x.x.x peer 10.64.64.64/32 ...`
        parts = line.strip().split()
        if "inet" in parts:
          i = parts.index("inet")
          ip = parts[i + 1].split("/")[0]
          if "peer" in parts:
            peer = parts[parts.index("peer") + 1].split("/")[0]
          break
      if ip:
        routes = self._ppp.maybe_install_routes(ip, peer)
        dns = routes and (self._ppp.dns_ready or self._ppp.maybe_install_dns(self._read_cellular_dns()))
        return {"ip_address": ip if dns else '', "connected": bool(routes and dns)}
    except Exception:
      pass
    return {"connected": False, "ip_address": ""}

  def _read_cellular_dns(self) -> list[str]:
    v = self._atv(f"AT+CGCONTRDP={DIAL_CID}", "+CGCONTRDP:")
    if not v:
      return []
    # +CGCONTRDP: <cid>,<bearer_id>,<apn>,<local_addr>,<gw_addr>,<dns_prim>,<dns_sec>,...
    fields = [f.strip().strip('"') for f in v.split(",")]
    dns_servers = []
    for d in fields[5:7]:
      try:
        dns_servers.append(str(IPv4Address(d)))
      except (AddressValueError, ValueError):
        pass
    if not dns_servers:
      logging.warning("no valid cellular DNS servers reported")
    return dns_servers

  def _poll_byte_counters(self) -> dict:
    try:
      with open("/sys/class/net/ppp0/statistics/tx_bytes") as f:
        tx = int(f.read().strip())
      with open("/sys/class/net/ppp0/statistics/rx_bytes") as f:
        rx = int(f.read().strip())
    except Exception:
      return {}
    return {"tx_bytes": tx, "rx_bytes": rx}

  def _poll(self):
    s: dict = {}
    for fn in (self._poll_signal, self._poll_operator, self._poll_band,
               self._poll_extra, self._poll_temps, self._poll_iface,
               self._poll_byte_counters):
      s.update(fn())
    if s:
      self._publish_state(**s)

  def run(self):
    logging.info("starting")
    self._publish_state(state=State.INITIALIZING.value)

    state = State.INITIALIZING

    handlers = {
      State.INITIALIZING: self._do_initializing,
      State.SEARCHING: self._do_searching,
      State.CONNECTING: self._do_connecting,
      State.CONNECTED: self._do_connected,
      State.DISCONNECTING: self._do_disconnecting,
    }

    while self.running:
      try:
        self._check_iccid(state)
        prev = state
        state = handlers[state]()
        if state != prev:
          self._publish_state(state=state.value)
          logging.info(f"{prev.value} -> {state.value}")
      except Exception:
        logging.exception(f"error in {state.value}")
        state = State.DISCONNECTING
      time.sleep(STATE_WAIT)

  def stop(self):
    self.running = False
    self._ppp.kill()
    self._ppp.cleanup_routes()
    try:
      os.remove(STATE_PATH)
    except FileNotFoundError:
      pass
    if self._has_modem_manager():
      subprocess.run(["sudo", "-n", "systemctl", "unmask", "--runtime", "ModemManager"], capture_output=True, timeout=10)
      subprocess.run(["sudo", "-n", "systemctl", "start", "ModemManager"], capture_output=True, timeout=10)


def main():
  m = Modem()

  def _sig(*_):
    m.running = False

  signal.signal(signal.SIGINT, _sig)
  signal.signal(signal.SIGTERM, _sig)
  m.run()
  m.stop()


if __name__ == "__main__":
  main()
