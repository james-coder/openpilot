/* The driver included below is extracted from the exact historical White
 * source by usb_history.py, with explicit boundedness patches. No CAN, legacy
 * flashing, ESP, UART or unrestricted vendor-command callbacks are included. */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#define STM32F413xx
#define STM32F4
#define PANDA
#define USB_VID 0xbbaa
#define USB_PID 0xddcc
#define MAX_RESP_LEN 64
#define min(a,b) ((a)<(b) ? (a) : (b))
#include "stm32f413xx.h"
#include "drivers/drivers.h"
#include "white_usb.h"
static bool failed,owned,window,recover;
static vgw_white_startup *startup;
static vgw_recovery_link link;
static uint8_t queue[16][8],head,tail,count;
int puts(const char *s) { (void)s; return 0; }
void puth(unsigned int i) { (void)i; }
void hexdump(const void *p,int n) { (void)p; (void)n; }
static void vgw_usb_fault(void) { failed=true; }
#ifdef VGW_RAM_PROBE
static void vgw_usb_probe_setup(const USB_Setup_TypeDef *p) {
  volatile uint32_t *trace=(volatile uint32_t *)0x2001c010U;
  uint32_t n=trace[0];
  if (n<32) {
    const uint8_t *raw=(const uint8_t *)p;
    for (unsigned i=0;i<2;i++) trace[4+2*n+i]=(uint32_t)raw[4*i]|((uint32_t)raw[4*i+1]<<8)|
      ((uint32_t)raw[4*i+2]<<16)|((uint32_t)raw[4*i+3]<<24);
    trace[0]=n+1;
  }
}
#endif
#include "drivers/usb.h"

void usb_cb_enumeration_complete(void) { owned=true; }
int usb_cb_control_msg(USB_Setup_TypeDef *p,uint8_t *out,int hardwired) {
  if (!hardwired || !p || !out) return 0;
#ifdef VGW_RAM_PROBE
  extern unsigned vgw_ram_probe_report(uint8_t out[64]);
  if (p->b.bmRequestType==0xc0 && p->b.bRequest==0xd0) return (int)vgw_ram_probe_report(out);
#endif
  if (p->b.bmRequestType==0xc0 && p->b.bRequest==0xd6) {
#ifdef VGW_RAM_PROBE
    const char *version="voltgw-RAM-PROBE-v1";
#else
    const char *version=window ? "voltgw-recovery-v1" : "voltgw-v1";
#endif
    size_t n=strlen(version); memcpy(out,version,n); return (int)n;
  }
  if (p->b.bmRequestType==0xc0 && p->b.bRequest==0xc1) { out[0]=1; return 1; }
  /* Explicit local USB-only cold-start request. Not exposed through CAN or
   * the running application's dispatcher. No flash writes here. */
  if (window && p->b.bmRequestType==0x40 && p->b.bRequest==0xb5 &&
      p->b.wValue.w==0x4757 && p->b.wIndex.w==0x5243 && p->b.wLength.w==0) recover=true;
  return 0;
}
int usb_cb_ep1_in(uint8_t *out,int maximum,int hardwired) {
  if (!hardwired || !out || maximum<8 || !count || window || failed) return 0;
  memcpy(out,queue[head],8); head=(uint8_t)((head+1U)%16U); count--; return 8;
}
void usb_cb_ep2_out(uint8_t *data,int length,int hardwired) {
  if (!hardwired || window || failed || length<=0 || length>64 || (length&7)) return;
  uint32_t now=vgw_white_startup_now(startup);
  for (int off=0;off<length;off+=8)
    (void)vgw_recovery_link_feed(&link,0,false,false,data+off,8,now);
}
void usb_cb_ep3_out(uint8_t *data,int length,int hardwired) { (void)data; (void)length; (void)hardwired; }
bool vgw_white_usb_init(vgw_white_startup *s,bool recovery_window) {
  if (!s || !s->ready || (recovery_window && s->watchdog.started)) return false;
  startup=s; failed=false; owned=false; recover=false; window=recovery_window;
  head=tail=count=0;
  if (!vgw_recovery_link_init(&link,0)) return false;
  RCC->AHB1ENR|=7U;
  /* Preserve all CAN/ESP control pins. Only USB PA11/12 AF10 and USB power
   * client control PB2/PA13 are touched; these match historical White code. */
  GPIOA->BSRR=1U<<13;
  GPIOB->BSRR=1U<<(2+16);
  GPIOA->MODER=(GPIOA->MODER&~((3U<<22)|(3U<<24)|(3U<<26)))|(2U<<22)|(2U<<24)|(1U<<26);
  GPIOA->AFR[1]=(GPIOA->AFR[1]&~((15U<<12)|(15U<<16)))|(10U<<12)|(10U<<16);
  GPIOA->OSPEEDR|=(3U<<22)|(3U<<24);
  GPIOB->MODER=(GPIOB->MODER&~(3U<<4))|(1U<<4);
  RCC->AHB2ENR|=1U<<7;
  RCC->AHB2RSTR|=1U<<7; RCC->AHB2RSTR&=~(1U<<7);
  usb_init();
  NVIC_DisableIRQ(OTG_FS_IRQn); /* single cooperative owner, no ISR callbacks */
  return !failed;
}
void vgw_white_usb_stop(void) {
  NVIC_DisableIRQ(OTG_FS_IRQn);
  USBx->GAHBCFG=0; USBx->GINTMSK=0; USBx_DEVICE->DCTL|=2U;
  RCC->AHB2RSTR|=1U<<7; RCC->AHB2RSTR&=~(1U<<7); RCC->AHB2ENR&=~(1U<<7);
  failed=true; owned=false; count=0;
}
bool vgw_white_usb_poll(void) {
  if (failed || !startup) return false;
  if (USBx->GINTSTS&USBx->GINTMSK) usb_irqhandler();
  return !failed;
}
bool vgw_white_usb_owned(void) { return owned; }
bool vgw_white_usb_recovery_requested(void) { return window && recover && owned && !failed; }
bool vgw_white_usb_send(void *ctx,const uint8_t data[8]) {
  (void)ctx;
  if (!owned || failed || window || count==16 || !data) return false;
  memcpy(queue[tail],data,8); tail=(uint8_t)((tail+1U)%16U); count++; return true;
}
bool vgw_white_usb_telemetry_send(void *ctx,const uint8_t data[8]) {
  if (link.rx.active || link.rx.complete || link.replying || link.flow_pending || count) return false;
  return vgw_white_usb_send(ctx,data);
}
bool vgw_white_usb_dispatch(vgw_recovery_service *service,uint64_t elapsed_ms) {
  if (!owned || failed || window || !service) return false;
  uint32_t now=vgw_white_startup_now(startup);
  if (link.rx.complete) {
    uint8_t reply[512]; size_t n=0;
    /* Authentication shares the CAN runtime's extended monotonic epoch. The
     * transport alone uses the wrapping hardware timer for short deadlines. */
    if (vgw_recovery_service_request(service,link.rx.data,link.rx.length,elapsed_ms,reply,&n)) {
      now=vgw_white_startup_now(startup);
      if (!vgw_recovery_link_reply(&link,reply,n,now)) return false;
    } else vgw_recovery_rx_clear(&link.rx);
  }
  (void)vgw_recovery_link_poll(&link,now,vgw_white_usb_send,NULL);
  return service->ready;
}
