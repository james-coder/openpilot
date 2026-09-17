#ifndef VGW_FLASH_MAP_H
#define VGW_FLASH_MAP_H
#include <stdint.h>
#include <stddef.h>
/* MCUboot flash-map ABI; trusted bindings only, never addresses from CAN. */
struct flash_area { uint8_t fa_id, fa_device_id; uint16_t pad16; uint32_t fa_off, fa_size; };
struct flash_sector { uint32_t fs_off, fs_size; };
int flash_device_base(uint8_t, uintptr_t *);
int flash_area_open(uint8_t, const struct flash_area **);
void flash_area_close(const struct flash_area *);
int flash_area_read(const struct flash_area *, uint32_t, void *, uint32_t);
int flash_area_write(const struct flash_area *, uint32_t, const void *, uint32_t);
int flash_area_erase(const struct flash_area *, uint32_t, uint32_t);
uint32_t flash_area_align(const struct flash_area *);
uint8_t flash_area_erased_val(const struct flash_area *);
int flash_area_get_sectors(int, uint32_t *, struct flash_sector *);
int flash_area_get_sector(const struct flash_area *, uint32_t, struct flash_sector *);
int flash_area_id_from_multi_image_slot(int, int);
int flash_area_id_from_image_slot(int);
int flash_area_id_to_multi_image_slot(int, int);
static inline uint32_t flash_area_get_off(const struct flash_area *a) { return a->fa_off; }
static inline uint32_t flash_area_get_size(const struct flash_area *a) { return a->fa_size; }
static inline uint8_t flash_area_get_device_id(const struct flash_area *a) { return a->fa_device_id; }
static inline uint8_t flash_area_get_id(const struct flash_area *a) { return a->fa_id; }
static inline uint32_t flash_sector_get_off(const struct flash_sector *s) { return s->fs_off; }
static inline uint32_t flash_sector_get_size(const struct flash_sector *s) { return s->fs_size; }
#endif
