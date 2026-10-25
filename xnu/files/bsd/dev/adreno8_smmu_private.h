/* Register view shared by the SMMU mapper and secure translation setup. */
#ifndef _DEV_ADRENO8_SMMU_PRIVATE_H
#define _DEV_ADRENO8_SMMU_PRIVATE_H
#include "adreno8_memory_private.h"

extern struct adreno8_block adreno8_smmu;
extern uint32_t adreno8_smmu_page, adreno8_smmu_pages;

#define SMMU_S2CR_FAULT          (2u << 16)
#define SMMU_S2CR_SHCFG_NSH      (3u << 8)
#define SMMU_TCR2_TBI            (1u << 5 | 1u << 6)

#endif
