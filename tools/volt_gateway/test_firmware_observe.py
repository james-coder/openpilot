from pathlib import Path
import subprocess


def test_native_observer_with_sanitizers(tmp_path):
  directory = Path(__file__).parent / 'firmware'
  exe = tmp_path / 'observe-test'
  subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O1', '-g', '-fsanitize=address,undefined',
                  '-fno-omit-frame-pointer', str(directory / 'observe.c'), str(directory / 'test_observe.c'), '-o', str(exe)],
                 check=True, capture_output=True, text=True)
  result = subprocess.run([str(exe)], check=True, capture_output=True, text=True, timeout=30)
  assert 'observer tests passed' in result.stdout
