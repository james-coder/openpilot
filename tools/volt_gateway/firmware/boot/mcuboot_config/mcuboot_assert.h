#ifndef VGW_MCUBOOT_ASSERT_H
#define VGW_MCUBOOT_ASSERT_H
_Noreturn void vgw_boot_panic(void);
#define assert(x) do { if (!(x)) vgw_boot_panic(); } while (0)
#define ASSERT(x) assert(x)
#endif
