#ifndef VGW_USB_RECOVERY_INTENT_H
#define VGW_USB_RECOVERY_INTENT_H
#include "white_board.h"
/* Fixed, linker-reserved SRAM mailbox survives SYSRESETREQ, not a wire address.
 * Entry is USB-local; this marker is NOT firmware authentication. */
#define VGW_USB_RECOVERY_INTENT 0x2001bfe0U
bool vgw_usb_recovery_mark(const vgw_white_mmio *);
bool vgw_usb_recovery_take(const vgw_white_mmio *);
#endif
