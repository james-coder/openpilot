"""Build a volatile, CAN-disabled USB probe. No hardware access in this builder.

Loaded only at 0x20004000, beyond the ROM's first 16 KiB SRAM workspace.
All ELF load addresses must be SRAM; flash programming is not an operation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from elftools.elf.elffile import ELFFile
import gcc_arm_none_eabi

from openpilot.tools.volt_gateway import usb_history


def build(repository,output,*,recovery=False,listen=False,debug=False):
  if recovery and listen:
    raise ValueError('separate recovery and listen probes required')
  if debug and (recovery or listen):
    raise ValueError('DEBUG memory monitor must use the CAN-disabled probe')
  output.mkdir(mode=0o700,parents=True,exist_ok=False)
  own=Path(__file__).parent/'firmware'
  gcc=Path(gcc_arm_none_eabi.__file__).parent/'toolchain/bin/arm-none-eabi-gcc'
  script=(own/'reset_emu.ld').read_text().replace('ORIGIN = 0x08000000, LENGTH = 256K','ORIGIN = 0x20004000, LENGTH = 48K')
  script=script.replace('ORIGIN = 0x20000000, LENGTH = 128K','ORIGIN = 0x20010000, LENGTH = 64K')
  script=script.replace('__bss_end <= __stack_top - 8192','__bss_end <= 0x2001b000')
  (output/'probe.ld').write_text(script)
  with tempfile.TemporaryDirectory(prefix='voltgw-probe-') as temporary:
    history=usb_history.extract(repository,Path(temporary))
    flags=['-std=c11','-g3','-Os','-Wall','-Wextra','-Werror','-mcpu=cortex-m4','-mthumb','-mfloat-abi=soft',
           '-ffreestanding','-fno-builtin','-ffunction-sections','-fdata-sections','-DVGW_RAM_PROBE',
           '-I',str(own),'-I',str(history),'-I',str(history/'inc')]
    names=('ram_probe','board_memory','status_led','white_startup','white_clock','white_watchdog','white_board','white_platform',
           'white_safety','white_usb','recovery_link','recovery_transport')
    if listen:
      names=('listen_probe',*names[1:],'white_rng','white_can')
    if debug:
      flags+=['-DDEBUG=1']
      names+=('debug_memory',)
    if recovery:
      flags+=['-DVGW_PROBE_RECOVERY']
      names+=('usb_recovery',)
    objects=[]
    for name in (*names,'white_reset'):
      source=own/(name+('.S' if name=='white_reset' else '.c'))
      obj=output/(name+'.o')
      extra=['-Wno-unused-parameter','-Wno-sign-compare','-Wno-attributes'] if name=='white_usb' else []
      subprocess.run([str(gcc),*flags,*extra,'-c',str(source),'-o',str(obj)],check=True,capture_output=True,text=True)
      objects.append(obj)
    elf=output/'NEVER_FLASH-probe.elf'
    subprocess.run([str(gcc),*flags,'-nostdlib','-T',str(output/'probe.ld'),'-Wl,--gc-sections,--no-undefined',
                    *map(str,objects),'-o',str(elf)],check=True,capture_output=True,text=True)
  with elf.open('rb') as stream:
    parsed=ELFFile(stream)
    segments=[(s['p_paddr'],s.data()) for s in parsed.iter_segments() if s['p_type']=='PT_LOAD' and s['p_filesz']]
    names={s.name for s in parsed.get_section_by_name('.symtab').iter_symbols()}
  forbidden=('vgw_white_flash_program','vgw_white_usb_dispatch','vgw_white_can_response')
  if not listen:
    forbidden+=('vgw_white_can_init','vgw_white_watchdog_start')
  if any(n in names for n in forbidden):
    raise ValueError('active CAN/flash/watchdog/dispatch code retained in passive probe')
  start=0x20004000
  segments=[(max(a,start),data[max(0,start-a):]) for a,data in segments if a+len(data)>start]
  if any(a<start or a+len(data)>0x2000fff0 for a,data in segments):
    raise ValueError('probe load segment outside dedicated SRAM range')
  end=max(a+len(data) for a,data in segments)
  blob=bytearray(end-start)
  for a,data in segments:
    blob[a-start:a-start+len(data)]=data
  (output/'NEVER_FLASH-probe.bin').write_bytes(blob)
  report={'sha256':hashlib.sha256(blob).hexdigest(),'size':len(blob),'load_address':start,'flash_writes':False,
          'history':usb_history.COMMIT,'timeout_seconds':60 if listen else 130 if recovery else 120,
          'recovery_window':recovery,'listen_only_selftest':listen,'debug_memory':debug}
  (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
  return report


def main():
  parser=argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--repository',type=Path,required=True)
  parser.add_argument('--output',type=Path,required=True)
  parser.add_argument('--recovery',action='store_true',help='execute the real cold USB recovery window from SRAM first')
  parser.add_argument('--listen',action='store_true',help='USB-only bench: test watchdog/RNG and silent CAN controllers, no CAN TX')
  parser.add_argument('--debug',action='store_true',help='DANGEROUS: DEBUG=1 arbitrary USB memory access, CAN-disabled bench RAM only')
  args=parser.parse_args()
  try:
    print(json.dumps(build(args.repository,args.output,recovery=args.recovery,listen=args.listen,debug=args.debug),indent=2))
  except subprocess.CalledProcessError as error:
    print(error.stderr)
    raise


if __name__=='__main__':
  main()
