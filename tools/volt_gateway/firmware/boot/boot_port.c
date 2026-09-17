#include "boot_port.h"
#include "crypto.h"
#include "flash_map_backend/flash_map_backend.h"
#include "bootutil/bootutil.h"
#include "bootutil/sign_key.h"
#include "bootutil_priv.h"
#include <string.h>

static const struct flash_area slots[2] = {
  {1, 0, 0, VGW_BOOT_SLOT0, VGW_BOOT_SLOT_SIZE},
  {2, 0, 0, VGW_BOOT_SLOT1, VGW_BOOT_SLOT_SIZE}
};
static vgw_boot_io io;
static bool ready, failed;
static int selected = -1;
static bool no_bootable_image;
static uint8_t indication[3] = {VGW_LED_BOOT, 255, 0};
static void indicate(uint8_t state, uint8_t slot, uint8_t code) {
  indication[0] = state; indication[1] = slot; indication[2] = code;
}
void vgw_boot_get_status(uint8_t out[3]) { if (out) memcpy(out, indication, sizeof(indication)); }
static uint8_t verification_key[91];
static uint8_t expected_target[44];
static const unsigned int key_length = sizeof(verification_key);
const struct bootutil_key bootutil_keys[] = {{verification_key, &key_length}};
const int bootutil_key_cnt = 1;

static int error(void) { failed = true; indicate(VGW_LED_FAULT, 255, VGW_LED_STORAGE); return -1; }
bool vgw_boot_faulted(void) { return failed; }
void vgw_boot_service(void) { if (ready) io.service(io.context); }
_Noreturn void vgw_boot_panic(void) {
  if (!failed) indicate(VGW_LED_FAULT, 255, VGW_LED_INTERNAL);
  failed = true;
  if (ready) io.panic(io.context);
  for (;;) { __asm__ volatile ("" ::: "memory"); }
}

static bool valid(const struct flash_area *area, uint32_t off, uint32_t len) {
  return ready && !failed && (area == &slots[0] || area == &slots[1]) &&
    off <= VGW_BOOT_SLOT_SIZE && len <= VGW_BOOT_SLOT_SIZE - off;
}

bool vgw_boot_init(const vgw_boot_io *binding, const uint8_t public_der[91], const uint8_t target[44]) {
  static const uint8_t prefix[26] = {0x30,0x59,0x30,0x13,0x06,0x07,0x2a,0x86,0x48,0xce,0x3d,0x02,0x01,
    0x06,0x08,0x2a,0x86,0x48,0xce,0x3d,0x03,0x01,0x07,0x03,0x42,0x00};
  ready = false; failed = false; selected = -1;
  no_bootable_image = false;
  indicate(VGW_LED_FAULT, 255, VGW_LED_CONFIG);
  memset(&io, 0, sizeof(io));
  memset(verification_key, 0, sizeof(verification_key));
  if (!binding || !public_der || !target || !binding->read || !binding->write || !binding->erase ||
      !binding->service || !binding->panic || memcmp(public_der, prefix, sizeof(prefix))) return false;
  vgw_crypto crypto;
  bool ok = vgw_crypto_init(&crypto, public_der + sizeof(prefix), 65);
  vgw_crypto_free(&crypto);
  if (!ok) { indicate(VGW_LED_FAULT, 255, VGW_LED_CRYPTO); return false; }
  memcpy(verification_key, public_der, sizeof(verification_key));
  memcpy(expected_target, target, sizeof(expected_target));
  io = *binding;
  ready = true;
  indicate(VGW_LED_BOOT, 255, 0);
  return true;
}

