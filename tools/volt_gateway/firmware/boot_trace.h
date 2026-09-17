#ifndef VGW_BOOT_TRACE_H
#define VGW_BOOT_TRACE_H
#include <stdint.h>
/* Reserved outside BSS and below the reserved stack. Non-secret breadcrumbs
 * survive software reset; not an arbitrary memory-access interface. */
static inline void vgw_boot_trace(uint32_t phase) {
#ifdef __arm__
  volatile uint32_t *p=(volatile uint32_t *)0x2001c800U;
  if (phase>=100U && p[0]==0x56474231U && p[2]==~p[1] && p[3]==0x31424756U)
    phase|=p[1]&0xffff0000U;
#ifdef VGW_APPLICATION_SLOT
  phase|=(VGW_APPLICATION_SLOT+1U)<<16;
#endif
  p[0]=0x56474231U; p[1]=phase; p[2]=~phase; p[3]=0x31424756U;
  __asm__ volatile("" ::: "memory");
#else
  (void)phase;
#endif
}
#endif
