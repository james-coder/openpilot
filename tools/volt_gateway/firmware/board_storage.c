#include "board_storage.h"
#define RAM __attribute__((section(".ramfunc.vgw_storage"), noinline))
static bool pump(void *ctx);
static RAM uint64_t now(vgw_board_storage *s) {
  return s->runtime->can.elapsed_ms+(uint32_t)(vgw_white_startup_now(&s->runtime->startup)-s->runtime->can.previous_ms);
}
static RAM bool permit(void *ctx) {
  vgw_board_storage *s=ctx;
  if (!s || !s->ready) return false;
  if (!s->boot_phase) return vgw_recovery_flash_permit(&s->guard);
  uint64_t stamp; bool allowed=false;
  return s->runtime->ready && s->runtime->can.ready && !s->runtime->can.failed &&
    !s->runtime->can.pending && !s->runtime->can.tx_inhibited &&
    vgw_white_safety_sample(s->safety,now(s),&stamp,&allowed) && allowed;
}
static RAM void service(void *ctx) {
  vgw_board_storage *s=ctx;
  if (!s->boot_phase) { vgw_recovery_flash_service(&s->guard); return; }
  vgw_white_watchdog *w=&s->runtime->startup.watchdog;
  vgw_white_watchdog_progress(w,VGW_PROGRESS_SCHEDULER);
  if (!vgw_white_can_poll(&s->runtime->can,vgw_white_safety_receive,s->safety)) goto fail;
  vgw_white_watchdog_progress(w,VGW_PROGRESS_RX);
  /* Boot phase exposes no command dispatcher. It drains CAN solely for safe
   * state observation; no optional response may be sent from this service. */
  vgw_white_watchdog_progress(w,VGW_PROGRESS_PROTOCOL);
  if (!permit(s)) goto fail;
  vgw_white_watchdog_progress(w,VGW_PROGRESS_HEALTH);
  return;
fail:
  vgw_white_watchdog_fail(w); vgw_white_can_stop(&s->runtime->can);
}
static bool locate(uint32_t offset,uint32_t length,unsigned *slot,uint32_t *relative) {
  if (!length) return false;
  if (offset>=VGW_BOOT_SLOT1) { *slot=1; *relative=offset-VGW_BOOT_SLOT1; }
  else if (offset>=VGW_BOOT_SLOT0) { *slot=0; *relative=offset-VGW_BOOT_SLOT0; }
  else return false;
  return *relative<VGW_BOOT_SLOT_SIZE && length<=VGW_BOOT_SLOT_SIZE-*relative;
}
static bool read_boot(void *ctx,uint32_t off,void *out,uint32_t size) {
  vgw_board_storage *s=ctx; unsigned slot; uint32_t relative;
  return s->ready && locate(off,size,&slot,&relative) && vgw_white_flash_read(&s->flash[slot],relative,out,size);
}
static bool write_boot(void *ctx,uint32_t off,const void *data,uint32_t size) {
  vgw_board_storage *s=ctx; unsigned slot; uint32_t relative;
  return s->ready && locate(off,size,&slot,&relative) &&
    vgw_white_flash_trailer(&s->flash[slot],relative,data,size);
}
static bool erase_boot(void *ctx,uint32_t off,uint32_t size) {
  vgw_board_storage *s=ctx; unsigned slot; uint32_t relative;
  return s->ready && s->boot_phase && size==VGW_BOOT_SECTOR_SIZE && locate(off,size,&slot,&relative) &&
    vgw_white_flash_erase(&s->flash[slot],relative);
}
static void boot_service(void *ctx) {
  vgw_board_storage *s=ctx;
  if (!pump(s)) vgw_white_physical_reset();
}
static void panic(void *ctx) { (void)ctx; vgw_white_physical_reset(); }
static void passive(void *ctx,const vgw_frame *f) {
  vgw_board_storage *s=ctx;
  vgw_white_safety_receive(s->safety,f);
  (void)vgw_observer_feed(&s->runtime->observer,f);
}
static bool pump(void *ctx) {
  vgw_board_storage *s=ctx;
  vgw_white_watchdog *w=&s->runtime->startup.watchdog;
  vgw_white_watchdog_progress(w,VGW_PROGRESS_SCHEDULER);
  /* Crypto slices can accumulate more than one ordinary main-loop batch.
   * Drain at most one queue capacity per bus, stopping early when empty.
   * IRQ producers remain enabled; no unbounded drain under a traffic flood. */
  for (unsigned batch=0;batch<VGW_CAN_RX_DEPTH/8U;batch++) {
    if (!vgw_white_can_poll(&s->runtime->can,passive,s)) return false;
    if (!s->runtime->can.rx_count[0] && !s->runtime->can.rx_count[1] && !s->runtime->can.rx_count[2]) break;
  }
  vgw_white_watchdog_progress(w,VGW_PROGRESS_RX|VGW_PROGRESS_PROTOCOL);
  uint64_t sampled; bool allowed;
  if (!vgw_white_clock_valid(&s->runtime->clock) ||
      !vgw_white_safety_sample(s->safety,now(s),&sampled,&allowed)) return false;
  vgw_white_watchdog_progress(w,VGW_PROGRESS_HEALTH);
  return vgw_white_watchdog_service(w,vgw_white_startup_now(&s->runtime->startup));
}
bool vgw_board_storage_init(vgw_board_storage *s,vgw_white_runtime *r,vgw_white_safety *safe,
                           vgw_recovery_service *recovery,const uint8_t key[91],const uint8_t target[44]) {
  if (!s || !r || !r->ready || !safe || !safe->ready || !recovery || !key || !target) return false;
  *s=(vgw_board_storage){.runtime=r,.recovery=recovery,.safety=safe,.boot_phase=true};
  s->platform=(vgw_white_platform){.startup=&r->startup,.permit=permit,.service=service,.context=s};
  vgw_flash_io flash;
  if (!vgw_white_physical_flash(&s->platform,&flash) ||
      !vgw_white_flash_init(&s->flash[0],&flash,0,true) || !vgw_white_flash_init(&s->flash[1],&flash,1,true)) return false;
  s->ready=true;
  const vgw_boot_io binding={s,read_boot,write_boot,erase_boot,boot_service,panic};
  if (!vgw_crypto_cooperative_init(pump,s) || !vgw_boot_init(&binding,key,target) ||
      !vgw_crypto_init(&s->crypto,key+26,65)) { s->ready=false; return false; }
  return true;
}
static bool sample(void *ctx,uint64_t *time,uint64_t *stamp,bool *allowed) {
  vgw_board_storage *s=ctx;
  /* Normal code is not flash-busy: service RX and watchdog before sampling,
   * including the repeated readback/hash loop in update_finish. */
  vgw_white_watchdog *w=&s->runtime->startup.watchdog;
  vgw_white_watchdog_progress(w,VGW_PROGRESS_SCHEDULER);
  if (!vgw_white_can_poll(&s->runtime->can,vgw_white_safety_receive,s->safety)) return false;
  vgw_white_watchdog_progress(w,VGW_PROGRESS_RX|VGW_PROGRESS_PROTOCOL);
  if (!vgw_white_clock_valid(&s->runtime->clock)) return false;
  vgw_white_watchdog_progress(w,VGW_PROGRESS_HEALTH);
  if (!vgw_white_watchdog_service(w,vgw_white_startup_now(&s->runtime->startup))) return false;
  *time=now(s); return vgw_white_safety_sample(s->safety,*time,stamp,allowed);
}
static bool erase(void *ctx,uint32_t off,uint32_t size) {
  vgw_board_storage *s=ctx;
  return size==VGW_BOOT_SECTOR_SIZE && vgw_white_flash_erase(&s->flash[s->inactive],off);
}
static bool write(void *ctx,uint32_t off,const uint8_t *data,size_t size) {
  vgw_board_storage *s=ctx;
  return size<=256 && off<VGW_BOOT_SLOT_SIZE-64 && size<=VGW_BOOT_SLOT_SIZE-64-off &&
    vgw_white_flash_program(&s->flash[s->inactive],off,data,(uint32_t)size);
}
static bool read(void *ctx,uint32_t off,uint8_t *data,size_t size) {
  vgw_board_storage *s=ctx;
  return size<=256 && vgw_white_flash_read(&s->flash[s->inactive],off,data,(uint32_t)size);
}
static bool hash_start(void *ctx) { return vgw_crypto_hash_start(&((vgw_board_storage *)ctx)->crypto); }
static bool hash_add(void *ctx,const uint8_t *p,size_t n) { return vgw_crypto_hash_add(&((vgw_board_storage *)ctx)->crypto,p,n); }
static bool hash_finish(void *ctx,uint8_t out[32]) { return vgw_crypto_hash_finish(&((vgw_board_storage *)ctx)->crypto,out); }
static uint32_t get32(const uint8_t *p) { return ((uint32_t)p[0]<<24)|((uint32_t)p[1]<<16)|((uint32_t)p[2]<<8)|p[3]; }
static bool commit(void *ctx,const uint8_t manifest[VGW_MANIFEST_SIZE]) {
  vgw_board_storage *s=ctx;
  return vgw_boot_commit_candidate(s->inactive,get32(manifest+50),get32(manifest+118));
}
bool vgw_board_storage_updater(vgw_board_storage *s,unsigned inactive,vgw_update_io *out) {
  if (!s || !s->ready || inactive>1 || !out || !s->boot_phase) return false;
  s->inactive=inactive;
  s->flash[1-inactive].image_mutation=false;
  *out=(vgw_update_io){.ctx=s,.capacity=VGW_BOOT_SLOT_SIZE,.erase_count=VGW_SLOT_SECTORS,
    .sample=sample,.erase=erase,.write=write,.read=read,.hash_start=hash_start,.hash_add=hash_add,
    .hash_finish=hash_finish,.mark_trial=commit};
  for (unsigned i=0;i<VGW_SLOT_SECTORS;i++) out->erase_sizes[i]=VGW_BOOT_SECTOR_SIZE;
  return true;
}
bool vgw_board_storage_recovery_ready(vgw_board_storage *s) {
  if (!s || !s->ready || !s->boot_phase || !s->recovery->ready ||
      !vgw_recovery_flash_init(&s->guard,s->runtime,s->recovery,vgw_white_safety_sample,vgw_white_safety_receive,s->safety)) return false;
  s->boot_phase=false; return true;
}