int flash_device_base(uint8_t id, uintptr_t *out) {
  if (!ready || !out || id != 0) return error();
  *out = VGW_BOOT_FLASH_BASE;
  return 0;
}
int flash_area_open(uint8_t id, const struct flash_area **out) {
  if (!out || !ready || failed || id < 1 || id > 2) return error();
  *out = &slots[id - 1];
  return 0;
}
void flash_area_close(const struct flash_area *area) { (void)area; }
uint32_t flash_area_align(const struct flash_area *area) { (void)area; return 4; }
uint8_t flash_area_erased_val(const struct flash_area *area) { (void)area; return 255; }
int flash_area_read(const struct flash_area *area, uint32_t off, void *out, uint32_t size) {
  if (!out || !valid(area, off, size) || !io.read(io.context, area->fa_off + off, out, size)) return error();
  /* MCUboot's image hash loop does not invoke our watchdog macro. Service
   * passive RX between its bounded flash chunks as well as crypto slices.
   * The service callback must not reenter bootutil or dispatch commands. */
  vgw_boot_service();
  return failed ? -1 : 0;
}
int flash_area_write(const struct flash_area *area, uint32_t off, const void *data, uint32_t size) {
  /* Bootutil writes trailer flags/magic only. No image-payload write API here. */
  if (!data || !valid(area, off, size) || off < VGW_BOOT_SLOT_SIZE - 64 ||
      !size || size > 32 || (off & 3) || (size & 3)) return error();
  if (!io.write(io.context, area->fa_off + off, data, size)) return error();
  uint8_t check[32];
  if (!io.read(io.context, area->fa_off + off, check, size) || memcmp(check, data, size)) return error();
  return 0;
}
int flash_area_erase(const struct flash_area *area, uint32_t off, uint32_t size) {
  if (!valid(area, off, size) || !size || off % VGW_BOOT_SECTOR_SIZE || size % VGW_BOOT_SECTOR_SIZE) return error();
  for (uint32_t pos = 0; pos < size; pos += VGW_BOOT_SECTOR_SIZE) {
    if (failed || !io.erase(io.context, area->fa_off + off + pos, VGW_BOOT_SECTOR_SIZE)) return error();
    /* Verify the complete erased sector; a lying/erroring driver cannot claim
     * an invalid image was removed. Service between bounded read chunks. */
    uint8_t check[256];
    for (uint32_t i = 0; i < VGW_BOOT_SECTOR_SIZE; i += sizeof(check)) {
      if (!io.read(io.context, area->fa_off + off + pos + i, check, sizeof(check))) return error();
      for (size_t j = 0; j < sizeof(check); j++) if (check[j] != 255) return error();
      vgw_boot_service();
    }
  }
  return 0;
}
int flash_area_get_sectors(int id, uint32_t *count, struct flash_sector *out) {
  if (!ready || failed || id < 1 || id > 2 || !count || !out || *count < VGW_SLOT_SECTORS) return error();
  *count = VGW_SLOT_SECTORS;
  for (unsigned i = 0; i < VGW_SLOT_SECTORS; i++) out[i] = (struct flash_sector){i * VGW_BOOT_SECTOR_SIZE, VGW_BOOT_SECTOR_SIZE};
  return 0;
}
int flash_area_get_sector(const struct flash_area *area, uint32_t off, struct flash_sector *out) {
  if (!out || !valid(area, off, 1)) return error();
  *out = (struct flash_sector){(off / VGW_BOOT_SECTOR_SIZE) * VGW_BOOT_SECTOR_SIZE, VGW_BOOT_SECTOR_SIZE};
  return 0;
}
int flash_area_id_from_multi_image_slot(int image, int slot) { return image == 0 && slot >= 0 && slot < 2 ? slot + 1 : -1; }
int flash_area_id_from_image_slot(int slot) { return flash_area_id_from_multi_image_slot(0, slot); }
int flash_area_id_to_multi_image_slot(int image, int area) { return image == 0 && area >= 1 && area <= 2 ? area - 1 : -1; }

