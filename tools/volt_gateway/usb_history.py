"""Exact historical White USB source extraction with fail-closed local patches.

Only build-directory files are written. No device access or downloaded tools.
"""
import io
from pathlib import Path
import subprocess
import tarfile

COMMIT='4e85803018ba8cfe20d7e1eb47bc7171ee38faf4'


def extract(repository: Path,root: Path):
  raw=subprocess.check_output(['git','--git-dir='+str(repository),'archive',COMMIT,'board/inc','board/drivers/usb.h','board/drivers/drivers.h','LICENSE'])
  with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
    archive.extractall(root,filter='data')
  path=root/'board/drivers/usb.h'
  text=path.read_text()
  replacements={
    'uint16_t ep0_txlen = 0;':
      'uint16_t ep0_txlen = 0;\nstatic bool ep0_zlp=false;',
    'void usb_setup() {':
      'void usb_setup() {\n  ep0_txlen=0; ep0_txdata=NULL; ep0_zlp=false; USBx_DEVICE->DIEPEMPMSK &= ~1U;'+
      '\n#ifdef VGW_RAM_PROBE\n  vgw_usb_probe_setup(&setup);\n#endif',
    'uint16_t wplen = min(len, 0x40);':
      'ep0_zlp=(len!=0 && (len%64)==0 && len<setup.b.wLength.w);\n  uint16_t wplen = min(len, 0x40);',
    'USB_WritePacket(binary_object_store_desc, min(sizeof(binary_object_store_desc), setup.b.wLength.w), 0);':
      'USB_WritePacket_EP0(binary_object_store_desc, min(sizeof(binary_object_store_desc), setup.b.wLength.w));',
    'if (USBx_INEP(0)->DIEPINT & USB_OTG_DIEPMSK_ITTXFEMSK) {':
      'if (USBx_INEP(0)->DIEPINT & USB_OTG_DIEPINT_XFRC) {\n      if (ep0_txlen==0 && ep0_zlp) { ep0_zlp=false; USB_WritePacket(NULL,0,0); }',
    'uint8_t numpacket = (len+(MAX_RESP_LEN-1))/MAX_RESP_LEN;':
      'uint8_t numpacket = len ? (len+(MAX_RESP_LEN-1))/MAX_RESP_LEN : 1;',
    'USB_WritePacket(configuration_desc, min(sizeof(configuration_desc), setup.b.wLength.w), 0);':
      'USB_WritePacket_EP0(configuration_desc, min(sizeof(configuration_desc), setup.b.wLength.w));',
    'USB_WritePacket(resp, min(resp_len, setup.b.wLength.w), 0);':
      'USB_WritePacket_EP0(resp, min(resp_len, setup.b.wLength.w));',
    'while ((USBx->GRSTCTL & USB_OTG_GRSTCTL_AHBIDL) == 0);':
      'for (unsigned budget=0; (USBx->GRSTCTL & USB_OTG_GRSTCTL_AHBIDL)==0; budget++) { if (budget==100000U) { vgw_usb_fault(); return; } }',
    'while ((USBx->GRSTCTL & USB_OTG_GRSTCTL_CSRST) == USB_OTG_GRSTCTL_CSRST);':
      'for (unsigned budget=0; USBx->GRSTCTL & USB_OTG_GRSTCTL_CSRST; budget++) { if (budget==100000U) { vgw_usb_fault(); return; } }',
    'while ((USBx->GRSTCTL & USB_OTG_GRSTCTL_TXFFLSH) == USB_OTG_GRSTCTL_TXFFLSH);':
      'for (unsigned budget=0; USBx->GRSTCTL & USB_OTG_GRSTCTL_TXFFLSH; budget++) { if (budget==100000U) { vgw_usb_fault(); return; } }',
    'while ((USBx->GRSTCTL & USB_OTG_GRSTCTL_RXFFLSH) == USB_OTG_GRSTCTL_RXFFLSH);':
      'for (unsigned budget=0; USBx->GRSTCTL & USB_OTG_GRSTCTL_RXFFLSH; budget++) { if (budget==100000U) { vgw_usb_fault(); return; } }',
    'USB_ReadPacket(&usbdata, len);':
      'if (len>64) { vgw_usb_fault(); return; } USB_ReadPacket(&usbdata, len);',
    'USB_ReadPacket(&setup, 8);':
      'if (((rxst & USB_OTG_GRXSTSP_BCNT)>>4)!=8) { vgw_usb_fault(); return; } USB_ReadPacket(&setup, 8);',
    'USBx_DFIFO(ep) = *((__attribute__((__packed__)) uint32_t *)src);':
      'uint32_t word=0; for (unsigned j=0;j<4 && i*4+j<len;j++) word|=(uint32_t)src[4*i+j]<<(8*j); USBx_DFIFO(ep)=word;',
    'for (i = 0; i < count32b; i++, src += 4)':
      'for (i = 0; i < count32b; i++)',
  }
  for old,new in replacements.items():
    if text.count(old)!=1:
      raise ValueError('historical USB source does not match reviewed patch')
    text=text.replace(old,new)
  # Both historical enable sites (initialization and the IRQ tail) must remain
  # disabled now that CAN-only NVIC interrupts are globally enabled.
  if text.count('NVIC_EnableIRQ(OTG_FS_IRQn);')!=2:
    raise ValueError('unexpected historical USB interrupt enable sites')
  text=text.replace('NVIC_EnableIRQ(OTG_FS_IRQn);','NVIC_DisableIRQ(OTG_FS_IRQn); /* gateway polling-only USB */')
  path.write_text(text)
  return root/'board'
