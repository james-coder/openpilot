#include "recovery_service.h"
static void copy(uint8_t *d,const uint8_t *s,size_t n) { for (size_t i=0;i<n;i++) d[i]=s[i]; }
static void zero(void *p,size_t n) { volatile uint8_t *v=p; for (size_t i=0;i<n;i++) v[i]=0; }
static bool same(const uint8_t *a,const uint8_t *b,size_t n) { uint8_t d=0; for (size_t i=0;i<n;i++) d|=a[i]^b[i]; return d==0; }
static bool nonzero(const uint8_t *p,size_t n) { uint8_t v=0; for (size_t i=0;i<n;i++) v|=p[i]; return v!=0; }
static uint32_t u32(const uint8_t *p) { return ((uint32_t)p[0]<<24)|((uint32_t)p[1]<<16)|((uint32_t)p[2]<<8)|p[3]; }
static uint64_t u64(const uint8_t *p) { return ((uint64_t)u32(p)<<32)|u32(p+4); }
static void put32(uint8_t *p,uint32_t v) { for (unsigned i=0;i<4;i++) p[3-i]=(uint8_t)(v>>(8*i)); }
size_t vgw_recovery_service_size(void) { return sizeof(vgw_recovery_service); }
static void end_session(vgw_recovery_service *s) {
  if (s->application_reset) s->application_reset(s->application_context);
  if (s->update.authority && s->update.state!=VGW_UPDATE_TRIAL) vgw_update_abort(&s->update);
  vgw_authority_close(&s->authority);
  s->active=false; s->fresh=false; s->request_size=s->reply_size=0;
  zero(s->host_key,32); zero(s->gateway_key,32); zero(s->session,8);
  zero(s->last_request,512); zero(s->last_reply,512); zero(s->opened,69); zero(s->open_reply,56);
}
bool vgw_recovery_service_application(vgw_recovery_service *s,vgw_command_fn command,void (*reset)(void *),void *ctx) {
  if (!s || !s->ready || s->active || s->application || !command || !reset || !ctx) return false;
  s->application=command; s->application_reset=reset; s->application_context=ctx;
  reset(ctx); return true;
}
void vgw_recovery_service_close(vgw_recovery_service *s) {
  if (!s) return;
  end_session(s); s->ready=false; zero(s->provision.pairing,32); zero(s->hello,sizeof(s->hello));
}
bool vgw_recovery_service_tick(vgw_recovery_service *s,uint64_t now) {
  if (!s || !s->ready) return false;
  if (now<s->previous || now>=(UINT64_C(1)<<63)) { vgw_recovery_service_close(s); return false; }
  s->previous=now;
  if (s->active && (now>=s->expires || now-s->last_authenticated>=20000U)) end_session(s);
  return true;
}
static bool hello(vgw_recovery_service *s) {
  uint8_t nonce[32];
  if (!s->nonce(s->nonce_context,nonce) || !nonzero(nonce,32) || same(nonce,s->hello+115,32)) {
    zero(nonce,32); vgw_recovery_service_close(s); return false;
  }
  copy(s->hello,(const uint8_t *)"VGR1\x00\x01\x00",7);
  copy(s->hello+7,s->provision.device,12); copy(s->hello+19,s->provision.layout,32);
  copy(s->hello+51,s->provision.policy,32); copy(s->hello+83,s->provision.build,32);
  copy(s->hello+115,nonce,32); zero(nonce,32); s->fresh=true; return true;
}
bool vgw_recovery_service_init(vgw_recovery_service *s,const vgw_recovery_provision *p,const vgw_update_io *storage,
  vgw_verify_fn verify,vgw_sha256_fn authority_hash,vgw_hmac_fn hmac,vgw_hash_fn hash,vgw_nonce_fn nonce,
  void *crypto,void *entropy,unsigned inactive_slot) {
  if (!s) return false;
  zero(s,sizeof(*s));
  if (!p || !storage || !verify || !authority_hash || !hmac || !hash || !nonce || inactive_slot>1 ||
      !nonzero(p->pairing,32) || !nonzero(p->device,12) || !nonzero(p->layout,32) ||
      !nonzero(p->policy,32) || !nonzero(p->build,32)) return false;
  s->provision=*p; s->storage=*storage; s->verify=verify; s->authority_hash=authority_hash;
  s->target_slot=(uint8_t)inactive_slot;
  s->hmac=hmac; s->hash=hash; s->nonce=nonce; s->crypto_context=crypto; s->nonce_context=entropy;
  s->authority.phase=VGW_PROGRAM;
  if (!vgw_update_init(&s->update,&s->authority,storage)) { vgw_recovery_service_close(s); return false; }
  s->ready=true; return hello(s);
}
static bool derive(vgw_recovery_service *s,const uint8_t host[32],uint8_t transcript_hash[32]) {
  uint8_t transcript[179],salt_input[64],salt[32],prk[32],info[52];
  copy(transcript,s->hello,147); copy(transcript+147,host,32);
  copy(salt_input,host,32); copy(salt_input+32,s->hello+115,32);
  bool ok=s->hash(transcript,sizeof(transcript),transcript_hash) && s->hash(salt_input,64,salt) &&
    s->hmac(salt,s->provision.pairing,32,prk);
  /* RFC5869 single block, matching protocol.session_key. Direction separation. */
  copy(info,(const uint8_t *)"voltgw-v1\0host",14);
  copy(info+14,transcript_hash,32); info[46]=1;
  ok=ok && s->hmac(prk,info,47,s->host_key);
  copy(info,(const uint8_t *)"voltgw-v1\0gateway",17);
  copy(info+17,transcript_hash,32); info[49]=1;
  ok=ok && s->hmac(prk,info,50,s->gateway_key);
  copy(s->session,transcript_hash,8);
  zero(transcript,sizeof(transcript)); zero(prk,32); zero(info,sizeof(info)); zero(salt,32);
  return ok;
}
/* Preserve the storage adapter's original context while enforcing transport
 * lease and monotonic time on every updater freshness check, including inside
 * erase/program/readback/commit. A pairing session alone still grants no flash. */
