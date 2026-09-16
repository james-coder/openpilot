import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location('checker', ROOT / 'check_usb_binding_log.py')
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


def test_actual_evidence_and_corruptions():
  policy = (ROOT / 'evidence/usb-bindings-20260916.log').read_text()
  controls = (ROOT / 'evidence/usb-positive-controls-20260916.log').read_text()
  assert checker.validate(policy, controls) == 40
  for bad in [policy.replace('BINDING_LAB_DONE', ''), policy + 'Kernel panic',
              policy.replace('"drivers": []', '"drivers": ["usbhid"]', 1),
              policy.replace('"status": "pass"', '"status": "fail"', 1),
              policy.replace('cycle_0_modified', 'cycle_1_modified', 1),
              policy.replace('"option", ', '', 1)]:
    with pytest.raises(ValueError):
      checker.validate(bad, controls)
  for bad in ['', controls.replace('"usbhid"', '"missing"'), controls + 'WARNING:']:
    with pytest.raises(ValueError):
      checker.validate(policy, bad)
