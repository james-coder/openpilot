#include "application.h"
#include "board_storage.h"
#include "status_led.h"
#include "boot_trace.h"
#include <string.h>
#if defined(VGW_HVAC_EXPERIMENT) && defined(VGW_APPLICATION_SLOT)
#include "hvac_trial.h"
static vgw_hvac_trial hvac;
#endif
#ifdef VGW_BOARD_USB
#include "white_usb.h"
void vgw_usb_recovery_window(void);
#endif

/* Independently linked loader and slot-local application. Configuration lives
 * in the separate provisioning sector, outside remotely writable application
 * slots. There are NO compiled default keys, CAN IDs or revision assumptions. */
static vgw_white_runtime runtime;
static vgw_recovery_service recovery;
static vgw_white_safety safety;
static vgw_board_storage storage;
static vgw_application application;
static vgw_recovery_provision provision;
static vgw_status_led led;
static uint8_t public_key[91], target[44];
static uint32_t divider;
static uint8_t powertrain;
#if defined(VGW_HVAC_EXPERIMENT) && defined(VGW_APPLICATION_SLOT)
static size_t hvac_command(void *ctx,uint8_t op,uint64_t now,uint8_t *out) {
  (void)ctx;
  /* USB-only first experiment; no production Tres changes required. */
  bool enabled=runtime.local_control && recovery.active && now<application.lease_until &&
    recovery.update.state!=VGW_UPDATE_RECEIVING;
  vgw_hvac_trial_step(&hvac,now,enabled);
  out[0]=(op==18 || (enabled && vgw_hvac_trial_start(&hvac,now))) ? 0 : 1;
  out[1]=1; out[2]=hvac.state; out[3]=hvac.attempts; out[4]=hvac.allowed;
  return 5;
}
static void hvac_attach(void) {
  application.experiment=hvac_command;
}
#else
static void hvac_attach(void) { }
#endif
/* F413 CMSIS IRQ numbers: CAN1_RX0=20, CAN2_RX0=64, CAN3_RX0=75.
 * These handlers only stage raw RX. Never protocol, crypto, flash or TX. */
__attribute__((section(".ramfunc.vgw_can_irq"))) void vgw_can1_rx0(void) { vgw_white_can_rx_irq(&runtime.can,1); }
__attribute__((section(".ramfunc.vgw_can_irq"))) void vgw_can2_rx0(void) { vgw_white_can_rx_irq(&runtime.can,2); }
__attribute__((section(".ramfunc.vgw_can_irq"))) void vgw_can3_rx0(void) { vgw_white_can_rx_irq(&runtime.can,3); }
static bool start_rx_interrupts(const vgw_white_mmio *io) {
  if (!vgw_white_can_enable_rx(&runtime.can)) return false;
  /* Equal priority, so CAN producers cannot nest each other. Clear only their
   * pending bits; other IRQs remain disabled. USB is explicitly polling-only. */
  io->write32(io->ctx,0xe000e414U,0x80808080U);
  io->write32(io->ctx,0xe000e440U,0x80808080U);
  io->write32(io->ctx,0xe000e448U,0x80808080U);
  io->write32(io->ctx,0xe000e280U,1U<<20);
  io->write32(io->ctx,0xe000e288U,(1U<<0)|(1U<<11));
  io->write32(io->ctx,0xe000e100U,1U<<20);
  io->write32(io->ctx,0xe000e108U,(1U<<0)|(1U<<11));
  __asm__ volatile("dsb\nisb\ncpsie i" ::: "memory");
  return true;
}
#ifndef VGW_BUILD_ID
#error Board images must have a source-derived build identity
#endif
static const uint8_t build_identity[32]=VGW_BUILD_ID;

