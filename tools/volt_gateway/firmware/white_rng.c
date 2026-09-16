#include "white_rng.h"
#define RNG 0x50060800U
static bool fail(vgw_white_rng *r) {
  r->failed=true; r->ready=false;
  if (r->clock && r->clock->startup) {
    const vgw_white_mmio *io=&r->clock->startup->watchdog.io;
    io->write32(io->ctx,RNG,0);
  }
  return false;
}
static bool word(vgw_white_rng *r,uint32_t *out) {
  if (!vgw_white_clock_valid(r->clock)) return fail(r);
  const vgw_white_mmio *io=&r->clock->startup->watchdog.io;
  if (io->read32(io->ctx,RNG)!=4U || !(io->read32(io->ctx,0x40023834U)&64U)) return fail(r);
  uint32_t start=io->read32(io->ctx,0x40000024U);
  for (unsigned i=0;i<10000U;i++) {
    uint32_t status=io->read32(io->ctx,RNG+4);
    if (status&0x66U) return fail(r); /* current and latched clock/seed errors */
    if (status&1U) {
      *out=io->read32(io->ctx,RNG+8);
      if (io->read32(io->ctx,RNG+4)&0x66U) return fail(r);
      return true;
    }
    if ((uint32_t)(io->read32(io->ctx,0x40000024U)-start)>2U) return fail(r);
  }
  return fail(r);
}
bool vgw_white_rng_init(vgw_white_rng *r,const vgw_white_clock *clock) {
  if (!r) return false;
  *r=(vgw_white_rng){.clock=clock,.failed=true};
  if (!vgw_white_clock_valid(clock)) return false;
  const vgw_white_mmio *io=&clock->startup->watchdog.io;
  io->write32(io->ctx,0x40023834U,io->read32(io->ctx,0x40023834U)|64U);
  if (!(io->read32(io->ctx,0x40023834U)&64U)) return false;
  io->write32(io->ctx,RNG,4U);
  if (!word(r,&r->previous)) return false;
  r->ready=true; r->failed=false;
  return true;
}
bool vgw_white_rng_nonce(vgw_white_rng *r,uint8_t out[32]) {
  if (!out) return false;
  for (unsigned i=0;i<32;i++) out[i]=0;
  if (!r || !r->ready || r->failed) return false;
  for (unsigned i=0;i<8;i++) {
    uint32_t value;
    if (!word(r,&value) || value==r->previous) {
      for (unsigned j=0;j<32;j++) out[j]=0;
      return fail(r);
    }
    r->previous=value;
    for (unsigned j=0;j<4;j++) out[4*i+j]=(uint8_t)(value>>(8*j));
  }
  return true;
}
