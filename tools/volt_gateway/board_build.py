"""Link complete loader/application compositions OFF DEVICE; never flashes.

Outputs remain explicitly non-deployable until USB recovery, immutable
provisioning and whole-image hardware validation are completed. No test flash,
host crypto traps or generated production secrets may enter these images.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile

import gcc_arm_none_eabi

from openpilot.tools.volt_gateway import target_crypto, mcuboot_port, usb_history

BOARD=('board_main','board_memory','board_storage','crypto_cooperative','application','authority','update','observe','status_led',
       'white_runtime','white_startup','white_watchdog','white_clock','white_rng','white_board','white_can',
       'white_platform','white_flash','white_safety','recovery_link','recovery_transport','recovery_service',
       'recovery_runtime','recovery_flash')


def linker(origin,length):
  template=(Path(__file__).parent/'firmware/reset_emu.ld').read_text()
  return template.replace('/* NEVER FLASH: reset mechanics harness, not a complete trusted loader. */',
                          '/* Board composition: release gates still apply. */').replace(
                            'ORIGIN = 0x08000000, LENGTH = 256K',f'ORIGIN = {origin:#x}, LENGTH = {length}')


def build(checkout: Path,archive: Path,output: Path,*,usb_repository: Path | None=None):
  output.mkdir(parents=True,exist_ok=False,mode=0o700)
  gcc=Path(gcc_arm_none_eabi.__file__).parent/'toolchain/bin/arm-none-eabi-gcc'
  own=Path(__file__).parent/'firmware'
  report={'production_ready':False,'flashed':False,'mcuboot_commit':mcuboot_port.COMMIT,
          'mbedtls_sha256':target_crypto.ARCHIVE_SHA256,'images':{},
          'gates':['protected USB recovery','verified physical provisioning','whole-image hardware validation']}
  with tempfile.TemporaryDirectory(prefix='voltgw-board-') as temp:
    root=Path(temp)
    usb=None
    if usb_repository is not None:
      usb=usb_history.extract(usb_repository,root/'usb')
    crypto=target_crypto.extract(archive,root)
    raw=subprocess.check_output(['git','-C',str(checkout),'archive',mcuboot_port.COMMIT,'boot/bootutil'])
    with tarfile.open(fileobj=io.BytesIO(raw)) as source:
      source.extractall(root,filter='data')
    boot=root/'boot/bootutil'
    flags=['-std=c11','-Os','-Wall','-Wextra','-Werror','-mcpu=cortex-m4','-mthumb','-mfloat-abi=soft',
           '-ffreestanding','-fno-builtin','-ffunction-sections','-fdata-sections','-fstack-usage',
           '-DMBEDTLS_CONFIG_FILE="crypto_config.h"']
    for path in (own,own/'boot',crypto/'include',boot/'include',boot/'src'):
      flags+=['-I',str(path)]
    if usb is not None:
      flags+=['-I',str(usb),'-I',str(usb/'inc'),'-DVGW_BOARD_USB']
    sources=[crypto/'library'/(n+'.c') for n in target_crypto.LIBRARIES]
    sources+=[boot/'src'/(n+'.c') for n in mcuboot_port.SOURCES]
    sources+=[own/'boot/boot_port.c',own/'crypto.c',own/'white_reset.S']
    sources+=[own/(n+'.c') for n in BOARD if n!='board_main']
    if usb is not None:
      sources+=[own/'white_usb.c',own/'usb_recovery.c']
    objects=[]
    for i,path in enumerate(sources):
      obj=output/f'{i}-{path.stem}.o'
      extra=['-Wno-unused-parameter'] if path.is_relative_to(boot) else []
      if path.name=='white_usb.c':
        extra+=['-Wno-unused-parameter','-Wno-sign-compare','-Wno-attributes']
      subprocess.run([str(gcc),*flags,*extra,'-c',str(path),'-o',str(obj)],check=True,capture_output=True,text=True)
      objects.append(obj)
    for name,slot,origin,length in [('loader',None,0x08000000,0x20000),('A',0,0x08040200,0x5fa00),('B',1,0x080a0200,0x5fa00)]:
      script=output/f'{name}.ld'
      script.write_text(linker(origin,length))
      entry=output/f'{name}-main.o'
      extra=[] if slot is None else [f'-DVGW_APPLICATION_SLOT={slot}']
      identity=hashlib.sha256(name.encode()+mcuboot_port.COMMIT.encode()+target_crypto.ARCHIVE_SHA256.encode())
      for path in sorted(own.rglob('*')):
        if path.is_file() and path.suffix in ('.h','.c','.S','.ld'):
          identity.update(str(path.relative_to(own)).encode()+b'\0'+path.read_bytes())
      identity.update(b'USB' if usb is not None else b'NO-USB')
      identity.update(Path(__file__).read_bytes())
      if usb is not None:
        identity.update(usb_history.COMMIT.encode())
        identity.update(Path(usb_history.__file__).read_bytes())
      extra+=['-DVGW_BUILD_ID={'+','.join(str(b) for b in identity.digest())+'}']
      subprocess.run([str(gcc),*flags,*extra,'-c',str(own/'board_main.c'),'-o',str(entry)],check=True,capture_output=True,text=True)
      binary=output/f'NOT_RELEASED-{name}.elf'
      subprocess.run([str(gcc),*flags,'-nostdlib','-T',str(script),'-Wl,--no-undefined,--gc-sections,--build-id=none',
                      '-Wl,--wrap=mbedtls_ecdsa_verify,--wrap=mbedtls_ecdsa_read_signature',
                      '-Wl,-Map='+str(output/f'{name}.map'),*map(str,objects),str(entry),'-o',str(binary)],
                     check=True,capture_output=True,text=True)
      symbols=subprocess.check_output(['nm','--defined-only',str(binary)],text=True)
      forbidden=('vgw_test_','vgw_emu_','vgw_boot_emu_','vgw_debug_','vgw_guard_test','mbedtls_ecdsa_sign')
      if any(n in symbols for n in forbidden):
        raise ValueError('test/private-signing code in board image')
      report['images'][name]={'sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
        'size':subprocess.check_output([str(gcc.with_name('arm-none-eabi-size')),str(binary)],text=True),
        'undefined':subprocess.check_output(['nm','-u',str(binary)],text=True)}
  (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
  return report


def main():
  parser=argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--checkout',type=Path,required=True)
  parser.add_argument('--archive',type=Path,default=target_crypto.archive_path())
  parser.add_argument('--output',type=Path,required=True)
  parser.add_argument('--usb-repository',type=Path)
  args=parser.parse_args()
  try:
    print(json.dumps(build(args.checkout,args.archive,args.output,usb_repository=args.usb_repository),indent=2))
  except subprocess.CalledProcessError as error:
    print(error.stderr)
    raise


if __name__=='__main__':
  main()