static uint16_t get16(const uint8_t *p) { return (uint16_t)(((uint16_t)p[0]<<8)|p[1]); }
static uint32_t get32(const uint8_t *p) { return ((uint32_t)get16(p)<<16)|get16(p+2); }
static bool configuration(vgw_white_can_config *can,uint16_t *request) {
  const uint8_t *p=(const uint8_t *)0x08020000U;
  if (memcmp(p,"VGWCFG1\0",8)) return false;
  uint32_t crc=0xffffffffU;
  for (unsigned i=0;i<252;i++) {
    crc^=p[i];
    for (unsigned b=0;b<8;b++) crc=(crc>>1)^((crc&1U) ? 0xedb88320U : 0);
  }
  if (~crc!=get32(p+252) || memcmp(p+8,(const void *)0x1fff7a10U,12)) return false;
  memcpy(provision.device,p+8,12); memcpy(provision.layout,p+20,32);
  memcpy(provision.policy,p+52,32); memcpy(provision.build,build_identity,32);
  memcpy(provision.pairing,p+116,32); memcpy(public_key,p+148,91);
  memcpy(target,provision.device,12); memcpy(target+12,provision.layout,32);
  *can=(vgw_white_can_config){p[239],p[240],p[241],get16(p+246)};
  powertrain=p[242];
  if (p[243]) return false;
  *request=get16(p+244); divider=get32(p+248);
  return powertrain<3 && (can->hscan_mask&(1U<<powertrain)) && *request<=0x7ff &&
    can->response_id<=0x7ff && (!can->backhaul_controller || *request!=can->response_id) &&
    (divider==3791U || divider==8862U);
}
static bool entropy(void *ctx,uint8_t out[32]) { return vgw_white_rng_nonce(ctx,out); }
static bool quiet(void *ctx,vgw_white_runtime *r) {
  (void)ctx;
  if (r->recovery.rx.complete) vgw_recovery_rx_clear(&r->recovery.rx);
  uint64_t sampled; bool allowed;
  return vgw_white_safety_sample(&safety,r->can.elapsed_ms,&sampled,&allowed);
}
static _Noreturn void fatal(void) { vgw_white_physical_reset(); }
static void indication(uint8_t state,uint8_t slot,uint8_t error) {
  uint32_t now=vgw_white_startup_now(&runtime.startup);
  vgw_led_set(&led,state,slot,error,now);
  uint8_t rgb=vgw_led_sample(&led,now);
  vgw_white_led(vgw_white_physical_mmio(),rgb);
  vgw_application_indication(&application,&led,rgb);
#ifdef VGW_BOARD_USB
  uint8_t snapshot[7];
  if (vgw_application_indicator_snapshot(&application,snapshot)) vgw_white_usb_indication(snapshot);
#endif
}
static bool boot_needs_safe_power(void) {
  /* This is only a conservative scheduling hint, never image authentication.
   * The boot port still verifies every signature and gates every flash write. */
  for (unsigned i=0;i<2;i++) {
#ifdef VGW_APPLICATION_SLOT
    if (i!=VGW_APPLICATION_SLOT) continue;
#endif
    const uint8_t *trailer=(const uint8_t *)(VGW_BOOT_FLASH_BASE+(i ? VGW_BOOT_SLOT1 : VGW_BOOT_SLOT0)+VGW_BOOT_SLOT_SIZE-32);
    bool erased=true;
    for (unsigned j=0;j<32;j++) if (trailer[j]!=255) erased=false;
    if (!erased && (trailer[0]!=1 || trailer[8]!=1)) return true;
  }
  return false;
}
void vgw_loader_main(void) {
#if defined(VGW_BOARD_USB) && !defined(VGW_APPLICATION_SLOT)
  vgw_usb_recovery_window();
#endif
  const vgw_white_mmio *io=vgw_white_physical_mmio();
  vgw_white_can_config can; uint16_t request;
  /* Invalid provisioning never enables a CAN controller. The fixed USB
   * recovery window above is independent of this sector and both app slots. */
  vgw_boot_trace(1);
  if (!vgw_white_quiesce(io) || !configuration(&can,&request)) fatal();
  vgw_boot_trace(2);
  if (!vgw_white_runtime_init(&runtime,io,&can,request)) fatal();
  if (!start_rx_interrupts(io)) fatal();
  vgw_boot_trace(3);
  if (!vgw_white_safety_init(&safety,&runtime.can,powertrain,divider)) fatal();
  runtime.listener=vgw_white_safety_receive; runtime.listener_context=&safety;
  vgw_led_init(&led,vgw_white_startup_now(&runtime.startup));
  /* A confirmed image can boot from bench USB or while moving, read-only.
   * Trial/repair metadata changes require the full physical power interlock. */
  while (boot_needs_safe_power()) {
    if (!vgw_white_runtime_step(&runtime,quiet,NULL)) fatal();
    uint64_t sampled; bool allowed=false;
    if (!vgw_white_safety_sample(&safety,runtime.can.elapsed_ms,&sampled,&allowed)) fatal();
    indication(VGW_LED_UPDATE_WAIT,255,0);
    if (allowed) break;
  }
  vgw_boot_trace(5);
  if (!vgw_board_storage_init(&storage,&runtime,&safety,&recovery,public_key,target)) fatal();
  unsigned inactive;
#ifdef VGW_APPLICATION_SLOT
  vgw_boot_trace(6);
  const unsigned running=VGW_APPLICATION_SLOT;
  uint32_t vector=io->read32(io->ctx,0xe000ed08U);
  if (!vgw_boot_resume(running,vector)) fatal();
  inactive=1-running;
#else
  vgw_boot_choice choice;
  vgw_boot_trace(7);
  int selected=vgw_boot_select(&choice);
  if (selected>=0) {
    indication(VGW_LED_BOOT,(uint8_t)selected,0);
    vgw_boot_trace(8);
    (void)vgw_white_physical_handoff(&choice); fatal();
  }
  if (selected!=-1) fatal();
  inactive=0; /* Both invalid: fixed self-contained signed recovery. */
#endif
  vgw_update_io updater;
  vgw_boot_trace(9);
  if (!vgw_board_storage_updater(&storage,inactive,&updater) ||
      !vgw_recovery_service_init(&recovery,&provision,&updater,vgw_crypto_verify,vgw_crypto_authority_hash,
        vgw_crypto_hmac,vgw_crypto_sha256,entropy,&storage.crypto,&runtime.rng,inactive) ||
      !vgw_application_init(&application,&runtime,&recovery)) fatal();
#if defined(VGW_HVAC_EXPERIMENT) && defined(VGW_APPLICATION_SLOT)
  vgw_hvac_trial_init(&hvac,&safety);
#endif
  hvac_attach();
#ifdef VGW_APPLICATION_SLOT
  /* Confirm only after the complete mandatory application/control stack has
   * initialized, not merely after reaching the application reset vector. */
  vgw_boot_trace(10);
  if (!vgw_boot_confirm()) fatal();
#endif
  vgw_boot_trace(11);
  if (!vgw_board_storage_recovery_ready(&storage)) fatal();
  uint8_t initial_status[3];
  vgw_boot_get_status(initial_status);
  indication(initial_status[0],initial_status[1],initial_status[2]);
#ifdef VGW_BOARD_USB
  /* Do not advertise a USB device while signature verification/boot work
   * cannot service enumeration requests. The cold recovery window is separate
   * and already disconnected before runtime startup. Only connect the normal
   * interface once its cooperative dispatcher can run continuously. */
  vgw_boot_trace(4);
  if (!vgw_white_usb_init(&runtime.startup,false)) fatal();
#endif
  for (;;) {
    vgw_boot_trace(12);
#ifdef VGW_BOARD_USB
    if (!vgw_white_usb_poll()) fatal();
    if (vgw_white_usb_owned() && !runtime.local_control) {
      /* One control owner, latched until reboot. Local USB cannot inherit a
       * CAN session or steal its active flash authorization. Re-authenticate. */
      vgw_recovery_service_close(&recovery);
      if (!vgw_recovery_service_init(&recovery,&provision,&updater,vgw_crypto_verify,vgw_crypto_authority_hash,
          vgw_crypto_hmac,vgw_crypto_sha256,entropy,&storage.crypto,&runtime.rng,inactive) ||
          !vgw_application_init(&application,&runtime,&recovery) || !vgw_recovery_link_init(&runtime.recovery,request)) fatal();
      runtime.local_control=true;
      hvac_attach();
      application.local_send=vgw_white_usb_telemetry_send;
    }
#endif
    vgw_boot_trace(13);
    if (!vgw_white_runtime_step(&runtime,vgw_application_step,&application)) fatal();
#if defined(VGW_HVAC_EXPERIMENT) && defined(VGW_APPLICATION_SLOT)
    vgw_hvac_trial_step(&hvac,runtime.can.elapsed_ms,runtime.local_control && recovery.active &&
      runtime.can.elapsed_ms<application.lease_until && recovery.update.state!=VGW_UPDATE_RECEIVING);
#endif
#ifdef VGW_BOARD_USB
    vgw_boot_trace(14);
    if (runtime.local_control && !vgw_white_usb_dispatch(&recovery,runtime.can.elapsed_ms)) fatal();
#endif
    vgw_application_telemetry(&application);
    uint8_t status[3];
    if (!vgw_update_led(&recovery.update,runtime.can.elapsed_ms,status)) vgw_boot_get_status(status);
    vgw_led_rx_health(status,runtime.can.tx_inhibited);
    indication(status[0],status[1],status[2]);
    vgw_boot_trace(15);
    /* Never auto-reboot after update: host retains control of parked validation.
     * Power cycle selects the signed trial; failed trial reverts on next boot. */
  }
}
