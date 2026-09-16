#include "white_platform.h"
#if !defined(__arm__) && !defined(__thumb__)
#error Physical White binding is ARM-only; never execute on the development host
#endif
#define RAM __attribute__((section(".ramfunc.vgw_platform"), noinline))
static RAM uint32_t read32(void *ctx, uint32_t address) {
  (void)ctx; return *(volatile uint32_t *)(uintptr_t)address;
}
static RAM uint16_t read16(void *ctx, uint32_t address) {
  (void)ctx; return *(volatile uint16_t *)(uintptr_t)address;
}
static RAM void write32(void *ctx, uint32_t address, uint32_t value) {
  (void)ctx; *(volatile uint32_t *)(uintptr_t)address=value;
  __asm__ volatile("dsb" ::: "memory");
}
const vgw_white_mmio *vgw_white_physical_mmio(void) {
  static const vgw_white_mmio io={0,read32,read16,write32};
  return &io;
}
RAM _Noreturn void vgw_white_physical_reset(void) {
  __asm__ volatile("cpsid i" ::: "memory");
  /* Startup already enabled GPIO clocks/configured active-low disable pins.
   * Reinstate clocks defensively, then disable the three differential PHYs.
   * Put SWCAN to sleep as well, independent of CAN queues. */
  write32(0,0x40023830U,read32(0,0x40023830U)|7U);
  write32(0,0x40020818U,(1U<<1)|(1U<<13));
  write32(0,0x40020018U,1);
  write32(0,0x40020418U,0xc0000000U);
  write32(0,0x40023820U,read32(0,0x40023820U)|(7U<<25));
  write32(0,0xe000ed0cU,0x05fa0004U);
  __asm__ volatile("dsb\nisb" ::: "memory");
  for (;;) __asm__ volatile("nop");
  __builtin_unreachable();
}
static RAM void fatal(void *ctx) { (void)ctx; vgw_white_physical_reset(); }
static RAM uint32_t now(void *ctx) {
  vgw_white_platform *p=ctx;
  return vgw_white_startup_now(p->startup);
}
static RAM bool permit(void *ctx) {
  vgw_white_platform *p=ctx;
  return p->startup->ready && !p->startup->watchdog.failed && p->permit(p->context);
}
static RAM bool enter(void *ctx) {
  vgw_white_platform *p=ctx;
  if (p->entered) return false;
  __asm__ volatile("mrs %0, primask\ncpsid i" : "=r"(p->primask) :: "memory");
  p->entered=true;
  return true;
}
static RAM void leave(void *ctx) {
  vgw_white_platform *p=ctx;
  if (!p->entered) vgw_white_physical_reset();
  p->entered=false;
  __asm__ volatile("dsb\nisb\nmsr primask, %0" :: "r"(p->primask) : "memory");
}
static RAM void service(void *ctx) {
  vgw_white_platform *p=ctx;
  p->service(p->context);
  if (!vgw_white_watchdog_service(&p->startup->watchdog,now(ctx))) vgw_white_physical_reset();
}
static RAM bool reg_write(void *ctx, uint32_t address, uint32_t value) {
  /* Only ACR, KEYR, SR and CR. Never option keys, option bytes or OTP. */
  if (address!=0x40023c00U && address!=0x40023c04U && address!=0x40023c0cU && address!=0x40023c10U) return false;
  write32(ctx,address,value); return true;
}
static RAM bool read_flash(void *ctx, uint32_t address, uint8_t *out, uint32_t size) {
  (void)ctx;
  if (!out || address<0x08000000U || address>0x08180000U || size>0x08180000U-address) return false;
  const volatile uint8_t *src=(const volatile uint8_t *)(uintptr_t)address;
  for (uint32_t i=0;i<size;i++) out[i]=src[i];
  return true;
}
static RAM bool program8(void *ctx, uint32_t address, uint8_t value) {
  (void)ctx;
  if (address<0x08040000U || address>=0x08180000U) return false;
  *(volatile uint8_t *)(uintptr_t)address=value;
  __asm__ volatile("dsb" ::: "memory");
  return true;
}
static bool sram(uintptr_t address, uint32_t size) {
  return address>=0x20000000U && address<=0x20020000U && size<=0x20020000U-address;
}
bool vgw_white_physical_flash(vgw_white_platform *p, vgw_flash_io *out) {
  if (!p || !out || !sram((uintptr_t)p,sizeof(*p)) || !sram((uintptr_t)p->startup,sizeof(*p->startup)) ||
      !p->startup->ready || !p->permit || !p->service || p->entered ||
      !((uintptr_t)p->permit&1U) || !((uintptr_t)p->service&1U) ||
      !sram((uintptr_t)p->permit,1) || !sram((uintptr_t)p->service,1)) return false;
  *out=(vgw_flash_io){p,read32,reg_write,read_flash,program8,now,permit,enter,leave,service,fatal,50000000U};
  return true;
}
__attribute__((naked,noreturn)) static void branch_app(uint32_t stack __attribute__((unused)),
                                                    uint32_t handler __attribute__((unused))) {
  __asm__ volatile("movs r2, #0\nmsr control, r2\nisb\nmsr msp, r0\ndsb\nisb\nbx r1");
}
bool vgw_white_physical_handoff(const vgw_boot_choice *choice) {
  uint32_t ipsr;
  __asm__ volatile("mrs %0, ipsr" : "=r"(ipsr));
  if (!choice || ipsr || choice->slot>1) return false;
  uint32_t base=VGW_BOOT_FLASH_BASE+(choice->slot ? VGW_BOOT_SLOT1 : VGW_BOOT_SLOT0);
  if (choice->vector_address!=base+VGW_BOOT_HEADER_SIZE || (choice->stack_pointer&7U) ||
      choice->stack_pointer<=0x20000000U || choice->stack_pointer>0x20020000U ||
      !(choice->reset_handler&1U) || (choice->reset_handler&~1U)<choice->vector_address+8U ||
      (choice->reset_handler&~1U)>=base+VGW_BOOT_SLOT_SIZE-64U ||
      read32(0,choice->vector_address)!=choice->stack_pointer ||
      read32(0,choice->vector_address+4)!=choice->reset_handler) return false;
  __asm__ volatile("cpsid i" ::: "memory");
  if (!vgw_white_quiesce(vgw_white_physical_mmio())) vgw_white_physical_reset();
  write32(0,0xe000e010U,0); /* SysTick CTRL */
  write32(0,0xe000e014U,0); write32(0,0xe000e018U,0);
  for (uint32_t i=0;i<8;i++) {
    write32(0,0xe000e180U+4U*i,0xffffffffU); /* NVIC ICER */
    write32(0,0xe000e280U+4U*i,0xffffffffU); /* NVIC ICPR */
  }
  write32(0,0xe000ed04U,(1U<<25)|(1U<<27)); /* clear SysTick/PendSV pending */
  write32(0,0xe000ed08U,choice->vector_address);
  branch_app(choice->stack_pointer,choice->reset_handler);
}
