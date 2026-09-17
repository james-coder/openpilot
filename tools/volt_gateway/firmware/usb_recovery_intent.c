#include "usb_recovery_intent.h"
#define MAGIC 0x56524732U
bool vgw_usb_recovery_present(const vgw_white_mmio *io) {
  return io && io->read32 && io->read32(io->ctx,VGW_USB_RECOVERY_INTENT)==MAGIC &&
    io->read32(io->ctx,VGW_USB_RECOVERY_INTENT+4)==~MAGIC &&
    (io->read32(io->ctx,0x40023874U)&(1U<<28));
}
/* White revC historical USB_POWER_CLIENT: PB2 low, PA13 high. This must
 * happen even when direct ROM entry deliberately skips USB enumeration. */
bool vgw_usb_recovery_client(const vgw_white_mmio *io) {
  if (!io || !io->read32 || !io->write32) return false;
  io->write32(io->ctx,0x40023830U,io->read32(io->ctx,0x40023830U)|3U);
  io->write32(io->ctx,0x40020018U,1U<<13);
  io->write32(io->ctx,0x40020418U,1U<<18);
  io->write32(io->ctx,0x40020000U,(io->read32(io->ctx,0x40020000U)&~(3U<<26))|(1U<<26));
  io->write32(io->ctx,0x40020400U,(io->read32(io->ctx,0x40020400U)&~(3U<<4))|(1U<<4));
  return (io->read32(io->ctx,0x40023830U)&3U)==3U &&
    (io->read32(io->ctx,0x40020000U)&(3U<<26))==(1U<<26) &&
    (io->read32(io->ctx,0x40020400U)&(3U<<4))==(1U<<4) &&
    (io->read32(io->ctx,0x40020014U)&(1U<<13)) && !(io->read32(io->ctx,0x40020414U)&4U);
}
void vgw_usb_recovery_trace(const vgw_white_mmio *io,uint32_t phase) {
  if (!io || !io->write32) return;
  io->write32(io->ctx,VGW_USB_RECOVERY_TRACE,0);
  io->write32(io->ctx,VGW_USB_RECOVERY_TRACE+4,phase);
  io->write32(io->ctx,VGW_USB_RECOVERY_TRACE+8,~phase);
  io->write32(io->ctx,VGW_USB_RECOVERY_TRACE,0x56524431U);
}
unsigned vgw_usb_recovery_report(const vgw_white_mmio *io,uint8_t out[16]) {
  if (!io || !io->read32 || !out) return 0;
  uint32_t phase=io->read32(io->ctx,VGW_USB_RECOVERY_TRACE+4);
  bool valid=io->read32(io->ctx,VGW_USB_RECOVERY_TRACE)==0x56524431U &&
    io->read32(io->ctx,VGW_USB_RECOVERY_TRACE+8)==~phase && phase>=1 && phase<=7;
  uint32_t words[4]={1,valid ? phase : 0,io->read32(io->ctx,0x40023874U),io->read32(io->ctx,0xe000ed28U)};
  for (unsigned i=0;i<4;i++) for (unsigned j=0;j<4;j++) out[4*i+j]=(uint8_t)(words[i]>>(24-8*j));
  return 16;
}
bool vgw_usb_recovery_mark(const vgw_white_mmio *io) {
  if (!io || !io->read32 || !io->write32) return false;
  io->write32(io->ctx,VGW_USB_RECOVERY_INTENT,0);
  io->write32(io->ctx,VGW_USB_RECOVERY_INTENT+4,~MAGIC);
  io->write32(io->ctx,VGW_USB_RECOVERY_INTENT,MAGIC);
  return io->read32(io->ctx,VGW_USB_RECOVERY_INTENT)==MAGIC &&
    io->read32(io->ctx,VGW_USB_RECOVERY_INTENT+4)==~MAGIC;
}
bool vgw_usb_recovery_take(const vgw_white_mmio *io) {
  if (!io || !io->read32 || !io->write32) return false;
  uint32_t a=io->read32(io->ctx,VGW_USB_RECOVERY_INTENT);
  uint32_t b=io->read32(io->ctx,VGW_USB_RECOVERY_INTENT+4);
  uint32_t reset=io->read32(io->ctx,0x40023874U);
  io->write32(io->ctx,VGW_USB_RECOVERY_INTENT,0);
  io->write32(io->ctx,VGW_USB_RECOVERY_INTENT+4,0);
  return a==MAGIC && b==~MAGIC && (reset&(1U<<28)) &&
    !io->read32(io->ctx,VGW_USB_RECOVERY_INTENT) && !io->read32(io->ctx,VGW_USB_RECOVERY_INTENT+4);
}
