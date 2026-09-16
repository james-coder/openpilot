from openpilot.tools.volt_gateway.validate import command, junit_summary
import sys


def test_skip_is_not_pass(tmp_path):
  report = tmp_path / 'tests.xml'
  report.write_text('<testsuites><testsuite><testcase name="emulation"><skipped/></testcase></testsuite></testsuites>')
  assert junit_summary(report)['status'] == 'incomplete'
  assert junit_summary(report)['passed'] == 0


def test_empty_and_missing_report_fail(tmp_path):
  report = tmp_path / 'tests.xml'
  assert junit_summary(report)['status'] == 'failed'
  report.write_text('<testsuites/>')
  assert junit_summary(report)['status'] == 'failed'


def test_failure_is_not_pass(tmp_path):
  report = tmp_path / 'tests.xml'
  report.write_text('<testsuites><testsuite><testcase><failure/></testcase><testcase/></testsuite></testsuites>')
  assert junit_summary(report)['failed'] == 1
  assert junit_summary(report)['passed'] == 1
  assert junit_summary(report)['status'] == 'failed'


def test_child_failure_and_timeout_reported(tmp_path):
  failed = command([sys.executable, '-c', 'raise SystemExit(3)'], tmp_path, tmp_path / 'failed.log')
  assert failed['status'] == 'failed' and failed['returncode'] == 3
  timed = command([sys.executable, '-c', 'import time; time.sleep(10)'], tmp_path, tmp_path / 'timeout.log', timeout=.1)
  assert timed['status'] == 'failed' and timed['timed_out']
