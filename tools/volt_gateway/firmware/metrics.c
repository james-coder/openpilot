/* Compile-only size probe. NOT linked into a gateway application. */
#include "authority.h"
#include "observe.h"
uint8_t vgw_authority_ram[sizeof(vgw_authority)];
uint8_t vgw_observer_ram[sizeof(vgw_observer)];
