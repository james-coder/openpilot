#include "debug_memory.h"
static uint32_t get(const uint8_t *p) {
  return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);
}
static void put(uint8_t *p,uint32_t n) { for (unsigned i=0;i<4;i++) p[i]=(uint8_t)(n>>(8*i)); }
void vgw_debug_memory(const uint8_t *request,size_t length,uint8_t response[16]) {
  uint32_t status=1,address=0,value=0;
  if (!response) return;
  if (request && length==16 && get(request)==0x314d4756U) {
    uint32_t op=get(request+4); address=get(request+8);
    if (op<=1 && !(address&3U)) {
      if (op==0) value=vgw_debug_read32(address);
      else { value=get(request+12); vgw_debug_write32(address,value); }
      status=0;
    }
  }
  put(response,0x31524756U); put(response+4,status); put(response+8,address); put(response+12,value);
}
