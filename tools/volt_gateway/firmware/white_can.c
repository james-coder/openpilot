#include "white_can.h"
#define RESET 0x40023820U
#define ENABLE 0x40023840U
#define MCR 0U
#define MSR 4U
#define TSR 8U
#define FIFO 12U
#define IER 20U
#define ESR 24U
#define BTR 28U
#define NART_RFLM 24U
#define RAM __attribute__((section(".ramfunc.vgw_can"), noinline))
static RAM uint32_t base(unsigned controller) {
  return controller==1 ? 0x40006400U : controller==2 ? 0x40006800U : 0x40006c00U;
}
static RAM const vgw_white_mmio *io_of(const vgw_white_can *s) { return &s->clock->startup->watchdog.io; }
static RAM uint32_t rd(const vgw_white_can *s,uint32_t a) { const vgw_white_mmio *io=io_of(s); return io->read32(io->ctx,a); }
static RAM void wr(const vgw_white_can *s,uint32_t a,uint32_t v) { const vgw_white_mmio *io=io_of(s); io->write32(io->ctx,a,v); }
static void change(const vgw_white_can *s,uint32_t a,uint32_t mask,uint32_t v) { wr(s,a,(rd(s,a)&~mask)|v); }
static RAM uint32_t inc(uint32_t n) { return n==UINT32_MAX ? n : n+1; }
static RAM uint32_t lock(void) {
  uint32_t saved=0;
#if defined(__arm__) || defined(__thumb__)
  __asm__ volatile("mrs %0, primask\ncpsid i" : "=r"(saved) :: "memory");
#endif
  return saved;
}
static RAM void unlock(uint32_t saved) {
#if defined(__arm__) || defined(__thumb__)
  __asm__ volatile("dmb\nmsr primask, %0" :: "r"(saved) : "memory");
#else
  (void)saved;
#endif
}
/* Accidental configuration-corruption check, not an authentication mechanism. */
static RAM uint32_t config_check(const vgw_white_can_config *c) {
  uint8_t bytes[5]={c->swcan_controller,c->hscan_mask,c->backhaul_controller,
    (uint8_t)c->response_id,(uint8_t)(c->response_id>>8)};
  uint32_t crc=0xffffffffU;
  for (unsigned i=0;i<5;i++) {
    crc^=bytes[i];
    for (unsigned j=0;j<8;j++) crc=(crc>>1)^((crc&1U) ? 0xedb88320U : 0U);
  }
  return ~crc;
}
static RAM bool active(const vgw_white_can *s,unsigned c) { return c==s->config.swcan_controller || (s->config.hscan_mask&(1U<<(c-1))); }
static RAM uint32_t timing(const vgw_white_can *s,unsigned c) {
  /* 24MHz / (8 quanta *6)=500k; /90=33.333k. 87.5% sample point. */
  return (5U<<16) | (c==s->config.swcan_controller ? 89U : 5U) |
    (c==s->config.backhaul_controller
#ifdef VGW_HVAC_EXPERIMENT
     || (c==3 && c==s->config.swcan_controller)
#endif
     ? 0U : 0x80000000U);
}
static bool wait(const vgw_white_can *s,uint32_t a,uint32_t mask,uint32_t expect) {
  uint32_t start=rd(s,0x40000024U);
  for (unsigned n=0;n<100000;n++) {
    if ((rd(s,a)&mask)==expect) return true;
    if ((uint32_t)(rd(s,0x40000024U)-start)>10U) break;
  }
  return false;
}
RAM void vgw_white_can_stop(vgw_white_can *s) {
  if (!s) return;
  uint32_t saved=lock();
  s->ready=false; s->failed=true; s->pending=false; s->tokens=0;
  if (s->clock && s->clock->startup) (void)vgw_white_quiesce(io_of(s));
  unlock(saved);
}
static bool alternate(const vgw_white_can *s,uint32_t port,unsigned pin,unsigned af,bool pullup) {
  uint32_t afr=port+32U+(pin/8U)*4U, shift=(pin%8U)*4U, pair=pin*2U;
  change(s,port+4U,1U<<pin,0);
  change(s,port+8U,3U<<pair,2U<<pair);
  change(s,port+12U,3U<<pair,pullup ? 1U<<pair : 0);
  change(s,afr,15U<<shift,af<<shift);
  change(s,port,3U<<pair,2U<<pair);
  return (rd(s,afr)&(15U<<shift))==(af<<shift) && (rd(s,port)&(3U<<pair))==(2U<<pair) &&
    !(rd(s,port+4U)&(1U<<pin)) && (rd(s,port+12U)&(3U<<pair))==(pullup ? 1U<<pair : 0) &&
    (rd(s,port+8U)&(3U<<pair))==(2U<<pair);
}
static bool filters(const vgw_white_can *s,unsigned controller,uint32_t enabled) {
  uint32_t b=base(controller), split=controller==1 ? 14U<<8 : 0;
  /* CAN1 owns the shared CAN2SB field; CAN3 only needs FINIT. Reserved
   * FMR bits are nonzero on silicon (CAN1 observed 0x2a1c0e01 at reset).
   * Preserve them and verify only the documented writable fields. */
  uint32_t mask=controller==1 ? 0x3f01U : 1U;
  uint32_t fmr=(rd(s,b+0x200U)&~mask)|split;
  wr(s,b+0x200U,fmr|1U);
  wr(s,b+0x21cU,0); wr(s,b+0x204U,0); /* inactive, mask mode */
  wr(s,b+0x20cU,enabled); wr(s,b+0x214U,0); /* 32 bit, FIFO0 */
  wr(s,b+0x240U,0); wr(s,b+0x244U,0);
  if (controller==1) { wr(s,b+0x2b0U,0); wr(s,b+0x2b4U,0); }
  wr(s,b+0x21cU,enabled); wr(s,b+0x200U,fmr);
  return (rd(s,b+0x200U)&mask)==split && rd(s,b+0x21cU)==enabled && rd(s,b+0x20cU)==enabled &&
    rd(s,b+0x204U)==0 && rd(s,b+0x214U)==0 && rd(s,b+0x240U)==0 && rd(s,b+0x244U)==0 &&
    (controller!=1 || (rd(s,b+0x2b0U)==0 && rd(s,b+0x2b4U)==0));
}
bool vgw_white_can_init(vgw_white_can *s,const vgw_white_clock *clock,const vgw_white_can_config *config) {
  if (!s) return false;
  *s=(vgw_white_can){.clock=clock,.failed=true};
  if (!vgw_white_clock_valid(clock) || !clock->startup->watchdog.started || !config || config->swcan_controller<2 || config->swcan_controller>3 ||
      !config->hscan_mask || config->hscan_mask>7 || (config->hscan_mask&(1U<<(config->swcan_controller-1))) ||
      config->backhaul_controller>3 || config->response_id>0x7ffU ||
      (config->backhaul_controller && !(config->hscan_mask&(1U<<(config->backhaul_controller-1))))) return false;
  s->config=*config;
  s->config_check=config_check(config);
  if ((rd(s,RESET)&(7U<<25))!=(7U<<25)) goto fail;
  /* CAN2 depends on CAN1 clock/filter block even if CAN1 RX is unused. */
  change(s,ENABLE,0,7U<<25);
  if ((rd(s,ENABLE)&(7U<<25))!=(7U<<25)) goto fail;
  change(s,RESET,7U<<25,0);
  if (rd(s,RESET)&(7U<<25)) goto fail;
  for (unsigned c=1;c<=3;c++) {
    uint32_t b=base(c);
    wr(s,b+IER,0); wr(s,b+MCR,NART_RFLM|1U);
    if (!wait(s,b+MSR,1,1) || rd(s,b+IER)!=0) goto fail;
    wr(s,b+BTR,timing(s,c));
    if (rd(s,b+BTR)!=timing(s,c)) goto fail;
  }
  if (!filters(s,1,(active(s,1) ? 1U : 0U)|(active(s,2) ? 1U<<14 : 0U)) ||
      !filters(s,3,active(s,3) ? 1U : 0U)) goto fail;
  for (unsigned c=1;c<=3;c++) {
    if (!active(s,c)) continue;
    /* F413 CAN1 PB8/PB9 is AF8, unlike F205/F405's AF9. Verified against
     * DS11581 table 11 and the historical White gpio_init STM32F4 branch. */
    uint32_t port=0x40020400U; unsigned rx,tx,af=c==1 ? 8 : c==3 ? 11 : 9;
    if (c==1) { rx=8; tx=9; }
    else if (c==2) { rx=c==config->swcan_controller ? 12 : 5; tx=rx+1; }
    else if (c==config->swcan_controller) { rx=3; tx=4; }
    else { port=0x40020000U; rx=8; tx=15; }
    /* Historical White setup pulls the GMLAN RX signal up (PB12).
     * Retain a recessive bias on the selected RX pad, including PB3/CAN3;
     * an undriven low/floating RX otherwise prevents bxCAN synchronization. */
    if (!alternate(s,port,rx,af,c==config->swcan_controller) || !alternate(s,port,tx,af,false)) goto fail;
    if (c==config->swcan_controller) {
      wr(s,0x40020418U,0xc000U); /* both mode bits simultaneously normal */
      if ((rd(s,0x40020414U)&0xc000U)!=0xc000U) goto fail;
    } else {
      uint32_t phy=c==3 ? 0x40020000U : 0x40020800U;
      uint32_t pin=c==1 ? 2U : c==2 ? 8192U : 1U;
      wr(s,phy+24U,pin<<16);
      if (rd(s,phy+20U)&pin) goto fail;
    }
    wr(s,base(c)+MCR,NART_RFLM);
    if (!wait(s,base(c)+MSR,1,0) || rd(s,base(c)+MCR)!=NART_RFLM) goto fail;
  }
  s->previous_ms=s->token_ms=rd(s,0x40000024U);
  for (unsigned c=0;c<3;c++) for (unsigned w=0;w<3;w++) s->stats[c].window_start[w]=s->previous_ms;
  s->tokens=2; s->ready=true; s->failed=false;
  return true;
fail:
  vgw_white_can_stop(s); return false;
}
static RAM void account(vgw_white_can *s,unsigned c,uint32_t now,uint32_t bits) {
  vgw_white_can_stats *st=&s->stats[c-1];
  for (unsigned w=0;w<3;w++) {
    uint32_t span=w==0 ? 10 : w==1 ? 100 : 1000;
    if ((uint32_t)(now-st->window_start[w])>=span) {
      st->window_start[w]=now; st->window_bits[w]=0;
    }
    if (UINT32_MAX-st->window_bits[w]>=bits) st->window_bits[w]+=bits;
    else st->window_bits[w]=UINT32_MAX;
    uint32_t rate=c==s->config.swcan_controller ? 33 : 500;
    uint32_t capacity=rate*span;
    uint32_t load=st->window_bits[w]>=capacity ? 1000U : st->window_bits[w]*1000U/capacity;
    if (load>st->peak_permille) st->peak_permille=load;
  }
}
/* Called only while interrupts are masked. Stage before any expensive callback.
 * The real FIFO holds three frames; eight reads also bound arrival during drain.
 * Separate per-controller queues prevent a busy HSCAN consuming SWCAN storage. */
