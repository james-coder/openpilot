#include "white_safety.h"
#define RAM __attribute__((section(".ramfunc.vgw_safety"), noinline))
#define ADC 0x40012000U
static RAM const vgw_white_mmio *io(vgw_white_safety *s) { return &s->can->clock->startup->watchdog.io; }
static RAM uint32_t rd(vgw_white_safety *s,uint32_t a) { const vgw_white_mmio *m=io(s); return m->read32(m->ctx,a); }
static RAM void wr(vgw_white_safety *s,uint32_t a,uint32_t v) { const vgw_white_mmio *m=io(s); m->write32(m->ctx,a,v); }
bool vgw_white_safety_init(vgw_white_safety *s,vgw_white_can *can,uint8_t pt,uint32_t divider) {
  if (!s || !can || !can->ready || pt>2 || !(can->config.hscan_mask&(1U<<pt)) ||
      (divider!=3791U && divider!=8862U)) return false;
  *s=(vgw_white_safety){.can=can,.divider_milli=divider,.powertrain_bus=pt};
  /* Historical White PC2 / ADC12 VIN sense. Divider is explicitly provisioned
   * for the identified revision: AB=3791, C=8862. Never select by measured VIN. */
  wr(s,0x40023844U,rd(s,0x40023844U)|(1U<<8));
  wr(s,0x40020800U,(rd(s,0x40020800U)&~48U)|48U);
  wr(s,0x4002080cU,rd(s,0x4002080cU)&~48U);
  wr(s,ADC+8,0); wr(s,ADC+4,0);
  wr(s,0x40012304U,1U<<16); /* PCLK2/4: 12MHz at the fixed 96MHz profile */
  wr(s,ADC+12,7U<<6); /* channel12: 480-cycle sampling */
  wr(s,ADC+56,12U<<15); /* injected length one, channel12 */
  wr(s,ADC+8,1);
  s->ready=(rd(s,0x40023844U)&(1U<<8)) && rd(s,ADC+8)==1 &&
    rd(s,ADC+12)==(7U<<6) && rd(s,ADC+56)==(12U<<15) && rd(s,0x40012304U)==(1U<<16);
  return s->ready;
}
RAM void vgw_white_safety_receive(void *ctx,const vgw_frame *f) {
  vgw_white_safety *s=ctx;
  if (!s || !s->ready || !f || f->bus!=s->powertrain_bus || f->flags) return;
  unsigned index;
  switch (f->address) {
    case 1001: index=0; break; /* ECMVehicleSpeed, both front wheel-derived speeds */
    case 309: index=1; break; /* ECMPRDNL */
    case 497: index=2; break; /* BCMGeneralPlatformStatus */
    default: return;
  }
  bool safe=false;
  if (f->dlc==8) {
    if (index==0) safe=!(f->data[0]|f->data[1]|f->data[4]|f->data[5]);
    if (index==1) safe=(f->data[0]&7U)==0; /* Park */
    if (index==2) safe=(f->data[0]&3U)==2; /* RUN, not accessory/off/crank */
  }
  s->received[index]=s->can->elapsed_ms; s->seen|=(uint8_t)(1U<<index); s->safe[index]=safe;
  if (!safe) s->stable=false;
}
static RAM bool power(vgw_white_safety *s,uint32_t now) {
  /* Nonblocking ADC. No polling loop, no stale power treated as current. */
  if (s->converting) {
    if (rd(s,ADC)&4U) {
      uint32_t raw=rd(s,ADC+60);
      s->converting=false;
      wr(s,ADC,rd(s,ADC)&~4U);
      s->power_valid=raw>0 && raw<4095U;
      s->voltage_mv=raw*s->divider_milli/1000U;
      s->adc_sampled=s->adc_started; /* never relabel an old completed conversion as fresh */
    } else if ((uint32_t)(now-s->adc_started)>2U) {
      s->power_valid=false; s->ready=false; return false;
    }
  }
  if (!s->converting && (!s->power_valid || (uint32_t)(now-s->adc_sampled)>=5U)) {
    wr(s,ADC,rd(s,ADC)&~4U); wr(s,ADC+8,1U|(1U<<22));
    s->adc_started=now; s->converting=true;
  }
  return s->power_valid && (uint32_t)(now-s->adc_sampled)<=10U &&
    s->voltage_mv>=12500U && s->voltage_mv<=15500U;
}
RAM bool vgw_white_safety_sample(void *ctx,uint64_t now,uint64_t *sampled,bool *allowed) {
  vgw_white_safety *s=ctx;
  if (allowed) *allowed=false;
  if (!s || !s->ready || !sampled || !allowed || now<s->previous) return false;
  s->previous=now; *sampled=now;
  bool safe=power(s,(uint32_t)now) && s->can->ready && !s->can->failed && !s->can->tx_inhibited && s->seen==7;
  for (unsigned i=0;i<3;i++) safe=safe && s->safe[i] && now>=s->received[i] && now-s->received[i]<=250U;
  if (!safe) { s->stable=false; return true; }
  if (!s->stable) { s->stable=true; s->stable_since=now; }
  /* Require five seconds of continuous fresh RUN/Park/zero-speed/voltage.
   * This cannot keep the vehicle awake; loss of any input cancels permission. */
  *allowed=now-s->stable_since>=5000U;
  return true;
}
