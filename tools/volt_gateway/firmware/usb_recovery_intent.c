#include "usb_recovery_intent.h"
#define MAGIC 0x56524732U
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
