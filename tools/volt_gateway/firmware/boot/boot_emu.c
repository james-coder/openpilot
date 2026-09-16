/* CPU TEST ONLY: mapped writable memory is NOT an STM32 flash driver.
 * No vector table, GPIO, CAN, real watchdog or deployable startup. NEVER FLASH. */
#include "boot_port.h"
#include <string.h>
volatile int32_t vgw_boot_emu_result;
volatile uint32_t vgw_boot_emu_services;
volatile uint32_t vgw_boot_emu_confirmed;
volatile uint32_t vgw_boot_emu_rgb, vgw_boot_emu_bsrr;
uint8_t vgw_boot_emu_status[3];

/* Freestanding test runtime; no libc or host crypto hooks. */
void *memcpy(void *d, const void *s, size_t n) {
  uint8_t *out=d; const uint8_t *in=s;
  for (size_t i=0; i<n; i++) out[i]=in[i];
  return d;
}
void *memset(void *d, int x, size_t n) {
  uint8_t *out=d; for (size_t i=0; i<n; i++) out[i]=(uint8_t)x; return d;
}
int memcmp(const void *a, const void *b, size_t n) {
  const uint8_t *x=a, *y=b;
  for (size_t i=0; i<n; i++) if (x[i]!=y[i]) return (int)x[i]-(int)y[i];
  return 0;
}
void *memmove(void *d, const void *s, size_t n) {
  uint8_t *out=d; const uint8_t *in=s;
  if ((uintptr_t)d <= (uintptr_t)s) return memcpy(d,s,n);
  for (size_t i=n; i>0; i--) out[i-1]=in[i-1];
  return d;
}
__attribute__((noinline,noreturn)) void vgw_boot_emu_done(void) { for (;;) __asm__ volatile ("nop"); }
static bool read_mem(void *ctx, uint32_t off, void *out, uint32_t len) {
  (void)ctx;
  if (off > VGW_BOOT_FLASH_SIZE || len > VGW_BOOT_FLASH_SIZE-off) return false;
  memcpy(out, (void *)(VGW_BOOT_FLASH_BASE+off), len); return true;
}
static bool write_mem(void *ctx, uint32_t off, const void *in, uint32_t len) {
  (void)ctx;
  if (off < VGW_BOOT_SLOT0 || off > VGW_BOOT_FLASH_SIZE || len > VGW_BOOT_FLASH_SIZE-off) return false;
  volatile uint8_t *out=(void *)(VGW_BOOT_FLASH_BASE+off);
  const uint8_t *src=in;
  for (uint32_t i=0; i<len; i++) {
    if ((out[i] & src[i]) != src[i]) return false;
    out[i] &= src[i];
  }
  return true;
}
static bool erase_mem(void *ctx, uint32_t off, uint32_t len) {
  (void)ctx;
  if (off < VGW_BOOT_SLOT0 || off > VGW_BOOT_FLASH_SIZE || len > VGW_BOOT_FLASH_SIZE-off) return false;
  memset((void *)(VGW_BOOT_FLASH_BASE+off), 255, len); return true;
}
static void service(void *ctx) { (void)ctx; vgw_boot_emu_services++; }
static void panic_test(void *ctx) { (void)ctx; vgw_boot_emu_result=-9; vgw_boot_emu_done(); }
void vgw_boot_emu_entry(void) {
  const uint8_t *fixture=(const uint8_t *)0x20018000U;
  vgw_boot_io io={NULL,read_mem,write_mem,erase_mem,service,panic_test};
  vgw_boot_choice choice;
  vgw_boot_emu_result=-3;
  if (vgw_boot_init(&io, fixture, fixture+91)) {
    vgw_boot_emu_result=vgw_boot_select(&choice);
    if (vgw_boot_emu_result>=0) {
      /* Execute a signed leaf-function probe, NOT a hardware reset handoff:
       * MSP/VTOR/interrupt ownership must be integrated separately. */
      ((void (*)(void))choice.reset_handler)();
      if (fixture[135]) vgw_boot_emu_confirmed=vgw_boot_confirm();
    }
  }
  vgw_boot_get_status(vgw_boot_emu_status);
  if (fixture[136]) {
    vgw_status_led led;
    vgw_led_init(&led, 0);
    vgw_led_set(&led, vgw_boot_emu_status[0], vgw_boot_emu_status[1], vgw_boot_emu_status[2], 0);
    vgw_boot_emu_rgb=vgw_led_sample(&led, 4000); /* post-intro steady indication */
    vgw_boot_emu_bsrr=vgw_white_led_bsrr((uint8_t)vgw_boot_emu_rgb);
  }
  vgw_boot_emu_done();
}
