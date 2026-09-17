#include "hvac_trial.h"
#define CAN3 0x40006c00U
static const vgw_white_mmio *io(vgw_hvac_trial *s) { return &s->safety->can->clock->startup->watchdog.io; }
static uint32_t rd(vgw_hvac_trial *s,uint32_t off) { const vgw_white_mmio *m=io(s); return m->read32(m->ctx,CAN3+off); }
static void wr(vgw_hvac_trial *s,uint32_t off,uint32_t v) { const vgw_white_mmio *m=io(s); m->write32(m->ctx,CAN3+off,v); }
static void fault(vgw_hvac_trial *s) {
  /* Abort only our SWCAN mailbox. No automatic retry or bus restart. */
  wr(s,8,1U<<7); s->state=5; s->allowed=false;
}
void vgw_hvac_trial_init(vgw_hvac_trial *s,vgw_white_safety *safety) {
  if (s) *s=(vgw_hvac_trial){.safety=safety};
}
static bool healthy(vgw_hvac_trial *s,uint64_t now) {
  vgw_white_can *c=s->safety->can;
  return c->ready && !c->failed && !c->tx_inhibited && c->config.swcan_controller==3 &&
    c->config.backhaul_controller==2 && c->config.hscan_mask==3 &&
    now==c->elapsed_ms && rd(s,28)==((5U<<16)|89U) && rd(s,0)==24U && !(rd(s,24)&7U);
}
static bool send(vgw_hvac_trial *s,bool press) {
  if (!(rd(s,8)&(1U<<26))) return false;
  /* Extended 0x10AD6080, DLC8. Two immutable payloads; no host bytes used.
   * Same action accompanied both ON and OFF presses. Meaning is provisional. */
  wr(s,8,1U);
  wr(s,0x184,8); wr(s,0x188,0x0207070aU); wr(s,0x18c,press ? 0x2bU : 0U);
  if (rd(s,0x184)!=8 || rd(s,0x188)!=0x0207070aU || rd(s,0x18c)!=(press ? 0x2bU : 0U)) return false;
  wr(s,0x180,(0x10ad6080U<<3)|5U);
  return true;
}
bool vgw_hvac_trial_start(vgw_hvac_trial *s,uint64_t now) {
  if (!s || !s->safety || !s->allowed || s->previous!=now ||
      (s->state!=0 && s->state!=4) || s->attempts>=4 || now<s->cooldown || !healthy(s,now)) return false;
  /* At most four attempts per boot, ten seconds between attempts. */
  s->attempts++; s->cooldown=now+10000U;
  if (!send(s,true)) { fault(s); return false; }
  s->state=1; s->deadline=now+20U;
  return true;
}
void vgw_hvac_trial_step(vgw_hvac_trial *s,uint64_t now,bool authorized) {
  if (!s || !s->safety || !s->safety->can || s->state==5) return;
  uint64_t sampled=0; bool safe=false;
  bool valid=vgw_white_safety_sample(s->safety,now,&sampled,&safe);
  s->allowed=valid && safe && sampled==now && authorized && healthy(s,now) && now>=s->previous;
  s->previous=now;
  if (s->state==0 || s->state==4) return;
  if (!s->allowed) { fault(s); return; }
  if (s->state==1 || s->state==3) {
    uint32_t status=rd(s,8);
    if (status&1U) {
      wr(s,8,1U);
      if ((status&14U)!=2U) { fault(s); return; }
      vgw_white_can_stats *stats=&s->safety->can->stats[2];
      if (stats->transmitted!=UINT32_MAX) stats->transmitted++;
      if (s->state==3) s->state=4;
      else { s->state=2; s->deadline=now+2500U; }
    } else if (now>=s->deadline) fault(s);
  } else if (s->state==2 && now>=s->deadline) {
    /* Never emit a delayed release after an unserviced scheduling gap. */
    if (now-s->deadline>20U || !send(s,false)) { fault(s); return; }
    s->state=3; s->deadline=now+20U;
  } else if (s->state!=2) fault(s);
}
