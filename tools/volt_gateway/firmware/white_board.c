#include "white_board.h"
#include "status_led.h"
#include <stddef.h>

#define GPIOA 0x40020000U
#define GPIOB 0x40020400U
#define GPIOC 0x40020800U
#define RCC_AHB1ENR 0x40023830U
#define RCC_APB1RSTR 0x40023820U
#define MODER 0U
#define OTYPER 4U
#define OSPEEDR 8U
#define PUPDR 12U
#define ODR 20U
#define BSRR 24U
#define CAN_RESET (7U << 25)

static bool valid(const vgw_white_mmio *io) { return io && io->read32 && io->read16 && io->write32; }
static void change(const vgw_white_mmio *io, uint32_t reg, uint32_t clear, uint32_t set) {
  io->write32(io->ctx, reg, (io->read32(io->ctx, reg) & ~clear) | set);
}
static uint32_t pairs(uint32_t pins) {
  uint32_t mask=0;
  for (unsigned i=0; i<16; i++) if (pins & (1U << i)) mask |= 3U << (2*i);
  return mask;
}
static void outputs(const vgw_white_mmio *io, uint32_t port, uint32_t pins) {
  uint32_t fields=pairs(pins), mode=0;
  for (unsigned i=0; i<16; i++) if (pins & (1U << i)) mode |= 1U << (2*i);
  change(io, port+OTYPER, pins, 0);  /* push-pull */
  change(io, port+OSPEEDR, fields, 0);
  change(io, port+PUPDR, fields, 0);
  change(io, port+MODER, fields, mode);
}
static void inputs(const vgw_white_mmio *io, uint32_t port, uint32_t pins) {
  change(io, port+MODER, pairs(pins), 0);
  change(io, port+PUPDR, pairs(pins), 0);
}
static bool verify_pins(const vgw_white_mmio *io, uint32_t port, uint32_t pins, bool output) {
  uint32_t mask=pairs(pins), mode=0;
  if (output) for (unsigned i=0; i<16; i++) if (pins & (1U << i)) mode |= 1U << (2*i);
  return (io->read32(io->ctx, port+MODER) & mask) == mode &&
    (io->read32(io->ctx, port+PUPDR) & mask) == 0 &&
    (!output || ((io->read32(io->ctx, port+OTYPER) & pins) == 0 &&
                 (io->read32(io->ctx, port+OSPEEDR) & mask) == 0));
}
bool vgw_white_identify(const vgw_white_mmio *io, vgw_white_identity *out) {
  if (!valid(io) || !out) return false;
  uint32_t id=io->read32(io->ctx, 0xe0042000U);
  *out=(vgw_white_identity){(uint16_t)(id & 0xfffU), (uint16_t)(id >> 16), io->read16(io->ctx, 0x1fff7a22U)};
  /* Revision is recorded, not silently claimed validated. Production revision
   * allowlist/errata review remains mandatory outside this family/size check. */
  return out->device_id == 0x463 && out->flash_kib == 1536;
}
bool vgw_white_quiesce(const vgw_white_mmio *io) {
  if (!valid(io)) return false;
  const uint32_t c_high=(1U<<1)|(1U<<13)|(1U<<6)|(1U<<7)|(1U<<9);
  const uint32_t c_low=(1U<<5)|(1U<<14);
  change(io, RCC_AHB1ENR, 0, 7);
  if ((io->read32(io->ctx, RCC_AHB1ENR) & 7) != 7) return false;
  /* Preload inactive levels before changing output modes, avoiding a low
   * enable glitch. LED pins default OFF; ESP/GPS power controls default low. */
  io->write32(io->ctx, GPIOC+BSRR, c_high | (c_low << 16));
  io->write32(io->ctx, GPIOA+BSRR, 1);
  outputs(io, GPIOC, c_high | c_low);
  outputs(io, GPIOA, 1);
  change(io, RCC_APB1RSTR, 0, CAN_RESET);
  inputs(io, GPIOB, (1U<<3)|(1U<<4)|(1U<<5)|(1U<<6)|(1U<<12)|(1U<<13));
  inputs(io, GPIOA, (1U<<8)|(1U<<15));
  return (io->read32(io->ctx, GPIOC+ODR) & (c_high | c_low)) == c_high &&
         (io->read32(io->ctx, GPIOA+ODR) & 1) == 1 &&
         (io->read32(io->ctx, RCC_APB1RSTR) & CAN_RESET) == CAN_RESET &&
         verify_pins(io, GPIOC, c_high | c_low, true) && verify_pins(io, GPIOA, 1, true) &&
         verify_pins(io, GPIOB, (1U<<3)|(1U<<4)|(1U<<5)|(1U<<6)|(1U<<12)|(1U<<13), false) &&
         verify_pins(io, GPIOA, (1U<<8)|(1U<<15), false);
}
void vgw_white_led(const vgw_white_mmio *io, uint8_t rgb) {
  if (valid(io)) io->write32(io->ctx, GPIOC+BSRR, vgw_white_led_bsrr(rgb));
}
