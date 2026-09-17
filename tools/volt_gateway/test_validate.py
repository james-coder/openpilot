from openpilot.tools.volt_gateway.validate import command, junit_summary, source_hashes
import sys
from types import SimpleNamespace
from openpilot.tools.volt_gateway import validate


def test_source_snapshot_detects_additions_edits_and_removals(tmp_path):
  original = tmp_path / 'first.c'
  original.write_text('one')
  snapshot = source_hashes(tmp_path, tmp_path)
  original.write_text('two')
  assert source_hashes(tmp_path, tmp_path) != snapshot
  original.write_text('one')
  added = tmp_path / 'new.c'
  added.write_text('three')
  assert source_hashes(tmp_path, tmp_path) != snapshot
  added.unlink()
  assert source_hashes(tmp_path, tmp_path) == snapshot
  original.unlink()
  assert source_hashes(tmp_path, tmp_path) != snapshot


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


def test_low_space_never_starts_child(tmp_path,monkeypatch):
  monkeypatch.setattr(validate.shutil,'disk_usage',lambda _:SimpleNamespace(free=1024))
  result=command([sys.executable,'-c','raise SystemExit(42)'],tmp_path,tmp_path/'space.log')
  assert result['status']=='failed' and result['returncode'] is None and result['resource_limit']


def test_scratch_cleanup_even_on_failed_validation(tmp_path,monkeypatch):
  seen=[]
  def fail(output,scratch):
    seen.append(scratch)
    (scratch/'disposable').write_text('test fixture')
    return {'status':'failed'}
  monkeypatch.setattr(validate,'run_with_scratch',fail)
  assert validate.run(tmp_path)['status']=='failed'
  assert len(seen)==1 and not seen[0].exists()
