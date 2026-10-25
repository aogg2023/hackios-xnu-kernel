/* Single-request ARC diagnostic for the observed Apps RSC DRV2 v3.8. */
#ifndef _DEV_ADRENO8_RPMH_PRIVATE_H
#define _DEV_ADRENO8_RPMH_PRIVATE_H
#include "adreno8_private.h"

/* Returns true without MMIO if disabled. An enabled request may run once per kernel;
 * every write requires a cold reset before kernel memory can be reused. */
bool adreno8_rpmh_arc_read(void);

#endif
