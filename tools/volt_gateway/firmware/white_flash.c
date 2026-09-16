#include "white_flash.h"

#define RAM __attribute__((section(".ramfunc.vgw_flash"), noinline))
#define ACR 0x40023c00U
#define KEYR 0x40023c04U
#define SR 0x40023c0cU
#define CR 0x40023c10U
#define LOCK 0x80000000U
#define BUSY 0x10000U
#define ERRORS 0xf2U
#define CACHES 0x700U
#define RESETS 0x1800U
#define SLOT_SIZE 0xa0000U
#define SECTOR_SIZE 0x20000U

size_t vgw_white_flash_size(void) { return sizeof(vgw_white_flash); }
bool vgw_white_flash_failed(const vgw_white_flash *f) { return !f || f->failed; }
static RAM bool fail(vgw_white_flash *f) { if (f) f->failed=true; return false; }
static RAM bool reg(vgw_white_flash *f, uint32_t addr, uint32_t value) {
  if (!f->io.reg_write(f->io.ctx, addr, value)) return fail(f);
  return true;
}
static RAM bool fatal(vgw_white_flash *f) {
  f->failed=true;
  f->io.fatal(f->io.ctx);
#ifdef VGW_FLASH_TEST
  return false; /* Native fault harness only; NEVER enable for a board image. */
#else
  for (;;) { __asm__ volatile ("" ::: "memory"); } /* callback unexpectedly returned */
  __builtin_unreachable();
#endif
}
static RAM bool bounds(vgw_white_flash *f, uint32_t off, uint32_t size) {
  if (!f || !f->ready || f->failed || f->active || !size || off > SLOT_SIZE || size > SLOT_SIZE-off) return fail(f);
  return true;
}
static RAM bool wait_idle(vgw_white_flash *f, uint32_t timeout) {
  uint32_t start=f->io.now_ms(f->io.ctx);
  for (uint32_t poll=0; poll<f->io.poll_limit; poll++) {
    f->last_sr=f->io.reg_read(f->io.ctx, SR);
    if (!f->io.permit(f->io.ctx)) f->failed=true;
    if (!(f->last_sr & BUSY)) {
      if (f->last_sr & ERRORS) f->failed=true;
      return !f->failed;
    }
    if ((uint32_t)(f->io.now_ms(f->io.ctx)-start) >= timeout) return fatal(f);
    f->io.service(f->io.ctx);
  }
  return fatal(f); /* Includes a frozen clock: never wait forever for BSY. */
}
static RAM bool finish(vgw_white_flash *f, bool ok) {
  if (f->io.reg_read(f->io.ctx, SR) & BUSY) return fatal(f);
  /* Never set mass erase, OPTCR or OPTKEYR. Reset operation bits and lock. */
  if (!reg(f, CR, LOCK) || f->io.reg_read(f->io.ctx, CR) != LOCK) return fatal(f);
  if (f->cache_changed) {
    uint32_t disabled=f->acr & ~(CACHES|RESETS);
    if (!reg(f, ACR, disabled | RESETS) || !reg(f, ACR, disabled) || !reg(f, ACR, f->acr) ||
        f->io.reg_read(f->io.ctx, ACR) != f->acr) return fatal(f);
  }
  if (f->entered) f->io.leave(f->io.ctx);
  f->entered=false; f->active=false; f->cache_changed=false;
  if (!ok) f->failed=true;
  return ok && !f->failed;
}
static RAM bool begin(vgw_white_flash *f) {
  if (!f->io.permit(f->io.ctx)) return fail(f);
  f->active=true;
  if (!f->io.enter(f->io.ctx)) { f->active=false; return fail(f); }
  f->entered=true;
  if (f->io.reg_read(f->io.ctx, SR) & BUSY) return fatal(f);
  if (f->io.reg_read(f->io.ctx, CR) != LOCK || (f->io.reg_read(f->io.ctx, SR) & ERRORS)) return finish(f, false);
  f->acr=f->io.reg_read(f->io.ctx, ACR);
  if (f->acr & RESETS) return finish(f, false);
  f->cache_changed=true;
  if (!reg(f, ACR, f->acr & ~CACHES) || f->io.reg_read(f->io.ctx, ACR) != (f->acr & ~CACHES) ||
      !reg(f, ACR, (f->acr & ~CACHES) | RESETS) || !reg(f, ACR, f->acr & ~CACHES) ||
      !reg(f, SR, 1) || !reg(f, KEYR, 0x45670123U) || !reg(f, KEYR, 0xcdef89abU) ||
      f->io.reg_read(f->io.ctx, CR) != 0) return finish(f, false);
  return true;
}
bool vgw_white_flash_init(vgw_white_flash *f, const vgw_flash_io *io, unsigned slot, bool image_mutation) {
  if (!f) return false;
  *f=(vgw_white_flash){0};
  f->failed=true;
  if (!io || slot>1 || !io->reg_read || !io->reg_write || !io->read || !io->program8 || !io->now_ms ||
      !io->permit || !io->enter || !io->leave || !io->service || !io->fatal || !io->poll_limit || io->poll_limit>50000000U) return false;
#if defined(__arm__) || defined(__thumb__)
  /* Entry-address checks supplement, NOT replace, a whole call-graph/link audit. */
  uintptr_t callbacks[]={(uintptr_t)io->reg_read,(uintptr_t)io->reg_write,(uintptr_t)io->read,(uintptr_t)io->program8,
    (uintptr_t)io->now_ms,(uintptr_t)io->permit,(uintptr_t)io->enter,(uintptr_t)io->leave,(uintptr_t)io->service,(uintptr_t)io->fatal};
  if ((uintptr_t)f < 0x20000000U || (uintptr_t)f > 0x20020000U-sizeof(*f)) return false;
  for (unsigned i=0;i<sizeof(callbacks)/sizeof(callbacks[0]);i++)
    if (!(callbacks[i]&1) || callbacks[i]<0x20000001U || callbacks[i]>=0x20020000U) return false;
#endif
  f->io=*io; f->base=slot ? 0x080e0000U : 0x08040000U;
  f->image_mutation=image_mutation; f->failed=false; f->ready=true;
  return true;
}
RAM bool vgw_white_flash_read(vgw_white_flash *f, uint32_t off, uint8_t *out, uint32_t size) {
  if (!out || !bounds(f, off, size)) return fail(f);
  if (!f->io.read(f->io.ctx, f->base+off, out, size)) return fail(f);
  return true;
}
RAM bool vgw_white_flash_erase(vgw_white_flash *f, uint32_t off) {
  if (!bounds(f, off, SECTOR_SIZE) || !f->image_mutation || off%SECTOR_SIZE) return fail(f);
  if (!begin(f)) return false;
  /* F413 large sectors 5..15 begin at 0x08020000. Slot A starts sector 6. */
  uint32_t sector=5U+(f->base+off-0x08020000U)/SECTOR_SIZE;
  bool ok=reg(f, CR, 2U | (sector<<3)); /* SER, PSIZE=x8 */
  if (ok && f->io.reg_read(f->io.ctx, CR) != (2U | (sector<<3))) ok=false;
  if (ok && !f->io.permit(f->io.ctx)) ok=false;
  if (ok) { ok=reg(f, CR, 2U | (sector<<3) | 0x10000U); bool idle=wait_idle(f, 5000); ok=ok && idle; }
  if (f->io.reg_read(f->io.ctx, SR)&BUSY) return fatal(f);
  uint8_t check[256];
  for (uint32_t i=0;ok && i<SECTOR_SIZE;i+=sizeof(check)) {
    ok=f->io.permit(f->io.ctx) && f->io.read(f->io.ctx,f->base+off+i,check,sizeof(check));
    if (ok) for (unsigned j=0;j<sizeof(check);j++) if (check[j]!=255) { ok=false; break; }
    f->io.service(f->io.ctx);
  }
  return finish(f, ok);
}
static RAM bool program(vgw_white_flash *f, uint32_t off, const uint8_t *data, uint32_t size) {
  uint8_t copy[256], previous[256];
  for (uint32_t i=0;i<size;i++) copy[i]=data[i]; /* Copy flash-resident input BEFORE programming. */
  if (!begin(f)) return false;
  bool ok=f->io.read(f->io.ctx,f->base+off,previous,size);
  if (ok) for (uint32_t i=0;i<size;i++) if ((previous[i]&copy[i])!=copy[i]) { ok=false; break; }
  for (uint32_t i=0;ok && i<size;i++) {
    if (copy[i]==previous[i]) continue;
    ok=f->io.permit(f->io.ctx) && reg(f,CR,1U) && f->io.reg_read(f->io.ctx,CR)==1U;
    if (ok) { ok=f->io.program8(f->io.ctx,f->base+off+i,copy[i]); bool idle=wait_idle(f,100); ok=ok && idle; }
    if (f->io.reg_read(f->io.ctx,SR)&BUSY) return fatal(f);
    if (!reg(f,CR,0)) ok=false;
  }
  if (ok) ok=f->io.read(f->io.ctx,f->base+off,previous,size);
  if (ok) for (uint32_t i=0;i<size;i++) if (previous[i]!=copy[i]) { ok=false; break; }
  return finish(f, ok && f->io.permit(f->io.ctx));
}
RAM bool vgw_white_flash_program(vgw_white_flash *f, uint32_t off, const uint8_t *data, uint32_t size) {
  if (!data || size>256 || !bounds(f,off,size) || !f->image_mutation || off>=SLOT_SIZE-64 || size>SLOT_SIZE-64-off) return fail(f);
  return program(f,off,data,size);
}
RAM bool vgw_white_flash_trailer(vgw_white_flash *f, uint32_t off, const uint8_t *data, uint32_t size) {
  if (!data || !bounds(f,off,size)) return fail(f);
  bool flag=(off==SLOT_SIZE-32 || off==SLOT_SIZE-24) && size==4;
  bool magic=off==SLOT_SIZE-16 && size==16;
  if (!flag && !magic) return fail(f);
  if (flag) {
    if (data[0]!=1 || data[1]!=255 || data[2]!=255 || data[3]!=255) return fail(f);
  } else {
    /* Scalar local words avoid a flash-resident constant lookup while busy. */
    uint32_t words[4]={0xf395c277U,0x7fefd260U,0x0f505235U,0x8079b62cU};
    for (unsigned i=0;i<16;i++) if (data[i]!=(uint8_t)(words[i/4]>>(8*(i%4)))) return fail(f);
  }
  return program(f,off,data,size);
}
