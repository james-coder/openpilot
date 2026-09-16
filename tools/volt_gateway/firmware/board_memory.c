#include <stdint.h>
#include <stddef.h>
#define RAM __attribute__((section(".ramfunc.memory"), noinline))
RAM void *memset(void *p,int c,size_t n) { uint8_t *d=p; for (size_t i=0;i<n;i++) d[i]=(uint8_t)c; return p; }
RAM void *memcpy(void *p,const void *q,size_t n) { uint8_t *d=p; const uint8_t *s=q; for (size_t i=0;i<n;i++) d[i]=s[i]; return p; }
RAM void *memmove(void *p,const void *q,size_t n) {
  if ((uintptr_t)p<=(uintptr_t)q) return memcpy(p,q,n);
  uint8_t *d=p; const uint8_t *s=q; for (size_t i=n;i>0;i--) d[i-1]=s[i-1]; return p;
}
RAM int memcmp(const void *p,const void *q,size_t n) {
  const uint8_t *a=p,*b=q; for (size_t i=0;i<n;i++) if (a[i]!=b[i]) return (int)a[i]-(int)b[i]; return 0;
}
size_t strlen(const char *s) { size_t n=0; while (s[n]) n++; return n; }