static int policy(unsigned slot, const struct image_header *h, vgw_boot_choice *out) {
  if (slot>1 || !h || h->ih_magic != IMAGE_MAGIC || h->ih_flags != IMAGE_F_ROM_FIXED || h->ih_load_addr != slots[slot].fa_off ||
      h->ih_hdr_size != VGW_BOOT_HEADER_SIZE || h->ih_img_size < 8 || h->ih_img_size > VGW_BOOT_SLOT_SIZE - 1024) return -3;
  /* Fixed protected schema; caller must also verify the signature. */
  uint8_t binding[52];
  static const uint8_t schema[8] = {0x08,0x69,52,0,0xa0,0,44,0};
  if (h->ih_protect_tlv_size != sizeof(binding)) return -3;
  if (flash_area_read(&slots[slot], h->ih_hdr_size + h->ih_img_size, binding, sizeof(binding))) return -2;
  if (memcmp(binding, schema, sizeof(schema)) || memcmp(binding + 8, expected_target, sizeof(expected_target))) return -3;
  uint32_t vectors[2];
  if (flash_area_read(&slots[slot], h->ih_hdr_size, vectors, sizeof(vectors))) return -2;
  uint32_t start = VGW_BOOT_FLASH_BASE + slots[slot].fa_off + h->ih_hdr_size;
  /* Conservative 128-KiB RAM envelope until exact board RAM is established. */
  if ((vectors[0] & 7) || vectors[0] <= 0x20000000U || vectors[0] > 0x20020000U ||
      !(vectors[1] & 1) || (vectors[1] & ~1U) < start + 8 || (vectors[1] & ~1U) >= start + h->ih_img_size) return -3;
  if (out) *out = (vgw_boot_choice){slot, start, vectors[0], vectors[1]};
  return 0;
}

int vgw_boot_select(vgw_boot_choice *out) {
  selected = -1; no_bootable_image = false;
  if (out) memset(out, 0, sizeof(*out));
  if (!ready || !out) return -3;
  if (failed) return -2;
  struct boot_rsp response;
  memset(&response, 0, sizeof(response));
  FIH_DECLARE(result, FIH_FAILURE);
  FIH_CALL(boot_go, result, &response);
  if (failed) return -2; /* Upstream copy_done failure may otherwise return success. */
  if (FIH_NOT_EQ(result, FIH_SUCCESS)) {
    no_bootable_image=true;
    indicate(VGW_LED_FAULT, 255, VGW_LED_NO_IMAGE); return -1;
  }
  indicate(VGW_LED_FAULT, 255, VGW_LED_IMAGE_POLICY);
  int slot = response.br_image_off == VGW_BOOT_SLOT0 ? 0 : response.br_image_off == VGW_BOOT_SLOT1 ? 1 : -1;
  if (slot < 0 || response.br_flash_dev_id != 0 || !response.br_hdr) return -3;
  vgw_boot_choice choice;
  int rc=policy((unsigned)slot, response.br_hdr, &choice);
  if (rc) return rc;
  struct boot_swap_state state;
  if (boot_read_swap_state(&slots[slot], &state) || failed) return -2;
  /* Handoff integrity check, independent of whether any LED is installed. */
  if (state.magic != BOOT_MAGIC_GOOD || state.copy_done != BOOT_FLAG_SET ||
      (state.image_ok != BOOT_FLAG_SET && state.image_ok != BOOT_FLAG_UNSET)) return -3;
  selected = slot;
  indicate(state.image_ok == BOOT_FLAG_SET ? VGW_LED_RUNNING : VGW_LED_TRIAL, (uint8_t)slot, 0);
  *out = choice;
  return slot;
}