static RAM void collect(vgw_white_can *s,unsigned c) {
  uint32_t b=base(c); unsigned index=c-1U;
  vgw_white_can_stats *st=&s->stats[index];
  for (unsigned n=0;n<8;n++) {
    uint32_t fifo=rd(s,b+FIFO);
    if (fifo&16U) { st->overflow=inc(st->overflow); s->tx_inhibited=true; wr(s,b+FIFO,16U); }
    if (!(fifo&3U)) break;
    vgw_white_can_raw raw={rd(s,b+0x1b0U),rd(s,b+0x1b4U)&15U,
      rd(s,b+0x1b8U),rd(s,b+0x1bcU),rd(s,0x40000024U),s->sequence++};
    wr(s,b+FIFO,32U);
    st->received=inc(st->received);
    account(s,c,raw.stamp,(raw.id&4U) ? 185U : 160U);
    if (raw.dlc>8) { st->malformed=inc(st->malformed); s->tx_inhibited=true; continue; }
    if (s->rx_count[index]>=VGW_CAN_RX_DEPTH) {
      st->software_drops=inc(st->software_drops); s->tx_inhibited=true; continue;
    }
    unsigned tail=s->rx_tail[index];
    if (tail>=VGW_CAN_RX_DEPTH) { s->tx_inhibited=true; s->failed=true; return; }
    s->rx[index][tail]=raw;
    s->rx_tail[index]=(uint8_t)((tail+1U)%VGW_CAN_RX_DEPTH);
    s->rx_count[index]++;
    if (s->rx_count[index]>st->queue_peak) st->queue_peak=s->rx_count[index];
  }
}
bool vgw_white_can_enable_rx(vgw_white_can *s) {
  if (!s || !s->ready || s->failed || s->rx_interrupts) return false;
  uint32_t saved=lock();
  s->rx_interrupts=true;
  bool ok=true;
  for (unsigned c=1;c<=3;c++) {
    uint32_t mask=active(s,c) ? 2U : 0U; /* FIFO0 pending only, no TX IRQ */
    wr(s,base(c)+IER,mask);
    if (rd(s,base(c)+IER)!=mask) ok=false;
  }
  if (!ok) vgw_white_can_stop(s);
  unlock(saved); return ok;
}
RAM void vgw_white_can_rx_irq(vgw_white_can *s,unsigned c) {
  if (!s || c<1 || c>3 || !s->ready || s->failed) return;
  uint32_t saved=lock();
  if (s->config_check!=config_check(&s->config) || !active(s,c)) {
    vgw_white_can_stop(s);
  } else {
    s->stats[c-1U].irq_calls=inc(s->stats[c-1U].irq_calls);
    collect(s,c);
  }
  unlock(saved);
}
RAM bool vgw_white_can_poll(vgw_white_can *s,vgw_can_receive receive,void *context) {
  if (!s || !s->ready || s->failed) return false;
  if (s->config_check!=config_check(&s->config) || !vgw_white_clock_valid(s->clock)) goto fail;
  uint32_t now=rd(s,0x40000024U), delta=now-s->previous_ms;
  if (delta>250U) goto fail;
  s->previous_ms=now; s->elapsed_ms+=delta;
  for (unsigned c=1;c<=3;c++) {
    if (!active(s,c)) continue;
    uint32_t b=base(c); vgw_white_can_stats *st=&s->stats[c-1];
    if (rd(s,b+BTR)!=timing(s,c) || rd(s,b+MCR)!=NART_RFLM || rd(s,b+IER)!=(s->rx_interrupts ? 2U : 0U)) goto fail;
    st->last_esr=rd(s,b+ESR);
    if (st->last_esr&6U) goto fail; /* error passive or bus-off: no automatic restart */
    uint32_t saved=lock();
    /* An IRQ may have accounted a newer timestamp since poll's initial sample.
     * Never roll its utilization window backwards using that older sample. */
    account(s,c,rd(s,0x40000024U),0);
    unlock(saved);
    for (unsigned n=0;n<8;n++) {
      saved=lock();
      collect(s,c); /* Also service while flash code has IRQs globally masked. */
      unsigned index=c-1U, head=s->rx_head[index];
      if (head>=VGW_CAN_RX_DEPTH || s->rx_count[index]>VGW_CAN_RX_DEPTH || s->failed) {
        unlock(saved); goto fail;
      }
      if (!s->rx_count[index]) { unlock(saved); break; }
      vgw_white_can_raw raw=s->rx[index][head];
      s->rx_head[index]=(uint8_t)((head+1U)%VGW_CAN_RX_DEPTH); s->rx_count[index]--;
      unlock(saved);
      uint32_t id=raw.id, dlc=raw.dlc, lo=raw.lo, hi=raw.hi;
      uint32_t sampled=rd(s,0x40000024U);
      uint32_t age=sampled-raw.stamp;
      if (age>st->max_queue_age_ms) st->max_queue_age_ms=age;
      uint64_t current=s->elapsed_ms+(uint32_t)(sampled-now);
      vgw_frame f={.timestamp_us=(current>=age ? current-age : 0)*1000U,.sequence=raw.sequence,
        .address=(id&4U) ? id>>3 : id>>21,.bus=(uint8_t)(c==s->config.swcan_controller ? 3 : c-1),
        .flags=(uint8_t)(((id&4U) ? VGW_EXTENDED : 0)|((id&2U) ? VGW_RTR : 0)),.dlc=(uint8_t)dlc};
      if (!(id&2U)) for (unsigned j=0;j<dlc;j++) f.data[j]=(uint8_t)((j<4 ? lo : hi)>>(8*(j%4)));
      if (receive) receive(context,&f);
    }
    if (c==s->config.backhaul_controller && s->pending) {
      uint32_t tsr=rd(s,b+TSR);
      if (tsr&1U) {
        if (tsr&2U) st->transmitted=inc(st->transmitted);
        if (tsr&4U) st->arbitration_lost=inc(st->arbitration_lost);
        if (tsr&8U) { st->tx_errors=inc(st->tx_errors); s->tx_inhibited=true; }
        wr(s,b+TSR,1U); s->pending=false;
      } else if ((uint32_t)(now-s->pending_ms)>20U) goto fail;
    }
  }
  return true;
fail:
  vgw_white_can_stop(s); return false;
}
static RAM bool response(vgw_white_can *s,const uint8_t data[8]) {
  if (!s || !data || !s->ready || s->failed || s->tx_inhibited || s->pending || !s->config.backhaul_controller) return false;
  for (unsigned c=1;c<=3;c++) if (active(s,c) && (s->rx_count[c-1U] || (rd(s,base(c)+FIFO)&3U))) return false;
  if (s->config_check!=config_check(&s->config) || !vgw_white_clock_valid(s->clock)) { vgw_white_can_stop(s); return false; }
  uint32_t now=rd(s,0x40000024U), b=base(s->config.backhaul_controller);
  if ((uint32_t)(now-s->previous_ms)>2U) return false; /* fresh health/RX service */
  if (rd(s,b+BTR)!=timing(s,s->config.backhaul_controller) || rd(s,b+MCR)!=NART_RFLM ||
      (rd(s,b+ESR)&7U)) return false;
  vgw_white_can_stats *st=&s->stats[s->config.backhaul_controller-1];
  if (st->window_bits[0]>=2500U || st->window_bits[1]>=25000U || st->window_bits[2]>=250000U) return false;
  uint32_t refill=(now-s->token_ms)/10U;
  if (refill) { s->tokens=(uint8_t)(refill>=2 || s->tokens+refill>=2 ? 2 : s->tokens+refill); s->token_ms=now; }
  if (!s->tokens || !(rd(s,b+TSR)&(1U<<26))) return false;
  uint32_t lo=0,hi=0;
  for (unsigned j=0;j<4;j++) { lo|=(uint32_t)data[j]<<(8*j); hi|=(uint32_t)data[j+4]<<(8*j); }
  wr(s,b+0x184U,8); wr(s,b+0x188U,lo); wr(s,b+0x18cU,hi);
  if (rd(s,b+0x184U)!=8 || rd(s,b+0x188U)!=lo || rd(s,b+0x18cU)!=hi) {
    vgw_white_can_stop(s); return false;
  }
  wr(s,b+0x180U,((uint32_t)s->config.response_id<<21)|1U);
  s->tokens--; s->pending=true; s->pending_ms=now;
  account(s,s->config.backhaul_controller,now,160U);
  return true;
}
bool vgw_white_can_response(vgw_white_can *s,const uint8_t data[8]) {
  uint32_t saved=lock();
  bool ok=response(s,data);
  unlock(saved); return ok;
}
