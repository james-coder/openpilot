#ifndef VGW_USB_RECOVERY_INTENT_H
#define VGW_USB_RECOVERY_INTENT_H
#include "white_board.h"
/* Fixed, linker-reserved SRAM mailbox survives SYSRESETREQ, not a wire address.
 * Entry is USB-local; this marker is NOT firmware authentication. */
#define VGW_USB_RECOVERY_INTENT 0x2001bfe0U
#define VGW_USB_RECOVERY_TRACE 0x2001bfc0U
bool vgw_usb_recovery_mark(const vgw_white_mmio *);
bool vgw_usb_recovery_take(const vgw_white_mmio *);
bool vgw_usb_recovery_present(const vgw_white_mmio *);
bool vgw_usb_recovery_client(const vgw_white_mmio *);
void vgw_usb_recovery_trace(const vgw_white_mmio *,uint32_t);
unsigned vgw_usb_recovery_report(const vgw_white_mmio *,uint8_t out[16]);
#endif
