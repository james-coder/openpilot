#ifndef VGW_GEOMETRY_H
#define VGW_GEOMETRY_H
/* Labeled White Panda: actual CPU read of FLASHSIZE=1024, DBGMCU DEV_ID=463.
 * The ROM DFU descriptor advertised 1536 KiB; it is NOT authoritative geometry. */
#define VGW_FLASH_BYTES 0x100000U
#define VGW_FLASH_KIB 1024U
#define VGW_SLOT_A 0x40000U
#define VGW_SLOT_B 0xa0000U
#define VGW_SLOT_BYTES 0x60000U
#define VGW_SECTOR_BYTES 0x20000U
#define VGW_SLOT_SECTORS 3U
#endif
