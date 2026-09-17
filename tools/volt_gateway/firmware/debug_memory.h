#ifndef VGW_DEBUG_MEMORY_H
#define VGW_DEBUG_MEMORY_H
#include <stdint.h>
#include <stddef.h>
#if !defined(DEBUG) || DEBUG != 1 || !defined(VGW_RAM_PROBE)
#error Arbitrary memory access is restricted to explicit DEBUG RAM bench builds
#endif
uint32_t vgw_debug_read32(uint32_t address);
void vgw_debug_write32(uint32_t address,uint32_t value);
void vgw_debug_memory(const uint8_t *request,size_t length,uint8_t response[16]);
#endif
