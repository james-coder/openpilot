#ifndef VGW_WHITE_USB_H
#define VGW_WHITE_USB_H
#include "white_startup.h"
#include "recovery_service.h"
#include "recovery_link.h"
/* Local USB is a separate explicitly selected transport, never a raw CAN TX
 * API. Enumerating USB latches local ownership until reset. */
bool vgw_white_usb_init(vgw_white_startup *,bool recovery_window);
void vgw_white_usb_stop(void);
bool vgw_white_usb_poll(void);
bool vgw_white_usb_owned(void);
bool vgw_white_usb_recovery_requested(void);
void vgw_white_usb_indication(const uint8_t[7]);
bool vgw_white_usb_dispatch(vgw_recovery_service *,uint64_t elapsed_ms);
bool vgw_white_usb_send(void *,const uint8_t[8]);
bool vgw_white_usb_telemetry_send(void *,const uint8_t[8]);
#endif
