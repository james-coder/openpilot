"""Assemble an owner-signed, private initial bench image. No hardware access.

This includes provisioning secrets: it is NOT a public update package and must
never be copied to the comma or Git. Both initial applications are confirmed,
so first USB-only boot needs no vehicle/power interlock metadata writes.
"""
import argparse
import getpass
import hashlib
import io
import json
from pathlib import Path
import resource
import struct
import sys
import zlib

from elftools.elf.elffile import ELFFile

from openpilot.tools.volt_gateway import boot_image, provisioning
from openpilot.tools.volt_gateway.operator import PRIVATE_ROOT, bounded_read, load_key, private_directory, write_new


def payload(raw: bytes, start: int, capacity: int) -> bytes:
  elf=ELFFile(io.BytesIO(raw))
  if elf.elfclass!=32 or elf['e_machine']!='EM_ARM' or elf['e_type']!='ET_EXEC' or not elf.little_endian:
    raise ValueError('expected linked ARM32 executable')
  vectors=elf.get_section_by_name('.vectors')
  symbols=elf.get_section_by_name('.symtab')
  if vectors is None or vectors['sh_addr']!=start or symbols is None:
    raise ValueError('wrong image vector address or missing symbols')
  names={symbol.name for symbol in symbols.iter_symbols()}
  if any(name.startswith(('vgw_test_','vgw_emu_','vgw_boot_emu_','vgw_debug_')) for name in names):
    raise ValueError('test harness is not board firmware')
  if not {'vgw_loader_main','vgw_white_usb_init','vgw_white_physical_reset'}<=names:
    raise ValueError('required board/recovery implementation absent')
  segments=[]
  for segment in elf.iter_segments():
    if segment['p_type']!='PT_LOAD' or not segment['p_filesz']:
      continue
    address,data=segment['p_paddr'],segment.data()
    if address<start:
      # ld includes ELF/program headers in the app's first page, before its
      # +512 vector origin. These are not allocated firmware sections and are
      # replaced by the signed MCUboot header, never copied into the payload.
      if (address!=start-512 or segment['p_offset']!=0 or data[:4]!=b'\x7fELF' or
          any(section['sh_flags']&2 and address<=section['sh_addr']<start for section in elf.iter_sections())):
        raise ValueError('unexpected bytes before application vectors')
      data=data[start-address:]
      address=start
    segments.append((address,data))
  segments.sort()
  if not segments or segments[0][0]!=start:
    raise ValueError('image load origin')
  end=start
  for address,data in segments:
    if address<end or address+len(data)>start+capacity:
      raise ValueError('overlapping/out-of-range load segment')
    end=address+len(data)
  result=bytearray(b'\xff'*(end-start))
  for address,data in segments:
    result[address-start:address-start+len(data)]=data
  sp,entry=struct.unpack_from('<II',result)
  if sp!=0x20020000 or not entry&1 or not start+8<=entry&~1<end:
    raise ValueError('invalid initial vectors')
  return bytes(result)


def assemble(loader: bytes, applications: tuple[bytes,bytes], record: bytes, key) -> bytes:
  if len(record)!=256 or record[:8]!=b'VGWCFG1\0' or zlib.crc32(record[:252])!=int.from_bytes(record[252:],'big'):
    raise ValueError('provisioning CRC/layout')
  public=key.public_key().export_key(format='DER')
  if record[148:239]!=public or not key.has_private() or record[20:52]!=provisioning.LAYOUT:
    raise ValueError('signer/provisioning/layout mismatch')
  expected,_=provisioning.passive_record(record[8:20],record[116:148],public,
    swcan=record[239],divider=int.from_bytes(record[248:252],'big'))
  if record!=expected:
    raise ValueError('first bench image must use canonical all-CAN-listen-only provisioning')
  if not 472<=len(loader)<=0x20000 or len(applications)!=2:
    raise ValueError('loader/application bounds')
  sp,entry=struct.unpack_from('<II',loader)
  if sp!=0x20020000 or not entry&1 or not 0x08000008<=entry&~1<0x08000000+len(loader):
    raise ValueError('loader vectors')
  flash=bytearray(b'\xff'*boot_image.FLASH_SIZE)
  flash[:len(loader)]=loader
  flash[0x20000:0x20100]=record
  for slot,application in enumerate(applications):
    version=2-slot  # deterministic initial A, with a separately linked B fallback
    signed=boot_image.sign(key,application,record[8:20],record[20:52],slot,version)
    boot_image.verify(signed,public,record[8:20],record[20:52],slot,version)
    offset=boot_image.SLOTS[slot]
    flash[offset:offset+len(signed)]=signed
    end=offset+boot_image.SLOT_SIZE
    flash[end-32]=flash[end-24]=1
    flash[end-16:end]=boot_image.MAGIC
  return bytes(flash)


def main():
  resource.setrlimit(resource.RLIMIT_CORE,(0,0))
  parser=argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--build',type=Path,required=True)
  parser.add_argument('--provisioning',type=Path,required=True)
  parser.add_argument('--output',type=Path,required=True)
  args=parser.parse_args()
  if not sys.stdin.isatty() or args.output.exists():
    parser.error('local interactive terminal and a new private output directory required')
  record=bounded_read(args.provisioning,256,private=True)
  if len(record)!=256:
    parser.error('provisioning record size')
  report=json.loads(bounded_read(args.build/'report.json',32768))
  parts=[]
  for name,start,size in [('loader',0x08000000,0x20000),('A',0x08040200,0x5fa00),('B',0x080a0200,0x5fa00)]:
    raw=bounded_read(args.build/f'NOT_RELEASED-{name}.elf',2*1024*1024)
    if report['images'][name]['sha256']!=hashlib.sha256(raw).hexdigest():
      parser.error('board build digest mismatch')
    parts.append(payload(raw,start,size))
  key=load_key(PRIVATE_ROOT/record[8:20].hex()/'firmware-key.pem',getpass.getpass('Unlock local key for initial bench image: '))
  image=assemble(parts[0],tuple(parts[1:]),record,key)
  private_directory(args.output)
  write_new(args.output/'factory-flash.bin',image)
  result={'device':record[8:20].hex(),'size':len(image),'sha256':hashlib.sha256(image).hexdigest(),
          'contains_secrets':True,'can_transmit_enabled':False,'flashed':False,'production_ready':False}
  write_new(args.output/'manifest.json',(json.dumps(result,indent=2)+'\n').encode())
  print('Owner-signed private bench image assembled. No hardware changed; this does not waive deployment gates.')


if __name__=='__main__':
  main()
