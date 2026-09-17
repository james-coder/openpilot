#include "hvac_trial.h"
#define CAN3 0x40006c00U
static const vgw_white_mmio *io(vgw_hvac_trial *s) { return &s->safety->can->clock->startup->watchdog.io; }
static uint32_t rd(vgw_hvac_trial *s,uint32_t off) { const vgw_white_mmio *m=io(s); return m->read32(m->ctx,CAN3+off); }
static void wr(vgw_hvac_trial *s,uint32_t off,uint32_t v) { const vgw_white_mmio *m=io(s); m->write32(m->ctx,CAN3+off,v); }
static void fault(vgw_hvac_trial *s,uint8_t reason) {
  s->reason=reason; s->tx_status=rd(s,8); s->error_status=rd(s,24);
  /* Abort only our SWCAN mailbox. No automatic retry or bus restart. */
  wr(s,8,1U<<7);
  /* An isolated raw-frame arbitration loss/timeout is an unsuccessful request,
   * not a permanent device failure. No retry; the next explicit command must
   * pass every interlock, cooldown and mailbox-empty check again. */
  s->state=(s->raw && (reason==2 || reason==3) && !(s->error_status&7U)) ? 6 : 5;
  s->allowed=false;
}
void vgw_hvac_trial_init(vgw_hvac_trial *s,vgw_white_safety *safety) {
  if (s) *s=(vgw_hvac_trial){.safety=safety};
}
static bool parked(const vgw_hvac_trial *s,uint64_t now) {
  if (!s || !s->safety || s->safety->seen!=7) return false;
  for (unsigned i=0;i<3;i++)
    if (!s->safety->safe[i] || now<s->safety->received[i] || now-s->safety->received[i]>250U) return false;
  return true;
}
bool vgw_hvac_trial_can_restart(const vgw_hvac_trial *s,uint64_t now) {
  return parked(s,now) && !(s->state>=1 && s->state<=3);
}
static bool healthy(vgw_hvac_trial *s,uint64_t now) {
  vgw_white_can *c=s->safety->can;
  return c->ready && !c->failed && !c->tx_inhibited && c->config.swcan_controller==3 &&
    c->config.backhaul_controller==2 && c->config.hscan_mask==3 &&
    now>=c->elapsed_ms && now-c->elapsed_ms<=10U &&
    rd(s,28)==((5U<<16)|89U) && rd(s,0)==24U && !(rd(s,24)&7U);
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
  s->raw=false; s->reason=0; s->tx_status=0; s->error_status=0;
  if (!send(s,true)) { fault(s,5); return false; }
  s->state=1; s->deadline=now+20U;
  return true;
}
bool vgw_hvac_trial_raw(vgw_hvac_trial *s,uint64_t now,const uint8_t *p,size_t n) {
  if (!p || n<6 || p[4]>1 || p[5]>8 || n!=(size_t)(6U+p[5])) return false;
  uint32_t address=((uint32_t)p[0]<<24)|((uint32_t)p[1]<<16)|((uint32_t)p[2]<<8)|p[3];
  if (address>(p[4] ? 0x1fffffffU : 0x7ffU)) return false;
  if (!s || !s->safety || !s->allowed || s->previous!=now || !parked(s,now) ||
      (s->state!=0 && s->state!=4 && s->state!=6) || now<s->cooldown || !healthy(s,now)) return false;
  if (!(rd(s,8)&(1U<<26))) return false;
  s->raw=true; s->reason=0; s->tx_status=0; s->error_status=0;
  uint32_t low=0,high=0;
  for (unsigned i=0;i<p[5];i++) {
    if (i<4) low|=(uint32_t)p[6+i]<<(8*i);
    else high|=(uint32_t)p[6+i]<<(8*(i-4));
  }
  wr(s,8,1U);
  wr(s,0x184,p[5]); wr(s,0x188,low); wr(s,0x18c,high);
  if (rd(s,0x184)!=p[5] || rd(s,0x188)!=low || rd(s,0x18c)!=high) {
    fault(s,5); return false;
  }
  /* Exactly one outstanding frame, at most one per second. State 3 completes
   * directly to done; unlike the legacy button pair it schedules no release. */
  s->cooldown=now+1000U;
  if (s->attempts<UINT8_MAX) s->attempts++;
  s->state=3; s->deadline=now+20U;
  wr(s,0x180,p[4] ? (address<<3)|5U : (address<<21)|1U);
  return true;
}
void vgw_hvac_trial_step(vgw_hvac_trial *s,uint64_t now,bool authorized) {
  if (!s || !s->safety || !s->safety->can || s->state==5) return;
  uint64_t sampled=0; bool safe=false;
  /* Keep flash-power diagnostics sampled, but do not impose the flash erase
   * voltage/dwell policy on two volatile cabin-button frames. Firmware updates
   * retain their separate, unchanged supply/awake requirements. */
  (void)vgw_white_safety_sample(s->safety,now,&sampled,&safe);
  s->allowed=parked(s,now) && authorized && healthy(s,now) && now>=s->previous;
  s->previous=now;
  if (s->state==0 || s->state==4 || s->state==6) return;
  if (s->state==1 || s->state==3) {
    uint32_t status=rd(s,8);
    if (status&1U) {
      vgw_white_can_stats *stats=&s->safety->can->stats[2];
      s->tx_status=status; s->error_status=rd(s,24);
      if ((status&4U) && stats->arbitration_lost!=UINT32_MAX) stats->arbitration_lost++;
      if ((status&8U) && stats->tx_errors!=UINT32_MAX) stats->tx_errors++;
      if ((status&14U)!=2U) {
        fault(s,(status&8U) ? 4 : (status&4U) ? 3 : 4);
        wr(s,8,1U); return;
      }
      wr(s,8,1U);
      /* Account for completed hardware TX before evaluating permission for
       * any future send. A delayed poll must not hide a successful frame. */
      if (stats->transmitted!=UINT32_MAX) stats->transmitted++;
      if (s->state==3) s->state=4;
      else if (!s->allowed) fault(s,1);
      else { s->state=2; s->deadline=now+2500U; }
    } else if (now>=s->deadline) fault(s,2);
    else if (!s->allowed) fault(s,1);
  } else if (s->state==2 && now>=s->deadline) {
    /* Never emit a delayed release after an unserviced scheduling gap. */
    if (!s->allowed) { fault(s,1); return; }
    if (now-s->deadline>20U || !send(s,false)) { fault(s,6); return; }
    s->state=3; s->deadline=now+20U;
  } else if (s->state!=2 || !s->allowed) fault(s,1);
}
