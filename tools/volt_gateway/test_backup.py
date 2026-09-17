import struct

import pytest

from openpilot.tools.volt_gateway.backup import dfuse_geometry, linux_read_command, read_command, verify_files, verify_reads


def test_read_command_has_no_write_or_unprotect_path():
  args = read_command(r'C:\Program Files\ST\STM32_Programmer_CLI.exe', '123456789ABC', 1048576, r'C:\private\read-1.bin')
  assert args[1:] == ['-c', 'port=USB1', 'sn=123456789ABC', '-u', '0x08000000', '1048576', r'C:\private\read-1.bin']


@pytest.mark.parametrize('serial', ['', 'x', '123456789ABC -e all', '123456789ABC\n-rdu'])
def test_serial_not_shell_or_cli_arguments(serial):
  with pytest.raises(ValueError):
    read_command(r'C:\STM32_Programmer_CLI.exe', serial, 1048576, r'C:\backup.bin')


@pytest.mark.parametrize('output', ['relative.bin', 'C:\\a\\..\\backup.bin', 'C:\\backup.bin" -e all', 'C:\\backup.hex',
                                   r'\\server\share\backup.bin', 'C:\\backup:stream.bin'])
def test_invalid_output(output):
  with pytest.raises(ValueError):
    read_command(r'C:\STM32_Programmer_CLI.exe', '123456789ABC', 1048576, output)


def test_double_read_and_vector_gate():
  data = struct.pack('<II', 0x20020000, 0x08000101) + bytes(1048576 - 8)
  result = verify_reads(data, data, len(data))
  assert result['two_reads_identical']
  assert not result['restoration_tested']
  for second in (data[:-1], data[:-1] + b'1'):
    with pytest.raises(ValueError):
      verify_reads(data, second, len(data))
  with pytest.raises(ValueError):
    verify_reads(bytes(len(data)), bytes(len(data)), len(data))
  with pytest.raises(ValueError):
    verify_reads(b'\xff' * len(data), b'\xff' * len(data), len(data))


def test_oversize_rejected_before_file_access(tmp_path):
  with pytest.raises(ValueError):
    verify_files(tmp_path / 'missing', tmp_path / 'missing-again', 2**40)


def test_linux_upload_only_and_geometry(tmp_path):
  assert dfuse_geometry('@Internal Flash  /0x08000000/04*016Kg,01*064Kg,011*128Kg') == 1572864
  assert dfuse_geometry('@Internal Flash  /0x08000000/04*016Kg,01*064Kg,07*128Kg') == 1048576
  args = linux_read_command('365236793036', 1572864, tmp_path / 'read.bin')
  assert '-U' in args and '-D' not in args and '-R' not in args
  assert args[args.index('-s') + 1] == '0x08000000:1572864'
  with pytest.raises(ValueError):
    dfuse_geometry('@Option Bytes /0x1fff0000/01*016Kg')
  with pytest.raises(ValueError):
    linux_read_command('365236793036:unprotect', 1572864, tmp_path / 'read.bin')
