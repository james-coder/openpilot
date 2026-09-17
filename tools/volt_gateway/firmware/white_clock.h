#ifndef VGW_WHITE_CLOCK_H
#define VGW_WHITE_CLOCK_H
#include "white_startup.h"
/* Fixed historical White clock profile, not protocol-controlled settings.
 * 16MHz HSE /8 *96 /2 = 96MHz SYSCLK; APB1=24MHz, APB2=48MHz;
 * PLLQ /4 = 48MHz RNG/USB; TIM2=48MHz /48000 = 1kHz.
 * HSE frequency is source-derived, NOT measured on the attached board yet. */
typedef struct { vgw_white_startup *startup; bool ready; } vgw_white_clock;
/* Cold startup only, before sessions or CAN/USB enable. Re-times TIM2 without
 * rolling its counter back; transition time is not a calibrated time source.
 * Failure latches watchdog failure and leaves CAN disabled. */
bool vgw_white_clock_init(vgw_white_clock *, vgw_white_startup *);
/* USB-only protected entry, cannot satisfy CAN's watchdog requirement. */
bool vgw_white_clock_usb_only(vgw_white_clock *,vgw_white_startup *);
bool vgw_white_clock_valid(const vgw_white_clock *);
#endif