bool vgw_boot_validate_candidate(unsigned slot, uint32_t size, uint32_t version) {
  if (!ready || failed || slot>1 || (int)slot==selected || (selected<0 && !no_bootable_image) ||
      size<VGW_BOOT_HEADER_SIZE+64 || size>VGW_BOOT_SLOT_SIZE-64 || (size&3)) return false;
  struct image_header header;
  if (flash_area_read(&slots[slot],0,&header,sizeof(header)) || policy(slot,&header,NULL)) return false;
  if (header.ih_ver.iv_major || header.ih_ver.iv_minor || header.ih_ver.iv_revision || header.ih_ver.iv_build_num!=version) return false;
  uint32_t end=header.ih_hdr_size+header.ih_img_size+header.ih_protect_tlv_size;
  if (end>size || size-end<4) return false;
  uint8_t tlv[4];
  if (flash_area_read(&slots[slot],end,tlv,sizeof(tlv))) return false;
  uint32_t length=(uint32_t)tlv[2] | ((uint32_t)tlv[3]<<8);
  if (tlv[0]!=7 || tlv[1]!=0x69 || length<4 || length>size-end || size-end-length>3) return false;
  for (uint32_t i=end+length;i<size;i++) {
    uint8_t padding;
    if (flash_area_read(&slots[slot],i,&padding,1) || padding!=255) return false;
  }
  uint8_t tmp[256];
  FIH_DECLARE(result, FIH_FAILURE);
  FIH_CALL(bootutil_img_validate,result,NULL,&header,&slots[slot],tmp,sizeof(tmp),NULL,0,NULL);
  return !failed && FIH_EQ(result,FIH_SUCCESS);
}

bool vgw_boot_resume(unsigned slot,uint32_t vector_address) {
  if (!ready || failed || selected>=0 || no_bootable_image || slot>1 ||
      vector_address!=VGW_BOOT_FLASH_BASE+slots[slot].fa_off+VGW_BOOT_HEADER_SIZE) return false;
  struct image_header header; vgw_boot_choice choice;
  if (flash_area_read(&slots[slot],0,&header,sizeof(header)) || policy(slot,&header,&choice)) return false;
  uint8_t buffer[256];
  FIH_DECLARE(result,FIH_FAILURE);
  FIH_CALL(bootutil_img_validate,result,NULL,&header,&slots[slot],buffer,sizeof(buffer),NULL,0,NULL);
  if (failed || FIH_NOT_EQ(result,FIH_SUCCESS)) return false;
  struct boot_swap_state state;
  if (boot_read_swap_state(&slots[slot],&state) || failed || state.magic!=BOOT_MAGIC_GOOD ||
      state.copy_done!=BOOT_FLAG_SET || (state.image_ok!=BOOT_FLAG_SET && state.image_ok!=BOOT_FLAG_UNSET)) return false;
  selected=(int)slot;
  indicate(state.image_ok==BOOT_FLAG_SET ? VGW_LED_RUNNING : VGW_LED_TRIAL,(uint8_t)slot,0);
  return true;
}

bool vgw_boot_commit_candidate(unsigned slot, uint32_t size, uint32_t version) {
  if (!vgw_boot_validate_candidate(slot,size,version)) return false;
  uint8_t trailer[64];
  if (flash_area_read(&slots[slot],VGW_BOOT_SLOT_SIZE-sizeof(trailer),trailer,sizeof(trailer))) return false;
  for (unsigned i=0;i<sizeof(trailer);i++) if (trailer[i]!=255) return false;
  if (boot_write_magic(&slots[slot]) || failed) return false;
  struct boot_swap_state state;
  return boot_read_swap_state(&slots[slot],&state)==0 && !failed && state.magic==BOOT_MAGIC_GOOD &&
    state.copy_done==BOOT_FLAG_UNSET && state.image_ok==BOOT_FLAG_UNSET;
}

bool vgw_boot_confirm(void) {
  if (!ready || failed || selected < 0) return false;
  const struct flash_area *area = &slots[selected];
  struct boot_swap_state state;
  if (boot_read_swap_state(area, &state) || failed || state.magic != BOOT_MAGIC_GOOD || state.copy_done != BOOT_FLAG_SET) return false;
  if (state.image_ok == BOOT_FLAG_SET) { indicate(VGW_LED_RUNNING, (uint8_t)selected, 0); return true; }
  if (state.image_ok != BOOT_FLAG_UNSET || boot_write_image_ok(area) || failed) return false;
  bool ok = boot_read_swap_state(area, &state) == 0 && !failed && state.image_ok == BOOT_FLAG_SET;
  if (ok) indicate(VGW_LED_RUNNING, (uint8_t)selected, 0);
  return ok;
}