static bool sample(void *ctx,uint64_t *now,uint64_t *sampled,bool *allowed) {
  vgw_recovery_service *s=ctx;
  if (!s->storage.sample(s->storage.ctx,now,sampled,allowed)) return false;
  *allowed=*allowed && s->active && *now>=s->previous && *now<s->expires;
  if (*now>=s->previous) s->previous=*now;
  return true;
}
static bool erase(void *ctx,uint32_t o,uint32_t n) { vgw_recovery_service *s=ctx; return s->storage.erase(s->storage.ctx,o,n); }
static bool write(void *ctx,uint32_t o,const uint8_t *p,size_t n) { vgw_recovery_service *s=ctx; return s->storage.write(s->storage.ctx,o,p,n); }
static bool read(void *ctx,uint32_t o,uint8_t *p,size_t n) { vgw_recovery_service *s=ctx; return s->storage.read(s->storage.ctx,o,p,n); }
static bool hash_start(void *ctx) { vgw_recovery_service *s=ctx; return s->storage.hash_start(s->storage.ctx); }
static bool hash_add(void *ctx,const uint8_t *p,size_t n) { vgw_recovery_service *s=ctx; return s->storage.hash_add(s->storage.ctx,p,n); }
static bool hash_finish(void *ctx,uint8_t *p) { vgw_recovery_service *s=ctx; return s->storage.hash_finish(s->storage.ctx,p); }
static bool mark_trial(void *ctx,const uint8_t *p) { vgw_recovery_service *s=ctx; return s->storage.mark_trial(s->storage.ctx,p); }
static bool open_session(vgw_recovery_service *s,const uint8_t *p,uint64_t now,uint8_t out[512],size_t *n) {
  if (s->active) {
    if (!same(p,s->opened,69) || now-s->last_open<1000U) return false;
    s->last_open=now;
    copy(out,s->open_reply,56); *n=56; return true; /* no reset of sequence/grant */
  }
  if (!s->fresh || (s->open_seen && now-s->last_open<1000U) || !nonzero(p+5,32)) return false;
  s->open_seen=true; s->last_open=now;
  uint8_t signed_data[200],tag[32],digest[32]={0};
  copy(signed_data,(const uint8_t *)"VOLT GW RECOVERY OPEN",21);
  copy(signed_data+21,s->hello,147); copy(signed_data+168,p+5,32);
  if (!s->hmac(s->provision.pairing,signed_data,sizeof(signed_data),tag)) goto fatal;
  if (!same(tag,p+37,32)) { zero(tag,32); return false; }
  vgw_update_io adapter=s->storage;
  adapter.ctx=s; adapter.sample=sample; adapter.erase=erase; adapter.write=write; adapter.read=read;
  adapter.hash_start=hash_start; adapter.hash_add=hash_add; adapter.hash_finish=hash_finish; adapter.mark_trial=mark_trial;
  if (!derive(s,p+5,digest) || !vgw_authority_init(&s->authority,s->provision.device,s->provision.layout,
      VGW_PROGRAM,digest,s->verify,s->authority_hash,s->crypto_context) ||
      !vgw_update_init(&s->update,&s->authority,&adapter)) goto fatal;
  s->expires=now+3600000U; s->last_authenticated=now; s->next_sequence=0; s->active=true; s->fresh=false;
  copy(s->opened,p,69);
  copy(s->open_reply,(const uint8_t *)"VGR1\x01\x01\x00\x00",8);
  copy(s->open_reply+8,s->session,8); put32(s->open_reply+16,3600000U);
  put32(s->open_reply+20,256U); /* fixed maximum programming chunk */
  if (!s->hmac(s->gateway_key,s->open_reply,24,s->open_reply+24)) goto fatal;
  copy(out,s->open_reply,56); *n=56;
  zero(digest,32); zero(tag,32); return true;
fatal:
  zero(digest,32); zero(tag,32); vgw_recovery_service_close(s); return false;
}
static size_t operation(vgw_recovery_service *s,uint8_t op,const uint8_t *p,size_t n,uint64_t now,uint8_t out[128]) {
  bool ok=false; size_t length=1;
  switch (op) {
    case 0x80: { /* Signed-image challenge, transport MAC already verified. */
      uint8_t nonce[32];
      if (n==VGW_SIGNED_IMAGE_SIZE) {
        if (!s->nonce(s->nonce_context,nonce)) { vgw_recovery_service_close(s); zero(nonce,32); break; }
        ok=vgw_authority_challenge(&s->authority,p,n,now,nonce,out+1);
        if (ok) length+=VGW_CHALLENGE_SIZE;
      }
      zero(nonce,32); break;
    }
    case 0x81: if (n==VGW_AUTHORIZATION_SIZE) ok=vgw_authority_accept(&s->authority,p,n,now); break;
    case 0x82: if (!n) ok=vgw_update_begin(&s->update); break;
    case 0x83: if (n>4 && n<=260 && s->update.state==VGW_UPDATE_RECEIVING) ok=vgw_update_chunk(&s->update,u32(p),p+4,n-4); break;
    case 0x84:
      if (!n) ok=s->update.state==VGW_UPDATE_TRIAL ||
        (s->update.state==VGW_UPDATE_RECEIVING && vgw_update_finish(&s->update));
      break;
    case 0x85: /* state/offset only; no addresses, keys or hidden configuration */
      if (!n) { ok=true; out[1]=(uint8_t)s->update.state; put32(out+2,s->update.offset); length=6; } break;
    case 0x86: if (!n && s->update.state!=VGW_UPDATE_TRIAL) { vgw_update_abort(&s->update); ok=true; } break;
    case 0x87: if (!n) ok=true; break; /* authenticated session close, no config reset */
    case 0x88: if (!n) { ok=true; put32(out+1,s->storage.capacity); out[5]=s->target_slot; length=6; } break;
    default:
      if (op>=1 && op<=0x3f && s->application) {
        size_t result=s->application(s->application_context,op,p,n,now,out);
        if (result>=1 && result<=128) return result;
      }
      break; /* no raw TX/write/reset/session/option-byte operations */
  }
  out[0]=ok ? 0 : 1; return length;
}
bool vgw_recovery_service_request(vgw_recovery_service *s,const uint8_t *p,size_t n,uint64_t now,uint8_t out[512],size_t *out_size) {
  if (out_size) *out_size=0;
  if (!out || !out_size || !p || n>512 || !vgw_recovery_service_tick(s,now)) return false;
  if (n==5 && same(p,(const uint8_t *)"VGR1\x00",5)) {
    if (s->public_seen && now-s->last_public<1000U) return false;
    s->public_seen=true; s->last_public=now;
    if (!s->fresh && !s->active && !hello(s)) return false;
    copy(out,s->hello,147); *out_size=147; return true;
  }
  if (n==69 && same(p,(const uint8_t *)"VGR1\x01",5)) return open_session(s,p,now,out,out_size);
  if (!s->active || n<48) return false;
  uint8_t tag[32];
  if (!s->hmac(s->host_key,p,n-16,tag)) { vgw_recovery_service_close(s); return false; }
  if (!same(tag,p+n-16,16)) { zero(tag,32); return false; }
  zero(tag,32);
  if (!same(p,(const uint8_t *)"VGW1\x01\x00\x01",7)) { end_session(s); return false; }
  if (!same(p+8,s->session,8) || u32(p+28)!=n-48) return false;
  if (s->request_size==n && same(p,s->last_request,n)) {
    s->last_authenticated=now;
    copy(out,s->last_reply,s->reply_size); *out_size=s->reply_size; return true;
  }
  if (u64(p+16)!=s->next_sequence || s->next_sequence==UINT64_MAX) return false;
  s->last_authenticated=now;
  uint8_t result[128];
  size_t result_size=operation(s,p[7],p+32,n-48,now,result);
  s->last_authenticated=s->previous; /* includes elapsed synchronous flash work */
  if (!s->ready) { zero(result,sizeof(result)); return false; }
  /* Cache before exposing the reply: a retransmission must not repeat erase,
   * program, operator authorization or the trial-marker commit. */
  copy(s->last_request,p,n); s->request_size=(uint16_t)n; s->next_sequence++;
  copy(s->last_reply,p,32); s->last_reply[6]=2; put32(s->last_reply+28,(uint32_t)result_size);
  copy(s->last_reply+32,result,result_size);
  if (!s->hmac(s->gateway_key,s->last_reply,32+result_size,tag)) { vgw_recovery_service_close(s); return false; }
  copy(s->last_reply+32+result_size,tag,16); zero(tag,32); zero(result,sizeof(result));
  s->reply_size=(uint16_t)(48+result_size);
  copy(out,s->last_reply,s->reply_size); *out_size=s->reply_size;
  if (p[7]==0x87 && n==48) end_session(s);
  return true;
}
