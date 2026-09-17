#ifndef VGW_RECOVERY_TRANSPORT_H
#define VGW_RECOVERY_TRANSPORT_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#define VGW_RECOVERY_PDU_MAX 512U
typedef enum { VGW_RX_REJECT=-1, VGW_RX_IGNORE=0, VGW_RX_MORE=1, VGW_RX_COMPLETE=2, VGW_RX_FLOW=3 } vgw_rx_result;
typedef struct {
  uint8_t data[VGW_RECOVERY_PDU_MAX];
  uint32_t started, last;
  uint16_t request_id, length, offset;
  uint8_t expected, block;
  bool configured, active, complete;
} vgw_recovery_rx;
/* Fixed trusted provisioning ID, NOT a command-selected address. No default ID
 * is chosen here. Actual unused ID/bus evidence and TX policy remain required.
 * No CAN transmit/flash/crypto callbacks: completion means only reassembled,
 * never authenticated. Caller must authorize the whole PDU before any action. */
bool vgw_recovery_rx_init(vgw_recovery_rx *, uint16_t request_id);
void vgw_recovery_rx_clear(vgw_recovery_rx *);
vgw_rx_result vgw_recovery_rx_feed(vgw_recovery_rx *, uint32_t id, bool extended,
                                  bool rtr, const uint8_t *, size_t dlc, uint32_t now_ms);
/* Completed buffer is owned until clear(); no new request overwrites it.
 * FLOW asks the rate-limited board transport to send FC(CTS,BS=8,STmin=10ms).
 * Not a TX authorization. Remote timeouts/retransmissions restart the PDU.
 * poll expiry during quiet periods; no watchdog progress depends on traffic. */
bool vgw_recovery_rx_expire(vgw_recovery_rx *, uint32_t now_ms);
#endif
