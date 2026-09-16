#ifndef VGW_MCUBOOT_CONFIG_H
#define VGW_MCUBOOT_CONFIG_H
/* Candidate port, NOT a deployment configuration. Both modes must be present;
 * upstream's Cargo direct-xip feature alone does not enable revert. */
#define MCUBOOT_DIRECT_XIP
#define MCUBOOT_DIRECT_XIP_REVERT
#define MCUBOOT_SIGN_EC256
#define MCUBOOT_USE_MBED_TLS
#define MCUBOOT_IMAGE_NUMBER 1
#define MCUBOOT_MAX_IMG_SECTORS 16
#define MCUBOOT_BOOT_MAX_ALIGN 8
#define MCUBOOT_USE_FLASH_AREA_GET_SECTORS
#define MCUBOOT_HAVE_ASSERT_H
/* FIH profile and real watchdog/entropy integration remain release gates. */
#define MCUBOOT_FIH_PROFILE_OFF
#define MCUBOOT_WATCHDOG_FEED() vgw_boot_service()
#define MCUBOOT_CPU_IDLE() vgw_boot_service()
void vgw_boot_service(void);
#endif
