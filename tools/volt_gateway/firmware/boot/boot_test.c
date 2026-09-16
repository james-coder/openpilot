/* Memory-backed native test binding only. No hardware or flash programming. */
#ifndef VGW_BOOT_TEST_HARNESS
#error Never link test flash into board firmware
#endif
#include "boot_port.h"
#include <setjmp.h>
#include <string.h>

static uint8_t flash[VGW_BOOT_FLASH_SIZE], key[91], target[44];
static jmp_buf escape;
static unsigned mutations, fail_at, mode, reads, fail_read;
static vgw_boot_choice choice;
static bool read_mem(void *ctx, uint32_t off, void *out, uint32_t len) {
  (void)ctx;
  if (++reads == fail_read || off > sizeof(flash) || len > sizeof(flash) - off) return false;
  memcpy(out, flash + off, len); return true;
}
static bool write_mem(void *ctx, uint32_t off, const void *data, uint32_t len) {
  (void)ctx;
  if (off > sizeof(flash) || len > sizeof(flash) - off) return false;
  bool fault = ++mutations == fail_at;
  if (fault && mode == 5) longjmp(escape, 1); /* abrupt loss before programming */
  if (fault && mode == 1) return false;
  if (fault && mode == 3) return true;
  uint32_t n = fault && mode == 2 ? len / 2 : len;
  const uint8_t *in = data;
  for (uint32_t i = 0; i < n; i++) {
    if ((flash[off+i] & in[i]) != in[i]) return false;
    flash[off+i] &= in[i];
  }
  if (fault && mode == 4) longjmp(escape, 1); /* loss after programming, before return */
  return !fault;
}
static bool erase_mem(void *ctx, uint32_t off, uint32_t len) {
  (void)ctx;
  if (off > sizeof(flash) || len > sizeof(flash) - off) return false;
  bool fault = ++mutations == fail_at;
  if (fault && mode == 5) longjmp(escape, 1);
  if (fault && mode == 1) return false;
  if (fault && mode == 3) return true;
  memset(flash + off, 255, fault && mode == 2 ? len / 2 : len);
  if (fault && mode == 4) longjmp(escape, 1);
  return !fault;
}
static void service(void *ctx) { (void)ctx; }
static void panic_test(void *ctx) { (void)ctx; longjmp(escape, 1); }
int vgw_test_setup(const uint8_t *public_key, const uint8_t *binding, const uint8_t *image, uint32_t size) {
  if (!public_key || !binding || !image || size != sizeof(flash)) return -1;
  memcpy(target, binding, sizeof(target));
  memcpy(key, public_key, sizeof(key)); memcpy(flash, image, size);
  mutations = fail_at = mode = reads = fail_read = 0;
  return 0;
}
void vgw_test_fault(unsigned operation, unsigned kind, unsigned read_operation) {
  mutations = reads = 0; fail_at = operation; mode = kind; fail_read = read_operation;
}
int vgw_test_boot(void) {
  if (setjmp(escape)) return -2;
  vgw_boot_io binding = {NULL, read_mem, write_mem, erase_mem, service, panic_test};
  if (!vgw_boot_init(&binding, key, target)) return -3;
  return vgw_boot_select(&choice);
}
int vgw_test_confirm(void) {
  if (setjmp(escape)) return 0;
  return vgw_boot_confirm();
}
int vgw_test_copy(uint8_t *out, uint32_t size) {
  if (!out || size != sizeof(flash)) return -1;
  memcpy(out, flash, size); return 0;
}
unsigned vgw_test_mutations(void) { return mutations; }
